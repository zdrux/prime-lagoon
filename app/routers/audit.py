from fastapi import APIRouter, Depends, HTTPException, Body
from sqlmodel import Session, select
from typing import List, Optional, Dict
from pydantic import BaseModel
import json
from datetime import datetime

from app.database import get_session
from app.models import AuditRule, AuditBundle, Cluster, ComplianceScore
from app.services.ocp import fetch_resources, get_val

router = APIRouter(
    prefix="/api/audit",
    tags=["audit"],
)

from app.dependencies import admin_required, operator_allowed
from app.models import User

class AuditResult(BaseModel):
    cluster_name: str
    rule_name: str
    status: str # "PASS", "FAIL", "ERROR", "SKIP"
    detail: str
    cluster_id: int
    bundle_name: Optional[str] = None
    bundle_id: Optional[int] = None
    resource_kind: Optional[str] = None
    namespace: Optional[str] = None
    failed_resources: Optional[List[Dict]] = None # List of resource snapshots

class BundleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    match_datacenter: Optional[str] = None
    match_environment: Optional[str] = None
    tags: Optional[str] = None # JSON

class BundleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    match_datacenter: Optional[str] = None
    match_environment: Optional[str] = None
    tags: Optional[str] = None

# --- Bundles ---
@router.post("/bundles", response_model=AuditBundle)
def create_bundle(bundle: BundleCreate, session: Session = Depends(get_session), _: User = Depends(admin_required)):
    db_bundle = AuditBundle.model_validate(bundle)
    session.add(db_bundle)
    session.commit()
    session.refresh(db_bundle)
    return db_bundle

@router.get("/bundles", response_model=List[AuditBundle])
def get_bundles(session: Session = Depends(get_session), _: User = Depends(operator_allowed)):
    return session.exec(select(AuditBundle)).all()

@router.put("/bundles/{bundle_id}", response_model=AuditBundle)
def update_bundle(bundle_id: int, bundle_update: BundleUpdate, session: Session = Depends(get_session), _: User = Depends(admin_required)):
    db_bundle = session.get(AuditBundle, bundle_id)
    if not db_bundle:
        raise HTTPException(status_code=404, detail="Bundle not found")
    
    data = bundle_update.model_dump(exclude_unset=True)
    for k,v in data.items():
        setattr(db_bundle, k, v)
    
    session.add(db_bundle)
    session.commit()
    session.refresh(db_bundle)
    return db_bundle

@router.delete("/bundles/{bundle_id}")
def delete_bundle(bundle_id: int, session: Session = Depends(get_session), _: User = Depends(admin_required)):
    bundle = session.get(AuditBundle, bundle_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="Bundle not found")
    
    rules = session.exec(select(AuditRule).where(AuditRule.bundle_id == bundle_id)).all()
    for r in rules:
        session.delete(r)
        
    session.delete(bundle)
    session.commit()
    return {"ok": True}

# --- Rules ---
@router.post("/rules", response_model=AuditRule)
def create_rule(rule: AuditRule, session: Session = Depends(get_session), _: User = Depends(admin_required)):
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule

@router.get("/rules", response_model=List[AuditRule])
def get_rules(session: Session = Depends(get_session), _: User = Depends(operator_allowed)):
    return session.exec(select(AuditRule)).all()

@router.put("/rules/{rule_id}", response_model=AuditRule)
def update_rule(rule_id: int, rule_update: AuditRule, session: Session = Depends(get_session), _: User = Depends(admin_required)):
    db_rule = session.get(AuditRule, rule_id)
    if not db_rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    
    rule_data = rule_update.model_dump(exclude_unset=True)
    for key, value in rule_data.items():
        setattr(db_rule, key, value)
        
    session.add(db_rule)
    session.commit()
    session.refresh(db_rule)
    return db_rule

@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, session: Session = Depends(get_session), _: User = Depends(admin_required)):
    rule = session.get(AuditRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    session.delete(rule)
    session.commit()
    return {"ok": True}

@router.post("/rules/{rule_id}/duplicate", response_model=AuditRule)
def duplicate_rule(rule_id: int, session: Session = Depends(get_session), _: User = Depends(admin_required)):
    db_rule = session.get(AuditRule, rule_id)
    if not db_rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    
    # Create a clone
    new_rule = AuditRule.model_validate(db_rule)
    new_rule.id = None # Let DB assign new ID
    new_rule.name = f"Copy of {new_rule.name}"
    
    session.add(new_rule)
    session.commit()
    session.refresh(new_rule)
    return new_rule

@router.post("/rules/{rule_id}/toggle")
def toggle_rule(rule_id: int, session: Session = Depends(get_session), _: User = Depends(admin_required)):
    db_rule = session.get(AuditRule, rule_id)
    if not db_rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    
    db_rule.is_enabled = not db_rule.is_enabled
    session.add(db_rule)
    session.commit()
    session.refresh(db_rule)
    return {"ok": True, "is_enabled": db_rule.is_enabled}

# --- Export/Import ---

class ExportRequest(BaseModel):
    rule_ids: List[int]
    bundle_ids: List[int]

@router.post("/export")
def export_rules(req: ExportRequest, session: Session = Depends(get_session)):
    bundles = []
    if req.bundle_ids:
        bundles = session.exec(select(AuditBundle).where(AuditBundle.id.in_(req.bundle_ids))).all()
    
    # Fetch rules explicitly requested
    rules = []
    if req.rule_ids:
        rules = session.exec(select(AuditRule).where(AuditRule.id.in_(req.rule_ids))).all()
    
    # Also fetch all rules belonging to exported bundles to ensure consistency
    if req.bundle_ids:
        bundle_rules = session.exec(select(AuditRule).where(AuditRule.bundle_id.in_(req.bundle_ids))).all()
        # Merge and avoid duplicates
        rule_ids_present = {r.id for r in rules}
        for br in bundle_rules:
            if br.id not in rule_ids_present:
                rules.append(br)
    
    return {
        "version": "1.0",
        "exported_at": datetime.now().isoformat(),
        "bundles": [b.model_dump() for b in bundles],
        "rules": [r.model_dump() for r in rules]
    }

class ImportData(BaseModel):
    bundles: List[dict]
    rules: List[dict]

@router.post("/import/preview")
def import_preview(data: ImportData, session: Session = Depends(get_session)):
    # Check for name conflicts
    existing_bundles = session.exec(select(AuditBundle)).all()
    existing_rules = session.exec(select(AuditRule)).all()
    
    bundle_names = {b.name: b for b in existing_bundles}
    rule_names = {r.name: r for r in existing_rules}
    
    preview = {
        "bundles": [],
        "rules": []
    }
    
    for b in data.bundles:
        conflict = b["name"] in bundle_names
        preview["bundles"].append({
            "name": b["name"],
            "status": "CONFLICT" if conflict else "NEW",
            "existing_id": bundle_names[b["name"]].id if conflict else None,
            "data": b
        })
        
    for r in data.rules:
        conflict = r["name"] in rule_names
        preview["rules"].append({
            "name": r["name"],
            "status": "CONFLICT" if conflict else "NEW",
            "existing_id": rule_names[r["name"]].id if conflict else None,
            "data": r
        })
        
    return preview

class ImportConfirmRow(BaseModel):
    name: str
    action: str # "CREATE", "OVERWRITE", "SKIP"
    existing_id: Optional[int] = None
    data: dict

class ImportConfirmRequest(BaseModel):
    bundles: List[ImportConfirmRow]
    rules: List[ImportConfirmRow]

@router.post("/import/confirm")
def import_confirm(req: ImportConfirmRequest, session: Session = Depends(get_session), _: User = Depends(admin_required)):
    # 1. Process Bundles
    bundle_id_map = {} # Maps old (exported) ID to new DB ID
    
    for b_req in req.bundles:
        if b_req.action == "SKIP":
            continue
            
        if b_req.action == "OVERWRITE" and b_req.existing_id:
            db_bundle = session.get(AuditBundle, b_req.existing_id)
            if db_bundle:
                for k, v in b_req.data.items():
                    if k != "id": setattr(db_bundle, k, v)
                session.add(db_bundle)
                bundle_id_map[b_req.data.get("id")] = db_bundle.id
        else:
            # CREATE
            new_bundle = AuditBundle(**{k: v for k, v in b_req.data.items() if k != "id"})
            session.add(new_bundle)
            session.flush() # Get ID
            bundle_id_map[b_req.data.get("id")] = new_bundle.id
            
    # 2. Process Rules
    for r_req in req.rules:
        if r_req.action == "SKIP":
            continue
            
        # Update bundle_id reference if it was part of the import
        r_data = r_req.data.copy()
        old_bundle_id = r_data.get("bundle_id")
        if old_bundle_id in bundle_id_map:
            r_data["bundle_id"] = bundle_id_map[old_bundle_id]
            
        if r_req.action == "OVERWRITE" and r_req.existing_id:
            db_rule = session.get(AuditRule, r_req.existing_id)
            if db_rule:
                for k, v in r_data.items():
                    if k != "id": setattr(db_rule, k, v)
                session.add(db_rule)
        else:
            # CREATE
            new_rule = AuditRule(**{k: v for k, v in r_data.items() if k != "id"})
            session.add(new_rule)
            
    session.commit()
    return {"ok": True}

# --- Matching Logic ---

def parse_tags(tag_str: Optional[str]) -> Dict[str, str]:
    if not tag_str:
        return {}
    try:
        return json.loads(tag_str)
    except:
        return {}

def tags_match(target_tags: Dict[str, str], cluster_tags: Dict[str, str]) -> bool:
    """
    Returns True if cluster_tags is a superset of target_tags.
    i.e., Cluster must have ALL tags defined in the target.
    """
    if not target_tags:
        return True # specific target has no tags requirement -> matches all (subject to other filters)
        
    for k, v in target_tags.items():
        if k not in cluster_tags or cluster_tags[k] != v:
            return False
    return True

def check_scope_match(scope_val: Optional[str], cluster_val: Optional[str]) -> bool:
    """
    Checks if a cluster value matches the scope definition.
    Scope can be:
    - None/Empty -> Match All
    - Exact String -> Match Exact
    - JSON List String -> Match if in list
    """
    if not scope_val:
        return True
    
    # Handle simple Exact Match first (common case)
    if scope_val == cluster_val:
        return True

    # Try parsing as JSON list
    try:
        scope_list = json.loads(scope_val)
        if isinstance(scope_list, list):
            return cluster_val in scope_list
    except:
        pass
        
    return False

def get_nested_value(data: dict, path: str):
    return get_val(data, path, case_insensitive=True)

class TargetRequest(BaseModel):
    datacenter: Optional[str] = None
    environment: Optional[str] = None
    tags: Optional[str] = None
    bundle_id: Optional[int] = None

class MatchRequest(BaseModel):
    tags: Optional[str] = None # JSON target tags
    match_datacenter: Optional[str] = None
    match_environment: Optional[str] = None

class ClusterMatch(BaseModel):
    id: int
    name: str

@router.post("/calculate-targets", response_model=List[ClusterMatch])
def calculate_targets(req: TargetRequest, session: Session = Depends(get_session)):
    clusters = session.exec(select(Cluster)).all()
    
    if req.bundle_id:
        bundle = session.get(AuditBundle, req.bundle_id)
        if not bundle:
            raise HTTPException(status_code=404, detail="Bundle not found")
        target_tags = parse_tags(bundle.tags)
        dc = bundle.match_datacenter
        env = bundle.match_environment
    else:
        target_tags = parse_tags(req.tags)
        dc = req.datacenter
        env = req.environment
        
    matches = []
    for c in clusters:
        if not check_scope_match(dc, c.datacenter):
            continue
        if not check_scope_match(env, c.environment):
            continue
        c_tags = parse_tags(c.tags)
        if tags_match(target_tags, c_tags):
            matches.append(ClusterMatch(id=c.id, name=c.name))
            
    return matches

@router.post("/match_clusters", response_model=List[ClusterMatch])
def match_clusters(req: MatchRequest, session: Session = Depends(get_session)):
    clusters = session.exec(select(Cluster)).all()
    target_tags = parse_tags(req.tags)
    matches = []
    
    for c in clusters:
        # Check explicit DC/Env scope if provided
        if not check_scope_match(req.match_datacenter, c.datacenter):
            continue
        if not check_scope_match(req.match_environment, c.environment):
            continue

        c_tags = parse_tags(c.tags)
        if tags_match(target_tags, c_tags):
            matches.append(ClusterMatch(id=c.id, name=c.name))
            
    return matches

@router.get("/compliance/latest", response_model=List[ComplianceScore])
def get_latest_scores(session: Session = Depends(get_session)):
    """ Returns the latest compliance score for each cluster """
    clusters = session.exec(select(Cluster)).all()
    scores = []
    for c in clusters:
        # Get latest by timestamp
        score = session.exec(
            select(ComplianceScore)
            .where(ComplianceScore.cluster_id == c.id)
            .order_by(ComplianceScore.timestamp.desc())
        ).first()
        if score:
            scores.append(score)
    return scores

class RunAuditRequest(BaseModel):
    cluster_id: Optional[int] = None
    rule_ids: Optional[List[int]] = None
    bundle_ids: Optional[List[int]] = None

@router.post("/run", response_model=List[AuditResult])
def run_audit(
    cluster_id: Optional[int] = None, 
    req: Optional[RunAuditRequest] = None,
    session: Session = Depends(get_session),
    _: User = Depends(admin_required)
):
    # Determine cluster_id from query param OR body
    target_cluster_id = cluster_id or (req.cluster_id if req else None)

    # Load rules and bundles
    rules = session.exec(select(AuditRule)).all()
    bundles = session.exec(select(AuditBundle)).all()
    bundle_map = {b.id: b for b in bundles}
    
    # Target Clusters
    query = select(Cluster)
    if target_cluster_id:
        query = query.where(Cluster.id == target_cluster_id)
    clusters = session.exec(query).all()
    
    results = []
    
    # Custom Selection override
    is_custom_run = req and (req.rule_ids or req.bundle_ids)
    
    for cluster in clusters:
            # Delegate to Service
            from app.services.compliance import evaluate_cluster_compliance
            
            # For run-audit endpoint, we might need to filter rules if it's a custom run
            rules_to_run = rules
            if is_custom_run:
                rules_to_run = []
                for r in rules:
                    if (req.rule_ids and r.id in req.rule_ids) or \
                       (req.bundle_ids and r.bundle_id in req.bundle_ids):
                        rules_to_run.append(r)
            
            try:
                # The service saves the score, but we also want the detailed AuditResult list returned here.
                # The service logic mirrors what was here, but for "Run Now" from UI we usually want the detailed JSON response.
                # Since I extracted the logic to 'evaluate_cluster_compliance' which returns a Score object (compact), 
                # I might need to adapt the service to return details too, or keep the logic here for interactive runs 
                # and use the service mainly for the background poller.
                
                # However, to avoid duplication, I SHOULD have made the service return the details.
                # Let's check what I wrote in the service.
                # The service function returns 'db_score'.
                # 'db_score.results_json' contains the compact results.
                
                score = evaluate_cluster_compliance(session, cluster, rules_to_run, bundles)
                
                if score:
                    # Convert compact results back to AuditResult (roughly) for the UI
                    compact = json.loads(score.results_json)
                    for c in compact:
                        results.append(AuditResult(
                            cluster_name=cluster.name,
                            cluster_id=cluster.id,
                            rule_name=c["rule_name"],
                            bundle_name=c.get("bundle_name"),
                            status=c["status"],
                            detail=c["detail"],
                            resource_kind=c["resource_kind"],
                            namespace=c.get("namespace"),
                            failed_resources=c.get("failed_resources")
                        ))
            except Exception as e:
                # Fallback if service fails completely
                results.append(AuditResult(
                    cluster_name=cluster.name,
                    cluster_id=cluster.id,
                    rule_name="System",
                    status="ERROR",
                    detail=str(e),
                    resource_kind="N/A"
                ))
            
            # Since evaluate_cluster_compliance iterates all rules for a cluster, we break the inner loop 
            # of the original code which was iterating rules.
            # wait, the original code structure was:
            # for cluster:
            #   for rule:
            #     check...
            
            # My service does:
            # for rule in rules: ...
            
            # So I should simple call the service once per cluster and NOT iterate rules here.
            break

    return results
                

@router.get("/history/{cluster_id}")
def get_audit_history(cluster_id: int, session: Session = Depends(get_session)):
    """Returns compact history of compliance scores for a cluster for graphing."""
    scores = session.exec(
        select(ComplianceScore)
        .where(ComplianceScore.cluster_id == cluster_id)
        .order_by(ComplianceScore.timestamp.asc())
    ).all()
    
    return [
        {
            "id": s.id,
            "timestamp": s.timestamp,
            "score": s.score,
            "passed": s.passed_count,
            "total": s.total_count
        }
        for s in scores
    ]

@router.get("/scores/{score_id}")
def get_score_details(score_id: int, session: Session = Depends(get_session)):
    """Returns the details (rule statuses) for a specific historical run."""
    score = session.get(ComplianceScore, score_id)
    if not score:
        raise HTTPException(status_code=404, detail="Score not found")
    
    # Parse the stored JSON
    details = []
    if score.results_json:
        try:
            raw_details = json.loads(score.results_json)
            # The user asked for "Simple history... just include rule names that succeeded or failed"
            # We filter the full details to be lightweight
            for r in raw_details:
                details.append({
                    "rule_name": r.get("rule_name", "Unknown"),
                    "status": r.get("status", "UNKNOWN"),
                    "message": r.get("detail", "")
                })
        except:
            details = []
            
    return {
        "id": score.id,
        "timestamp": score.timestamp,
        "score": score.score,
        "results": details
    }
