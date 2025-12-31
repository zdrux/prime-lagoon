import json
import logging
from datetime import datetime
from typing import List, Optional, Dict
from sqlmodel import Session, select

from app.models import AuditRule, AuditBundle, Cluster, ComplianceScore
from app.services.ocp import fetch_resources, get_val
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Reusing the models from audit.py might be circular if not careful.
# Ideally, these Pydantic models should be in a shared schemas.py or models.py if they are API schemas.
# For now, I will redefine a minimal internal result structure or import if safe.
# Since AuditResult is a Pydantic model used for API response, let's keep it there or duplicate lightweight version.
# I'll define a lightweight class for internal use to avoid circular imports with routers if possible.

class ComplianceResult:
    def __init__(self, cluster_name, rule_name, status, detail, resource_kind, namespace, failed_resources=None, bundle_name=None):
        self.cluster_name = cluster_name
        self.rule_name = rule_name
        self.status = status
        self.detail = detail
        self.resource_kind = resource_kind
        self.namespace = namespace
        self.failed_resources = failed_resources
        self.bundle_name = bundle_name

def parse_tags(tag_str: Optional[str]) -> Dict[str, str]:
    if not tag_str:
        return {}
    try:
        return json.loads(tag_str)
    except:
        return {}

def tags_match(target_tags: Dict[str, str], cluster_tags: Dict[str, str]) -> bool:
    if not target_tags:
        return True
    for k, v in target_tags.items():
        if k not in cluster_tags or cluster_tags[k] != v:
            return False
    return True

def check_scope_match(scope_val: Optional[str], cluster_val: Optional[str]) -> bool:
    if not scope_val:
        return True
    if scope_val == cluster_val:
        return True
    try:
        scope_list = json.loads(scope_val)
        if isinstance(scope_list, list):
            return cluster_val in scope_list
    except:
        pass
    return False

def get_nested_value(data: dict, path: str):
    return get_val(data, path, case_insensitive=True)

def evaluate_cluster_compliance(session: Session, cluster: Cluster, rules: List[AuditRule], bundles: List[AuditBundle], run_timestamp: Optional[datetime] = None) -> Optional[ComplianceScore]:
    """
    Evaluates all applicable rules for a single cluster and saves a ComplianceScore.
    """
    logger.info(f"Running compliance check for cluster: {cluster.name}")
    
    bundle_map = {b.id: b for b in bundles}
    cluster_tags = parse_tags(cluster.tags)
    
    results = []
    
    for rule in rules:
        if not rule.is_enabled:
            continue

        # Determine scope
        target_tags = parse_tags(rule.tags)
        bundle_name = "Ad-hoc"
        bundle_id = None

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
            if not check_scope_match(rule.match_datacenter, cluster.datacenter):
                continue
            if not check_scope_match(rule.match_environment, cluster.environment):
                continue
            if not tags_match(target_tags, cluster_tags):
                continue

        # Rule applies, execute it
        try:
            resources = fetch_resources(cluster, rule.api_version, rule.resource_kind, rule.namespace, timeout=30)
            
            # Filter resources by name if specified
            if rule.match_resource_name:
                if rule.operator == "contains":
                    resources = [r for r in resources if rule.match_resource_name in (get_val(r, 'metadata.name') or "")]
                else:
                    resources = [r for r in resources if get_val(r, 'metadata.name') == rule.match_resource_name]

            # Condition Processing
            passed_items = []
            failed_items_details = []
            failed_snapshots = []
            
            conditions = []
            if rule.field_path:
                conditions.append({"path": rule.field_path, "op": rule.operator, "val": rule.expected_value})
            
            if rule.extra_conditions:
                try:
                    extras = json.loads(rule.extra_conditions)
                    conditions.extend(extras)
                except: pass

            for item in resources:
                item_data = item.to_dict() if hasattr(item, 'to_dict') else dict(item)
                item_name = item_data.get('metadata', {}).get('name', '?')
                
                if not conditions:
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
                            m = False
                            for sub_item in actual:
                                if isinstance(sub_item, str):
                                    if exp_str in sub_item.lower():
                                        m = True; break
                                elif isinstance(sub_item, dict):
                                    if exp_str in str(sub_item).lower():
                                        m = True; break
                        else:
                            m = exp_str in str(actual).lower() if actual else False
                    
                    cond_results.append({"match": m, "cond": cond, "actual": actual})
                
                passed_list = [c["match"] for c in cond_results]
                item_pass = any(passed_list) if rule.condition_logic == "OR" else all(passed_list)
                
                if item_pass:
                    passed_items.append(item_data)
                else:
                    item_failed_details = []
                    for cm in cond_results:
                        if not cm["match"]:
                            c = cm["cond"]
                            actual_str = f"'{cm['actual']}'" if cm['actual'] is not None else "None"
                            item_failed_details.append(f"'{c['path']}' {c['op']} '{c['val']}' (Actual: {actual_str})")
                    
                    failed_items_details.append(f"Item '{item_name}' failed: " + "; ".join(item_failed_details))
                    if len(failed_snapshots) < 3:
                        failed_snapshots.append(item_data)

            # Determine Result
            status = "SKIP"
            detail = ""
            
            if rule.check_type == "EXISTENCE":
                if passed_items:
                    status = "PASS"
                    detail = f"Found {len(passed_items)} matching resource(s)"
                else:
                    status = "FAIL"
                    detail = f"No {rule.resource_kind} found matching conditions"
            
            elif rule.check_type == "FORBIDDANCE":
                if not passed_items:
                    status = "PASS"
                    detail = f"No matching {rule.resource_kind} found (as expected)"
                else:
                    status = "FAIL"
                    detail = f"Found {len(passed_items)} matching resource(s) that should not exist"
                    # For forbiddance, passed items are the failing ones
                    failed_snapshots = passed_items[:3]

            else: # VALIDATION
                if not resources:
                    status = "SKIP"
                    detail = f"No {rule.resource_kind} found to validate"
                elif not failed_items_details:
                    status = "PASS"
                    detail = f"Verified on {len(passed_items)} items"
                else:
                    status = "FAIL"
                    detail = "; ".join(failed_items_details[:3])

            results.append(ComplianceResult(
                cluster_name=cluster.name,
                rule_name=rule.name,
                status=status,
                detail=detail,
                resource_kind=rule.resource_kind,
                namespace=rule.namespace,
                failed_resources=failed_snapshots if status == "FAIL" else None,
                bundle_name=bundle_name
            ))

        except Exception as e:
            logger.error(f"Rule {rule.name} failed on {cluster.name}: {e}")
            results.append(ComplianceResult(
                cluster_name=cluster.name,
                rule_name=rule.name,
                status="ERROR",
                detail=str(e),
                resource_kind=rule.resource_kind,
                namespace=rule.namespace
            ))

    # Save Score
    total = len(results)
    if total == 0:
        return None
        
    passed = len([r for r in results if r.status == 'PASS'])
    score_val = (passed / total) * 100
    
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
        for r in results
    ]

    ts_str = run_timestamp.strftime("%Y-%m-%d %H:%M:%S") if run_timestamp else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    
    db_score = ComplianceScore(
        cluster_id=cluster.id,
        timestamp=ts_str,
        passed_count=passed,
        total_count=total,
        score=round(score_val, 1),
        results_json=json.dumps(compact_results)
    )
    session.add(db_score)
    session.commit()
    
    logger.info(f"Compliance check finished for {cluster.name}: Score {db_score.score}%")
    return db_score
