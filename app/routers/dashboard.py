from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from typing import Any, List, Dict, Optional

from datetime import datetime, timedelta
import json
from app.database import get_session
from app.models import Cluster, LicenseUsage, AppConfig, LicenseRule, ClusterSnapshot
from app.services.ocp import fetch_resources, get_cluster_stats, parse_cpu, get_detailed_stats, parse_memory_to_gb, get_dynamic_client

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
    "machineautoscalers": {"api_version": "autoscaling.openshift.io/v1beta1", "kind": "MachineAutoscaler"},
}

def get_snapshot_for_cluster(session: Session, cluster_id: int, target_time: datetime) -> Optional[ClusterSnapshot]:
    """Finds the closest successful snapshot ON or BEFORE the target time (with 5m grace)."""
    # Adding a grace period to target_time to account for multi-cluster polling delays
    grace_target = target_time + timedelta(seconds=300)
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

@router.get("/summary")
def get_dashboard_summary(snapshot_time: Optional[str] = Query(None), mode: Optional[str] = Query(None), session: Session = Depends(get_session)):
    clusters = session.exec(select(Cluster)).all()
    
    # Fetch Config
    rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True)).all()
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
            target_dt = datetime.strptime(snapshot_time, "%Y-%m-%dT%H:%M:%S")
        except:
            pass

    results = []

    # Fast Mode: Return latest snapshot data immediately
    if mode == "fast" and not target_dt:
        for cluster in clusters:
            # Get latest snapshot
            snap = session.exec(select(ClusterSnapshot).where(
                ClusterSnapshot.cluster_id == cluster.id,
                ClusterSnapshot.status == "Success"
            ).order_by(ClusterSnapshot.timestamp.desc()).limit(1)).first()
            
            if snap and snap.data_json:
                snapshot_data = json.loads(snap.data_json)
                stats = get_cluster_stats(cluster, snapshot_data=snapshot_data)
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
            done, not_done = concurrent.futures.wait(futures.keys(), timeout=10)
            
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

    return {
        "clusters": results,
        "global_stats": global_stats,
        "timestamp": timestamp if not target_dt else snapshot_time
    }

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
        print(f"Error fetching live stats for {cluster.name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
