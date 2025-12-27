from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select, SQLModel
from typing import List, Optional
import json
import asyncio
from pydantic import BaseModel
from app.database import get_session
from app.models import Cluster, ClusterCreate, ClusterRead, ClusterUpdate, AppConfig, ClusterSnapshot
from app.services.scheduler import reschedule_job

class ConfigUpdate(BaseModel):
    poll_interval_minutes: int
    snapshot_retention_days: int

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
def test_connection_endpoint(data: ClusterTestRequest, session: Session = Depends(get_session)):
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
def create_cluster(cluster: ClusterCreate, session: Session = Depends(get_session)):
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
def read_clusters(offset: int = 0, limit: int = 100, session: Session = Depends(get_session)):
    clusters = session.exec(select(Cluster).offset(offset).limit(limit)).all()
    return clusters

@router.post("/config/scheduler")
def update_scheduler_config(config: ConfigUpdate, session: Session = Depends(get_session)):
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
    
    session.commit()
    
    # Trigger Reschedule
    reschedule_job()
    
    return {
        "status": "updated", 
        "interval": config.poll_interval_minutes,
        "retention_days": config.snapshot_retention_days
    }

@router.get("/config/scheduler/run-stream")
def trigger_manual_poll_stream(session: Session = Depends(get_session)):
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
def trigger_manual_poll(session: Session = Depends(get_session)):
    """Manually triggers the background poller."""
    from app.services.poller import poll_all_clusters
    try:
        poll_all_clusters()
        return {"status": "success", "message": "Manual poll triggered"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/snapshots", response_model=List[dict])
def list_snapshots(limit: int = 50, offset: int = 0, session: Session = Depends(get_session)):
    """Groups snapshots by global timestamp (run)."""
    # Fetch flat list with LEFT OUTER JOIN to include snapshots even if cluster is deleted
    statement = select(ClusterSnapshot, Cluster.name).join(Cluster, isouter=True).order_by(ClusterSnapshot.timestamp.desc()).offset(offset).limit(limit)
    results = session.exec(statement).all()
    
    # Check for groupings
    groups = []
    # Helper to find or create group
    def get_or_create_group(ts):
        # Timestamps are datetime objects. We compare by value equality.
        # Since we just unified them in poller, equality check should be fine.
        # But for robustness with legacy data (bucketing), let's just group by exact TS string for now.
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
            "snapshots": []
        }
        groups.append(new_g)
        return new_g

    for snap, c_name in results:
        g = get_or_create_group(snap.timestamp)
        
        # Aggregate logic
        g['total_clusters'] += 1
        g['total_nodes'] += (snap.node_count or 0)
        g['total_vcpu'] += (snap.vcpu_count or 0)
        
        if snap.status != 'Success':
            g['status'] = 'Partial/Failed'

        # Resolve Name: captured_name (historical) > c_name (live join) > "Unknown"
        resolved_name = snap.captured_name or c_name or "Unknown Cluster"

        g['snapshots'].append({
            "id": snap.id,
            "cluster_name": resolved_name,
            "status": snap.status,
            "node_count": snap.node_count,
            "vcpu_count": snap.vcpu_count
        })
        
    return groups

@router.post("/snapshots/cleanup")
def cleanup_snapshots(request: CleanupRequest, session: Session = Depends(get_session)):
    """Deletes snapshots older than X days."""
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=request.days)
    
    statement = select(ClusterSnapshot).where(ClusterSnapshot.timestamp < cutoff)
    results = session.exec(statement).all()
    
    count = 0
    for snap in results:
        session.delete(snap)
        count += 1
    
    session.commit()
    return {"status": "success", "deleted_count": count}

@router.delete("/snapshots/{snapshot_id}")
def delete_snapshot(snapshot_id: int, session: Session = Depends(get_session)):
    snap = session.get(ClusterSnapshot, snapshot_id)
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    
    session.delete(snap)
    session.commit()
    return {"ok": True}

@router.get("/{cluster_id}", response_model=ClusterRead)
def read_cluster(cluster_id: int, session: Session = Depends(get_session)):
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return cluster

@router.delete("/{cluster_id}")
def delete_cluster(cluster_id: int, session: Session = Depends(get_session)):
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    session.delete(cluster)
    session.commit()
    return {"ok": True}

@router.get("/config/db-stats")
def get_db_stats(session: Session = Depends(get_session)):
    """Returns database size and record counts."""
    import os
    from app.database import DATABASE_URL
    from app.models import ClusterSnapshot
    from sqlmodel import func
    
    # Get file size
    db_file = DATABASE_URL.replace("sqlite:///", "")
    size_bytes = 0
    if os.path.exists(db_file):
        size_bytes = os.path.getsize(db_file)
    
    # Get record counts
    cluster_count = session.exec(select(func.count(Cluster.id))).one()
    snapshot_count = session.exec(select(func.count(ClusterSnapshot.id))).one()
    
    # Calculate avg snapshot size (heuristically)
    avg_snapshot_size_kb = 0
    if snapshot_count > 0:
        # This is a bit of a guess since we don't know the exact base size,
        # but it gives the user an idea of growth.
        avg_snapshot_size_kb = (size_bytes / 1024) / snapshot_count

    return {
        "file_size_mb": round(size_bytes / (1024 * 1024), 2),
        "cluster_count": cluster_count,
        "snapshot_count": snapshot_count,
        "avg_snapshot_size_kb": round(avg_snapshot_size_kb, 2),
        "db_filename": db_file
    }

@router.patch("/{cluster_id}", response_model=ClusterRead)
def update_cluster(cluster_id: int, cluster: ClusterUpdate, session: Session = Depends(get_session)):
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
        
