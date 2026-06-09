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

def _minor_version(version: Optional[str]) -> Optional[tuple]:
    if not version:
        return None
    parts = str(version).lstrip("v").split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None

def _extract_cluster_version(clusterversions: List[Dict[str, Any]]) -> str:
    version_obj = next((cv for cv in clusterversions if cv.get("metadata", {}).get("name") == "version"), None)
    if not version_obj:
        return "-"

    history = version_obj.get("status", {}).get("history") or []
    for item in history:
        if item.get("state") == "Completed" and item.get("version"):
            return item["version"]

    return version_obj.get("status", {}).get("desired", {}).get("version") or "-"

def _parse_olm_properties(raw_value: Optional[str]) -> List[Dict[str, Any]]:
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []

def _extract_csv_compatibility(csv_obj: Dict[str, Any], cluster_version: str) -> Dict[str, Any]:
    annotations = csv_obj.get("metadata", {}).get("annotations", {}) or {}
    properties = _parse_olm_properties(annotations.get("olm.properties"))

    max_ocp = None
    min_ocp = None
    for prop in properties:
        prop_type = prop.get("type")
        if prop_type == "olm.maxOpenShiftVersion":
            max_ocp = str(prop.get("value") or "").lstrip("v") or None
        elif prop_type == "olm.minOpenShiftVersion":
            min_ocp = str(prop.get("value") or "").lstrip("v") or None

    distribution_range = annotations.get("com.redhat.openshift.versions") or annotations.get("operators.openshift.io/valid-subscription")
    status = "unknown"
    reason = "No OpenShift compatibility metadata found in CSV annotations."

    cluster_minor = _minor_version(cluster_version)
    max_minor = _minor_version(max_ocp)
    min_minor = _minor_version(min_ocp)

    if max_ocp:
        status = "compatible"
        reason = f"CSV declares max OpenShift {max_ocp}."
        if cluster_minor and max_minor and cluster_minor > max_minor:
            status = "blocked"
            reason = f"Cluster {cluster_version} is newer than CSV max OpenShift {max_ocp}."
    elif min_ocp:
        status = "compatible"
        reason = f"CSV declares min OpenShift {min_ocp}."

    if status == "compatible" and min_minor and cluster_minor and cluster_minor < min_minor:
        status = "blocked"
        reason = f"Cluster {cluster_version} is older than CSV min OpenShift {min_ocp}."

    return {
        "status": status,
        "reason": reason,
        "max_openshift_version": max_ocp or "-",
        "min_openshift_version": min_ocp or "-",
        "openshift_versions": distribution_range or "-",
        "properties_found": bool(properties),
    }

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
            func.json_extract(ClusterSnapshot.data_json, '$.clusterversions').label("clusterversions"),
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
                raw_cluster_versions = result[3]
                raw_errors = result[4]

                csvs = json.loads(raw_csvs) if raw_csvs else []
                subs = json.loads(raw_subs) if raw_subs else []
                clusterversions = json.loads(raw_cluster_versions) if raw_cluster_versions else []
                errors = json.loads(raw_errors) if raw_errors else {}
                cluster_version = _extract_cluster_version(clusterversions)
                cluster_info["version"] = cluster_version
                
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
                        compatibility = _extract_csv_compatibility(csv_obj, cluster_version)
                        
                        # Extract owned CRDs
                        owned = csv_obj.get("spec", {}).get("customresourcedefinitions", {}).get("owned", [])
                        managed_crds = [{"name": o.get("name"), "kind": o.get("kind"), "displayName": o.get("displayName")} for o in owned]
                    else:
                        # Fallback if we have currentCSV but no CSV object (maybe pending install)
                        version = status.get("currentCSV", "Pending")
                        compatibility = {
                            "status": "unknown",
                            "reason": "Installed CSV details were not present in the snapshot.",
                            "max_openshift_version": "-",
                            "min_openshift_version": "-",
                            "openshift_versions": "-",
                            "properties_found": False,
                        }
                    
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
                        "managed_crds": managed_crds,
                        "openshift_compatibility": compatibility
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
