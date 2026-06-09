from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from typing import List, Dict, Any, Optional
import json
from datetime import datetime

from app.database import get_session
from app.models import Cluster, ClusterSnapshot

router = APIRouter(
    prefix="/api/operators",
    tags=["operators"],
)

@router.get("/matrix")
def get_operator_matrix(snapshot_time: Optional[str] = None, session: Session = Depends(get_session)):
    """
    Returns a matrix of installed operators across all clusters.
    Data is sourced from the latest successful snapshot (or specific snapshot_time) for each cluster.
    """
    from sqlalchemy import func, text
    
    clusters = session.exec(select(Cluster)).all()
    
    matrix_data = {
        "clusters": [],
        "operators": {}
    }

    target_ts = None
    if snapshot_time:
        try:
            # Handle potential 'Z' suffix or T separator
            clean_ts = snapshot_time.replace("T", " ").replace("Z", "")
            # Truncate potential fractional seconds if present in input string
            if "." in clean_ts:
                clean_ts = clean_ts.split(".")[0]
            target_ts = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
        except:
            print(f"Failed to parse snapshot time: {snapshot_time}")
            pass
    
    latest_ts = None
    for cluster in clusters:
        # Optimize: Fetch ONLY the needed fields using json_extract
        # We need: timestamp, csvs, subscriptions, __errors
        # Note: json_extract returns the JSON string for objects/arrays in SQLite
        
        query = select(
            ClusterSnapshot.timestamp,
            func.json_extract(ClusterSnapshot.data_json, '$.csvs').label("csvs"),
            func.json_extract(ClusterSnapshot.data_json, '$.subscriptions').label("subscriptions"),
            func.json_extract(ClusterSnapshot.data_json, '$.__errors').label("errors")
        ).where(ClusterSnapshot.cluster_id == cluster.id)
        
        if target_ts:
            # Match logic from dashboard.py: 
            # Allow up to 10 minutes (600s) delay (grace period) and pick the latest one in that window
            from datetime import timedelta
            grace_target = target_ts + timedelta(seconds=600)
            query = query.where(ClusterSnapshot.timestamp <= grace_target)
            query = query.where(ClusterSnapshot.status == "Success")
            query = query.order_by(ClusterSnapshot.timestamp.desc())
        else:
            query = query.where(ClusterSnapshot.status == "Success").order_by(ClusterSnapshot.timestamp.desc())
        
        # Execute optimized query
        # This avoids loading the full 50MB+ data_json into Python memory
        result = session.exec(query.limit(1)).first()
        
        # Result is a tuple: (timestamp, csvs_json, subscriptions_json, errors_json) or None
        
        if result and (not latest_ts or result[0] > latest_ts):
            latest_ts = result[0]
            
        cluster_info = {
            "id": cluster.id,
            "name": cluster.name,
            "environment": cluster.environment,
            "datacenter": cluster.datacenter,
            "has_data": False,
            "data_collected": False
        }
        
        if result:
            cluster_info["has_data"] = True
            try:
                # Parse the extracted JSON fragments
                # SQLite json_extract returns the value. 
                # If it didn't find the key, it returns None.
                
                raw_csvs = result[1]
                raw_subs = result[2]
                raw_errors = result[3]

                csvs = json.loads(raw_csvs) if raw_csvs else []
                subs = json.loads(raw_subs) if raw_subs else []
                errors = json.loads(raw_errors) if raw_errors else {}
                
                # Check for Data Collection Status
                # If both are None (not just empty lists, but null in DB extract), data might be missing structure
                # But json_extract returns NULL if key missing.
                # Logic: If key was missing in original JSON, result is None.
                if raw_csvs is None and raw_subs is None:
                    cluster_info["data_collected"] = False
                else:
                    cluster_info["data_collected"] = True

                # Check if we have specific errors for OLM resources
                if errors.get("subscriptions") == "Forbidden" or errors.get("csvs") == "Forbidden":
                    cluster_info["auth_error"] = True
                else:
                    cluster_info["auth_error"] = False

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
                    
                    managed_crds = []
                    if installed_csv_name and installed_csv_name in csv_map:
                        csv_obj = csv_map[installed_csv_name]
                        version = csv_obj.get("spec", {}).get("version", "Unknown")
                        display_name = csv_obj.get("spec", {}).get("displayName", pkg_name)
                        provider = csv_obj.get("spec", {}).get("provider", {}).get("name", "Unknown") if isinstance(csv_obj.get("spec", {}).get("provider"), dict) else csv_obj.get("spec", {}).get("provider", "Unknown")
                        phase = csv_obj.get("status", {}).get("phase", "Unknown")
                        
                        # Extract owned CRDs
                        owned = csv_obj.get("spec", {}).get("customresourcedefinitions", {}).get("owned", [])
                        managed_crds = [{"name": o.get("name"), "kind": o.get("kind"), "displayName": o.get("displayName")} for o in owned]
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
                        "subscription_name": meta.get("name"),
                        "namespace": meta.get("namespace"),
                        "approval": spec.get("installPlanApproval", "Automatic"),
                        "source": spec.get("source"),
                        "managed_crds": managed_crds
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
        "operators": op_list,
        "snapshot_time": (latest_ts.isoformat() + "Z") if latest_ts else None
    }
