from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from typing import List

from app.database import get_session
from app.models import Cluster, ClusterCreate, ClusterRead, ClusterUpdate

router = APIRouter(
    prefix="/api/admin/clusters",
    tags=["admin"],
)

@router.post("/", response_model=ClusterRead)
def create_cluster(cluster: ClusterCreate, session: Session = Depends(get_session)):
    db_cluster = Cluster.model_validate(cluster)
    session.add(db_cluster)
    session.commit()
    session.refresh(db_cluster)
    return db_cluster

@router.get("/", response_model=List[ClusterRead])
def read_clusters(offset: int = 0, limit: int = 100, session: Session = Depends(get_session)):
    clusters = session.exec(select(Cluster).offset(offset).limit(limit)).all()
    return clusters

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

@router.patch("/{cluster_id}", response_model=ClusterRead)
def update_cluster(cluster_id: int, cluster: ClusterUpdate, session: Session = Depends(get_session)):
    db_cluster = session.get(Cluster, cluster_id)
    if not db_cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    cluster_data = cluster.model_dump(exclude_unset=True)
    for key, value in cluster_data.items():
        setattr(db_cluster, key, value)
        
    session.add(db_cluster)
    session.commit()
    session.refresh(db_cluster)
    return db_cluster
