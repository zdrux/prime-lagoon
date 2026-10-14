import fnmatch
import json
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List

from sqlmodel import Session, select

from app.database import engine
from app.models import Cluster, ReportRun, ReportRunCluster
from app.services.ocp import fetch_resources


logger = logging.getLogger(__name__)
REPORT_RETENTION_DAYS = 30
_report_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ocp-report")


def default_rbac_report_config() -> Dict[str, Any]:
    return {
        "cluster_roles": [],
        "role_match": "any_exact",
        "namespace_exclusions": [],
        "identity_exclusions": ["system:serviceaccount:*"],
        "subject_kinds": ["Group", "User", "ServiceAccount"],
        "user_condition": {
            "quantifier": "ALL",
            "operator": "contains",
            "values": ["ssid"],
            "case_sensitive": False,
        },
        "empty_group_policy": "review",
        "missing_group_policy": "review",
    }


def normalize_rbac_report_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = default_rbac_report_config()
    normalized.update(config or {})
    normalized["cluster_roles"] = list(dict.fromkeys(
        str(value).strip() for value in normalized.get("cluster_roles", []) if str(value).strip()
    ))
    normalized["namespace_exclusions"] = list(dict.fromkeys(
        str(value).strip() for value in normalized.get("namespace_exclusions", []) if str(value).strip()
    ))
    normalized["identity_exclusions"] = list(dict.fromkeys(
        str(value).strip() for value in normalized.get("identity_exclusions", []) if str(value).strip()
    ))
    normalized["subject_kinds"] = ["Group", "User", "ServiceAccount"]

    raw_condition = normalized.get("user_condition") or {}
    condition = default_rbac_report_config()["user_condition"].copy()
    condition.update(raw_condition)
    condition["quantifier"] = "ALL"
    condition["operator"] = condition.get("operator") if condition.get("operator") in {"contains", "not_contains"} else "contains"
    raw_values = raw_condition.get("values")
    if raw_values is None and "value" in raw_condition:
        raw_values = [raw_condition.get("value")]
    if raw_values is None:
        raw_values = condition.get("values") or []
    if isinstance(raw_values, str):
        raw_values = [raw_values]
    elif not isinstance(raw_values, (list, tuple, set)):
        raw_values = [raw_values]
    condition["values"] = list(dict.fromkeys(
        str(value).strip() for value in raw_values if str(value).strip()
    ))
    condition.pop("value", None)
    condition["case_sensitive"] = bool(condition.get("case_sensitive", False))
    normalized["user_condition"] = condition
    return normalized


def validate_rbac_report_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_rbac_report_config(config)
    if not normalized["cluster_roles"]:
        raise ValueError("At least one ClusterRole name is required")
    if not normalized["user_condition"]["values"]:
        raise ValueError("At least one user-name comparison value is required")
    return normalized


def _resource_dict(resource: Any) -> Dict[str, Any]:
    if hasattr(resource, "to_dict"):
        return resource.to_dict()
    return dict(resource)


def _namespace_is_excluded(namespace: str, patterns: Iterable[str]) -> bool:
    namespace_lower = (namespace or "").lower()
    return any(fnmatch.fnmatchcase(namespace_lower, pattern.lower()) for pattern in patterns)


def _identity_is_excluded(identity: str, patterns: Iterable[str]) -> bool:
    identity_lower = str(identity or "").lower()
    return any(fnmatch.fnmatchcase(identity_lower, pattern.lower()) for pattern in patterns)


def _user_matches(username: str, condition: Dict[str, Any]) -> bool:
    actual = str(username or "")
    expected_values = condition["values"]
    if not condition.get("case_sensitive"):
        actual = actual.lower()
        expected_values = [value.lower() for value in expected_values]
    contains_any = any(expected in actual for expected in expected_values)
    return not contains_any if condition["operator"] == "not_contains" else contains_any


def analyze_rbac_access(
    cluster: Cluster,
    role_bindings: Iterable[Any],
    cluster_role_bindings: Iterable[Any],
    groups: Iterable[Any],
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Join RBAC bindings to OpenShift groups and return evidence rows."""
    config = validate_rbac_report_config(config)
    selected_roles = set(config["cluster_roles"])
    exclusions = config["namespace_exclusions"]
    identity_exclusions = config["identity_exclusions"]
    condition = config["user_condition"]
    group_map = {}
    for group in groups:
        group_data = _resource_dict(group)
        group_name = group_data.get("metadata", {}).get("name")
        if group_name:
            group_map[group_name] = group_data

    rows: List[Dict[str, Any]] = []
    binding_sets = (("RoleBinding", role_bindings), ("ClusterRoleBinding", cluster_role_bindings))
    for binding_kind, bindings in binding_sets:
        for binding in bindings:
            binding_data = _resource_dict(binding)
            metadata = binding_data.get("metadata", {})
            role_ref = binding_data.get("roleRef", {})
            if role_ref.get("kind") != "ClusterRole" or role_ref.get("name") not in selected_roles:
                continue

            namespace = metadata.get("namespace") if binding_kind == "RoleBinding" else None
            if namespace and _namespace_is_excluded(namespace, exclusions):
                continue

            for subject in binding_data.get("subjects") or []:
                subject_kind = subject.get("kind", "Unknown")
                subject_name = subject.get("name", "")
                subject_namespace = subject.get("namespace") or namespace or "?"
                canonical_identity = (
                    f"system:serviceaccount:{subject_namespace}:{subject_name}"
                    if subject_kind == "ServiceAccount" else subject_name
                )
                if subject_kind in {"User", "ServiceAccount"} and _identity_is_excluded(
                    canonical_identity, identity_exclusions
                ):
                    continue
                base_row = {
                    "cluster_id": cluster.id,
                    "cluster": cluster.name,
                    "environment": cluster.environment or "-",
                    "datacenter": cluster.datacenter or "-",
                    "scope": namespace or "Cluster-wide",
                    "namespace": namespace,
                    "binding_kind": binding_kind,
                    "binding_name": metadata.get("name", "?"),
                    "cluster_role": role_ref.get("name"),
                    "subject_kind": subject_kind,
                    "subject_name": subject_name,
                    "total_users": None,
                    "matching_users": [],
                    "nonmatching_users": [],
                    "finding": "OTHER_SUBJECT_REVIEW",
                    "detail": f"Unsupported subject kind {subject_kind}",
                }

                if subject_kind == "Group":
                    group = group_map.get(subject_name)
                    if not group:
                        is_system = subject_name.startswith("system:")
                        base_row["finding"] = "SYSTEM_GROUP_REVIEW" if is_system else "GROUP_NOT_FOUND"
                        base_row["detail"] = (
                            "Built-in or virtual group is not represented by an OpenShift Group object"
                            if is_system else "Referenced OpenShift Group could not be resolved"
                        )
                    else:
                        all_users = list(group.get("users") or [])
                        users = [
                            user for user in all_users
                            if not _identity_is_excluded(user, identity_exclusions)
                        ]
                        excluded_count = len(all_users) - len(users)
                        if all_users and not users:
                            continue
                        base_row["total_users"] = len(users)
                        if not users:
                            base_row["finding"] = "EMPTY_GROUP_REVIEW"
                            base_row["detail"] = "Group exists but contains no users"
                        else:
                            matching = [user for user in users if _user_matches(user, condition)]
                            nonmatching = [user for user in users if not _user_matches(user, condition)]
                            base_row["matching_users"] = matching
                            base_row["nonmatching_users"] = nonmatching
                            base_row["finding"] = "CLEAR" if not nonmatching else "NONMATCHING_USER"
                            base_row["detail"] = (
                                "Every group member satisfies the user-name condition"
                                if not nonmatching else f"{len(nonmatching)} group member(s) do not satisfy the user-name condition"
                            )
                            if excluded_count:
                                base_row["detail"] += f"; {excluded_count} identity exclusion(s) applied"
                elif subject_kind == "User":
                    matched = _user_matches(subject_name, condition)
                    base_row["total_users"] = 1
                    base_row["matching_users"] = [subject_name] if matched else []
                    base_row["nonmatching_users"] = [] if matched else [subject_name]
                    base_row["finding"] = "CLEAR" if matched else "NONMATCHING_USER"
                    base_row["detail"] = (
                        "Direct user satisfies the user-name condition"
                        if matched else "Direct user does not satisfy the user-name condition"
                    )
                elif subject_kind == "ServiceAccount":
                    base_row["subject_name"] = f"{subject_namespace}/{subject_name}"
                    base_row["finding"] = "SERVICE_ACCOUNT"
                    base_row["detail"] = "ServiceAccount access is recorded but is not evaluated as a human username"

                rows.append(base_row)

    return rows


def collect_rbac_report(cluster: Cluster, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    role_bindings = fetch_resources(cluster, "rbac.authorization.k8s.io/v1", "RoleBinding", timeout=60)
    cluster_role_bindings = fetch_resources(cluster, "rbac.authorization.k8s.io/v1", "ClusterRoleBinding", timeout=60)
    groups = fetch_resources(cluster, "user.openshift.io/v1", "Group", timeout=60)
    return analyze_rbac_access(cluster, role_bindings, cluster_role_bindings, groups, config)


def _summarize(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    findings = Counter(row.get("finding", "UNKNOWN") for row in rows)
    return {"row_count": len(rows), "findings": dict(sorted(findings.items()))}


def execute_report_run(run_id: int) -> None:
    with Session(engine) as session:
        run = session.get(ReportRun, run_id)
        if not run:
            return
        run.status = "RUNNING"
        run.started_at = datetime.now(timezone.utc)
        session.add(run)
        session.commit()

        try:
            config = validate_rbac_report_config(json.loads(run.config_json))
            cluster_runs = session.exec(
                select(ReportRunCluster).where(ReportRunCluster.run_id == run.id).order_by(ReportRunCluster.id)
            ).all()
            all_rows: List[Dict[str, Any]] = []
            completed_clusters = 0
            failed_clusters = 0

            for cluster_run in cluster_runs:
                cluster = session.get(Cluster, cluster_run.cluster_id)
                cluster_run.status = "RUNNING"
                cluster_run.started_at = datetime.now(timezone.utc)
                session.add(cluster_run)
                session.commit()

                try:
                    if not cluster:
                        raise ValueError("Cluster no longer exists")
                    rows = collect_rbac_report(cluster, config)
                    cluster_run.results_json = json.dumps(rows, default=str)
                    cluster_run.row_count = len(rows)
                    cluster_run.status = "COMPLETED"
                    completed_clusters += 1
                    all_rows.extend(rows)
                except Exception as exc:
                    logger.exception("Report run %s failed for cluster %s", run.id, cluster_run.cluster_name)
                    cluster_run.status = "FAILED"
                    cluster_run.error = str(exc)[:1000]
                    failed_clusters += 1
                finally:
                    cluster_run.completed_at = datetime.now(timezone.utc)
                    session.add(cluster_run)
                    session.commit()

            summary = _summarize(all_rows)
            summary.update({
                "cluster_count": len(cluster_runs),
                "completed_clusters": completed_clusters,
                "failed_clusters": failed_clusters,
            })
            run.summary_json = json.dumps(summary)
            if failed_clusters == 0:
                run.status = "COMPLETED"
            elif completed_clusters:
                run.status = "PARTIAL"
            else:
                run.status = "FAILED"
        except Exception as exc:
            logger.exception("Report run %s failed", run.id)
            run.status = "FAILED"
            run.summary_json = json.dumps({"error": str(exc)[:1000]})
        finally:
            run.completed_at = datetime.now(timezone.utc)
            run.expires_at = run.completed_at + timedelta(days=REPORT_RETENTION_DAYS)
            session.add(run)
            session.commit()


def start_report_run(run_id: int) -> None:
    _report_executor.submit(execute_report_run, run_id)


def cleanup_expired_report_runs(session: Session) -> int:
    now = datetime.now(timezone.utc)
    runs = session.exec(select(ReportRun).where(ReportRun.expires_at != None, ReportRun.expires_at < now)).all()
    for run in runs:
        children = session.exec(select(ReportRunCluster).where(ReportRunCluster.run_id == run.id)).all()
        for child in children:
            session.delete(child)
        session.delete(run)
    if runs:
        session.commit()
    return len(runs)
