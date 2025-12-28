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
    session: Session = Depends(get_session)
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
        cluster_tags = parse_tags(cluster.tags)
        
        for rule in rules:
            # Determine scope
            rule_dc = rule.match_datacenter
            rule_env = rule.match_environment
            bundle_name = "Ad-hoc"
            bundle_id = None
            
            target_tags = parse_tags(rule.tags)

            # --- Rule Filtering ---
            if is_custom_run:
                # If custom selection is provided, ONLY run selected rules/bundles
                # and bypass scope/trait matching.
                is_selected = False
                if req.rule_ids and rule.id in req.rule_ids:
                    is_selected = True
                if req.bundle_ids and rule.bundle_id in req.bundle_ids:
                    is_selected = True
                
                if not is_selected:
                    continue
                
                # Setup bundle info for results
                if rule.bundle_id and rule.bundle_id in bundle_map:
                    bundle_name = bundle_map[rule.bundle_id].name
                    bundle_id = rule.bundle_id
            else:
                # Standard Mode: Match by Traits/Scope
                if rule.bundle_id and rule.bundle_id in bundle_map:
                    bundle = bundle_map[rule.bundle_id]
                    bundle_name = bundle.name
                    bundle_id = bundle.id
                    
                    # Check Bundle Scope
                    if not check_scope_match(bundle.match_datacenter, cluster.datacenter):
                        continue
                    if not check_scope_match(bundle.match_environment, cluster.environment):
                        continue
                    if not tags_match(parse_tags(bundle.tags), cluster_tags):
                        continue
                else:
                    # Ad-hoc Rule Scope
                    if not check_scope_match(rule_dc, cluster.datacenter):
                        continue
                    if not check_scope_match(rule_env, cluster.environment):
                        continue
                    if not tags_match(target_tags, cluster_tags):
                        continue
            
            # If we reached here, rule applies.
            try:
                resources = fetch_resources(cluster, rule.api_version, rule.resource_kind, rule.namespace)
                
                # Filter resources by name if specified
                if rule.match_resource_name:
                    if rule.operator == "contains":
                        resources = [r for r in resources if rule.match_resource_name in (get_val(r, 'metadata.name') or "")]
                    else: # Default/Equals
                        resources = [r for r in resources if get_val(r, 'metadata.name') == rule.match_resource_name]

                # --- Shared Condition Processing ---
                passed_items = []
                failed_items_details = []
                failed_snapshots = []
                
                # Prepare conditions
                conditions = []
                if rule.field_path:
                    conditions.append({"path": rule.field_path, "op": rule.operator, "val": rule.expected_value})
                
                if rule.extra_conditions:
                    try:
                        extras = json.loads(rule.extra_conditions)
                        conditions.extend(extras)
                    except: pass

                for item in resources:
                    item_data = item.to_dict() if hasattr(item, 'to_dict') else item
                    item_name = item_data.get('metadata', {}).get('name', '?')
                    
                    if not conditions:
                        # If no conditions defined, every resource matches (e.g. basic EXISTENCE check)
                        passed_items.append(item_data)
                        continue

                    cond_results = []
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
                            exp_str = str(exp).lower()
                            if isinstance(actual, list):
                                # Smarter list matching
                                m = False
                                for item in actual:
                                    if isinstance(item, str):
                                        if exp_str in item.lower():
                                            m = True; break
                                    elif isinstance(item, dict):
                                        # Relaxed logic: Check string dump of item to allow matching 'type', 'kind', etc.
                                        # (Restores 'grep-style' behavior for objects in list while keeping case-insensitivity)
                                        if exp_str in str(item).lower():
                                            m = True; break
                            else:
                                m = exp_str in str(actual).lower() if actual else False
                        
                        cond_results.append({"match": m, "cond": cond, "actual": actual})
                    
                    # Evaluate item based on logic (AND/OR)
                    passed_list = [c["match"] for c in cond_results]
                    item_pass = any(passed_list) if rule.condition_logic == "OR" else all(passed_list)
                    
                    if item_pass:
                        passed_items.append(item_data)
                    else:
                        # Build failure details for this item
                        item_failed_details = []
                        for cm in cond_results:
                            if not cm["match"]:
                                c = cm["cond"]
                                actual_str = f"'{cm['actual']}'" if cm['actual'] is not None else "None"
                                item_failed_details.append(f"'{c['path']}' {c['op']} '{c['val']}' (Actual: {actual_str})")
                        
                        failed_items_details.append(f"Item '{item_name}' failed: " + "; ".join(item_failed_details))
                        if len(failed_snapshots) < 3:
                            failed_snapshots.append(item_data)

                # --- Decision Stage based on Check Type ---
                if rule.check_type == "EXISTENCE":
                    if passed_items:
                        results.append(AuditResult(
                            cluster_name=cluster.name, cluster_id=cluster.id,
                            rule_name=rule.name, bundle_name=bundle_name, bundle_id=bundle_id,
                            status="PASS", detail=f"Found {len(passed_items)} matching resource(s)",
                            resource_kind=rule.resource_kind, namespace=rule.namespace
                        ))
                    else:
                        results.append(AuditResult(
                            cluster_name=cluster.name, cluster_id=cluster.id,
                            rule_name=rule.name, bundle_name=bundle_name, bundle_id=bundle_id,
                            status="FAIL", detail=f"No {rule.resource_kind} found matching conditions",
                            resource_kind=rule.resource_kind, namespace=rule.namespace
                        ))

                elif rule.check_type == "FORBIDDANCE":
                    if not passed_items:
                        results.append(AuditResult(
                            cluster_name=cluster.name, cluster_id=cluster.id,
                            rule_name=rule.name, bundle_name=bundle_name, bundle_id=bundle_id,
                            status="PASS", detail=f"No matching {rule.resource_kind} found (as expected)",
                            resource_kind=rule.resource_kind, namespace=rule.namespace
                        ))
                    else:
                        results.append(AuditResult(
                            cluster_name=cluster.name, cluster_id=cluster.id,
                            rule_name=rule.name, bundle_name=bundle_name, bundle_id=bundle_id,
                            status="FAIL", detail=f"Found {len(passed_items)} matching resource(s) that should not exist",
                            resource_kind=rule.resource_kind, namespace=rule.namespace,
                            failed_resources=passed_items[:3]
                        ))

                else: # Default: VALIDATION
                    if not resources:
                        results.append(AuditResult(
                            cluster_name=cluster.name, cluster_id=cluster.id,
                            rule_name=rule.name, bundle_name=bundle_name, bundle_id=bundle_id,
                            status="SKIP", detail=f"No {rule.resource_kind} found to validate",
                            resource_kind=rule.resource_kind, namespace=rule.namespace
                        ))
                    elif not failed_items_details:
                        results.append(AuditResult(
                            cluster_name=cluster.name, cluster_id=cluster.id,
                            rule_name=rule.name, bundle_name=bundle_name, bundle_id=bundle_id,
                            status="PASS", detail=f"Verified on {len(passed_items)} items",
                            resource_kind=rule.resource_kind, namespace=rule.namespace
                        ))
                    else:
                        results.append(AuditResult(
                            cluster_name=cluster.name, cluster_id=cluster.id,
                            rule_name=rule.name, bundle_name=bundle_name, bundle_id=bundle_id,
                            status="FAIL", detail="; ".join(failed_items_details[:3]),
                            resource_kind=rule.resource_kind, namespace=rule.namespace,
                            failed_resources=failed_snapshots
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
    if target_cluster_id:
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
