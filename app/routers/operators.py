from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from typing import List, Dict, Any
import json
from datetime import datetime

from app.database import get_session
from app.models import Cluster, ClusterSnapshot

router = APIRouter(
    prefix="/api/operators",
    tags=["operators"],
)

@router.get("/matrix")
def get_operator_matrix(session: Session = Depends(get_session)):
    """
    Returns a matrix of installed operators across all clusters.
    Data is sourced from the latest successful snapshot for each cluster.
    """
    clusters = session.exec(select(Cluster)).all()
    
    # Structure:
    # {
    #   "clusters": [{id, name, ...}],
    #   "operators": {
    #       "OpName": {
    #           "displayName": "...",
    #           "provider": "...",
    #           "installations": {
    #               "ClusterName": { "version": "...", "status": "...", "channel": "..." }
    #           }
    #       }
    #   }
    # }
    
    matrix_data = {
        "clusters": [],
        "operators": {}
    }
    
    for cluster in clusters:
        # Get latest snapshot
        snap = session.exec(select(ClusterSnapshot).where(
            ClusterSnapshot.cluster_id == cluster.id,
            ClusterSnapshot.status == "Success"
        ).order_by(ClusterSnapshot.timestamp.desc()).limit(1)).first()
        
        cluster_info = {
            "id": cluster.id,
            "name": cluster.name,
            "has_data": False
        }
        
        if snap and snap.data_json:
            cluster_info["has_data"] = True
            try:
                data = json.loads(snap.data_json)
                subs = data.get("subscriptions", [])
                csvs = data.get("csvs", [])
                
                # Create a lookup for CSVs by name (metadata.name)
                # csvs is a list of minified objects from poller
                csv_map = {c["metadata"]["name"]: c for c in csvs if "metadata" in c and "name" in c["metadata"]}
                
                for sub in subs:
                    # Subscription details
                    # api_version: operators.coreos.com/v1alpha1
                    # kind: Subscription
                    meta = sub.get("metadata", {})
                    spec = sub.get("spec", {})
                    status = sub.get("status", {})
                    
                    pkg_name = spec.get("name") # e.g. "advanced-cluster-management"
                    if not pkg_name: 
                        continue
                        
                    channel = spec.get("channel", "unknown")
                    installed_csv_name = status.get("installedCSV")
                    
                    # Find installed CSV details
                    version = "Unknown"
                    display_name = pkg_name
                    provider = "Unknown"
                    phase = "Unknown"
                    
                    if installed_csv_name and installed_csv_name in csv_map:
                        csv_obj = csv_map[installed_csv_name]
                        version = csv_obj.get("spec", {}).get("version", "Unknown")
                        display_name = csv_obj.get("spec", {}).get("displayName", pkg_name)
                        provider = csv_obj.get("spec", {}).get("provider", {}).get("name", "Unknown") if isinstance(csv_obj.get("spec", {}).get("provider"), dict) else csv_obj.get("spec", {}).get("provider", "Unknown")
                        phase = csv_obj.get("status", {}).get("phase", "Unknown")
                    else:
                        # Fallback if we have currentCSV but no CSV object (maybe pending install)
                        version = status.get("currentCSV", "Pending")
                    
                    # Add to Matrix
                    if pkg_name not in matrix_data["operators"]:
                        matrix_data["operators"][pkg_name] = {
                            "name": pkg_name,
                            "displayName": display_name,
                            "provider": provider,
                            "installations": {}
                        }
                    
                    # We might have duplicates if multiple subscriptions for same package (namespaces?)
                    # For now, overwrite or simple combine? Overwrite is safest for fleet view.
                    matrix_data["operators"][pkg_name]["installations"][cluster.name] = {
                        "version": version,
                        "channel": channel,
                        "status": phase,
                        "subscription_name": meta.get("name")
                    }
                    
                    # Update display name if it was just the package name before
                    if display_name != pkg_name and matrix_data["operators"][pkg_name]["displayName"] == pkg_name:
                         matrix_data["operators"][pkg_name]["displayName"] = display_name

            except Exception as e:
                print(f"Error processing snapshot for operators matrix {cluster.name}: {e}")
        
        matrix_data["clusters"].append(cluster_info)

    # Sort Clusters by Name
    matrix_data["clusters"].sort(key=lambda x: x["name"])
    
    # Sort Operators by Display Name and generic list
    # Convert dict to list for frontend
    op_list = []
    for k, v in matrix_data["operators"].items():
        op_list.append(v)
    
    op_list.sort(key=lambda x: x["displayName"])
    
    return {
        "clusters": matrix_data["clusters"],
        "operators": op_list
    }
