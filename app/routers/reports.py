import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from pydantic import BaseModel

from app.database import get_session
from app.models import Cluster, ClusterSnapshot, LicenseRule, AppConfig
from app.services.license import calculate_licenses
from app.services.ocp import parse_cpu, parse_memory_to_gb, get_val

router = APIRouter(prefix="/api/reports", tags=["reports"])

class ReportFilter(BaseModel):
    environments: List[str] = []
    datacenters: List[str] = []
    # If we add date range later, it goes here. For now, "Latest" is implied.

@router.post("/preview")
def preview_report_scope(filters: ReportFilter, session: Session = Depends(get_session)):
    """
    Returns a list of clusters that match the selected filters.
    """
    query = select(Cluster)
    clusters = session.exec(query).all()
    
    matched = []
    for c in clusters:
        # Filter Env
        if filters.environments:
            c_env = (c.environment or "").upper()
            if c_env not in filters.environments:
                continue
                
        # Filter DC
        if filters.datacenters:
            c_dc = (c.datacenter or "").upper()
            if c_dc not in filters.datacenters:
                continue
                
        matched.append({
            "id": c.id, 
            "name": c.name, 
            "environment": c.environment or "-", 
            "datacenter": c.datacenter or "-"
        })
        
    return matched

@router.post("/generate")
def generate_report_data(filters: ReportFilter, session: Session = Depends(get_session)):
    """
    Generates the full dataset for the report.
    Returns a list of flat dictionaries representing rows.
    Frontend will handle column selection and CSV/Excel generation.
    """
    # 1. Identify Clusters
    query = select(Cluster)
    clusters = session.exec(query).all()
    
    target_clusters = []
    for c in clusters:
        if filters.environments:
            if (c.environment or "").upper() not in filters.environments:
                continue
        if filters.datacenters:
            if (c.datacenter or "").upper() not in filters.datacenters:
                continue
        target_clusters.append(c)
        
    if not target_clusters:
        return []

    # 2. Prepare for License Calculation
    # We need rules and default logic
    rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True).order_by(LicenseRule.order, LicenseRule.id)).all()
    default_include = (session.get(AppConfig, "LICENSE_DEFAULT_INCLUDE") or AppConfig(value="False")).value.lower() == "true"
    
    report_rows = []
    
    # 3. Process Each Cluster
    for c in target_clusters:
        # Get latest success snapshot
        snap = session.exec(select(ClusterSnapshot).where(
            ClusterSnapshot.cluster_id == c.id,
            ClusterSnapshot.status == "Success"
        ).order_by(ClusterSnapshot.timestamp.desc()).limit(1)).first()
        
        if not snap or not snap.data_json:
            continue
            
        try:
            data = json.loads(snap.data_json)
            nodes = data.get("nodes", [])
            
            # Run license calc to get "Licenses Consumed" status for each node
            lic_res = calculate_licenses(nodes, rules, default_include)
            lic_details_map = {d["name"]: d for d in lic_res["details"]}
            
            for node in nodes:
                name = node.get("metadata", {}).get("name", "Unknown")
                labels = node.get("metadata", {}).get("labels", {})
                
                # Get usage/capacity from snapshot or parse raw
                # Logic reused from ocp.py, but simplified for reading snapshot
                capacity_info = node.get('__capacity', {})
                # Fallback if __capacity missing (older snapshots?)
                if not capacity_info:
                    raw_cpu = get_val(node, 'status.capacity.cpu')
                    raw_mem = get_val(node, 'status.capacity.memory')
                    capacity_info = {
                        "cpu": parse_cpu(raw_cpu),
                        "memory_gb": parse_memory_to_gb(raw_mem)
                    }
                
                # Get License Info
                lic_info = lic_details_map.get(name, {"licenses": 0, "status": "UNKNOWN"})
                
                # Filter: Include Licensed nodes only
                if lic_info["licenses"] > 0 or lic_info["status"].upper() == "LICENSED":
                    row = {
                        "Cluster Name": c.name,
                        "Environment": c.environment or "-",
                        "Datacenter": c.datacenter or "-",
                        "Node Name": name,
                        "Node vCPU": capacity_info.get("cpu", 0),
                        "Node Memory (GB)": round(capacity_info.get("memory_gb", 0), 1),
                        "Node MAPID": labels.get("mapid", "-"),
                        "LOB": labels.get("lob", "-"),
                        "Licenses Consumed": lic_info["licenses"],
                        "License Status": lic_info["status"]
                    }
                    report_rows.append(row)
                
        except Exception as e:
            print(f"Error generating report for cluster {c.name}: {e}")
            continue
            
    return report_rows
