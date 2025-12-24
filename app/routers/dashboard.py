from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from typing import Any, List, Dict, Optional

from datetime import datetime, timedelta
import json
from app.database import get_session
from app.models import Cluster, LicenseUsage, AppConfig, LicenseRule, ClusterSnapshot
from app.services.ocp import fetch_resources, get_cluster_stats, parse_cpu, get_detailed_stats, parse_memory_to_gb

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
    "machineautoscalers": {"api_version": "autoscaling.openshift.io/v1beta1", "kind": "MachineAutoscaler"},
    "ingresscontrollers": {"api_version": "operator.openshift.io/v1", "kind": "IngressController"},
}

def get_snapshot_for_cluster(session: Session, cluster_id: int, target_time: datetime) -> Optional[ClusterSnapshot]:
    """Finds the closest successful snapshot ON or BEFORE the target time."""
    statement = select(ClusterSnapshot).where(
        ClusterSnapshot.cluster_id == cluster_id,
        ClusterSnapshot.timestamp <= target_time,
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
    statement = select(ClusterSnapshot.timestamp).where(ClusterSnapshot.timestamp >= cutoff).distinct().order_by(ClusterSnapshot.timestamp.desc())
    timestamps = session.exec(statement).all()
    # Format them nicely
    return [t.strftime("%Y-%m-%dT%H:%M:%S") for t in timestamps]

@router.get("/{cluster_id}/resources/{resource_type}")
def get_cluster_resources(cluster_id: int, resource_type: str, snapshot_time: Optional[str] = Query(None), session: Session = Depends(get_session)):
    if resource_type not in RESOURCE_MAP:
        raise HTTPException(status_code=400, detail="Invalid resource type")
    
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")

    # Time Travel Logic
    if snapshot_time:
        try:
            target_dt = datetime.strptime(snapshot_time, "%Y-%m-%dT%H:%M:%S")
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
        return fetch_resources(cluster, meta["api_version"], meta["kind"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{cluster_id}/details")
def get_cluster_details(cluster_id: int, snapshot_time: Optional[str] = Query(None), session: Session = Depends(get_session)):
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    snapshot_data = None
    if snapshot_time:
        try:
            target_dt = datetime.strptime(snapshot_time, "%Y-%m-%dT%H:%M:%S")
            snap = get_snapshot_for_cluster(session, cluster_id, target_dt)
            if snap and snap.data_json:
                snapshot_data = json.loads(snap.data_json)
        except ValueError:
            pass # Ignore invalid time format, fallback to live? Or error? Let's fallback for robustness but maybe should error.

    try:
        return get_detailed_stats(cluster, snapshot_data=snapshot_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{cluster_id}/ingress/{name}/details")
def get_ingress_details_endpoint(cluster_id: int, name: str, snapshot_time: Optional[str] = Query(None), session: Session = Depends(get_session)):
    from app.services.ocp import get_ingress_details
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    snapshot_data = None
    if snapshot_time:
        try:
            target_dt = datetime.strptime(snapshot_time, "%Y-%m-%dT%H:%M:%S")
            snap = get_snapshot_for_cluster(session, cluster_id, target_dt)
            if snap and snap.data_json:
                snapshot_data = json.loads(snap.data_json)
        except:
            pass

    try:
        return get_ingress_details(cluster, name, snapshot_data=snapshot_data)
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
            target_dt = datetime.strptime(snapshot_time, "%Y-%m-%dT%H:%M:%S")
            snap = get_snapshot_for_cluster(session, cluster_id, target_dt)
            if snap and snap.data_json:
                snapshot_data = json.loads(snap.data_json)
        except:
            pass

    try:
        return get_node_details(cluster, node_name, snapshot_data=snapshot_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{cluster_id}/license-details/{usage_id}")
def get_license_details(cluster_id: int, usage_id: str, snapshot_time: Optional[str] = Query(None), session: Session = Depends(get_session)):
    """Returns detailed license breakdown for a cluster, either from history or a snapshot."""
    # usage_id can be "null" if coming from a dashboard without usage history record (like custom snapshot views)
    
    # Priority 1: Time Travel Snapshot
    if snapshot_time:
        try:
            target_dt = datetime.strptime(snapshot_time, "%Y-%m-%dT%H:%M:%S")
            snap = get_snapshot_for_cluster(session, cluster_id, target_dt)
            if snap and snap.data_json:
                data = json.loads(snap.data_json)
                nodes = data.get("nodes", [])
                from app.models import LicenseRule
                rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True)).all()
                lic_data = calculate_licenses(nodes, rules)
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
        from app.models import LicenseRule
        rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True)).all()
        lic_data = calculate_licenses(nodes, rules)
        return {
            "node_count": lic_data["node_count"],
            "total_vcpu": lic_data["total_vcpu"],
            "license_count": lic_data["total_licenses"],
            "details": lic_data["details"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/summary")
def get_dashboard_summary(snapshot_time: Optional[str] = Query(None), session: Session = Depends(get_session)):
    clusters = session.exec(select(Cluster)).all()
    
    # Fetch Config
    rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True)).all()
    
    summary = []
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
            target_dt = datetime.strptime(snapshot_time, "%Y-%m-%dT%H:%M:%S")
        except:
            pass

    for cluster in clusters:
        # Determine stats source
        snapshot_data = None
        if target_dt:
             snap = get_snapshot_for_cluster(session, cluster.id, target_dt)
             if snap and snap.data_json:
                 snapshot_data = json.loads(snap.data_json)
                 stats = get_cluster_stats(cluster, snapshot_data=snapshot_data)
                 s_nodes = snapshot_data.get("nodes", [])
                 lic_data = calculate_licenses(s_nodes, rules)
                 
                 summary.append({
                    "id": cluster.id,
                    "name": cluster.name,
                    "datacenter": cluster.datacenter,
                    "environment": cluster.environment,
                    "stats": stats,
                    "license_info": {
                        "count": lic_data["total_licenses"],
                        "usage_id": "null" # Signal to JS that this is snapshot-based
                    },
                    "licensed_node_count": lic_data["node_count"],
                    "licensed_vcpu_count": lic_data["total_vcpu"]
                 })
                 
                 # Accumulate Globals
                 global_stats["total_nodes"] += stats["node_count"]
                 global_stats["total_licensed_nodes"] += lic_data["node_count"]
                 global_stats["total_vcpu"] += stats["vcpu_count"]
                 global_stats["total_licensed_vcpu"] += lic_data["total_vcpu"]
                 global_stats["total_licenses"] += lic_data["total_licenses"]
                 
                 continue # Next cluster
             else:
                 # Snapshot requested but missing for this cluster?
                 # Show empty or last known?
                 # Let's show "N/A"
                 summary.append({
                    "id": cluster.id,
                    "name": cluster.name,
                    "datacenter": cluster.datacenter,
                    "environment": cluster.environment,
                    "stats": {"node_count": "-", "vcpu_count": "-", "version": "-", "console_url": "#"},
                    "license_info": {"count": "-", "usage_id": None},
                    "licensed_node_count": "-",
                    "licensed_vcpu_count": "-"
                 })
                 continue


        # Live Logic (Old Path)
        try:
             nodes = fetch_resources(cluster, "v1", "Node")
             stats = get_cluster_stats(cluster, nodes=nodes)
        except Exception as e:
             print(f"Error fetching nodes for {cluster.name}: {e}")
             nodes = []
             stats = {"node_count": 0, "vcpu_count": 0, "version": "-", "console_url": "#"}

        # Calculate Licenses
        lic_data = calculate_licenses(nodes, rules)
        
        # Save History ONLY IF LIVE
        if not target_dt:
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
            
        summary.append({
            "id": cluster.id,
            "name": cluster.name,
            "datacenter": cluster.datacenter,
            "environment": cluster.environment,
            "stats": stats,
            "license_info": {
                "count": lic_data["total_licenses"],
                "usage_id": usage.id if not target_dt else None # TODO: fix this ref if snapshot
            },
            "licensed_node_count": lic_data["node_count"],
            "licensed_vcpu_count": lic_data["total_vcpu"]
        })
        
        global_stats["total_nodes"] += (stats["node_count"] if isinstance(stats["node_count"], int) else 0)
        global_stats["total_licensed_nodes"] += lic_data["node_count"]
        global_stats["total_vcpu"] += (stats["vcpu_count"] if isinstance(stats["vcpu_count"], int) else 0)
        global_stats["total_licensed_vcpu"] += lic_data["total_vcpu"]
        global_stats["total_licenses"] += lic_data["total_licenses"]

    return {
        "clusters": summary,
        "global_stats": global_stats,
        "timestamp": timestamp if not target_dt else snapshot_time
    }
