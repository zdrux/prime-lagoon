import urllib3
from typing import Optional, List, Any
from kubernetes import client
from openshift.dynamic import DynamicClient
from app.models import Cluster

# Disable insecure request warnings for now as many internal OCP clusters use self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_dynamic_client(cluster: Cluster) -> DynamicClient:
    configuration = client.Configuration()
    configuration.host = cluster.api_url
    configuration.verify_ssl = False  # Allowing self-signed for internal clusters
    configuration.api_key = {"authorization": "Bearer " + cluster.token}
    
    # Create the ApiClient with the custom configuration
    api_client = client.ApiClient(configuration)
    
# Return the DynamicClient
    return DynamicClient(api_client)

def parse_memory_to_gb(mem_str: str) -> float:
    if not mem_str:
         return 0.0
    
    # Handle Ki, Mi, Gi, Ti, or bytes
    unit_multipliers = {
        'Ki': 1024,
        'Mi': 1024**2,
        'Gi': 1024**3,
        'Ti': 1024**4,
        'm': 1e-3, 
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

def parse_cpu(cpu_val: Any) -> float:
    """
    Robustly parses Kubernetes CPU quantity to float cores.
    Handles: "4", 4, "4000m", "500m", "0.5"
    """
    if cpu_val is None:
        return 0.0
        
    try:
        # If it's already a number, return float
        if isinstance(cpu_val, (int, float)):
             return float(cpu_val)
             
        # If string
        s = str(cpu_val).strip()
        if not s:
            return 0.0
            
        if s.endswith('m'):
            return float(s[:-1]) / 1000.0
            
        return float(s)
    except Exception:
        return 0.0

def fetch_resources(cluster: Cluster, api_version: str, kind: str, namespace: Optional[str] = None):
    """
    Generic fetcher with enrichment for specific types.
    """
    dyn_client = get_dynamic_client(cluster)
    resource_api = dyn_client.resources.get(api_version=api_version, kind=kind)
    
    resp = resource_api.get(namespace=namespace)
    items = resp.items
    
    # Enrichment
    if kind == 'Node':
        items = enrich_nodes_with_metrics(cluster, dyn_client, items)
    elif kind == 'Machine':
        items = enrich_machines(items)
        
    return items

def enrich_nodes_with_metrics(cluster: Cluster, dyn_client: DynamicClient, nodes: List[Any]) -> List[Any]:
    """Fetches metrics for all nodes and attaches to node objects."""
    metrics_map = {}
    try:
        metrics_api = dyn_client.resources.get(api_version='metrics.k8s.io/v1beta1', kind='NodeMetrics')
        m_resp = metrics_api.get()
        for m in m_resp.items:
            metrics_map[m.metadata.name] = m
    except Exception as e:
        print(f"Error fetching node metrics for {cluster.name}: {e}")

    enriched = []
    for node in nodes:
        node_name = node.metadata.name
        m = metrics_map.get(node_name)
        
        # Base dict for JSON serialization
        n_dict = dict(node)
        n_dict['__metrics'] = None
        
        if m:
            cpu_usage = parse_cpu(m.usage.cpu)
            mem_usage_bytes = parse_memory_to_gb(m.usage.memory) * (1024**3) # Convert back to bytes for consistency if needed, or just use GB
            
            capacity_cpu = parse_cpu(node.status.capacity.cpu)
            capacity_mem = parse_memory_to_gb(node.status.capacity.memory)
            
            n_dict['__metrics'] = {
                "cpu_usage": cpu_usage,
                "mem_usage_gb": parse_memory_to_gb(m.usage.memory),
                "cpu_percent": round((cpu_usage / capacity_cpu * 100), 1) if capacity_cpu > 0 else 0,
                "mem_percent": round((parse_memory_to_gb(m.usage.memory) / capacity_mem * 100), 1) if capacity_mem > 0 else 0
            }
        enriched.append(n_dict)
    return enriched

def enrich_machines(machines: List[Any]) -> List[Any]:
    """Adds capacity info to machines for UI consistency."""
    enriched = []
    for m in machines:
        m_dict = dict(m)
        # Machine doesn't have live metrics usually, but we can extract capacity from labels/spec if needed
        # For now just ensure __enriched exists to avoid JS errors if any
        m_dict['__enriched'] = {
            "cpu": parse_cpu(m.metadata.labels.get('machine.openshift.io/instance-type')), # Placeholder logic
            "memory": 0 # Placeholder
        }
        # Actually, machine.openshift.io/instance-type is just a name. 
        # For machines, we usually just want to see the type.
        enriched.append(m_dict)
    return enriched

def get_cluster_stats(cluster: Cluster, nodes: Optional[List[Any]] = None, snapshot_data: Optional[dict] = None):
    try:
        if snapshot_data:
            # Offline Mode from Snapshot
            nodes = snapshot_data.get("nodes", [])
            node_count = len(nodes)
            vcpu_count = 0
            for node in nodes:
                 # In snapshot, node is a dict, not a K8s object
                 # Capacity is usually at node['status']['capacity']['cpu']
                 try:
                     cpu = node['status']['capacity']['cpu']
                     vcpu_count += parse_cpu(cpu)
                 except:
                     pass
                     
            cluster_version = "N/A"
            # Attempt to find version in clusteroperators or infrastructures if stored? 
            # Or assume we stored it specifically? 
            # For now, let's look at clusteroperators if we store them
            pass # TODO: Extract version from snapshot if possible
            
            console_url = "N/A"
            # TODO: Extract console from route snapshot
            
            # Simple return for snapshot mode for now
            return {
                "id": cluster.id,
                "node_count": node_count,
                "vcpu_count": int(vcpu_count),
                "version": cluster_version,
                "console_url": console_url
            }

        dyn_client = get_dynamic_client(cluster)
        
        # Nodes and vCPUs
        if nodes is None:
            v1_nodes = dyn_client.resources.get(api_version='v1', kind='Node')
            nodes = v1_nodes.get().items
        node_count = len(nodes)
        
        vcpu_count = 0
        for node in nodes:
            # Capacity is usually a string like "4" or "4000m"
            cpu = node.status.capacity.cpu
            vcpu_count += parse_cpu(cpu)
                 
        # Version Info
        cluster_version = "N/A"
        try:
             version_resource = dyn_client.resources.get(api_version='config.openshift.io/v1', kind='ClusterVersion')
             v_obj = version_resource.get(name='version')
             cluster_version = v_obj.status.desired.version
        except Exception:
             pass
             
        # Console URL
        console_url = "N/A"
        try:
             route_resource = dyn_client.resources.get(api_version='route.openshift.io/v1', kind='Route')
             console_route = route_resource.get(name='console', namespace='openshift-console')
             if console_route:
                 console_url = f"https://{console_route.spec.host}"
        except Exception:
             pass
             
        return {
            "id": cluster.id,
            "node_count": node_count,
            "vcpu_count": int(vcpu_count),
            "version": cluster_version,
            "console_url": console_url
        }
    except Exception as e:
        print(f"Error fetching stats for {cluster.name}: {e}")
        return {
            "id": cluster.id,
            "node_count": "-",
            "vcpu_count": "-",
            "version": "-",
            "console_url": "#"
        }
def get_detailed_stats(cluster: Cluster, snapshot_data: Optional[dict] = None):
    try:
        if snapshot_data:
            # Offline extraction
            nodes = snapshot_data.get("nodes", [])
            machines = snapshot_data.get("machines", [])
            machinesets = snapshot_data.get("machinesets", [])
            autoscalers = snapshot_data.get("machineautoscalers", [])
            projects = snapshot_data.get("projects", [])
            ingress = snapshot_data.get("ingresscontrollers", [])

            # Summaries
            node_roles = {}
            for n in nodes:
                roles = [l.split('/')[1] for l in n['metadata'].get('labels', {}) if l.startswith('node-role.kubernetes.io/')]
                role = roles[0] if roles else 'worker'
                node_roles[role] = node_roles.get(role, 0) + 1

            # Build response
            return {
                "cluster_name": cluster.name,
                "api_url": cluster.api_url,
                "version": "Snapshot", # TODO: enhance
                "console_url": "#",    # TODO: enhance
                "node_count": len(nodes),
                "node_roles": node_roles,
                "machine_count": len(machines),
                "machineset_count": len(machinesets),
                "autoscaler_count": len(autoscalers),
                "project_count": len(projects),
                "ingress_count": len(ingress),
                "status_message": "Snapshot View"
            }

        dyn_client = get_dynamic_client(cluster)
        
        # 1. Cluster Version
        version_api = dyn_client.resources.get(api_version='config.openshift.io/v1', kind='ClusterVersion')
        version_obj = version_api.get(name='version')
        
        # 2. Infrastructure
        infra_api = dyn_client.resources.get(api_version='config.openshift.io/v1', kind='Infrastructure')
        infra_obj = infra_api.get(name='cluster')
        
        # 3. Cluster Operators
        co_api = dyn_client.resources.get(api_version='config.openshift.io/v1', kind='ClusterOperator')
        operators = co_api.get().items
        
        # 4. Console URL (re-use logic or fetch explicitly)
        console_url = "N/A"
        try:
             route_resource = dyn_client.resources.get(api_version='route.openshift.io/v1', kind='Route')
             console_route = route_resource.get(name='console', namespace='openshift-console')
             if console_route:
                 console_url = f"https://{console_route.spec.host}"
        except Exception:
             pass

        return {
            "api_url": cluster.api_url,
            "console_url": console_url,
            "version_info": {
                "desired_version": version_obj.status.desired.version,
                "history": [h.to_dict() for h in version_obj.status.history],
                "cluster_id": version_obj.spec.clusterID
            },
            "infrastructure": {
                "type": infra_obj.status.platformStatus.type,
                "api_server_url": infra_obj.status.apiServerURL,
                "infrastructure_name": infra_obj.status.infrastructureName
            },
            "operators": [
                {
                    "name": o.metadata.name,
                    "status": {
                        "available": any(c.type == "Available" and c.status == "True" for c in o.status.conditions),
                        "degraded": any(c.type == "Degraded" and c.status == "True" for c in o.status.conditions),
                        "progressing": any(c.type == "Progressing" and c.status == "True" for c in o.status.conditions)
                    },
                    "message": next((c.message for c in o.status.conditions if (c.type == "Degraded" or c.type == "Progressing") and c.status == "True" and getattr(c, 'message', None)), 
                               next((c.message for c in o.status.conditions if c.type == "Available" and getattr(c, 'message', None)), ""))
                } for o in operators
            ]
        }
    except Exception as e:
        print(f"Error fetching detailed stats for {cluster.name}: {e}")
        raise e

def get_ingress_details(cluster: Cluster, name: str, snapshot_data: Optional[dict] = None):
    try:
        if snapshot_data:
            # Offline Logic
            ingress = snapshot_data.get("ingresscontrollers", [])
            target_ic = next((i for i in ingress if i['metadata']['name'] == name), None)
            if not target_ic:
                return {"error": "Ingress not found in snapshot"}
            
            # TODO: We might not have full deployment/pod details in the raw lists unless we dump them all
            # For this MVP, let's return the IC spec itself
            return {
                "name": name,
                "domain": target_ic['status'].get('domain', 'N/A'),
                "replicas": target_ic['spec'].get('replicas', 2),
                "strategy": "Snapshot View",
                "nodeSelector": target_ic['spec'].get('nodePlacement', {}).get('nodeSelector', {}),
                "tolerations": target_ic['spec'].get('nodePlacement', {}).get('tolerations', []),
                "pods": [] # Detailed pods might be heavy to store freely?
            }
        dyn_client = get_dynamic_client(cluster)
        
        # 1. Fetch IngressController CR (for domain etc)
        ic_api = dyn_client.resources.get(api_version='operator.openshift.io/v1', kind='IngressController')
        ic = ic_api.get(name=name, namespace='openshift-ingress-operator')
        
        # 2. Fetch Deployment in openshift-ingress (actual runner)
        deploy_api = dyn_client.resources.get(api_version='apps/v1', kind='Deployment')
        deployment_name = f"router-{name}"
        
        node_selector = {}
        tolerations = []
        selector_str = f"ingresscontroller.operator.openshift.io/owning-ingresscontroller={name}"
        try:
            dep = deploy_api.get(name=deployment_name, namespace='openshift-ingress')
            node_selector = dep.spec.template.spec.nodeSelector or {}
            tolerations = dep.spec.template.spec.tolerations or []
            
            # Use the deployment's own label selector to find pods
            if hasattr(dep.spec, 'selector') and hasattr(dep.spec.selector, 'matchLabels'):
                match_labels = dep.spec.selector.matchLabels
                selector_str = ",".join([f"{k}={v}" for k, v in match_labels.items()])
        except Exception as de:
            print(f"Could not find deployment {deployment_name} in openshift-ingress: {de}")

        # 3. Fetch Pods to find which nodes they are on
        pod_api = dyn_client.resources.get(api_version='v1', kind='Pod')
        pods = pod_api.get(namespace='openshift-ingress', label_selector=selector_str).items
        
        enriched_pods = []
        for p in pods:
            # Check readiness
            is_ready = any(c.type == 'Ready' and c.status == 'True' for c in (p.status.conditions or []))
            # Restart count
            restart_count = sum(cs.restartCount for cs in (p.status.containerStatuses or []))
            
            enriched_pods.append({
                "name": p.metadata.name,
                "node": p.spec.nodeName or '-',
                "status": p.status.phase,
                "ready": is_ready,
                "restarts": restart_count,
                "startTime": p.status.startTime
            })

        router_nodes = list(set([p['node'] for p in enriched_pods if p['status'] == 'Running' and p['node'] != '-']))
        
        return {
            "name": ic.metadata.name,
            "labels": ic.metadata.labels or {},
            "spec": ic.spec.to_dict(),
            "status": ic.status.to_dict() if hasattr(ic, 'status') else {},
            "deployment": {
                "name": deployment_name,
                "node_selector": node_selector,
                "tolerations": tolerations,
                "selector": selector_str
            },
            "pods": enriched_pods,
            "router_nodes": router_nodes
        }
    except Exception as e:
        print(f"Error fetching ingress details for {name} on {cluster.name}: {e}")
        raise e

def get_node_details(cluster: Cluster, node_name: str, snapshot_data: Optional[dict] = None):
    try:
        if snapshot_data:
            nodes = snapshot_data.get("nodes", [])
            node = next((n for n in nodes if n['metadata']['name'] == node_name), None)
            if not node:
                 return {"error": "Node not found in snapshot"}
            
            return {
                "name": node_name,
                "role": "worker", # parse form labels
                "status": "Ready", # parse from conditions
                "labels": node['metadata'].get('labels', {}),
                "capacity": node['status'].get('capacity', {}),
                "allocatable": node['status'].get('allocatable', {}),
                "conditions": node['status'].get('conditions', []),
                "events": [] # Events are not snapshotted usually
            }
        dyn_client = get_dynamic_client(cluster)
        
        # 1. Fetch Node object
        node_api = dyn_client.resources.get(api_version='v1', kind='Node')
        node = node_api.get(name=node_name)
        
        # 2. Fetch Events for this node
        event_api = dyn_client.resources.get(api_version='v1', kind='Event')
        events = event_api.get(field_selector=f"involvedObject.kind=Node,involvedObject.name={node_name}").items
        # Sort events by lastTimestamp or firstTimestamp
        events = sorted(events, key=lambda e: e.lastTimestamp or e.firstTimestamp or "", reverse=True)[:20]
        
        # 3. Fetch Pods on this node to calculate requests/limits
        pod_api = dyn_client.resources.get(api_version='v1', kind='Pod')
        pods = pod_api.get(field_selector=f"spec.nodeName={node_name}").items
        
        total_cpu_req = 0.0
        total_cpu_lim = 0.0
        total_mem_req = 0.0
        total_mem_lim = 0.0
        
        for p in pods:
            if p.status.phase in ['Succeeded', 'Failed']:
                continue
            for c in p.spec.containers:
                res = getattr(c, 'resources', None)
                if res:
                    reqs = getattr(res, 'requests', None)
                    if reqs:
                        total_cpu_req += parse_cpu(reqs.get('cpu'))
                        total_mem_req += parse_memory_to_gb(reqs.get('memory'))
                    
                    lims = getattr(res, 'limits', None)
                    if lims:
                        total_cpu_lim += parse_cpu(lims.get('cpu'))
                        total_mem_lim += parse_memory_to_gb(lims.get('memory'))
        
        # 4. Metrics? 
        cpu_usage = 0.0
        mem_usage = 0.0
        try:
             metrics_api = dyn_client.resources.get(api_version='metrics.k8s.io/v1beta1', kind='NodeMetrics')
             m = metrics_api.get(name=node_name)
             cpu_usage = parse_cpu(m.usage.cpu)
             mem_usage = parse_memory_to_gb(m.usage.memory)
        except:
             pass

        capacity_cpu = parse_cpu(node.status.capacity.cpu)
        capacity_mem = parse_memory_to_gb(node.status.capacity.memory)
        
        return {
            "name": node_name,
            "labels": node.metadata.labels or {},
            "annotations": node.metadata.annotations or {},
            "capacity": {
                "cpu": capacity_cpu,
                "memory": capacity_mem
            },
            "usage": {
                "cpu": cpu_usage,
                "memory": mem_usage,
                "cpu_percent": (cpu_usage / capacity_cpu * 100) if capacity_cpu > 0 else 0,
                "mem_percent": (mem_usage / capacity_mem * 100) if capacity_mem > 0 else 0
            },
            "requests_limits": {
                "cpu_req": total_cpu_req,
                "cpu_lim": total_cpu_lim,
                "mem_req": total_mem_req,
                "mem_lim": total_mem_lim,
                "cpu_req_percent": (total_cpu_req / capacity_cpu * 100) if capacity_cpu > 0 else 0,
                "mem_req_percent": (total_mem_req / capacity_mem * 100) if capacity_mem > 0 else 0
            },
            "events": [{
                "type": e.type,
                "reason": e.reason,
                "message": e.message,
                "lastTimestamp": e.lastTimestamp or e.firstTimestamp,
                "count": e.count
            } for e in events]
        }
    except Exception as e:
        print(f"Error fetching node details for {node_name} on {cluster.name}: {e}")
        raise e
