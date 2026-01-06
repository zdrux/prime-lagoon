from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select, SQLModel
from typing import List, Optional
import json
import asyncio
from pydantic import BaseModel
from app.database import get_session
from app.models import Cluster, ClusterCreate, ClusterRead, ClusterUpdate, AppConfig, ClusterSnapshot, User
from app.services.scheduler import reschedule_job
from app.dependencies import admin_required

class ConfigUpdate(BaseModel):
    poll_interval_minutes: int
    snapshot_retention_days: int
    collect_olm: bool
    run_compliance: bool

class CleanupRequest(BaseModel):
    days: int

class ClusterTestRequest(SQLModel):
    api_url: str
    token: str
    cluster_id: Optional[int] = None

router = APIRouter(
    prefix="/api/admin/clusters",
    tags=["admin"],
)

@router.post("/test-connection")
def test_connection_endpoint(data: ClusterTestRequest, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    """Verifies connection to the cluster using provided credentials."""
    from app.services.ocp import get_dynamic_client
    
    token_to_use = data.token
    
    # If token is masked and we have a cluster_id, fetch the real token from DB
    if token_to_use == "********" and data.cluster_id:
        db_cluster = session.get(Cluster, data.cluster_id)
        if db_cluster:
            token_to_use = db_cluster.token
            
    # Create temp cluster object
    temp_cluster = Cluster(name="test", api_url=data.api_url, token=token_to_use)
    
    try:
        # 1. Connect
        client = get_dynamic_client(temp_cluster)
        
        # 2. Try simple fetch
        version_api = client.resources.get(api_version='config.openshift.io/v1', kind='ClusterVersion')
        try:
             # Try getting specific named object often present
             version_obj = version_api.get(name='version')
             v = version_obj.status.desired.version
             return {"success": True, "message": f"Connected successfully! Version: {v}"}
        except Exception:
             # Fallback if specific object not found, just listing is enough proof
             version_api.get(limit=1)
             return {"success": True, "message": "Connected successfully! (ClusterVersion list accessible)"}
             
    except Exception as e:
        return {"success": False, "message": str(e)}

@router.post("/", response_model=ClusterRead)
def create_cluster(cluster: ClusterCreate, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    db_cluster = Cluster.model_validate(cluster)
    
    # Try to fetch unique ID immediately
    try:
        from app.services.ocp import get_cluster_unique_id
        # We need to commit first to get an ID? No, but we need the object to pass to get_dynamic_client
        # get_dynamic_client uses api_url and token from the object.
        uid = get_cluster_unique_id(db_cluster)
        if uid:
            db_cluster.unique_id = uid
        else:
            # Fallback for offline or error: name + random suffix?
            # Or just leave None? User said "derived from the cluster". 
            # If offline, maybe we can't derive it. 
            # Let's generate a placeholder so at least it HAS a unique ID.
            import uuid
            db_cluster.unique_id = f"{db_cluster.name}-{str(uuid.uuid4())[:8]}"
            
    except Exception as e:
        print(f"Failed to fetch ID during creation: {e}")
        # Fallback
        import uuid
        db_cluster.unique_id = f"{db_cluster.name}-{str(uuid.uuid4())[:8]}"

    session.add(db_cluster)
    session.commit()
    session.refresh(db_cluster)
    return db_cluster

@router.get("/", response_model=List[ClusterRead])
def read_clusters(offset: int = 0, limit: int = 100, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    clusters = session.exec(select(Cluster).offset(offset).limit(limit)).all()
    return clusters

@router.post("/config/scheduler")
def update_scheduler_config(config: ConfigUpdate, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    # Update Interval
    db_interval = session.get(AppConfig, "POLL_INTERVAL_MINUTES")
    if not db_interval:
        db_interval = AppConfig(key="POLL_INTERVAL_MINUTES", value=str(config.poll_interval_minutes))
        session.add(db_interval)
    else:
        db_interval.value = str(config.poll_interval_minutes)
        session.add(db_interval)
    
    # Update Retention
    db_retention = session.get(AppConfig, "SNAPSHOT_RETENTION_DAYS")
    if not db_retention:
        db_retention = AppConfig(key="SNAPSHOT_RETENTION_DAYS", value=str(config.snapshot_retention_days))
        session.add(db_retention)
    else:
        db_retention.value = str(config.snapshot_retention_days)
        session.add(db_retention)
        
    # Update OLM Collection
    db_olm = session.get(AppConfig, "SNAPSHOT_COLLECT_OLM")
    if not db_olm:
        db_olm = AppConfig(key="SNAPSHOT_COLLECT_OLM", value=str(config.collect_olm))
        session.add(db_olm)
    else:
        db_olm.value = str(config.collect_olm)
        session.add(db_olm)

    # Update Compliance
    db_comp = session.get(AppConfig, "SNAPSHOT_COLLECT_COMPLIANCE")
    if not db_comp:
        db_comp = AppConfig(key="SNAPSHOT_COLLECT_COMPLIANCE", value=str(config.run_compliance))
        session.add(db_comp)
    else:
        db_comp.value = str(config.run_compliance)
        session.add(db_comp)
    
    session.commit()
    
    # Trigger Reschedule
    reschedule_job()
    
    return {
        "status": "updated", 
        "interval": config.poll_interval_minutes,
        "retention_days": config.snapshot_retention_days,
        "collect_olm": config.collect_olm,
        "run_compliance": config.run_compliance
    }

@router.get("/config/scheduler/run-stream")
def trigger_manual_poll_stream(session: Session = Depends(get_session), user: User = Depends(admin_required)):
    """Manually triggers the background poller and streams progress updates."""
    from app.services.poller import poll_all_clusters

    def event_generator():
        queue = asyncio.Queue()

        def progress_callback(data):
            # Since the poller runs in a sync way, we need to bridge to async
            # But the poller is currently blocking. 
            # For simplicity in this demo-like app, we'll run it and yield.
            # However, to be truly streaming while polling, 
            # we'd need the poller to be async or run in a thread.
            queue.put_nowait(data)

        # Run poller in a separate thread to allow yielding status
        import threading
        thread = threading.Thread(target=poll_all_clusters, args=(progress_callback,))
        thread.start()

        while thread.is_alive() or not queue.empty():
            try:
                # Poll the queue for new messages
                import time
                while not queue.empty():
                    msg = queue.get_nowait()
                    yield f"data: {json.dumps(msg)}\n\n"
                time.sleep(0.1)
                # Check if thread died with error but queue is empty
                if not thread.is_alive() and queue.empty():
                    break
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                break
        
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.post("/config/scheduler/run")
def trigger_manual_poll(session: Session = Depends(get_session), user: User = Depends(admin_required)):
    """Manually triggers the background poller."""
    from app.services.poller import poll_all_clusters
    try:
        poll_all_clusters()
        return {"status": "success", "message": "Manual poll triggered"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/snapshots", response_model=dict)
def list_snapshots(limit: int = 50, offset: int = 0, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    """Groups snapshots by global timestamp (run)."""
    from sqlmodel import func

    # 1. Get total unique timestamps for pagination
    total_runs = session.exec(select(func.count(func.distinct(ClusterSnapshot.timestamp)))).one()
    total_snapshots = session.exec(select(func.count(ClusterSnapshot.id))).one()
    
    # 2. Get distinct timestamps first (Paginated)
    ts_statement = select(ClusterSnapshot.timestamp)\
        .distinct()\
        .order_by(ClusterSnapshot.timestamp.desc())\
        .offset(offset)\
        .limit(limit)
        
    timestamps = session.exec(ts_statement).all()
    
    if not timestamps:
        return {
            "total_runs": total_runs,
            "total_snapshots": total_snapshots,
            "groups": []
        }

    from app.models import ComplianceScore
    compliance_runs = session.exec(select(ComplianceScore.timestamp).where(ComplianceScore.timestamp.in_([ts.strftime("%Y-%m-%d %H:%M:%S") for ts in timestamps]))).all()
    compliance_ts_set = set(compliance_runs)

    from sqlalchemy.orm import defer
    
    # 3. Fetch all snapshots for these timestamps
    # CRITICAL OPTIMIZATION: Defer data_json to avoid loading megabytes of data per row
    statement = select(ClusterSnapshot, Cluster.name)\
        .join(Cluster, isouter=True)\
        .where(ClusterSnapshot.timestamp.in_(timestamps))\
        .order_by(ClusterSnapshot.timestamp.desc())\
        .options(defer(ClusterSnapshot.data_json))
        
    results = session.exec(statement).all()
    
    # Check for groupings
    groups = []
    # Helper to find or create group
    def get_or_create_group(ts):
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        for g in groups:
            if g['timestamp_str'] == ts_str:
                return g
        
        # New Group
        new_g = {
            "group_id": ts_str, # Use TS as ID
            "timestamp": ts,
            "timestamp_str": ts_str,
            "total_clusters": 0,
            "status": "Success", # Will degrade if any child is partial/failed
            "total_nodes": 0,
            "total_vcpu": 0,
            "collected_components": ["Cluster"], # Cluster always collected
            "snapshots": []
        }
        
        if ts_str in compliance_ts_set:
            new_g["collected_components"].append("Compliance")
            
        groups.append(new_g)
        return new_g

    for snap, c_name in results:
        g = get_or_create_group(snap.timestamp)
        g['total_clusters'] += 1
        g['total_nodes'] += (snap.node_count or 0)
        g['total_vcpu'] += (snap.vcpu_count or 0)
        
        if snap.status != 'Success':
            g['status'] = 'Partial/Failed'

        resolved_name = snap.captured_name or c_name or "Unknown Cluster"

        g['snapshots'].append({
            "id": snap.id,
            "cluster_name": resolved_name,
            "status": snap.status,
            "node_count": snap.node_count,
            "vcpu_count": snap.vcpu_count
        })

    # After aggregating, check for OLM data efficiently for each group
    for g in groups:
        if "Operator" not in g["collected_components"]:
            try:
                # Optimized check: Fetch ONE snapshot's data_json partially or fully for this group
                # Using a separate query to allow the main query to remain light
                sample_snap = session.exec(
                    select(ClusterSnapshot.data_json)
                    .where(ClusterSnapshot.timestamp == g['timestamp'])
                    .where(ClusterSnapshot.data_json.is_not(None))
                    .limit(1)
                ).first()
                
                if sample_snap:
                    # Quick check string properties without parsing full JSON if possible? 
                    # JSON parsing is safer. Even 50MB for one file is better than 50MB * 100
                    data_sample = json.loads(sample_snap)
                    if data_sample.get('csvs') or data_sample.get('subscriptions'):
                        g["collected_components"].append("Operator")
            except:
                pass
        
    return {
        "total_runs": total_runs,
        "total_snapshots": total_snapshots,
        "groups": groups
    }

class BulkDeleteRequest(BaseModel):
    group_ids: List[str] # List of timestamp strings

@router.post("/snapshots/bulk-delete")
def bulk_delete_snapshots(request: BulkDeleteRequest, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    """Deletes all snapshots belonging to multiple runs."""
    from sqlalchemy import text, func
    
    deleted_count = 0
    for ts_str in request.group_ids:
        # 1. Delete associated data using exact string match for timestamp
        # LicenseUsage and ComplianceScore use string timestamps saved with %Y-%m-%d %H:%M:%S
        session.execute(text("DELETE FROM licenseusage WHERE timestamp = :ts"), {"ts": ts_str})
        session.execute(text("DELETE FROM compliancescore WHERE timestamp = :ts"), {"ts": ts_str})
        
        # 2. Find and delete ClusterSnapshots
        # We use strftime to match the string timestamp from UI (SQLite specific)
        statement = select(ClusterSnapshot).where(func.strftime("%Y-%m-%d %H:%M:%S", ClusterSnapshot.timestamp) == ts_str)
        snaps = session.exec(statement).all()
        for s in snaps:
            session.delete(s)
            deleted_count += 1
            
    session.commit()
    return {"status": "success", "deleted_count": deleted_count}


@router.post("/snapshots/cleanup")
def cleanup_snapshots(request: CleanupRequest, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    """Deletes snapshots older than X days."""
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=request.days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    
    from sqlalchemy import text
    # Delete related records based on timestamp
    # Note: Compliance/License tables use string timestamps
    session.execute(text("DELETE FROM licenseusage WHERE timestamp < :cutoff"), {"cutoff": cutoff_str})
    session.execute(text("DELETE FROM compliancescore WHERE timestamp < :cutoff"), {"cutoff": cutoff_str})
    
    # Delete Snapshots
    statement = select(ClusterSnapshot).where(ClusterSnapshot.timestamp < cutoff)
    results = session.exec(statement).all()
    
    count = 0
    for snap in results:
        session.delete(snap)
        count += 1
    
    session.commit()
    return {"status": "success", "deleted_count": count}

@router.delete("/snapshots/{snapshot_id}")
def delete_snapshot(snapshot_id: int, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    snap = session.get(ClusterSnapshot, snapshot_id)
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    
    session.delete(snap)
    session.commit()
    return {"ok": True}

@router.get("/{cluster_id}", response_model=ClusterRead)
def read_cluster(cluster_id: int, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return cluster

@router.delete("/{cluster_id}")
def delete_cluster(cluster_id: int, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    session.delete(cluster)
    session.commit()
    return {"ok": True}

@router.get("/config/db-stats")
def get_db_stats(session: Session = Depends(get_session), user: User = Depends(admin_required)):
    """Returns database size and record counts."""
    import os
    import shutil
    from app.database import DATABASE_URL
    from app.models import ClusterSnapshot, LicenseUsage, ComplianceScore
    from sqlmodel import func, select
    
    # Get file path
    db_file = DATABASE_URL.replace("sqlite:///", "")
    db_dir = os.path.dirname(db_file) or "."
    
    # Get file size
    size_bytes = 0
    if os.path.exists(db_file):
        size_bytes = os.path.getsize(db_file)
    
    # Get disk space
    disk_usage = shutil.disk_usage(db_dir)
    free_mb = round(disk_usage.free / (1024 * 1024), 2)
    total_disk_mb = round(disk_usage.total / (1024 * 1024), 2)
    
    # Get record counts
    cluster_count = session.exec(select(func.count(Cluster.id))).one()
    snapshot_count = session.exec(select(func.count(ClusterSnapshot.id))).one()
    
    # Calculate Record Sizes
    snapshot_size_bytes = session.exec(select(func.sum(func.length(ClusterSnapshot.data_json)))).one() or 0
    usage_size_bytes = session.exec(select(func.sum(func.length(LicenseUsage.details_json)))).one() or 0
    compliance_size_bytes = session.exec(select(func.sum(func.length(ComplianceScore.results_json)))).one() or 0
    
    # ESTIMATE Operator portion within snapshots (sum of lengths of csvs + subscriptions keys)
    # We'll use a rough estimation if json_extract is not performing well or available
    # For this demo, let's try to fetch a few samples and calculate average ratio
    op_ratio = 0.4 # Default estimate: 40% of snapshot data is OLM data (CSVs are bulky)
    try:
        sample = session.exec(select(ClusterSnapshot.data_json).limit(5)).all()
        if sample:
            ratios = []
            for sj in sample:
                try:
                    d = json.loads(sj)
                    full_len = len(sj)
                    
                    # Safe Extraction of Operator Data Size
                    op_len = 0
                    if "csvs" in d:
                        op_len += len(json.dumps(d["csvs"]))
                    if "subscriptions" in d:
                        op_len += len(json.dumps(d["subscriptions"]))
                        
                    if full_len > 0: ratios.append(op_len / full_len)
                except Exception as e:
                    # If JSON fails or other error, assume 0 for this sample or skip
                    continue
            if ratios: op_ratio = sum(ratios) / len(ratios)
            else: op_ratio = 0 # No OLM data found in samples
    except: 
        op_ratio = 0

    op_data_bytes = int(snapshot_size_bytes * op_ratio)
    inventory_data_bytes = snapshot_size_bytes - op_data_bytes

    total_json_bytes = snapshot_size_bytes + usage_size_bytes + compliance_size_bytes
    other_size_bytes = max(0, size_bytes - total_json_bytes)
    
    avg_snap_size_mb = round((snapshot_size_bytes / (1024 * 1024)) / snapshot_count, 2) if snapshot_count > 0 else 0
    
    return {
        "file_size_mb": round(size_bytes / (1024 * 1024), 2),
        "free_space_mb": free_mb,
        "total_disk_mb": total_disk_mb,
        "is_space_critical": free_mb < (size_bytes / (1024 * 1024)) * 1.2, # Warning if less than 120% of DB size
        "cluster_count": cluster_count,
        "snapshot_count": snapshot_count,
        "snapshot_data_mb": round(snapshot_size_bytes / (1024 * 1024), 2),
        "op_data_mb": round(op_data_bytes / (1024 * 1024), 2),
        "inventory_data_mb": round(inventory_data_bytes / (1024 * 1024), 2),
        "usage_data_mb": round(usage_size_bytes / (1024 * 1024), 2),
        "compliance_data_mb": round(compliance_size_bytes / (1024 * 1024), 2),
        "other_data_mb": round(other_size_bytes / (1024 * 1024), 2),
        "avg_snapshot_size_mb": avg_snap_size_mb,
        "db_filename": db_file
    }

@router.post("/config/db-vacuum", status_code=202)
def vacuum_db(background_tasks: BackgroundTasks, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    """Runs SQLite VACUUM to reclaim space in the background."""
    from app.services.maintenance import run_vacuum_task
    
    background_tasks.add_task(run_vacuum_task)
    return {"status": "accepted", "message": "Database optimization started in background."}

@router.patch("/{cluster_id}", response_model=ClusterRead)
def update_cluster(cluster_id: int, cluster: ClusterUpdate, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    db_cluster = session.get(Cluster, cluster_id)
    if not db_cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    cluster_data = cluster.model_dump(exclude_unset=True)
    
    # If token is masked, remove it from update so we don't overwrite with '********'
    if cluster_data.get('token') == "********":
        del cluster_data['token']
        
    for key, value in cluster_data.items():
        setattr(db_cluster, key, value)
        
    session.add(db_cluster)
    session.commit()
    session.refresh(db_cluster)
    return db_cluster

    return db_cluster
        
