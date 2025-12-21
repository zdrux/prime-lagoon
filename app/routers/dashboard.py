from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from typing import Any, List, Dict

from app.database import get_session
from app.models import Cluster
from app.services.ocp import fetch_resources, get_cluster_stats

def parse_memory_to_gb(mem_str: str) -> float:
    if not mem_str:
         return 0.0
    
    # Handle Ki, Mi, Gi, Ti, or bytes
    unit_multipliers = {
        'Ki': 1024,
        'Mi': 1024**2,
        'Gi': 1024**3,
        'Ti': 1024**4,
        'm': 1e-3, # Unusual for memory but possible in generic parser
        'k': 1000,
        'M': 1000**2,
        'G': 1000**3,
        'T': 1000**4
    }
    
    try:
        # Check standard units
        for unit, mult in unit_multipliers.items():
            if mem_str.endswith(unit):
                return float(mem_str[:-len(unit)]) * mult / (1024**3)
        
        # Plain number assumed bytes
        return float(mem_str) / (1024**3)
    except:
        return 0.0

def parse_cpu(cpu_str: str) -> float:
    if not cpu_str:
        return 0.0
    if cpu_str.endswith('m'):
        return float(cpu_str[:-1]) / 1000.0
    return float(cpu_str)

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
}

@router.get("/summary")
def get_dashboard_summary(session: Session = Depends(get_session)):
    clusters = session.exec(select(Cluster)).all()
    summary = []
    # In a real app, this should be async or background task
    for cluster in clusters:
        stats = get_cluster_stats(cluster)
        summary.append({
            "id": cluster.id,
            "name": cluster.name,
            "datacenter": cluster.datacenter,
            "environment": cluster.environment,
            "stats": stats
        })
    return summary

@router.get("/{cluster_id}/resources/{resource_type}")
def get_cluster_resources(cluster_id: int, resource_type: str, session: Session = Depends(get_session)):
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    resource_type = resource_type.lower()
    if resource_type not in RESOURCE_MAP:
        raise HTTPException(status_code=400, detail=f"Resource type '{resource_type}' not supported. Valid types: {list(RESOURCE_MAP.keys())}")
    
    mapping = RESOURCE_MAP[resource_type]
    
    try:
        items = fetch_resources(cluster, mapping["api_version"], mapping["kind"])
        
        # Special handling for Machines to enrich with CPU/Memory from Nodes
        if resource_type == "machines":
             try:
                 nodes = fetch_resources(cluster, "v1", "Node")
                 node_map = {n.metadata.name: n for n in nodes}
                 
                 enriched_items = []
                 for item in items:
                     # Convert to dict first
                     d = item.to_dict()
                     
                     # Find node ref
                     node_name = d.get("status", {}).get("nodeRef", {}).get("name")
                     if node_name and node_name in node_map:
                         node = node_map[node_name]
                         # Get Capacity
                         capacity = node.status.capacity
                         cpu = parse_cpu(capacity.get("cpu"))
                         mem_gb = parse_memory_to_gb(capacity.get("memory"))
                         
                         d["__enriched"] = {
                             "cpu": cpu,
                             "memory_gb": round(mem_gb, 2)
                         }
                     else:
                         d["__enriched"] = {
                             "cpu": "-",
                             "memory_gb": "-"
                         }
                     enriched_items.append(d)
                 return enriched_items
             except Exception as e:
                 print(f"Error enriching machines: {e}")
                 # Fallback to returning items without enrichment
                 return [item.to_dict() for item in items]
                 
        # Serialize items to simple dicts for JSON response
        return [item.to_dict() for item in items]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
