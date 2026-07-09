from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select, func
from typing import Any, List, Dict, Optional

from datetime import datetime, timedelta, timezone
import json
from app.database import get_session
from app.models import Cluster, LicenseUsage, AppConfig, LicenseRule, ClusterSnapshot, User
from app.dependencies import get_current_user_optional
from app.services.page_access import require_page_access
from app.services.ocp import fetch_resources, get_cluster_stats, parse_cpu, get_detailed_stats, parse_memory_to_gb, get_dynamic_client, get_argocd_application_details, get_argocd_applicationset_details, get_val

# Consolidated license calculation logic is now in poller, but for realtime we still might need it
# Or we can reuse the logic
from app.services.license import calculate_licenses


router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
)

RESOURCE_MAP = {
    "nodes": {"api_version": "v1", "kind": "Node"},
    "machines": {"api_version": "machine.openshift.io/v1beta1", "kind": "Machine"},
    "machinesets": {"api_version": "machine.openshift.io/v1beta1", "kind": "MachineSet"},
    "projects": {"api_version": "project.openshift.io/v1", "kind": "Project"},
    "persistentvolumes": {"api_version": "v1", "kind": "PersistentVolume"},
    "persistentvolumeclaims": {"api_version": "v1", "kind": "PersistentVolumeClaim"},
    "storageclasses": {"api_version": "storage.k8s.io/v1", "kind": "StorageClass"},
    "machineautoscalers": {"api_version": "autoscaling.openshift.io/v1beta1", "kind": "MachineAutoscaler"},

}

def parse_storage_to_gib(storage_val: Any) -> float:
    """Parse Kubernetes storage quantities into GiB."""
    if storage_val is None:
        return 0.0

    s = str(storage_val).strip()
    if not s:
        return 0.0

    units = {
        "Ki": 1024,
        "Mi": 1024 ** 2,
        "Gi": 1024 ** 3,
        "Ti": 1024 ** 4,
        "Pi": 1024 ** 5,
        "K": 1000,
        "M": 1000 ** 2,
        "G": 1000 ** 3,
        "T": 1000 ** 4,
        "P": 1000 ** 5,
    }

    try:
        for unit, multiplier in units.items():
            if s.endswith(unit):
                return float(s[:-len(unit)]) * multiplier / (1024 ** 3)
        return float(s) / (1024 ** 3)
    except Exception:
        return 0.0

def _storage_class_is_azure_file(name: Optional[str], storage_class_map: Dict[str, Dict[str, Any]]) -> bool:
    if not name:
        return False

    storage_class = storage_class_map.get(name) or {}
    provisioner = str(storage_class.get("provisioner") or "").lower()
    normalized_name = str(name).lower().replace("_", "-")
    return (
        "azure-file" in normalized_name
        or "azurefile" in normalized_name
        or "file.csi.azure.com" in provisioner
        or "kubernetes.io/azure-file" in provisioner
    )

def _is_azure_file_volume(pv: Dict[str, Any], pvc: Optional[Dict[str, Any]], storage_class_map: Dict[str, Dict[str, Any]]) -> bool:
    pv_spec = pv.get("spec", {}) if pv else {}
    pvc_spec = pvc.get("spec", {}) if pvc else {}

    if pv_spec.get("azureFile"):
        return True

    csi_driver = str(get_val(pv, "spec.csi.driver") or "").lower()
    if csi_driver == "file.csi.azure.com":
        return True

    pv_class = pv_spec.get("storageClassName")
    pvc_class = pvc_spec.get("storageClassName")
    return _storage_class_is_azure_file(pv_class, storage_class_map) or _storage_class_is_azure_file(pvc_class, storage_class_map)

def _get_resource_mapid_labels(resource: Dict[str, Any]) -> Dict[str, str]:
    labels = get_val(resource, "metadata.labels") or {}
    mapid = labels.get("mapid")
    if not mapid or mapid == "Unmapped":
        return {}
    return {
        "mapid": str(mapid),
        "lob": labels.get("lob") or "-"
    }

def _add_project_mapids_to_breakdown(
    session: Session,
    cluster_map: Dict[int, Cluster],
    mapid_stats: Dict[str, Dict[str, Any]]
) -> None:
    """
    Add MAPIDs that are present only on project labels to the analytics result.
    These entries do not affect license or node totals, but make the MAPID
    searchable and allow the existing Resources drill-down to show namespaces.
    """
    for cluster_id, cluster in cluster_map.items():
        snap = session.exec(select(ClusterSnapshot).where(
            ClusterSnapshot.cluster_id == cluster_id,
            ClusterSnapshot.status == "Success"
        ).order_by(ClusterSnapshot.timestamp.desc()).limit(1)).first()

        if not snap or not snap.data_json:
            continue

        try:
            snapshot_data = json.loads(snap.data_json)
        except Exception:
            continue

        project_counts = {}
        project_lobs = {}
        for project in snapshot_data.get("projects", []) or []:
            label_info = _get_resource_mapid_labels(project)
            if not label_info:
                continue

            mid = label_info["mapid"]
            project_counts[mid] = project_counts.get(mid, 0) + 1
            if label_info["lob"] != "-":
                project_lobs[mid] = label_info["lob"]

        for mid, project_count in project_counts.items():
            lob = project_lobs.get(mid, "-")

            if mid not in mapid_stats:
                mapid_stats[mid] = {
                    "mapid": mid,
                    "lob": lob,
                    "total_licenses": 0,
                    "total_nodes": 0,
                    "total_projects": 0,
                    "total_vcpu": 0.0,
                    "clusters": []
                }
            elif mapid_stats[mid]["lob"] in ("-", "Unknown") and lob != "-":
                mapid_stats[mid]["lob"] = lob

            mapid_stats[mid]["total_projects"] = mapid_stats[mid].get("total_projects", 0) + project_count

            cluster_entry = next(
                (c for c in mapid_stats[mid]["clusters"] if c["cluster_id"] == cluster_id),
                None
            )
            if cluster_entry:
                cluster_entry["projects"] = cluster_entry.get("projects", 0) + project_count
            else:
                mapid_stats[mid]["clusters"].append({
                    "name": cluster.name,
                    "cluster_id": cluster.id,
                    "environment": cluster.environment or "-",
                    "datacenter": cluster.datacenter or "-",
                    "licenses": 0,
                    "nodes": 0,
                    "projects": project_count,
                    "vcpu": 0.0
                })

def _build_storage_rows(cluster: Cluster, snapshot_data: Dict[str, Any], snapshot_time: Optional[datetime]) -> Dict[str, Any]:
    pvs = snapshot_data.get("persistentvolumes", []) or []
    pvcs = snapshot_data.get("persistentvolumeclaims", []) or []
    storageclasses = snapshot_data.get("storageclasses", []) or []
    projects = snapshot_data.get("projects", []) or []
    errors = snapshot_data.get("__errors", {}) or {}

    storage_class_map = {
        get_val(sc, "metadata.name"): {
            "provisioner": get_val(sc, "provisioner"),
            "reclaim_policy": get_val(sc, "reclaimPolicy"),
            "volume_binding_mode": get_val(sc, "volumeBindingMode"),
            "allow_expansion": get_val(sc, "allowVolumeExpansion"),
        }
        for sc in storageclasses
        if get_val(sc, "metadata.name")
    }

    pvc_map = {}
    for pvc in pvcs:
        pvc_namespace = get_val(pvc, "metadata.namespace") or "-"
        pvc_name = get_val(pvc, "metadata.name") or "-"
        pvc_map[(pvc_namespace, pvc_name)] = pvc

    namespace_mapid_map = {
        get_val(project, "metadata.name"): get_val(project, "metadata.labels.mapid") or "-"
        for project in projects
        if get_val(project, "metadata.name")
    }

    claimed_keys = set()
    rows = []

    for pv in pvs:
        claim_namespace = get_val(pv, "spec.claimRef.namespace") or "-"
        claim_name = get_val(pv, "spec.claimRef.name") or "-"
        pvc = pvc_map.get((claim_namespace, claim_name))
        if pvc:
            claimed_keys.add((claim_namespace, claim_name))

        pv_capacity = get_val(pv, "spec.capacity.storage")
        pvc_capacity = get_val(pvc, "spec.resources.requests.storage") if pvc else None
        storage_class = get_val(pv, "spec.storageClassName") or (get_val(pvc, "spec.storageClassName") if pvc else "") or ""
        sc_details = storage_class_map.get(storage_class) or {}
        azure_secret_name = get_val(pv, "spec.azureFile.secretName") or "-"

        rows.append({
            "cluster_id": cluster.id,
            "cluster_name": cluster.name,
            "environment": cluster.environment or "-",
            "datacenter": cluster.datacenter or "-",
            "pv_name": get_val(pv, "metadata.name") or "-",
            "pvc_name": claim_name,
            "namespace": claim_namespace,
            "namespace_mapid": namespace_mapid_map.get(claim_namespace, "-"),
            "pv_phase": get_val(pv, "status.phase") or "-",
            "pvc_phase": get_val(pvc, "status.phase") if pvc else "-",
            "capacity": pv_capacity or "-",
            "capacity_gib": round(parse_storage_to_gib(pv_capacity), 2),
            "requested": pvc_capacity or "-",
            "access_modes": ", ".join(get_val(pv, "spec.accessModes") or get_val(pvc, "spec.accessModes") or []) or "-",
            "storage_class": storage_class or "-",
            "reclaim_policy": get_val(pv, "spec.persistentVolumeReclaimPolicy") or sc_details.get("reclaim_policy") or "-",
            "volume_mode": get_val(pv, "spec.volumeMode") or get_val(pvc, "spec.volumeMode") or "-",
            "volume_binding_mode": sc_details.get("volume_binding_mode") or "-",
            "allow_expansion": sc_details.get("allow_expansion"),
            "azure_file": _is_azure_file_volume(pv, pvc, storage_class_map),
            "backend": "Azure Files" if _is_azure_file_volume(pv, pvc, storage_class_map) else "-",
            "azure_secret_name": azure_secret_name,
            "azure_storage_account_name": azure_secret_name,
            "azure_secret_namespace": get_val(pv, "spec.azureFile.secretNamespace") or "-",
            "azure_share_name": get_val(pv, "spec.azureFile.shareName") or "-",
            "csi_driver": get_val(pv, "spec.csi.driver") or "-",
            "created_at": get_val(pv, "metadata.creationTimestamp") or "-",
        })

    for pvc_key, pvc in pvc_map.items():
        if pvc_key in claimed_keys:
            continue
        pvc_namespace, pvc_name = pvc_key
        storage_class = get_val(pvc, "spec.storageClassName") or ""
        sc_details = storage_class_map.get(storage_class) or {}

        rows.append({
            "cluster_id": cluster.id,
            "cluster_name": cluster.name,
            "environment": cluster.environment or "-",
            "datacenter": cluster.datacenter or "-",
            "pv_name": "-",
            "pvc_name": pvc_name,
            "namespace": pvc_namespace,
            "namespace_mapid": namespace_mapid_map.get(pvc_namespace, "-"),
            "pv_phase": "-",
            "pvc_phase": get_val(pvc, "status.phase") or "-",
            "capacity": "-",
            "capacity_gib": 0,
            "requested": get_val(pvc, "spec.resources.requests.storage") or "-",
            "access_modes": ", ".join(get_val(pvc, "spec.accessModes") or []) or "-",
            "storage_class": storage_class or "-",
            "reclaim_policy": sc_details.get("reclaim_policy") or "-",
            "volume_mode": get_val(pvc, "spec.volumeMode") or "-",
            "volume_binding_mode": sc_details.get("volume_binding_mode") or "-",
            "allow_expansion": sc_details.get("allow_expansion"),
            "azure_file": _storage_class_is_azure_file(storage_class, storage_class_map),
            "backend": "Azure Files" if _storage_class_is_azure_file(storage_class, storage_class_map) else "-",
            "azure_secret_name": "-",
            "azure_storage_account_name": "-",
            "azure_secret_namespace": "-",
            "azure_share_name": "-",
            "csi_driver": "-",
            "created_at": get_val(pvc, "metadata.creationTimestamp") or "-",
        })

    return {
        "rows": rows,
        "errors": {
            key: errors.get(key)
            for key in ["persistentvolumes", "persistentvolumeclaims", "storageclasses"]
            if errors.get(key)
        },
        "snapshot_time": snapshot_time.strftime("%Y-%m-%dT%H:%M:%S") if snapshot_time else None,
    }

def get_snapshot_for_cluster(session: Session, cluster_id: int, target_time: datetime) -> Optional[ClusterSnapshot]:
    """Finds the closest successful snapshot ON or BEFORE the target time (with 5m grace)."""
    # Adding a grace period to target_time to account for multi-cluster polling delays
    grace_target = target_time + timedelta(seconds=600)
    statement = select(ClusterSnapshot).where(
        ClusterSnapshot.cluster_id == cluster_id,
        ClusterSnapshot.timestamp <= grace_target,
        ClusterSnapshot.status == "Success"
    ).order_by(ClusterSnapshot.timestamp.desc()).limit(1)
    return session.exec(statement).first()

@router.get("/snapshots")
def get_available_snapshots(session: Session = Depends(get_session)):
    """Returns a list of distinct timestamps where snapshots are available."""
    # This might be heavy if lots of snapshots. For now, let's just get distinct truncated timestamps or similar.
    # Actually, let's return all unique timestamps from the last 7 days?
    # Or just return a list of dates?
    # For MVP: Return all timestamps from the last 30 days
    cutoff = datetime.utcnow() - timedelta(days=30)
    statement = select(ClusterSnapshot.timestamp).where(ClusterSnapshot.timestamp >= cutoff).order_by(ClusterSnapshot.timestamp.desc())
    timestamps = session.exec(statement).all()
    
    # Bucket timestamps within 300s (5m) of each other
    grouped = []
    for t in timestamps:
        if not grouped or (grouped[-1] - t).total_seconds() > 300:
            grouped.append(t)

    return [t.strftime("%Y-%m-%dT%H:%M:%S") for t in grouped]

@router.get("/storage")
def get_storage_analytics(
    snapshot_time: Optional[str] = Query(None),
    cluster_id: Optional[int] = Query(None),
    environment: Optional[str] = Query(None),
    datacenter: Optional[str] = Query(None),
    azure_files: bool = Query(False),
    session: Session = Depends(get_session),
    user: Optional[User] = Depends(get_current_user_optional)
):
    require_page_access("cluster_storage" if cluster_id else "storage_analytics", user, session)

    query = select(Cluster)
    if cluster_id:
        query = query.where(Cluster.id == cluster_id)
    if environment:
        query = query.where(func.upper(Cluster.environment) == environment.upper())
    if datacenter:
        query = query.where(func.upper(Cluster.datacenter) == datacenter.upper())

    clusters = session.exec(query.order_by(Cluster.name)).all()

    target_dt = None
    if snapshot_time:
        try:
            target_dt = datetime.strptime(snapshot_time.replace("T", " "), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid snapshot_time format")

    rows = []
    cluster_status = []
    snapshot_times = []

    for cluster in clusters:
        snapshot_data = None
        snap = None

        if target_dt:
            snap = get_snapshot_for_cluster(session, cluster.id, target_dt)
        else:
            snap = session.exec(select(ClusterSnapshot).where(
                ClusterSnapshot.cluster_id == cluster.id,
                ClusterSnapshot.status == "Success"
            ).order_by(ClusterSnapshot.timestamp.desc()).limit(1)).first()

        if snap and snap.data_json:
            try:
                snapshot_data = json.loads(snap.data_json)
                snapshot_times.append(snap.timestamp)
            except Exception as e:
                cluster_status.append({"cluster": cluster.name, "status": "error", "message": f"Snapshot parse failed: {e}"})

        if snapshot_data is None and not target_dt:
            snapshot_data = {}
            try:
                snapshot_data["persistentvolumes"] = fetch_resources(cluster, "v1", "PersistentVolume", timeout=120)
                snapshot_data["persistentvolumeclaims"] = fetch_resources(cluster, "v1", "PersistentVolumeClaim", timeout=120)
                snapshot_data["storageclasses"] = fetch_resources(cluster, "storage.k8s.io/v1", "StorageClass", timeout=120)
                snapshot_data["projects"] = fetch_resources(cluster, "project.openshift.io/v1", "Project", timeout=120)
                cluster_status.append({"cluster": cluster.name, "status": "live", "message": "No snapshot found; live data fetched"})
            except Exception as e:
                cluster_status.append({"cluster": cluster.name, "status": "error", "message": str(e)})
                snapshot_data = {}
        elif snapshot_data is None:
            cluster_status.append({"cluster": cluster.name, "status": "missing", "message": "No matching snapshot found"})
            snapshot_data = {}

        storage_data = _build_storage_rows(cluster, snapshot_data, snap.timestamp if snap else None)
        rows.extend(storage_data["rows"])
        if storage_data["errors"]:
            cluster_status.append({"cluster": cluster.name, "status": "partial", "message": storage_data["errors"]})

    if azure_files:
        rows = [row for row in rows if row["azure_file"]]

    rows.sort(key=lambda row: (
        str(row["cluster_name"]).lower(),
        str(row["namespace"]).lower(),
        str(row["pvc_name"]).lower(),
        str(row["pv_name"]).lower(),
    ))

    total_capacity_gib = round(sum(row.get("capacity_gib") or 0 for row in rows), 2)
    azure_capacity_gib = round(sum(row.get("capacity_gib") or 0 for row in rows if row.get("azure_file")), 2)

    return {
        "rows": rows,
        "summary": {
            "clusters": len(clusters),
            "volumes": len([row for row in rows if row["pv_name"] != "-"]),
            "claims": len([row for row in rows if row["pvc_name"] != "-"]),
            "total_capacity_gib": total_capacity_gib,
            "azure_file_count": len([row for row in rows if row.get("azure_file")]),
            "azure_file_capacity_gib": azure_capacity_gib,
        },
        "status": cluster_status,
        "timestamp": max(snapshot_times).strftime("%Y-%m-%dT%H:%M:%S") if snapshot_times else None,
    }

@router.get("/{cluster_id}/resources/{resource_type}")
def get_cluster_resources(cluster_id: int, resource_type: str, snapshot_time: Optional[str] = Query(None), session: Session = Depends(get_session), user: Optional[User] = Depends(get_current_user_optional)):
    require_page_access("cluster_resources", user, session)
    if resource_type not in RESOURCE_MAP:
        raise HTTPException(status_code=400, detail="Invalid resource type")
    
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")

    # Time Travel Logic
    if snapshot_time:
        try:
            # Handle both T and space separators
            clean_ts = snapshot_time.replace("T", " ")
            target_dt = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
            snap = get_snapshot_for_cluster(session, cluster_id, target_dt)
            if snap and snap.data_json:
                data = json.loads(snap.data_json)
                return data.get(resource_type, [])
            return [] # Snapshot missing or empty
        except ValueError:
            pass # Fallback to live? Or empty? Better empty/error for specific historical query
            return []

    # Live Logic
    meta = RESOURCE_MAP[resource_type]
    try:
        return fetch_resources(cluster, meta["api_version"], meta["kind"], namespace=meta.get("namespace"))
    except Exception as e:
        print(f"Error checking resources {resource_type} for cluster {cluster_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{cluster_id}/details")
def get_cluster_details(cluster_id: int, snapshot_time: Optional[str] = Query(None), session: Session = Depends(get_session)):
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    snapshot_data = None
    if snapshot_time:
        try:
            clean_ts = snapshot_time.replace("T", " ")
            target_dt = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
            snap = get_snapshot_for_cluster(session, cluster_id, target_dt)
            if snap and snap.data_json:
                snapshot_data = json.loads(snap.data_json)
                if snap.service_mesh_json:
                    try:
                        snapshot_data['service_mesh'] = json.loads(snap.service_mesh_json)
                    except Exception:
                        pass
                if snap.argocd_json:
                    try:
                        snapshot_data['argocd'] = json.loads(snap.argocd_json)
                    except Exception:
                        pass
        except ValueError:
            pass # Ignore invalid time format, fallback to live? Or error? Let's fallback for robustness but maybe should error.

    try:
        return get_detailed_stats(cluster, snapshot_data=snapshot_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{cluster_id}/nodes/{node_name}/details")
def get_node_details_endpoint(cluster_id: int, node_name: str, snapshot_time: Optional[str] = Query(None), session: Session = Depends(get_session)):
    from app.services.ocp import get_node_details
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    snapshot_data = None
    if snapshot_time:
        try:
            clean_ts = snapshot_time.replace("T", " ")
            target_dt = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
            snap = get_snapshot_for_cluster(session, cluster_id, target_dt)
            if snap and snap.data_json:
                snapshot_data = json.loads(snap.data_json)
        except:
            pass

    try:
        return get_node_details(cluster, node_name, snapshot_data=snapshot_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{cluster_id}/machines/{machine_name}/details")
def get_machine_details_endpoint(cluster_id: int, machine_name: str, snapshot_time: Optional[str] = Query(None), session: Session = Depends(get_session)):
    from app.services.ocp import get_machine_details
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    snapshot_data = None
    if snapshot_time:
        try:
            clean_ts = snapshot_time.replace("T", " ")
            target_dt = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
            snap = get_snapshot_for_cluster(session, cluster_id, target_dt)
            if snap and snap.data_json:
                snapshot_data = json.loads(snap.data_json)
        except:
            pass

    try:
        return get_machine_details(cluster, machine_name, snapshot_data=snapshot_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{cluster_id}/license-details/{usage_id}")
def get_license_details(cluster_id: int, usage_id: str, snapshot_time: Optional[str] = Query(None), session: Session = Depends(get_session)):
    """Returns detailed license breakdown for a cluster, either from history or a snapshot."""
    # usage_id can be "null" if coming from a dashboard without usage history record (like custom snapshot views)
    
    # Priority 1: Time Travel Snapshot
    if snapshot_time:
        try:
            clean_ts = snapshot_time.replace("T", " ")
            target_dt = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
            snap = get_snapshot_for_cluster(session, cluster_id, target_dt)
            if snap and snap.data_json:
                data = json.loads(snap.data_json)
                nodes = data.get("nodes", [])
                from app.models import LicenseRule, AppConfig
                rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True).order_by(LicenseRule.order, LicenseRule.id)).all()
                default_include = (session.get(AppConfig, "LICENSE_DEFAULT_INCLUDE") or AppConfig(value="False")).value.lower() == "true"
                lic_data = calculate_licenses(nodes, rules, default_include=default_include)
                return {
                    "node_count": lic_data["node_count"],
                    "total_vcpu": lic_data["total_vcpu"],
                    "license_count": lic_data["total_licenses"],
                    "details": lic_data["details"]
                }
        except Exception as e:
            print(f"Error fetching snapshot for license details: {e}")

    # Priority 2: Historical Usage Record
    if usage_id and usage_id != "null":
        try:
            usage = session.get(LicenseUsage, int(usage_id))
            if usage:
                return {
                    "node_count": usage.node_count,
                    "total_vcpu": usage.total_vcpu,
                    "license_count": usage.license_count,
                    "details": json.loads(usage.details_json) if usage.details_json else []
                }
        except:
            pass

    # Priority 3: Fallback (Live) - If we get here, we just calculate live?
    # But usually this endpoint is for audits. If we want live, we fetch live.
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
        
    try:
        nodes = fetch_resources(cluster, "v1", "Node")
        from app.models import LicenseRule, AppConfig
        rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True).order_by(LicenseRule.order, LicenseRule.id)).all()
        default_include = (session.get(AppConfig, "LICENSE_DEFAULT_INCLUDE") or AppConfig(value="False")).value.lower() == "true"
        lic_data = calculate_licenses(nodes, rules, default_include=default_include)
        return {
            "node_count": lic_data["node_count"],
            "total_vcpu": lic_data["total_vcpu"],
            "license_count": lic_data["total_licenses"],
            "details": lic_data["details"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class DashboardCache:
    def __init__(self):
        self.data = None
        self.timestamp = None
    
    def is_valid(self, ttl_minutes):
        if not self.data or not self.timestamp:
            return False
        delta = datetime.now(timezone.utc) - self.timestamp
        return delta < timedelta(minutes=ttl_minutes)

    def set(self, data):
        self.data = data
        self.timestamp = datetime.now(timezone.utc)

dashboard_cache = DashboardCache()

@router.get("/summary")
def get_dashboard_summary(snapshot_time: Optional[str] = Query(None), mode: Optional[str] = Query(None), refresh: bool = Query(False), session: Session = Depends(get_session)):
    ttl_minutes = int((session.get(AppConfig, "DASHBOARD_CACHE_TTL_MINUTES") or AppConfig(value="15")).value)

    # 1. Check Cache (Live Mode only)
    if not snapshot_time and mode != "fast" and not refresh:
        if dashboard_cache.is_valid(ttl_minutes):
            return dashboard_cache.data

    clusters = session.exec(select(Cluster)).all()
    
    # Fetch Config
    timestamp = datetime.now(timezone.utc).isoformat()
    rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True).order_by(LicenseRule.order, LicenseRule.id)).all()
    default_include = (session.get(AppConfig, "LICENSE_DEFAULT_INCLUDE") or AppConfig(value="False")).value.lower() == "true"
    
    global_stats = {
        "total_nodes": 0,
        "total_licensed_nodes": 0,
        "total_vcpu": 0,
        "total_licensed_vcpu": 0,
        "total_licenses": 0
    }
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Time Travel Logic
    target_dt = None
    if snapshot_time:
        try:
            clean_ts = snapshot_time.replace("T", " ")
            target_dt = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
        except:
            pass

    results = []

    # Fast Mode: Return latest snapshot data immediately
    if mode == "fast" and not target_dt:
        for cluster in clusters:
            try:
                # Get latest snapshot
                snap = session.exec(select(ClusterSnapshot).where(
                    ClusterSnapshot.cluster_id == cluster.id,
                    ClusterSnapshot.status == "Success"
                ).order_by(ClusterSnapshot.timestamp.desc()).limit(1)).first()
                
                if snap and snap.data_json:
                    snapshot_data = json.loads(snap.data_json)
                    stats = get_cluster_stats(cluster, snapshot_data=snapshot_data)
                    
                    # Inject Service Mesh status from snapshot
                    stats['has_service_mesh'] = False
                    if snap.service_mesh_json:
                        try:
                            sm_data = json.loads(snap.service_mesh_json)
                            stats['has_service_mesh'] = sm_data.get('is_active', False)
                        except:
                            pass
                    
                    # Inject ArgoCD status from snapshot
                    stats['has_argocd'] = False
                    if snap.argocd_json:
                        try:
                            cd_data = json.loads(snap.argocd_json)
                            stats['has_argocd'] = cd_data.get('is_active', False)
                        except:
                            pass

                    s_nodes = snapshot_data.get("nodes", [])
                    lic_data = calculate_licenses(s_nodes, rules, default_include=default_include)
                    
                    # Use frozen identity if available (for snapshots)
                    c_name = snap.captured_name or cluster.name
                    c_uid = snap.captured_unique_id or cluster.unique_id
                    
                    results.append({
                        "id": cluster.id,
                        "name": c_name,
                        "unique_id": c_uid,
                        "datacenter": cluster.datacenter,
                        "environment": cluster.environment,
                        "stats": stats,
                        "license_info": {
                            "count": lic_data["total_licenses"],
                            "usage_id": "null"
                        },
                        "licensed_node_count": lic_data["node_count"],
                        "licensed_vcpu_count": lic_data["total_vcpu"],
                        "status": "yellow" # Indicating stale/snapshot data
                    })
                    
                    # Globals
                    global_stats["total_nodes"] += (stats["node_count"] if isinstance(stats["node_count"], int) else 0)
                    global_stats["total_licensed_nodes"] += lic_data["node_count"]
                    global_stats["total_vcpu"] += (stats["vcpu_count"] if isinstance(stats["vcpu_count"], int) else 0)
                    global_stats["total_licensed_vcpu"] += lic_data["total_vcpu"]
                    global_stats["total_licenses"] += lic_data["total_licenses"]
                else:
                    # No snapshot available
                    results.append({
                        "id": cluster.id,
                        "name": cluster.name,
                        "datacenter": cluster.datacenter,
                        "environment": cluster.environment,
                        "stats": {"node_count": "-", "vcpu_count": "-", "version": "-", "console_url": "#"},
                        "license_info": {"count": "-", "usage_id": None},
                        "licensed_node_count": "-",
                        "licensed_vcpu_count": "-",
                        "status": "gray" # No data
                    })
            except Exception as e:
                import traceback
                print(f"ERROR processing cluster {cluster.name} in fast mode:")
                traceback.print_exc()
                results.append({
                    "id": cluster.id,
                    "name": cluster.name,
                    "status": "red",
                    "error": str(e)
                })
        
        # Sort results
        results.sort(key=lambda x: x["name"])
        return {
            "clusters": results,
            "global_stats": global_stats,
            "timestamp": timestamp
        }

    import concurrent.futures

    def process_cluster(cluster):
        """Helper to process a single cluster, intended for thread pool."""
        # Create a new session for this thread if needed, OR relies on passed-in objects being thread-safe enough for read.
        # Since we are just reading the 'cluster' object data which is already in memory, it's fine.
        # HOWEVER, 'rules' is also bound to the main session. 
        # But we are just reading 'rules' attributes (ints/strings), not doing lazy loads, so usually okay.
        # For 'calculate_licenses', it operates on standard dicts/objects.
        
        # NOTE: DB session is NOT thread safe. We must not use 'session' here for lazy loading.
        # 'cluster' attributes should be accessed/loaded before passing if they were lazy, 
        # but here they are eager enough or simple columns.
        
        if target_dt:
             # This path is fast (DB only), so we can return early or handle it outside.
             return None 
        
        # Live Logic (Slow Path)
        try:
             nodes = fetch_resources(cluster, "v1", "Node")
             stats = get_cluster_stats(cluster, nodes=nodes)
             
             # Check Operators for Red Status
             # We need to fetch ClusterOperators to determine health
             # This is an extra call but needed for the Red status requirement
             operator_status = "green"
             try:
                 dyn_client = get_dynamic_client(cluster)
                 co_api = dyn_client.resources.get(api_version='config.openshift.io/v1', kind='ClusterOperator')
                 operators = co_api.get().items
                 
                 # Check for degraded or not available
                 has_errors = False
                 for o in operators:
                     degraded = any(c.type == "Degraded" and c.status == "True" for c in o.status.conditions)
                     available = any(c.type == "Available" and c.status == "True" for c in o.status.conditions)
                     if degraded or not available:
                         has_errors = True
                         break
                 
                 if has_errors:
                     operator_status = "red"
             except Exception as oe:
                 print(f"Error checking operators for {cluster.name}: {oe}")
                 operator_status = "red" # Assume error if we can't check

             return {"success": True, "cluster": cluster, "stats": stats, "nodes": nodes, "operator_status": operator_status}
        except Exception as e:
             print(f"Error fetching nodes for {cluster.name}: {e}")
             return {"success": False, "cluster": cluster, "error": str(e)}

    # 1. Prepare tasks
    futures = {}
    
    # We will process Snapshot logic strictly in main thread to avoid DB complexity,
    # and only thread the Live logic.
    
    if target_dt:
        # Serial execution for Time Travel (Fast, DB only)
        for cluster in clusters:
             snap = get_snapshot_for_cluster(session, cluster.id, target_dt)
             if snap and snap.data_json:
                 snapshot_data = json.loads(snap.data_json)
                 stats = get_cluster_stats(cluster, snapshot_data=snapshot_data)

                 # Inject Service Mesh status from snapshot
                 stats['has_service_mesh'] = False
                 if snap.service_mesh_json:
                     try:
                         sm_data = json.loads(snap.service_mesh_json)
                         stats['has_service_mesh'] = sm_data.get('is_active', False)
                     except:
                         pass
                 
                 # Inject ArgoCD status from snapshot
                 stats['has_argocd'] = False
                 if snap.argocd_json:
                     try:
                         cd_data = json.loads(snap.argocd_json)
                         stats['has_argocd'] = cd_data.get('is_active', False)
                     except:
                         pass

                 s_nodes = snapshot_data.get("nodes", [])
                 lic_data = calculate_licenses(s_nodes, rules, default_include=default_include)
                 
                 # Use frozen identity if available (for snapshots)
                 c_name = snap.captured_name or cluster.name
                 c_uid = snap.captured_unique_id or cluster.unique_id
                 
                 results.append({
                    "id": cluster.id,
                    "name": c_name,
                    "unique_id": c_uid, # Pass unique ID to frontend
                    "datacenter": cluster.datacenter,
                    "environment": cluster.environment,
                    "stats": stats,
                    "license_info": {
                        "count": lic_data["total_licenses"],
                        "usage_id": "null"
                    },
                    "licensed_node_count": lic_data["node_count"],
                    "licensed_vcpu_count": lic_data["total_vcpu"],
                    "status": "yellow" # Snapshot view
                 })
                 
                 # Globals
                 global_stats["total_nodes"] += (stats["node_count"] if isinstance(stats["node_count"], int) else 0)
                 global_stats["total_licensed_nodes"] += lic_data["node_count"]
                 global_stats["total_vcpu"] += (stats["vcpu_count"] if isinstance(stats["vcpu_count"], int) else 0)
                 global_stats["total_licensed_vcpu"] += lic_data["total_vcpu"]
                 global_stats["total_licenses"] += lic_data["total_licenses"]
             else:
                 # Snapshot missing
                 results.append({
                    "id": cluster.id,
                    "name": cluster.name,
                    "datacenter": cluster.datacenter,
                    "environment": cluster.environment,
                    "stats": {"node_count": "-", "vcpu_count": "-", "version": "-", "console_url": "#"},
                    "license_info": {"count": "-", "usage_id": None},
                    "licensed_node_count": "-",
                    "licensed_vcpu_count": "-",
                    "status": "gray"
                 })
    else:
        # Parallel Execution for Live Mode
        # Use a manual executor to allow early exit (shutdown wait=False)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
        try:
            for cluster in clusters:
                futures[executor.submit(process_cluster, cluster)] = cluster
                
            # Wait for results with timeout
            # Increased from 10s to 45s to allow for slower/more clusters
            done, not_done = concurrent.futures.wait(futures.keys(), timeout=45)
            
            for f in done:
                res = f.result()
                if not res: continue 
                
                cluster = res["cluster"]
                if res["success"]:
                    stats = res["stats"]
                    nodes = res["nodes"]
                    op_status = res.get("operator_status", "green")
                    
                    # Lic calc (safe to run in main thread)
                    lic_data = calculate_licenses(nodes, rules, default_include=default_include)
                    
                    # Save History
                    usage = LicenseUsage(
                        cluster_id=cluster.id,
                        timestamp=timestamp,
                        node_count=lic_data["node_count"],
                        total_vcpu=lic_data["total_vcpu"],
                        license_count=lic_data["total_licenses"],
                        details_json=json.dumps(lic_data["details"])
                    )
                    session.add(usage)
                    
                    results.append({
                        "id": cluster.id,
                        "name": cluster.name,
                        "unique_id": cluster.unique_id,
                        "datacenter": cluster.datacenter,
                        "environment": cluster.environment,
                        "stats": stats,
                        "license_info": {
                            "count": lic_data["total_licenses"],
                            "usage_id": usage
                        },
                        "licensed_node_count": lic_data["node_count"],
                        "licensed_vcpu_count": lic_data["total_vcpu"],
                        "status": op_status
                    })
                    
                    global_stats["total_nodes"] += (stats["node_count"] if isinstance(stats["node_count"], int) else 0)
                    global_stats["total_licensed_nodes"] += lic_data["node_count"]
                    global_stats["total_vcpu"] += (stats["vcpu_count"] if isinstance(stats["vcpu_count"], int) else 0)
                    global_stats["total_licensed_vcpu"] += lic_data["total_vcpu"]
                    global_stats["total_licenses"] += lic_data["total_licenses"]
                
                else:
                    # Failed case
                    results.append({
                        "id": cluster.id,
                        "name": cluster.name,
                        "datacenter": cluster.datacenter,
                        "environment": cluster.environment,
                        "stats": {"node_count": "-", "vcpu_count": "-", "version": "-", "console_url": "#"},
                        "license_info": {"count": "-", "usage_id": None},
                        "licensed_node_count": "-",
                        "licensed_vcpu_count": "-",
                        "status": "red" # Fetch error
                    })
            
            # Handle timed out tasks
            for f in not_done:
                cluster = futures[f]
                # print(f"Cluster {cluster.name} timed out") # Optional log
                results.append({
                    "id": cluster.id,
                    "name": cluster.name,
                    "datacenter": cluster.datacenter,
                    "environment": cluster.environment,
                    "stats": {"node_count": "-", "vcpu_count": "-", "version": "-", "console_url": "#"},
                    "license_info": {"count": "-", "usage_id": None},
                    "licensed_node_count": "-",
                    "licensed_vcpu_count": "-",
                    "status": "yellow" # Timed out, maybe still polling or just slow
                })

        finally:
            # Important: Do not wait for hanging threads!
            executor.shutdown(wait=False, cancel_futures=True)

        # Commit all usages
        try:
            session.commit()
            # Refresh IDs
            for r in results:
                u = r["license_info"].get("usage_id")
                if u and isinstance(u, LicenseUsage):
                    r["license_info"]["usage_id"] = u.id
        except Exception as e:
            print(f"Error commiting usage stats: {e}")

    # Sort results by ID or Name to maintain order
    results.sort(key=lambda x: x["name"])

    response_data = {
        "clusters": results,
        "global_stats": global_stats,
        "timestamp": timestamp if not target_dt else snapshot_time,
        "ttl_minutes": ttl_minutes
    }

    # Save to Cache in Live Mode
    if not snapshot_time:
        dashboard_cache.set(response_data)

    return response_data

@router.get("/{cluster_id}/live_stats")
def get_cluster_live_stats(cluster_id: int, session: Session = Depends(get_session)):
    """Fetches live stats for a single cluster, including operator status."""
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")

    from app.routers.dashboard import get_dynamic_client # Import here to avoid circular if any
    
    # 1. Fetch Resources
    try:
        nodes = fetch_resources(cluster, "v1", "Node")
        stats = get_cluster_stats(cluster, nodes=nodes)
        
        # 2. Check Operators
        operator_status = "green"
        try:
            dyn_client = get_dynamic_client(cluster)
            co_api = dyn_client.resources.get(api_version='config.openshift.io/v1', kind='ClusterOperator')
            operators = co_api.get().items
            
            has_errors = False
            for o in operators:
                degraded = any(c.type == "Degraded" and c.status == "True" for c in o.status.conditions)
                available = any(c.type == "Available" and c.status == "True" for c in o.status.conditions)
                if degraded or not available:
                    has_errors = True
                    break
            
            if has_errors:
                operator_status = "red"
        except Exception as oe:
             print(f"Error checking operators for {cluster.name}: {oe}")
             operator_status = "red"

        # 3. Calculate Licenses
        from app.models import LicenseRule, AppConfig
        rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True).order_by(LicenseRule.order, LicenseRule.id)).all()
        default_include = (session.get(AppConfig, "LICENSE_DEFAULT_INCLUDE") or AppConfig(value="False")).value.lower() == "true"
        lic_data = calculate_licenses(nodes, rules, default_include=default_include)
        
        # 4. Save History
        # We should save history even for single updates? Yes, why not.
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        usage = LicenseUsage(
             cluster_id=cluster.id,
             timestamp=timestamp,
             node_count=lic_data["node_count"],
             total_vcpu=lic_data["total_vcpu"],
             license_count=lic_data["total_licenses"],
             details_json=json.dumps(lic_data["details"])
        )
        session.add(usage)
        session.commit()
        session.refresh(usage)
        
        return {
            "id": cluster.id,
            "name": cluster.name,
            "stats": stats,
            "license_info": {
                "count": lic_data["total_licenses"],
                "usage_id": usage.id
            },
            "licensed_node_count": lic_data["node_count"],
            "licensed_vcpu_count": lic_data["total_vcpu"],
            "status": operator_status
        }

    except Exception as e:
        print(f"Error fetching live stats for {cluster_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{cluster_id}/argocd/application/{namespace}/{name}")
def get_argocd_app_details(cluster_id: int, namespace: str, name: str, session: Session = Depends(get_session)):
    """Fetches live details for a specific ArgoCD Application."""
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")

    try:
        return get_argocd_application_details(cluster, namespace, name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{cluster_id}/argocd/applicationset/{namespace}/{name}")
def get_argocd_appset_details(cluster_id: int, namespace: str, name: str, session: Session = Depends(get_session)):
    """Fetches live details for a specific ArgoCD ApplicationSet."""
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")

    try:
        return get_argocd_applicationset_details(cluster, namespace, name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/simple-clusters")
def get_simple_clusters(session: Session = Depends(get_session)):
    """Returns a simple list of clusters for fast initial dashboard loading."""
    clusters = session.exec(select(Cluster)).all()
    results = []
    
    # Pre-fetch latest snapshots for all clusters to determine SM status efficienty?
    # Or just do a subquery? For now, N+1 query on snapshots limit 1 is okay for small N clusters.
    for c in clusters:
        # Check if latest snapshot has service mesh
        has_sm = False
        snap = session.exec(select(ClusterSnapshot).where(
            ClusterSnapshot.cluster_id == c.id,
            ClusterSnapshot.status == "Success"
        ).order_by(ClusterSnapshot.timestamp.desc()).limit(1)).first()
        
        if snap and snap.service_mesh_json:
             try:
                 sm_data = json.loads(snap.service_mesh_json)
                 if sm_data.get("is_active"):
                     has_sm = True
             except:
                 pass
        
        # Check for ArgoCD
        has_cd = False
        if snap and snap.argocd_json:
             try:
                 cd_data = json.loads(snap.argocd_json)
                 if cd_data.get("is_active"):
                     has_cd = True
             except:
                 pass

        results.append({
            "id": c.id,
            "name": c.name,
            "unique_id": c.unique_id,
            "datacenter": c.datacenter,
            "has_service_mesh": has_sm,
            "has_argocd": has_cd,
            "environment": c.environment,
            "status": "yellow" # Default to loading/stale state
        })
    results.sort(key=lambda x: x["name"])
    return results

@router.get("/trends")
def get_resource_trends(
    environment: Optional[str] = Query(None),
    datacenter: Optional[str] = Query(None),
    cluster_id: Optional[int] = Query(None),
    days: int = Query(30),
    start_date: Optional[str] = Query(None),
    session: Session = Depends(get_session)
):
    """
    Returns aggregated time-series data for global or cluster-specific analytics.
    Buckets data by unified poll timestamps from ClusterSnapshot.
    """
    # 1. Base Query for Clusters (apply filters if any)
    cluster_query = select(Cluster.id, Cluster.name)
    if cluster_id:
        cluster_query = cluster_query.where(Cluster.id == cluster_id)
    if environment:
        cluster_query = cluster_query.where(Cluster.environment == environment)
    if datacenter:
        cluster_query = cluster_query.where(Cluster.datacenter == datacenter)
    
    clusters = session.exec(cluster_query).all()
    filtered_cluster_ids = [c.id for c in clusters]
    cluster_map = {c.id: c.name for c in clusters}

    if not filtered_cluster_ids:
        return [] if cluster_id else {}

    # 2. Get Snapshots for these clusters
    # Logic: Start Date > Days priority
    if start_date:
        try:
             cutoff = datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
             # Fallback
             cutoff = datetime.utcnow() - timedelta(days=days)
    else:
        cutoff = datetime.utcnow() - timedelta(days=days)
    
    if cluster_id:
        # Single cluster summary (used by cluster details modal)
        statement = select(
            ClusterSnapshot.timestamp,
            func.sum(ClusterSnapshot.node_count).label("nodes"),
            func.sum(ClusterSnapshot.vcpu_count).label("vcpus"),
            func.sum(ClusterSnapshot.license_count).label("licenses"),
            func.sum(ClusterSnapshot.licensed_node_count).label("licensed_nodes")
        ).where(
            ClusterSnapshot.cluster_id == cluster_id,
            ClusterSnapshot.timestamp >= cutoff,
            ClusterSnapshot.status == "Success"
        ).group_by(
            ClusterSnapshot.timestamp
        ).order_by(
            ClusterSnapshot.timestamp.asc()
        )
        
        results = session.exec(statement).all()
        trends = []
        for row in results:
            trends.append({
                "timestamp": row.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "nodes": row.nodes,
                "vcpus": int(row.vcpus),
                "licenses": row.licenses,
                "licensed_nodes": row.licensed_nodes
            })
        return trends

    else:
        # Per-cluster license trends (used by global dashboard summary cards)
        statement = select(
            ClusterSnapshot.cluster_id,
            ClusterSnapshot.timestamp,
            ClusterSnapshot.license_count
        ).where(
            ClusterSnapshot.cluster_id.in_(filtered_cluster_ids),
            ClusterSnapshot.timestamp >= cutoff,
            ClusterSnapshot.status == "Success"
        ).order_by(
            ClusterSnapshot.timestamp.asc()
        )
        
        results = session.exec(statement).all()
        
        trends = {}
        for row in results:
            name = cluster_map.get(row.cluster_id, f"Cluster {row.cluster_id}")
            if name not in trends:
                trends[name] = []
            trends[name].append({
                "timestamp": row.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "licenses": row.license_count
            })
        return trends

@router.get("/mapid/global-trends")
def get_mapid_global_trends(days: int = Query(30), session: Session = Depends(get_session)):
    """Returns aggregated MAPID license usage trends across all clusters."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    from app.models import MapidLicenseUsage
    
    # We need to aggregate by timestamp and mapid
    # Since different clusters poll at slightly different times, we might need to bucket by day or hour?
    # For now, let's just return raw data and let UI handle it, OR bucket by Day.
    # Bucketing by Day is safest for global trends.
    
    # SQLAlchemy doesn't have a simple "date_trunc" across all DBs easily without func usage that varies.
    # We'll fetch all and aggregate in python for MVP simplicity (assuming volume isn't massive yet).
    # Optimization: Filter fields
    
    # Aggregate by Day + MAPID
    # Logic: For a given Day, a Cluster might have multiple snapshots. 
    # We should take the MAX usage for that MAPID on that Cluster for that Day.
    # Then SUM these Maxes across all Clusters to get Global Day Total.
    
    statement = select(
        MapidLicenseUsage.cluster_id, # Added cluster_id
        MapidLicenseUsage.timestamp,
        MapidLicenseUsage.mapid,
        MapidLicenseUsage.license_count
    ).where(
        MapidLicenseUsage.timestamp >= cutoff.isoformat()
    )
    results = session.exec(statement).all()
    
    # BACKFILL: If no results found, try to populate from ClusterSnapshots
    if not results:
        # Check if we have snapshots in this period?
        snap_stmt = select(ClusterSnapshot).where(
            ClusterSnapshot.timestamp >= cutoff,
            ClusterSnapshot.status == "Success"
        )
        snapshots = session.exec(snap_stmt).all()
        
        if snapshots:
            # We have snapshots but no MapidLicenseUsage. Backfill.
            from app.models import LicenseRule, AppConfig
            from app.services.license import calculate_mapid_usage
            
            rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True).order_by(LicenseRule.order, LicenseRule.id)).all()
            default_include = (session.get(AppConfig, "LICENSE_DEFAULT_INCLUDE") or AppConfig(value="False")).value.lower() == "true"
            
            for snap in snapshots:
                if not snap.data_json: continue
                try:
                    data = json.loads(snap.data_json)
                    nodes = data.get("nodes", [])
                    mapid_data_list = calculate_mapid_usage(
                        nodes,
                        rules,
                        default_include=default_include,
                        projects=data.get("projects", [])
                    )
                    
                    for m_data in mapid_data_list:
                        m_usage = MapidLicenseUsage(
                            cluster_id=snap.cluster_id,
                            timestamp=snap.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                            mapid=m_data["mapid"],
                            lob=m_data["lob"],
                            node_count=m_data["node_count"],
                            total_vcpu=m_data["total_vcpu"],
                            license_count=m_data["license_count"]
                        )
                        session.add(m_usage)
                except Exception as e:
                    print(f"Error backfilling snapshot {snap.id}: {e}")
            
            session.commit()
            
            # Re-query
            results = session.exec(statement).all()

    # Processing:
    # 1. Bucket by Date -> Cluster -> MAPID -> Max(License)
    processed = {} # Date -> { ClusterID -> { MAPID -> MaxLic } }
    
    for row in results:
        dt = row.timestamp.split(" ")[0]
        cid = row.cluster_id
        mid = row.mapid
        
        if dt not in processed: processed[dt] = {}
        if cid not in processed[dt]: processed[dt][cid] = {}
        
        current_max = processed[dt][cid].get(mid, 0)
        if row.license_count > current_max:
            processed[dt][cid][mid] = row.license_count
            
    # 2. Sum across clusters: Date -> MAPID -> Sum(MaxLic)
    final_agg = {} # Date -> { MAPID -> Total }
    
    for dt, clusters in processed.items():
        if dt not in final_agg: final_agg[dt] = {}
        for cid, mapids in clusters.items():
            for mid, count in mapids.items():
                if mid not in final_agg[dt]: final_agg[dt][mid] = 0
                final_agg[dt][mid] += count
                
    # 3. Chart Format
    dates = sorted(list(final_agg.keys()))
    all_mapids = set()
    for d in final_agg.values():
        all_mapids.update(d.keys())
        
    datasets = []
    for m in sorted(list(all_mapids)):
        data = []
        for d in dates:
            data.append(final_agg[d].get(m, 0))
        datasets.append({
            "label": m,
            "data": data
        })
        
    return {
        "labels": dates,
        "datasets": datasets
    }

@router.get("/mapid-breakdown")
def get_mapid_breakdown(
    environment: Optional[str] = Query(None),
    datacenter: Optional[str] = Query(None),
    session: Session = Depends(get_session)
):
    """
    Returns the latest MAPID usage aggregated by MAPID.
    Structure:
    [
      {
        "mapid": "12345",
        "lob": "Retail",
        "total_licenses": 50,
        "total_nodes": 10,
        "clusters": [
           { "name": "Cluster A", "environment": "PROD", "licenses": 20, "nodes": 4 },
           ...
        ]
      },
      ...
    ]
    """
    # 1. Identify Target Clusters
    query = select(Cluster)
    if environment:
        query = query.where(Cluster.environment == environment)
    if datacenter:
        query = query.where(Cluster.datacenter == datacenter)
    
    target_clusters = session.exec(query).all()
    cluster_map = {c.id: c for c in target_clusters}
    target_ids = list(cluster_map.keys())

    if not target_ids:
        return []

    from app.models import MapidLicenseUsage

    # 2. Fetch Latest Usage for EACH Cluster
    # Since we can't easily doing a "Greatest-N-per-Group" in basic SQLModel/SQLAlchemy without complex subqueries,
    # and the number of clusters is manageable (dozens to hundreds, not millions),
    # we can fetch recent records and filter in Python or iterate.
    # Efficient approach: Fetch all MapidLicenseUsage records for these clusters from the last 7 days (or 24h),
    # then for each cluster, pick the latest timestamp, and use only those records.
    
    cutoff = datetime.utcnow() - timedelta(days=7) # generous window to catch stale clusters too
    
    # We need to grab data. 
    # Let's fetch all records for target clusters > cutoff
    stmt = select(MapidLicenseUsage).where(
        MapidLicenseUsage.cluster_id.in_(target_ids),
        MapidLicenseUsage.timestamp >= cutoff.isoformat()
    )
    records = session.exec(stmt).all()

    # 3. Filter for Latest per Cluster
    # Map: cluster_id -> max_timestamp
    latest_ts_map = {}
    for r in records:
        if r.cluster_id not in latest_ts_map:
            latest_ts_map[r.cluster_id] = r.timestamp
        else:
            if r.timestamp > latest_ts_map[r.cluster_id]:
                latest_ts_map[r.cluster_id] = r.timestamp

    # 4. Aggregate
    mapid_stats = {} # mapid -> { details... }

    for r in records:
        # Must be the latest snapshot for that cluster
        if r.timestamp != latest_ts_map[r.cluster_id]:
            continue
            
        mid = r.mapid
        if mid not in mapid_stats:
            mapid_stats[mid] = {
                "mapid": mid,
                "lob": r.lob or "-",
                "total_licenses": 0,
                "total_nodes": 0,
                "total_projects": 0,
                "total_vcpu": 0.0,
                "clusters": []
            }
        
        c = cluster_map[r.cluster_id]
        
        mapid_stats[mid]["total_licenses"] += r.license_count
        mapid_stats[mid]["total_nodes"] += r.node_count
        mapid_stats[mid]["total_vcpu"] += r.total_vcpu
        
        mapid_stats[mid]["clusters"].append({
            "name": c.name,
            "cluster_id": c.id,
            "environment": c.environment or "-",
            "datacenter": c.datacenter or "-",
            "licenses": r.license_count,
            "nodes": r.node_count,
            "projects": 0,
            "vcpu": r.total_vcpu
        })

    _add_project_mapids_to_breakdown(session, cluster_map, mapid_stats)

    # Convert to list
    results = list(mapid_stats.values())
    
    # Sort by total licenses desc
    results.sort(key=lambda x: (-x["total_licenses"], x["mapid"]))
    
    return results
            


@router.get("/mapid/cluster-breakdown")
def get_mapid_cluster_breakdown(session: Session = Depends(get_session)):
    """Returns the latest breakdown of MAPIDs per cluster."""
    clusters = session.exec(select(Cluster)).all()
    results = []
    
    from app.models import MapidLicenseUsage
    
    for c in clusters:
        # Get latest timestamp for this cluster
        last_entry = session.exec(select(MapidLicenseUsage).where(MapidLicenseUsage.cluster_id == c.id).order_by(MapidLicenseUsage.timestamp.desc()).limit(1)).first()
        
        if not last_entry:
            continue
            
        latest_ts = last_entry.timestamp
        
        # Get all records for this TS
        entries = session.exec(select(MapidLicenseUsage).where(
            MapidLicenseUsage.cluster_id == c.id,
            MapidLicenseUsage.timestamp == latest_ts
        )).all()
        
        mapids = []
        for e in entries:
            mapids.append({
                "mapid": e.mapid,
                "lob": e.lob,
                "node_count": e.node_count,
                "license_count": e.license_count,
                "vcpu": e.total_vcpu
            })
            
        results.append({
            "cluster_name": c.name,
            "cluster_id": c.id, 
            "environment": c.environment or "None",
            "datacenter": c.datacenter or "None",
            "timestamp": latest_ts,
            "mapids": mapids
        })
        
    return results

@router.get("/mapid/unmapped-nodes")
def get_unmapped_nodes_details(session: Session = Depends(get_session)):
    """
    Returns a list of nodes that are licensed but have 'Unmapped' MAPID.
    Optimization: Only checks clusters that have reported 'Unmapped' usage in MapidLicenseUsage.
    """
    from app.models import MapidLicenseUsage, LicenseRule, AppConfig, NamespaceExclusionRule
    from app.services.license import calculate_licenses
    
    results = []
    cutoff = datetime.utcnow() - timedelta(days=7)

    # 1. Identify clusters that HAVE unmapped nodes recently
    # We look for records with mapid="Unmapped" (case sensitive match to service logic)
    # or empty string.
    stmt = select(MapidLicenseUsage.cluster_id).where(
        (MapidLicenseUsage.mapid == "Unmapped") | (MapidLicenseUsage.mapid == ""),
        MapidLicenseUsage.timestamp >= cutoff.isoformat()
    ).distinct()
    
    target_cluster_ids = session.exec(stmt).all()
    
    if not target_cluster_ids:
        return []
        
    # 2. Fetch clusters
    clusters = session.exec(select(Cluster).where(Cluster.id.in_(target_cluster_ids))).all()
    
    # 3. Load snapshot only for these clusters
    rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True).order_by(LicenseRule.order, LicenseRule.id)).all()
    ns_rules = session.exec(select(NamespaceExclusionRule).where(NamespaceExclusionRule.is_active == True)).all()
    default_include = (session.get(AppConfig, "LICENSE_DEFAULT_INCLUDE") or AppConfig(value="False")).value.lower() == "true"
    
    import re

    for c in clusters:
        # Get latest snapshot
        snap = session.exec(select(ClusterSnapshot).where(
            ClusterSnapshot.cluster_id == c.id,
            ClusterSnapshot.status == "Success"
        ).order_by(ClusterSnapshot.timestamp.desc()).limit(1)).first()
        
        if snap and snap.data_json:
            try:
                data = json.loads(snap.data_json)
                nodes = data.get("nodes", [])
                projects = data.get("projects", [])
                
                # --- NODES CHECK ---
                lic_res = calculate_licenses(nodes, rules, default_include)
                
                for detail in lic_res["details"]:
                    if detail["status"] == "INCLUDED":
                        # Check labels
                        node_obj = next((n for n in nodes if n["metadata"]["name"] == detail["name"]), None)
                        if node_obj:
                            labels = node_obj["metadata"].get("labels", {})
                            val = labels.get("mapid", "Unmapped")
                            
                            if val == "Unmapped" or val == "":
                                results.append({
                                    "cluster_name": c.name,
                                    "node_name": f"[Node] {detail['name']}",
                                    "reason": f"Licensed Node missing MAPID"
                                })
                
                # --- PROJECTS CHECK ---
                for p in projects:
                    name = p["metadata"]["name"]
                    labels = p["metadata"].get("labels", {})
                    
                    # 1. Check Exclusions
                    is_excluded = False
                    for rule in ns_rules:
                        try:
                            if re.search(rule.match_pattern, name):
                                is_excluded = True
                                break
                        except:
                            pass # Bad regex
                    
                    if is_excluded:
                        continue
                        
                    # 2. Check MapID
                    val = labels.get("mapid", "Unmapped")
                    if val == "Unmapped" or val == "":
                         results.append({
                            "cluster_name": c.name,
                            "node_name": f"[Project] {name}",
                            "reason": "Project missing MAPID"
                        })

            except Exception as e:
                print(f"Error checking unmapped nodes for {c.name}: {e}")


    return results


@router.get("/{cluster_id}/mapid/{mapid}/resources")
def get_mapid_resources(cluster_id: int, mapid: str, snapshot_time: Optional[str] = Query(None), session: Session = Depends(get_session)):
    """Returns nodes and projects (namespaces) for a specific Cluster + MAPID."""
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    # Logic similar to other endpoints: Fetch snapshot or live
    snapshot_data = None
    if snapshot_time:
        try:
            clean_ts = snapshot_time.replace("T", " ")
            target_dt = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
            snap = get_snapshot_for_cluster(session, cluster_id, target_dt)
            if snap and snap.data_json:
                snapshot_data = json.loads(snap.data_json)
        except:
             pass

    # If no snapshot_data found (and no time requesting), try live or fallback to latest snapshot?
    # For drill down consistency, if no time is provided, we should probably look at latest snapshot 
    # since the analytics view is based on "latest" or specific time.
    if not snapshot_data and not snapshot_time:
         snap = session.exec(select(ClusterSnapshot).where(
            ClusterSnapshot.cluster_id == cluster_id,
            ClusterSnapshot.status == "Success"
        ).order_by(ClusterSnapshot.timestamp.desc()).limit(1)).first()
         if snap and snap.data_json:
             snapshot_data = json.loads(snap.data_json)
    
    nodes = []
    projects = []
    
    if snapshot_data:
        nodes = snapshot_data.get("nodes", [])
        projects = snapshot_data.get("projects", [])
    else:
        # Fallback to live fetch (might be slow but accurate)
        try:
            nodes = fetch_resources(cluster, "v1", "Node")
            projects = fetch_resources(cluster, "project.openshift.io/v1", "Project")
        except:
            pass
            
    # Filter
    filtered_nodes = []
    for n in nodes:
        lbls = n.get("metadata", {}).get("labels", {})
        if str(lbls.get("mapid", "")).lower() == mapid.lower():
            filtered_nodes.append({
                "name": n["metadata"]["name"],
                "creationTimestamp": n["metadata"]["creationTimestamp"]
            })
            
    filtered_projects = []
    for p in projects:
        lbls = p.get("metadata", {}).get("labels", {})
        if str(lbls.get("mapid", "")).lower() == mapid.lower():
             filtered_projects.append({
                "name": p["metadata"]["name"],
                "creationTimestamp": p["metadata"]["creationTimestamp"],
                "requester": p["metadata"].get("annotations", {}).get("openshift.io/requester", "-")
            })

    return {
        "nodes": filtered_nodes,
        "projects": filtered_projects
    }

@router.get("/trends/diffs")
def get_resource_trends_diffs(
    environment: Optional[str] = Query(None),
    datacenter: Optional[str] = Query(None),
    cluster_id: Optional[int] = Query(None),
    days: int = Query(30),
    start_date: Optional[str] = Query(None),
    session: Session = Depends(get_session)
):
    """
    Returns a list of specific changes (Added/Removed nodes) that caused license count shifts.
    """
    # 1. Base Query
    cluster_query = select(Cluster.id, Cluster.name)
    if cluster_id:
        cluster_query = cluster_query.where(Cluster.id == cluster_id)
    if environment:
        cluster_query = cluster_query.where(Cluster.environment == environment)
    if datacenter:
        cluster_query = cluster_query.where(Cluster.datacenter == datacenter)
    
    clusters = session.exec(cluster_query).all()
    filtered_cluster_ids = [c.id for c in clusters]
    cluster_map = {c.id: c.name for c in clusters}

    if not filtered_cluster_ids:
        return []

    # 2. Cutoff
    if start_date:
        try:
             cutoff = datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
             cutoff = datetime.utcnow() - timedelta(days=days)
    else:
        cutoff = datetime.utcnow() - timedelta(days=days)


    # Global Cache for Diff Results (Simple In-Memory)
    # Key: f"{prev_id}-{curr_id}", Value: List[ChangeDict]
    # Since snapshots are immutable, this is safe.
    global DIFF_CACHE
    if 'DIFF_CACHE' not in globals():
        DIFF_CACHE = {}
    
    # 3. Fetch Snapshots
    # We need strictly ordered snapshots per cluster
    statement = select(
        ClusterSnapshot.id,
        ClusterSnapshot.cluster_id, 
        ClusterSnapshot.timestamp, 
        ClusterSnapshot.license_count,
        ClusterSnapshot.data_json
    ).where(
        ClusterSnapshot.cluster_id.in_(filtered_cluster_ids),
        ClusterSnapshot.timestamp >= cutoff,
        ClusterSnapshot.status == "Success"
    ).order_by(
        ClusterSnapshot.cluster_id,
        ClusterSnapshot.timestamp.asc()
    )
    
    rows = session.exec(statement).all()
    
    # Group by cluster
    grouped = {}
    for r in rows:
        if r.cluster_id not in grouped: grouped[r.cluster_id] = []
        grouped[r.cluster_id].append(r)

    changes = []
    
    from app.services.license import calculate_licenses
    from app.models import LicenseRule, AppConfig

    # Optimization: Cache rules since they don't change per loop
    rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True).order_by(LicenseRule.order, LicenseRule.id)).all()
    default_include = (session.get(AppConfig, "LICENSE_DEFAULT_INCLUDE") or AppConfig(value="False")).value.lower() == "true"

    # Helper to find vCPU
    def get_vcpu(name, nodes_list):
        n = next((x for x in nodes_list if x['metadata']['name'] == name), None)
        if n:
            try:
                return n['status']['capacity'].get('cpu', '?')
            except:
                return '?'
        return '?'

    # Helper to find MAPID label
    def get_mapid(name, nodes_list):
        n = next((x for x in nodes_list if x['metadata']['name'] == name), None)
        if n:
            return n.get('metadata', {}).get('labels', {}).get('mapid', '-')
        return '-'

    for cid, snaps in grouped.items():
        if len(snaps) < 2: continue
        
        c_name = cluster_map.get(cid, "Unknown")
        
        # Compare i with i-1
        for i in range(1, len(snaps)):
            curr = snaps[i]
            prev = snaps[i-1]
            
            if curr.license_count != prev.license_count:
                # CACHE CHECK
                cache_key = f"{prev.id}-{curr.id}"
                if cache_key in DIFF_CACHE:
                    # Cache Hit
                    changes.extend(DIFF_CACHE[cache_key])
                    continue

                # CACHE MISS - Calculate
                local_changes = []
                try:
                    prev_data = json.loads(prev.data_json) if prev.data_json else {}
                    curr_data = json.loads(curr.data_json) if curr.data_json else {}
                    
                    prev_nodes = prev_data.get("nodes", [])
                    curr_nodes = curr_data.get("nodes", [])
                    
                    # Calculate licenses to know WHICH nodes are licensed
                    prev_lic = calculate_licenses(prev_nodes, rules, default_include)
                    curr_lic = calculate_licenses(curr_nodes, rules, default_include)
                    
                    prev_licensed_names = set(d['name'] for d in prev_lic['details'] if d['status'] == 'INCLUDED')
                    curr_licensed_names = set(d['name'] for d in curr_lic['details'] if d['status'] == 'INCLUDED')
                    
                    added = curr_licensed_names - prev_licensed_names
                    removed = prev_licensed_names - curr_licensed_names
                    
                    timestamp = curr.timestamp.strftime("%Y-%m-%d %H:%M:%S")

                    for node_name in added:
                        vcpu = get_vcpu(node_name, curr_nodes)
                        mapid = get_mapid(node_name, curr_nodes)
                        local_changes.append({
                            "timestamp": timestamp,
                            "cluster": c_name,
                            "type": "ADDED",
                            "detail": f"Node {node_name} (Licensed)",
                            "vcpu": vcpu,
                            "mapid": mapid,
                            "diff": curr.license_count - prev.license_count
                        })
                        
                    for node_name in removed:
                        vcpu = get_vcpu(node_name, prev_nodes)
                        mapid = get_mapid(node_name, prev_nodes)
                        local_changes.append({
                            "timestamp": timestamp,
                            "cluster": c_name,
                            "type": "REMOVED",
                            "detail": f"Node {node_name} (Licensed)",
                            "vcpu": vcpu,
                            "mapid": mapid,
                            "diff": curr.license_count - prev.license_count
                        })

                    # If count changed but no nodes added/removed (maybe CPU count changed on existing node?)
                    if not added and not removed:
                         local_changes.append({
                            "timestamp": timestamp,
                            "cluster": c_name,
                            "type": "MODIFIED",
                            "detail": "License count changed but set of licensed nodes matches. Likely vCPU adjustment.",
                            "vcpu": "-",
                            "mapid": "-",
                            "diff": curr.license_count - prev.license_count
                        })
                    
                    # STORE IN CACHE
                    DIFF_CACHE[cache_key] = local_changes
                    changes.extend(local_changes)

                except Exception as e:
                    print(f"Error diffing snapshots for {c_name}: {e}")
                    
    # Sort by timestamp desc
    changes.sort(key=lambda x: x["timestamp"], reverse=True)
    return changes
