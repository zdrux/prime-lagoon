import json
import logging
from datetime import datetime
from sqlmodel import Session, select
from app.database import engine
from app.models import Cluster, ClusterSnapshot, LicenseUsage, LicenseRule
from app.services.ocp import fetch_resources, parse_cpu, get_val
from app.services.license import calculate_licenses

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
}

def poll_all_clusters(progress_callback=None):
    """Main entry point for the scheduler."""
    logger.info("Starting background poll of all clusters...")
    run_timestamp = datetime.utcnow() # Unified timestamp for the entire run
    
    with Session(engine) as session:
        clusters = session.exec(select(Cluster)).all()
        rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True)).all()
    
    total = len(clusters)
    for i, cluster in enumerate(clusters):
        try:
            if progress_callback:
                progress_callback({"type": "cluster_start", "cluster": cluster.name, "index": i + 1, "total": total})
            poll_cluster(cluster.id, rules, progress_callback, run_timestamp)
            if progress_callback:
                progress_callback({"type": "cluster_end", "cluster": cluster.name})
        except Exception as e:
            logger.error(f"Failed to poll cluster {cluster.name}: {e}")
            if progress_callback:
                progress_callback({"type": "error", "cluster": cluster.name, "message": str(e)})

def poll_cluster(cluster_id: int, rules: list, progress_callback=None, run_timestamp=None):
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
        for i, key in enumerate(res_keys):
            meta = POLL_RESOURCES[key]
            try:
                if progress_callback:
                    progress_callback({
                        "type": "resource_start", 
                        "cluster": cluster.name, 
                        "resource": key,
                        "resource_index": i + 1,
                        "resource_total": len(res_keys)
                    })
                
                items = fetch_resources(cluster, meta["api_version"], meta["kind"])
                # Convert K8s objects to pure dicts for JSON serialization
                # Use .to_dict() if available for recursive serialization, otherwise use dict()
                snapshot_data[key] = [item.to_dict() if hasattr(item, 'to_dict') else dict(item) for item in items]
            except Exception as e:
                logger.error(f"Error fetching {key} for {cluster.name}: {e}")
                snapshot_data[key] = []
                status = "Partial"

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
        lic_data = calculate_licenses(nodes, rules)
        
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

        # 4. Create ClusterSnapshot
        snapshot = ClusterSnapshot(
            cluster_id=cluster.id,
            timestamp=run_timestamp,
            status=status,
            captured_name=cluster.name,          # Freeze name
            captured_unique_id=cluster.unique_id, # Freeze unique ID
            node_count=total_node_count,
            vcpu_count=total_vcpu_count,
            data_json=json.dumps(snapshot_data, default=str) # default=str handles datetime objects in k8s responses
        )
        session.add(snapshot)
        
        session.commit()
        logger.info(f"Snapshot saved for {cluster.name}")
