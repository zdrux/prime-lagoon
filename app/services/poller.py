import json
import logging
from datetime import datetime
from sqlmodel import Session, select
from app.database import engine
from app.models import Cluster, ClusterSnapshot, LicenseUsage, LicenseRule, MapidLicenseUsage
from app.services.ocp import fetch_resources, parse_cpu, get_val, get_service_mesh_details, get_argocd_details
from app.services.license import calculate_licenses, calculate_mapid_usage

logger = logging.getLogger(__name__)

# Reusing the resource map from dashboard logic
POLL_RESOURCES = {
    "nodes": {"api_version": "v1", "kind": "Node"},
    "machines": {"api_version": "machine.openshift.io/v1beta1", "kind": "Machine"},
    "machinesets": {"api_version": "machine.openshift.io/v1beta1", "kind": "MachineSet"},
    "projects": {"api_version": "project.openshift.io/v1", "kind": "Project"},
    "machineautoscalers": {"api_version": "autoscaling.openshift.io/v1beta1", "kind": "MachineAutoscaler"},
    "clusteroperators": {"api_version": "config.openshift.io/v1", "kind": "ClusterOperator"},
    "infrastructures": {"api_version": "config.openshift.io/v1", "kind": "Infrastructure"},
    "clusterversions": {"api_version": "config.openshift.io/v1", "kind": "ClusterVersion"},
    # OLM Resources are optional, defined in config
}

def poll_all_clusters(progress_callback=None):
    """Main entry point for the scheduler."""
    logger.info("Starting background poll of all clusters...")
    run_timestamp = datetime.utcnow() # Unified timestamp for the entire run
    
    with Session(engine) as session:
        from app.models import AppConfig
        clusters = session.exec(select(Cluster)).all()
        rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True).order_by(LicenseRule.order, LicenseRule.id)).all()
        default_include = (session.get(AppConfig, "LICENSE_DEFAULT_INCLUDE") or AppConfig(value="False")).value.lower() == "true"
        
        # Poll Config
        collect_olm = (session.get(AppConfig, "SNAPSHOT_COLLECT_OLM") or AppConfig(value="True")).value.lower() == "true"
        run_compliance = (session.get(AppConfig, "SNAPSHOT_COLLECT_COMPLIANCE") or AppConfig(value="False")).value.lower() == "true"
        
        # Load Audit Rules if needed
        audit_rules = []
        audit_bundles = []
        if run_compliance:
            from app.models import AuditRule, AuditBundle
            audit_rules = session.exec(select(AuditRule)).all()
            audit_bundles = session.exec(select(AuditBundle)).all()
    
    total = len(clusters)
    for i, cluster in enumerate(clusters):
        try:
            if progress_callback:
                progress_callback({"type": "cluster_start", "cluster": cluster.name, "index": i + 1, "total": total})
            poll_cluster(
                cluster.id, rules, progress_callback, run_timestamp, 
                default_include=default_include,
                collect_olm=collect_olm,
                run_compliance=run_compliance,
                audit_rules=audit_rules,
                audit_bundles=audit_bundles
            )
            if progress_callback:
                progress_callback({"type": "cluster_end", "cluster": cluster.name})
        except Exception as e:
            logger.error(f"Failed to poll cluster {cluster.name}: {e}")
            if progress_callback:
                progress_callback({"type": "error", "cluster": cluster.name, "message": str(e)})

    # 4. Cleanup old snapshots
    try:
        with Session(engine) as session:
            cleanup_old_snapshots(session)
    except Exception as e:
        logger.error(f"Failed to cleanup old snapshots: {e}")

def cleanup_old_snapshots(session: Session):
    """Deletes snapshots older than the configured retention period."""
    from app.models import AppConfig
    from datetime import timedelta
    
    config = session.get(AppConfig, "SNAPSHOT_RETENTION_DAYS")
    days = int(config.value) if config else 30
    
    logger.info(f"Running automated cleanup (Retention: {days} days)...")
    cutoff = datetime.utcnow() - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    
    from sqlalchemy import text
    from app.models import LicenseUsage, ComplianceScore, MapidLicenseUsage

    # 1. Delete associated data
    session.execute(text("DELETE FROM licenseusage WHERE timestamp < :cutoff"), {"cutoff": cutoff_str})
    session.execute(text("DELETE FROM mapidlicenseusage WHERE timestamp < :cutoff"), {"cutoff": cutoff_str})
    session.execute(text("DELETE FROM compliancescore WHERE timestamp < :cutoff"), {"cutoff": cutoff_str})

    # 2. Snapshots
    statement = select(ClusterSnapshot).where(ClusterSnapshot.timestamp < cutoff)
    old_snapshots = session.exec(statement).all()
    
    count = 0
    for snap in old_snapshots:
        session.delete(snap)
        count += 1
    
    if count > 0:
        session.commit()
        logger.info(f"Automated cleanup deleted {count} old snapshots.")
    else:
        logger.info("No old snapshots to cleanup.")

def poll_cluster(
    cluster_id: int, 
    rules: list, 
    progress_callback=None, 
    run_timestamp=None, 
    default_include=False,
    collect_olm=True,
    run_compliance=False,
    audit_rules=None,
    audit_bundles=None
):
    """Fetches all resources for a cluster, saves snapshot, and updates license usage."""
    if run_timestamp is None:
        run_timestamp = datetime.utcnow()

    with Session(engine) as session:
        cluster = session.get(Cluster, cluster_id)
        if not cluster:
            return

        logger.info(f"Polling cluster: {cluster.name}")
        snapshot_data = {}
        status = "Success"
        
        # 1. Fetch all resources
        res_keys = list(POLL_RESOURCES.keys())
        
        # Add Optional Resources
        if collect_olm:
            res_keys.append("subscriptions")
            res_keys.append("csvs")

        for i, key in enumerate(res_keys):
            if key in POLL_RESOURCES:
                meta = POLL_RESOURCES[key]
            elif key == "subscriptions":
                meta = {"api_version": "operators.coreos.com/v1alpha1", "kind": "Subscription"}
            elif key == "csvs":
                meta = {"api_version": "operators.coreos.com/v1alpha1", "kind": "ClusterServiceVersion"}
            
            try:
                if progress_callback:
                    progress_callback({
                        "type": "resource_start", 
                        "cluster": cluster.name, 
                        "resource": key,
                        "resource_index": i + 1,
                        "resource_total": len(res_keys)
                    })
                
                timeout = 600 if key in ["csvs", "subscriptions"] else 120
                
                # Fetch CSVs as Table to reduced payload size
                use_table = (key == "csvs")
                items = fetch_resources(cluster, meta["api_version"], meta["kind"], timeout=timeout, use_table=use_table)
                
                if use_table and key == "csvs":
                     # Process Table Response for CSVs
                     minified_csvs = []
                     
                     # Table structure: { "kind": "Table", "columnDefinitions": [...], "rows": [...] }
                     # Fallback if somehow we got a list (e.g. mock or error in fetcher logic fallback)
                     if isinstance(items, dict) and items.get("kind") == "Table":
                         rows = items.get("rows", [])
                         cols = items.get("columnDefinitions", [])
                         
                         # Map column names to indices for robust parsing
                         # Typically: Name, Display, Version, Replaces, Phase
                         col_idx = {c["name"].lower(): i for i, c in enumerate(cols)}
                         
                         for row in rows:
                             # row["object"] contains PartialObjectMetadata (name, namespace, etc)
                             metadata = row.get("object", {}).get("metadata", {})
                             w_cells = row.get("cells", [])
                             
                             # Extract cells safely
                             display_name = w_cells[col_idx["display"]] if "display" in col_idx and col_idx["display"] < len(w_cells) else ""
                             version = w_cells[col_idx["version"]] if "version" in col_idx and col_idx["version"] < len(w_cells) else ""
                             phase = w_cells[col_idx["phase"]] if "phase" in col_idx and col_idx["phase"] < len(w_cells) else ""
                             
                             minified_csvs.append({
                                 "metadata": {
                                     "name": metadata.get("name"),
                                     "namespace": metadata.get("namespace"),
                                     "creationTimestamp": metadata.get("creationTimestamp")
                                 },
                                 "spec": {
                                     "version": version,
                                     "displayName": display_name,
                                     "provider": "Unknown", # Not usually in Table
                                     "customresourcedefinitions": {
                                         "owned": [] # Not in Table
                                     }
                                 },
                                 "status": {
                                     "phase": phase
                                 }
                             })
                         snapshot_data[key] = minified_csvs
                     else:
                         # Fallback if fetch_resources returned generic items list (e.g. mock override or server ignored Accept header)
                         if isinstance(items, dict) and "items" in items:
                             # It's a Kubernetes List object as a dict
                             items = items.get("items", [])
                         
                         resource_list = [item.to_dict() if hasattr(item, 'to_dict') else dict(item) for item in items]
                         minified_csvs = []
                         for csv in resource_list:
                             minified_csvs.append({
                                 "metadata": {
                                     "name": csv.get("metadata", {}).get("name"),
                                     "namespace": csv.get("metadata", {}).get("namespace"),
                                     "creationTimestamp": csv.get("metadata", {}).get("creationTimestamp")
                                 },
                                 "spec": {
                                     "version": csv.get("spec", {}).get("version"),
                                     "displayName": csv.get("spec", {}).get("displayName"),
                                     "provider": csv.get("spec", {}).get("provider"),
                                     "customresourcedefinitions": {
                                         "owned": [
                                             {
                                                 "name": o.get("name"), 
                                                 "kind": o.get("kind"), 
                                                 "displayName": o.get("displayName")
                                             } 
                                             for o in csv.get("spec", {}).get("customresourcedefinitions", {}).get("owned", [])
                                         ]
                                     }
                                 },
                                 "status": {
                                     "phase": csv.get("status", {}).get("phase"),
                                     "reason": csv.get("status", {}).get("reason")
                                 }
                             })
                         snapshot_data[key] = minified_csvs
                else:
                    # Standard List Handling
                    resource_list = [item.to_dict() if hasattr(item, 'to_dict') else dict(item) for item in items]
                    snapshot_data[key] = resource_list

            except Exception as e:
                # Check for Forbidden (403)
                is_forbidden = False
                error_str = str(e)
                if "403" in error_str or "Forbidden" in error_str:
                    is_forbidden = True
                
                if is_forbidden:
                    logger.warning(f"Permission denied fetching {key} for {cluster.name}")
                    if "__errors" not in snapshot_data:
                        snapshot_data["__errors"] = {}
                    snapshot_data["__errors"][key] = "Forbidden"
                elif "ReadTimeoutError" in error_str or "Timeout" in error_str or "timed out" in error_str:
                    logger.warning(f"Timeout fetching {key} for {cluster.name}")
                    if "__errors" not in snapshot_data:
                        snapshot_data["__errors"] = {}
                    snapshot_data["__errors"][key] = "Timeout"
                else:
                    logger.error(f"Error fetching {key} for {cluster.name}: {e}")
                    if "__errors" not in snapshot_data:
                        snapshot_data["__errors"] = {}
                    snapshot_data["__errors"][key] = str(e)
                
                snapshot_data[key] = []
                # Partial status is still appropriate
                status = "Partial"

        # 1.5 Fetch Service Mesh Details
        sm_data = {}
        try:
             sm_data = get_service_mesh_details(cluster)
        except Exception as e:
             logger.error(f"Error checking Service Mesh for {cluster.name}: {e}")

        # 1.6 Fetch ArgoCD Details
        argocd_data = {}
        try:
             argocd_data = get_argocd_details(cluster)
        except Exception as e:
             logger.error(f"Error checking ArgoCD for {cluster.name}: {e}")


        # 2. Calculate Stats from collected resources
        nodes = snapshot_data.get("nodes", [])
        total_node_count = len(nodes)
        total_vcpu_count = 0.0
        for node in nodes:
            try:
                raw_cpu = get_val(node, 'status.capacity.cpu')
                total_vcpu_count += parse_cpu(raw_cpu)
            except:
                pass

        # 3. Calculate License Usage (Logic consolidated here)
        # We use the fetched nodes from the snapshot data
        lic_data = calculate_licenses(nodes, rules, default_include=default_include)
        
        # Save License Usage Record
        usage = LicenseUsage(
            cluster_id=cluster.id,
            timestamp=run_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            node_count=lic_data["node_count"],
            total_vcpu=lic_data["total_vcpu"],
            license_count=lic_data["total_licenses"],
            details_json=json.dumps(lic_data["details"])
        )
        session.add(usage)

        # 3b. Calculate and Save MAPID Usage
        mapid_data_list = calculate_mapid_usage(nodes, rules, default_include=default_include)
        for m_data in mapid_data_list:
            m_usage = MapidLicenseUsage(
                cluster_id=cluster.id,
                timestamp=run_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                mapid=m_data["mapid"],
                lob=m_data["lob"],
                node_count=m_data["node_count"],
                total_vcpu=m_data["total_vcpu"],
                license_count=m_data["license_count"]
            )
            session.add(m_usage)

        # 4. Create ClusterSnapshot
        snapshot = ClusterSnapshot(
            cluster_id=cluster.id,
            timestamp=run_timestamp,
            status=status,
            captured_name=cluster.name,          # Freeze name
            captured_unique_id=cluster.unique_id, # Freeze unique ID
            node_count=total_node_count,
            vcpu_count=total_vcpu_count,
            project_count=len(snapshot_data.get("projects", [])),
            machineset_count=len(snapshot_data.get("machinesets", [])),
            machine_count=len(snapshot_data.get("machines", [])),
            license_count=lic_data["total_licenses"],
            licensed_node_count=lic_data["node_count"],
            service_mesh_json=json.dumps(sm_data, default=str),
            argocd_json=json.dumps(argocd_data, default=str),
            data_json=json.dumps(snapshot_data, default=str) # default=str handles datetime objects in k8s responses
        )
        session.add(snapshot)
        
        session.commit()
        logger.info(f"Snapshot saved for {cluster.name}")

        # 5. Run Compliance checks (if enabled)
        if run_compliance and audit_rules:
            if progress_callback:
                progress_callback({
                    "type": "resource_start", 
                    "cluster": cluster.name, 
                    "resource": "Compliance Audit",
                    "resource_index": len(res_keys) + 1,
                    "resource_total": len(res_keys) + 1
                })
            from app.services.compliance import evaluate_cluster_compliance
            try:
                evaluate_cluster_compliance(session, cluster, audit_rules, audit_bundles, run_timestamp=run_timestamp)
            except Exception as e:
                logger.error(f"Failed to run compliance for {cluster.name}: {e}")
