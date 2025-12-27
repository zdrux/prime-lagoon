from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select, func
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from app.database import get_session
from app.models import Cluster, ClusterSnapshot, LicenseUsage, User
from app.dependencies import admin_required

router = APIRouter(
    prefix="/api/analytics",
    tags=["analytics"],
)

@router.get("/trends")
def get_resource_trends(
    environment: Optional[str] = Query(None),
    datacenter: Optional[str] = Query(None),
    days: int = Query(30),
    session: Session = Depends(get_session),
    user: User = Depends(admin_required)
):
    """
    Returns aggregated time-series data for global analytics.
    Buckets data by unified poll timestamps from ClusterSnapshot.
    """
    # 1. Base Query for Clusters (apply filters if any)
    cluster_query = select(Cluster.id)
    if environment:
        cluster_query = cluster_query.where(Cluster.environment == environment)
    if datacenter:
        cluster_query = cluster_query.where(Cluster.datacenter == datacenter)
    
    filtered_cluster_ids = session.exec(cluster_query).all()
    if not filtered_cluster_ids:
        return []

    # 2. Get Snapshots for these clusters in the last X days
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    # We group by the raw timestamp (unified by poller)
    # Note: SQLite stores datetime as strings, but SQLModel handles conversions.
    # Grouping by ClusterSnapshot.timestamp allows us to see "runs" across clusters.
    
    statement = select(
        ClusterSnapshot.timestamp,
        func.sum(ClusterSnapshot.node_count).label("nodes"),
        func.sum(ClusterSnapshot.vcpu_count).label("vcpus"),
        func.sum(ClusterSnapshot.project_count).label("projects"),
        func.sum(ClusterSnapshot.machineset_count).label("machinesets"),
        func.sum(ClusterSnapshot.machine_count).label("machines"),
        func.sum(ClusterSnapshot.license_count).label("licenses"),
        func.sum(ClusterSnapshot.licensed_node_count).label("licensed_nodes"),
        func.count(ClusterSnapshot.id).label("cluster_count")
    ).where(
        ClusterSnapshot.cluster_id.in_(filtered_cluster_ids),
        ClusterSnapshot.timestamp >= cutoff,
        ClusterSnapshot.status == "Success"
    ).group_by(
        ClusterSnapshot.timestamp
    ).order_by(
        ClusterSnapshot.timestamp.asc()
    )
    
    results = session.exec(statement).all()
    
    # 3. Format results
    trends = []
    for row in results:
        trends.append({
            "timestamp": row.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "nodes": row.nodes,
            "vcpus": int(row.vcpus),
            "projects": row.projects,
            "machinesets": row.machinesets,
            "machines": row.machines,
            "clusters": row.cluster_count,
            "licenses": row.licenses,
            "licensed_nodes": row.licensed_nodes
        })

    return trends
