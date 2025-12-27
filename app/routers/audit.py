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
def create_bundle(bundle: BundleCreate, session: Session = Depends(get_session)):
    db_bundle = AuditBundle.model_validate(bundle)
    session.add(db_bundle)
    session.commit()
    session.refresh(db_bundle)
    return db_bundle

@router.get("/bundles", response_model=List[AuditBundle])
def get_bundles(session: Session = Depends(get_session)):
    return session.exec(select(AuditBundle)).all()

@router.put("/bundles/{bundle_id}", response_model=AuditBundle)
def update_bundle(bundle_id: int, bundle_update: BundleUpdate, session: Session = Depends(get_session)):
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
def delete_bundle(bundle_id: int, session: Session = Depends(get_session)):
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
def create_rule(rule: AuditRule, session: Session = Depends(get_session)):
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule

@router.get("/rules", response_model=List[AuditRule])
def get_rules(session: Session = Depends(get_session)):
    return session.exec(select(AuditRule)).all()

@router.put("/rules/{rule_id}", response_model=AuditRule)
def update_rule(rule_id: int, rule_update: AuditRule, session: Session = Depends(get_session)):
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
def delete_rule(rule_id: int, session: Session = Depends(get_session)):
    rule = session.get(AuditRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    session.delete(rule)
    session.commit()
    return {"ok": True}

@router.post("/rules/{rule_id}/duplicate", response_model=AuditRule)
def duplicate_rule(rule_id: int, session: Session = Depends(get_session)):
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
def import_confirm(req: ImportConfirmRequest, session: Session = Depends(get_session)):
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

def get_nested_value(data: dict, path: str):
    return get_val(data, path)

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
        if dc and dc != c.datacenter:
            continue
        if env and env != c.environment:
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
        if req.match_datacenter and req.match_datacenter != c.datacenter:
            continue
        if req.match_environment and req.match_environment != c.environment:
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

@router.post("/run", response_model=List[AuditResult])
def run_audit(cluster_id: Optional[int] = None, session: Session = Depends(get_session)):
    rules = session.exec(select(AuditRule)).all()
    bundles = session.exec(select(AuditBundle)).all()
    bundle_map = {b.id: b for b in bundles}
    
    query = select(Cluster)
    if cluster_id:
        query = query.where(Cluster.id == cluster_id)
    clusters = session.exec(query).all()
    
    results = []
    
    for cluster in clusters:
        cluster_tags = parse_tags(cluster.tags)
        
        for rule in rules:
            # Determine scope
            rule_dc = rule.match_datacenter
            rule_env = rule.match_environment
            bundle_name = "Ad-hoc"
            bundle_id = None
            
            target_tags = parse_tags(rule.tags)
            
            if rule.bundle_id and rule.bundle_id in bundle_map:
                bundle = bundle_map[rule.bundle_id]
                bundle_name = bundle.name
                bundle_id = bundle.id
                # Bundle scope overrides/defines rule scope? 
                # Usually Bundle is the grouper.
                # Let's say if Bundle has tags/dc/env, check those. 
                
                # Check Bundle Scope
                # 1. DC/Env (Legacy support)
                if bundle.match_datacenter and bundle.match_datacenter != cluster.datacenter:
                    continue
                if bundle.match_environment and bundle.match_environment != cluster.environment:
                    continue
                
                # 2. Tags
                bundle_tags = parse_tags(bundle.tags)
                if not tags_match(bundle_tags, cluster_tags):
                    continue
                    
            else:
                # Ad-hoc Rule Scope
                if rule_dc and rule_dc != cluster.datacenter:
                    continue
                if rule_env and rule_env != cluster.environment:
                    continue
                if not tags_match(target_tags, cluster_tags):
                    continue
            
            # If we reached here, rule applies.
            try:
                resources = fetch_resources(cluster, rule.api_version, rule.resource_kind, rule.namespace)
                
                # Filter by name if specified
                if rule.match_resource_name:
                    resources = [r for r in resources if get_val(r, 'metadata.name') == rule.match_resource_name]

                if not resources:
                    results.append(AuditResult(
                        cluster_name=cluster.name,
                        cluster_id=cluster.id,
                        rule_name=rule.name,
                        bundle_name=bundle_name,
                        bundle_id=bundle_id,
                        status="SKIP",
                        detail=f"No {rule.resource_kind} found",
                        resource_kind=rule.resource_kind,
                        namespace=rule.namespace
                    ))
                    continue
                    
                fail_reasons = []
                failed_snapshots = []
                pass_count = 0
                
                # Prepare conditions
                conditions = [
                    {"path": rule.field_path, "op": rule.operator, "val": rule.expected_value}
                ]
                if rule.extra_conditions:
                    try:
                        extras = json.loads(rule.extra_conditions)
                        conditions.extend(extras)
                    except: pass

                for item in resources:
                    item_data = item.to_dict() if hasattr(item, 'to_dict') else item
                    item_name = item_data.get('metadata', {}).get('name', '?')
                    
                    cond_matches = []
                    for cond in conditions:
                        actual = get_nested_value(item_data, cond["path"])
                        op = cond["op"]
                        exp = cond["val"]
                        
                        m = False
                        if op == "exists":
                            m = actual is not None
                        elif op == "equals":
                            m = str(actual) == str(exp)
                        elif op == "contains":
                            m = str(exp) in str(actual) if actual else False
                        
                        cond_matches.append({"match": m, "cond": cond, "actual": actual})
                    
                    # Evaluate item based on logic
                    passed_list = [c["match"] for c in cond_matches]
                    item_pass = any(passed_list) if rule.condition_logic == "OR" else all(passed_list)
                    
                    if not item_pass:
                        # Capture snapshot for first 3 failures
                        if len(failed_snapshots) < 3:
                            failed_snapshots.append(item_data)

                        # Build a descriptive failure string for this item
                        failed_details = []
                        for cm in cond_matches:
                            if not cm["match"]:
                                c = cm["cond"]
                                actual_str = f"'{cm['actual']}'" if cm['actual'] is not None else "None"
                                failed_details.append(f"'{c['path']}' {c['op']} '{c['val']}' (Actual: {actual_str})")
                        
                        fail_reasons.append(f"Item '{item_name}' failed: " + "; ".join(failed_details))
                    else:
                        pass_count += 1
                        
                if fail_reasons:
                    results.append(AuditResult(
                        cluster_name=cluster.name,
                        cluster_id=cluster.id,
                        rule_name=rule.name,
                        bundle_name=bundle_name,
                        bundle_id=bundle_id,
                        status="FAIL",
                        detail="; ".join(fail_reasons[:3]),
                        resource_kind=rule.resource_kind,
                        namespace=rule.namespace,
                        failed_resources=failed_snapshots
                    ))
                else:
                    results.append(AuditResult(
                        cluster_name=cluster.name,
                        cluster_id=cluster.id,
                        rule_name=rule.name,
                        bundle_name=bundle_name,
                        bundle_id=bundle_id,
                        status="PASS",
                        detail=f"Verified on {pass_count} items",
                        resource_kind=rule.resource_kind,
                        namespace=rule.namespace
                    ))
                    
            except Exception as e:
                error_msg = str(e)
                # Detect common K8s API errors and provide user-friendly messages
                if "403" in error_msg or "Forbidden" in error_msg:
                    error_msg = f"Forbidden: Service account lacks permissions for {rule.resource_kind} ({rule.api_version})"
                elif "404" in error_msg or "Not Found" in error_msg:
                    error_msg = f"Not Found: Resource kind {rule.resource_kind} or API {rule.api_version} not available on this cluster"

                results.append(AuditResult(
                    cluster_name=cluster.name,
                    cluster_id=cluster.id,
                    rule_name=rule.name,
                    bundle_name=bundle_name,
                    bundle_id=bundle_id,
                    status="ERROR",
                    detail=error_msg,
                    resource_kind=rule.resource_kind,
                    namespace=rule.namespace
                ))
                
    # Persist scores per cluster
    if cluster_id:
        cluster_results = results
        total = len(cluster_results)
        passed = len([r for r in cluster_results if r.status == 'PASS'])
        
        # Serialize subset of result fields to save space
        compact_results = [
            {
                "rule_name": r.rule_name,
                "bundle_name": r.bundle_name,
                "status": r.status,
                "detail": r.detail,
                "resource_kind": r.resource_kind,
                "namespace": r.namespace,
                "failed_resources": r.failed_resources
            }
            for r in cluster_results
        ]
        
        if total > 0:
            score_val = (passed / total) * 100
            db_score = ComplianceScore(
                cluster_id=cluster_id,
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                passed_count=passed,
                total_count=total,
                score=round(score_val, 1),
                results_json=json.dumps(compact_results)
            )
            session.add(db_score)
            session.commit()

    return results
