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
    Generic fetcher.
    Example: 
        fetch_resources(c, 'v1', 'Node')
        fetch_resources(c, 'machine.openshift.io/v1beta1', 'Machine')
    """
    dyn_client = get_dynamic_client(cluster)
    resource_api = dyn_client.resources.get(api_version=api_version, kind=kind)
    
    # Fetch list
    # The return object is a ResourceList, which has 'items'.
    # Each item is a ResourceInstance (dict-like).
    
    resp = resource_api.get(namespace=namespace)
    return resp.items

def get_cluster_stats(cluster: Cluster, nodes: Optional[List[Any]] = None):
    try:
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
                 
        # Console URL
        # Usually found in ConfigMap or Route in openshift-console namespace
        # Or 'console.openshift.io/v1' Console/cluster
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
            "console_url": console_url
        }
    except Exception as e:
        print(f"Error fetching stats for {cluster.name}: {e}")
        return {
            "id": cluster.id,
            "node_count": "-",
            "vcpu_count": "-",
            "console_url": "#"
        }
