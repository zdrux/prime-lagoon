from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from typing import Any, List, Dict

from datetime import datetime, timedelta
import json
from app.database import get_session
from app.models import Cluster, LicenseUsage, AppConfig, LicenseRule
from app.services.ocp import fetch_resources, get_cluster_stats, parse_cpu, get_detailed_stats, parse_memory_to_gb
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

@router.get("/{cluster_id}/details")
def get_cluster_details(cluster_id: int, session: Session = Depends(get_session)):
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    try:
        return get_detailed_stats(cluster)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{cluster_id}/ingress/{name}/details")
def get_ingress_details_endpoint(cluster_id: int, name: str, session: Session = Depends(get_session)):
    from app.services.ocp import get_ingress_details
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    try:
        return get_ingress_details(cluster, name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{cluster_id}/nodes/{node_name}/details")
def get_node_details_endpoint(cluster_id: int, node_name: str, session: Session = Depends(get_session)):
    from app.services.ocp import get_node_details
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    try:
        return get_node_details(cluster, node_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/summary")
def get_dashboard_summary(session: Session = Depends(get_session)):
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

    for cluster in clusters:
        # Fetch nodes once
        try:
             nodes = fetch_resources(cluster, "v1", "Node")
        except Exception as e:
             print(f"Error fetching nodes for {cluster.name}: {e}")
             nodes = []

        # Calculate Licenses
        lic_data = calculate_licenses(nodes, rules)
        
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
        session.commit()
        
        stats = get_cluster_stats(cluster, nodes=nodes)
        
        # Aggregate Global Stats
        global_stats["total_nodes"] += len(nodes)
        global_stats["total_licensed_nodes"] += lic_data["node_count"]
        global_stats["total_vcpu"] += stats["total_vcpu"]
        global_stats["total_licensed_vcpu"] += lic_data["total_vcpu"]
        global_stats["total_licenses"] += lic_data["total_licenses"]

        summary.append({
            "id": cluster.id,
            "name": cluster.name,
            "datacenter": cluster.datacenter,
            "environment": cluster.environment,
            "stats": stats,
            "licensed_node_count": lic_data["node_count"],
            "licensed_vcpu_count": int(lic_data["total_vcpu"]),
            "license_info": {
                "count": lic_data["total_licenses"],
                "usage_id": usage.id
            }
        })
    return {
        "clusters": summary,
        "global_stats": global_stats
    }

@router.get("/{cluster_id}/license-details/{usage_id}")
def get_license_details(cluster_id: int, usage_id: int, session: Session = Depends(get_session)):
     usage = session.get(LicenseUsage, usage_id)
     if not usage or usage.cluster_id != cluster_id:
          raise HTTPException(status_code=404, detail="License usage not found")
     
     return {
         "id": usage.id,
         "timestamp": usage.timestamp,
         "node_count": usage.node_count,
         "total_vcpu": usage.total_vcpu,
         "license_count": usage.license_count,
         "details": json.loads(usage.details_json or "[]")
     }

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
                 
        # Special handling for Nodes to include metrics
        if resource_type == "nodes":
             metrics_data = {}
             try:
                 from app.services.ocp import get_dynamic_client
                 dyn = get_dynamic_client(cluster)
                 metrics_api = dyn.resources.get(api_version='metrics.k8s.io/v1beta1', kind='NodeMetrics')
                 metrics_items = metrics_api.get().items
                 metrics_data = {m.metadata.name: m for m in metrics_items}
             except:
                 pass
             
             enriched_items = []
             for item in items:
                 d = item.to_dict()
                 name = d["metadata"]["name"]
                 m = metrics_data.get(name)
                 
                 cpu_usage = 0.0
                 mem_usage_gb = 0.0
                 if m:
                     cpu_usage = parse_cpu(m.usage.cpu)
                     mem_usage_gb = parse_memory_to_gb(m.usage.memory)
                 
                 capacity = d.get("status", {}).get("capacity", {})
                 total_cpu = parse_cpu(capacity.get("cpu"))
                 total_mem_gb = parse_memory_to_gb(capacity.get("memory"))
                 
                 d["__metrics"] = {
                     "cpu_usage": cpu_usage,
                     "mem_usage_gb": round(mem_usage_gb, 2),
                     "cpu_percent": round((cpu_usage / total_cpu * 100) if total_cpu > 0 else 0, 1),
                     "mem_percent": round((mem_usage_gb / total_mem_gb * 100) if total_mem_gb > 0 else 0, 1)
                 }
                 enriched_items.append(d)
             return enriched_items

        # Serialize items to simple dicts for JSON response
        return [item.to_dict() for item in items]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
