import urllib3
import re
from typing import Optional, List, Any
from kubernetes import client
from openshift.dynamic import DynamicClient, exceptions as dyn_exc
from app.models import Cluster

# Disable insecure request warnings for now as many internal OCP clusters use self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_val(obj, path, case_insensitive=False):
    """
    Helper to safely get nested values from object or dict.
    Handles metadata.labels['foo.bar'], bracketed indices [0], and dot indices .0
    """
    if not path:
        return obj
    
    # Matches:
    # 1. ["double quoted"]
    # 2. ['single quoted']
    # 3. [0] (numeric index)
    # 4. ."double quoted field"
    # 5. .'single quoted field'
    # 6. field (simple field, potentially preceded by a dot)
    parts = re.findall(r'\["([^"\]]+)"\]|\[\'([^\'\]]+)\'\]|\[(\d+)\]|(?:\."([^"]+)")|(?:\.\'([^\']+)\')|\.?([^.\[\]"\']+)', path)
    curr = obj
    
    for b_double, b_single, b_num, d_double, d_single, field in parts:
        p = b_double or b_single or b_num or d_double or d_single or field
        if curr is None:
            return None
            
        if isinstance(curr, list):
            try:
                idx = int(p)
                if 0 <= idx < len(curr):
                    curr = curr[idx]
                else:
                    return None
            except (ValueError, IndexError):
                return None
        elif isinstance(curr, dict):
            val = curr.get(p)
            if val is None and case_insensitive:
                p_lower = p.lower()
                for k, v in curr.items():
                    if k.lower() == p_lower:
                        val = v
                        break
            curr = val
        else:
            # Object or other type
            # Try numeric index if it looks like one, for list-like objects
            if p.isdigit():
                try:
                    idx = int(p)
                    curr = curr[idx]
                except (TypeError, IndexError, KeyError):
                    curr = getattr(curr, p, None)
            else:
                val = getattr(curr, p, None)
                if val is None and case_insensitive:
                    p_lower = p.lower()
                    for attr in dir(curr):
                        if attr.lower() == p_lower:
                            val = getattr(curr, attr)
                            break
                curr = val
                
    return curr

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
    Handles: "4", 4, "4000m", "500m", "0.5", "100n", "100u"
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
        if s.endswith('u'):
            return float(s[:-1]) / 1000000.0
        if s.endswith('n'):
            return float(s[:-1]) / 1000000000.0
            
        return float(s)
    except Exception:
        return 0.0

def fetch_resources(cluster: Cluster, api_version: str, kind: str, namespace: Optional[str] = None, timeout: int = 300, use_table: bool = False):
    """
    Generic fetcher with enrichment for specific types.
    """
    dyn_client = get_dynamic_client(cluster)
    resource_api = dyn_client.resources.get(api_version=api_version, kind=kind)
    
    kwargs = {'_request_timeout': timeout}
    if namespace:
        kwargs['namespace'] = namespace
        
    if use_table:
        # Request Table format to reduce payload size (no full schemas/icons)
        # Fixed header: g=meta.k8s.io (not /v1 suffix)
        kwargs['header_params'] = {'Accept': 'application/json;as=Table;g=meta.k8s.io;v=v1'}

    resp = resource_api.get(**kwargs)
    
    if use_table:
        # Return the raw Table object (dict)
        return resp.to_dict() if hasattr(resp, 'to_dict') else resp

    items = resp.items
    
    # Enrichment
    if kind == 'Node':
        items = enrich_nodes_with_metrics(cluster, dyn_client, items)
    elif kind == 'Machine':
        items = enrich_machines(items)
    else:
        # Ensure we return dicts, as ResourceInstance objects might not be fully serializable by FastAPI
        # effectively handling IngressController, Project, etc.
        items = [item.to_dict() if hasattr(item, 'to_dict') else item for item in items]
        
    return items

def get_cluster_unique_id(cluster: Cluster) -> Optional[str]:
    """Fetches the unique OpenShift Cluster ID from the ClusterVersion resource."""
    try:
        dyn_client = get_dynamic_client(cluster)
        version_api = dyn_client.resources.get(api_version='config.openshift.io/v1', kind='ClusterVersion')
        # OpenShift usually has a singleton ClusterVersion named 'version'
        version_obj = version_api.get(name='version')
        return version_obj.spec.clusterID
    except Exception as e:
        print(f"Error fetching cluster ID for {cluster.name}: {e}")
        return None

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
        n_dict = node.to_dict()
        
        # Always include capacity info
        capacity_cpu = parse_cpu(node.status.capacity.cpu)
        capacity_mem = parse_memory_to_gb(node.status.capacity.memory)
        n_dict['__capacity'] = {
            "cpu": capacity_cpu,
            "memory_gb": round(capacity_mem, 1)
        }

        n_dict['__metrics'] = None
        
        if m:
            cpu_usage = parse_cpu(m.usage.cpu)
            
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
        m_dict = m.to_dict() if hasattr(m, 'to_dict') else m
        
        # 1. Capture VM Type (Size)
        # Try different common paths for OCP Machine objects
        vm_type = get_val(m, 'spec.providerSpec.value.vmSize') or \
                  get_val(m, 'spec.providerSpec.value.instanceType') or \
                  get_val(m, 'metadata.labels["machine.openshift.io/instance-type"]') or \
                  get_val(m, 'status.providerStatus.instanceType') # AWS fallback
        
        # 2. Capture CPU/Memory from status.capacity if present 
        # (Standard for some Machine API versions/providers)
        cpu_raw = get_val(m, 'status.capacity.cpu')
        mem_raw = get_val(m, 'status.capacity.memory')
        
        # 3. Fallback for Infrastructure Labels if they are in status summary
        # Some machines report it in status.nodeRef's linked node, but here we only have the machine.
        
        m_dict['__enriched'] = {
            "vm_type": vm_type or "-",
            "cpu": parse_cpu(cpu_raw) if cpu_raw else 0,
            "memory_gb": parse_memory_to_gb(mem_raw) if mem_raw else 0
        }
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
                 cpu = get_val(node, 'status.capacity.cpu')
                 vcpu_count += parse_cpu(cpu)
                      
            cluster_version = "N/A"
            upgrade_status = None
            
            # Try to get from clusterversions list if captured (new poller)
            cv_list = snapshot_data.get("clusterversions", [])
            target_cv = next((cv for cv in cv_list if get_val(cv, 'metadata.name') == 'version'), None)
            if target_cv:
                # Target version is what the cluster DESIRES to be
                target_version = get_val(target_cv, 'status.desired.version') or "N/A"
                
                # Current version is the most recent "Completed" entry in history
                history = get_val(target_cv, 'status.history') or []
                cluster_version = "N/A"
                for h in history:
                    if h.get('state') == 'Completed':
                        cluster_version = h.get('version')
                        break
                
                # If no completed history (fresh cluster?), fallback to desired
                if cluster_version == "N/A":
                    cluster_version = target_version
                
                # Check for upgrade
                conditions = get_val(target_cv, 'status.conditions') or []
                prog = next((c for c in conditions if c.get('type') == 'Progressing' and c.get('status') == 'True'), None)
                if prog:
                    msg = prog.get('message', '')
                    pct = 0
                    # Try to regex percentage "X of Y done (Z% complete)"
                    import re
                    match = re.search(r'(\d+)% complete', msg)
                    if match:
                        pct = int(match.group(1))
                    
                    upgrade_status = {
                        "is_upgrading": True,
                        "message": msg,
                        "percentage": pct,
                        "target_version": target_version
                    }
            
            console_url = "#"
            # Try to get from routes list if captured (new poller)
            routes = snapshot_data.get("routes", [])
            console_route = next((r for r in routes if get_val(r, 'metadata.name') == 'console' and get_val(r, 'metadata.namespace') == 'openshift-console'), None)
            if console_route:
                host = get_val(console_route, 'spec.host')
                if host:
                    console_url = f"https://{host}"
            
            return {
                "id": cluster.id,
                "node_count": node_count,
                "vcpu_count": int(vcpu_count),
                "version": cluster_version,
                "console_url": console_url,
                "upgrade_status": upgrade_status
            }

        dyn_client = get_dynamic_client(cluster)
        
        # Nodes and vCPUs
        if nodes is None:
            v1_nodes = dyn_client.resources.get(api_version='v1', kind='Node')
            nodes = v1_nodes.get().items
        node_count = len(nodes)
        
        vcpu_count = 0
        for node in nodes:
            # Handle both dicts (enriched) and K8s objects
            cpu = get_val(node, 'status.capacity.cpu')
            vcpu_count += parse_cpu(cpu)
                 
        # Version Info
        cluster_version = "N/A"
        upgrade_status = None
        try:
             version_resource = dyn_client.resources.get(api_version='config.openshift.io/v1', kind='ClusterVersion')
             v_obj = version_resource.get(name='version')
             
             # Target version is what the cluster DESIRES to be
             target_version = v_obj.status.desired.version
             
             # Current version is the most recent "Completed" entry in history
             history = v_obj.status.history or []
             cluster_version = "N/A"
             for h in history:
                 if h.state == 'Completed':
                     cluster_version = h.version
                     break
             
             # Fallback
             if cluster_version == "N/A":
                 cluster_version = target_version
             
             # Check for upgrade
             conditions = v_obj.status.conditions or []
             # Conditions is a list of objects/dicts depending on client
             prog = next((c for c in conditions if c.type == 'Progressing' and c.status == 'True'), None)
             if prog:
                msg = prog.message
                pct = 0
                import re
                match = re.search(r'(\d+)% complete', msg)
                if match:
                    pct = int(match.group(1))
                
                upgrade_status = {
                    "is_upgrading": True,
                    "message": msg,
                    "percentage": pct,
                    "target_version": target_version
                }

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
            "console_url": console_url,
            "upgrade_status": upgrade_status
        }
    except Exception as e:
        print(f"Error fetching stats for {cluster.name}: {e}")
        return {
            "id": cluster.id,
            "node_count": "-",
            "vcpu_count": "-",
            "version": "-",
            "console_url": "#",
            "upgrade_status": None
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
            ],
            "service_mesh": get_service_mesh_details(cluster, snapshot_data=None)
        }
    except Exception as e:
        print(f"Error fetching detailed stats for {cluster.name}: {e}")
        raise e

def get_service_mesh_details(cluster: Cluster, snapshot_data: Optional[dict] = None) -> dict:
    """
    Detects and inventories Service Mesh (v2 Maistra and v3 Istio/Sail).
    Returns Control Planes, Membership, and Traffic config.
    """
    mesh_data = {
        "is_active": False,
        "control_planes": [],
        "membership": [],
        "traffic": {
            "gateways": [],
            "virtual_services": []
        },
        "summary": {
            "mesh_size": 0,
            "version": "None"
        }
    }

    try:
        if snapshot_data:
            return snapshot_data.get('service_mesh', mesh_data)

        dyn_client = get_dynamic_client(cluster)

        # --- 1. Control Plane Detection ---
        
        # v2: ServiceMeshControlPlane
        v2_cp_list = []
        try:
            smcp_api = dyn_client.resources.get(api_version='maistra.io/v2', kind='ServiceMeshControlPlane')
            v2_cp_list = smcp_api.get().items
        except dyn_exc.ResourceNotFoundError:
            pass
        except Exception as e:
            print(f"Error checking SMCP v2 on {cluster.name}: {e}")

        # v3: Istio (Sail Operator)
        v3_cp_list = []
        try:
            istio_api = dyn_client.resources.get(api_version='sail.operator.openshift.io/v1', kind='Istio')
            v3_cp_list = istio_api.get().items
        except dyn_exc.ResourceNotFoundError:
            # Fallback to upstream istio.io if sail not using own group yet or different version
            try:
                istio_api = dyn_client.resources.get(api_version='istio.io/v1beta1', kind='Istio')
                v3_cp_list = istio_api.get().items
            except:
                pass
        except Exception:
            pass

        if not v2_cp_list and not v3_cp_list:
            return mesh_data

        mesh_data["is_active"] = True
        
        # Process v2
        for smcp in v2_cp_list:
            mesh_data["control_planes"].append({
                "type": "Maistra v2",
                "name": smcp.metadata.name,
                "namespace": smcp.metadata.namespace,
                "version": smcp.status.get('chartVersion', 'Unknown'),
                "status": smcp.status.get('conditions', [{}])[0].get('type', 'Unknown'),
                "components": [c for c in smcp.status.get('readiness', {}).get('components', {}).keys()]
            })

        # Process v3
        for istio in v3_cp_list:
             mesh_data["control_planes"].append({
                "type": "Istio v3",
                "name": istio.metadata.name,
                "namespace": istio.metadata.namespace,
                "version": istio.spec.get('version', 'Unknown'),
                "status": "Active" # Simplify for now
            })
            
        # --- 2. Membership (Namespaces) ---
        member_namespaces = set()
        
        # v2: ServiceMeshMemberRoll (usually in same NS as CP)
        try:
            smmr_api = dyn_client.resources.get(api_version='maistra.io/v1', kind='ServiceMeshMemberRoll')
            # Check all known CP namespaces
            cp_namespaces = set([cp['namespace'] for cp in mesh_data["control_planes"] if cp['type'] == 'Maistra v2'])
            
            for ns in cp_namespaces:
                try:
                    smmr = smmr_api.get(namespace=ns, name='default') # Usually named default
                    if smmr:
                        member_namespaces.update(smmr.status.get('members', []))
                        # Also add control plane namespace itself
                        member_namespaces.add(ns) 
                except:
                    pass
        except:
            pass

        # v3 & v2 (Auto Injection): Check Namespace labels
        # v3 often uses istio-injection=enabled or istio.io/rev=xxx
        try:
            ns_api = dyn_client.resources.get(api_version='v1', kind='Namespace')
            all_ns = ns_api.get().items
            for n in all_ns:
                lbls = n.metadata.get('labels', {})
                if 'istio-injection' in lbls or 'istio.io/rev' in lbls or 'maistra.io/member-of' in lbls:
                    member_namespaces.add(n.metadata.name)
        except:
            pass
            
        mesh_data["membership"] = sorted(list(member_namespaces))
        
        # --- 3. Traffic (Gateways & VS) ---
        # Fetch from all member namespaces + CP namespaces
        # Note: This could be heavy if many namespaces. For MVP, fetch from all or just CP?
        # Let's fetch from all namespaces essentially, or use label selector if possible.
        # Actually client.get() without namespace fetches all.
        
        try:
            gw_api = dyn_client.resources.get(api_version='networking.istio.io/v1beta1', kind='Gateway')
            gateways = gw_api.get().items
            for gw in gateways:
                mesh_data["traffic"]["gateways"].append({
                    "name": gw.metadata.name,
                    "namespace": gw.metadata.namespace,
                    "selector": gw.spec.get('selector', {}),
                    "servers": len(gw.spec.get('servers', []))
                })
        except:
            pass # CRD might not exist if v2 not fully ready or using v1alpha3

        try:
            vs_api = dyn_client.resources.get(api_version='networking.istio.io/v1beta1', kind='VirtualService')
            vservices = vs_api.get().items
            for vs in vservices:
                 mesh_data["traffic"]["virtual_services"].append({
                    "name": vs.metadata.name,
                    "namespace": vs.metadata.namespace,
                    "hosts": vs.spec.get('hosts', []),
                    "gateways": vs.spec.get('gateways', [])
                })
        except:
            pass

        # --- 4. Mesh Size (Proxy Count) ---
        # Count pods with 'istio-proxy' container in member namespaces
        # Optimization: Just count all pods with label 'security.istio.io/tlsMode' or container name
        count = 0
        try:
            pod_api = dyn_client.resources.get(api_version='v1', kind='Pod')
            # Fetching all pods is heavy. Let's try to limit if possible.
            # But we need a count. 
            # If we know member namespaces, we could loop them? 
            # Or just fetch all pods (we probably already rely on caching or this is an on-demand detailed fetch)
            # Since this is "get_detailed_stats" called usually for a single cluster view, it might be acceptable.
            # A better way might be to ask Prometheus, but we stick to K8s API for now.
            
            # Filter by label selector common to proxies?
            # 'service.istio.io/canonical-name' is common
            pods = pod_api.get(label_selector='service.istio.io/canonical-name').items
            count = len(pods)
        except:
            pass
            
        mesh_data["summary"]["mesh_size"] = count
        if mesh_data["control_planes"]:
             mesh_data["summary"]["version"] = mesh_data["control_planes"][0]['version']

    except Exception as e:
        print(f"Error fetching service mesh details for {cluster.name}: {e}")
    
    return mesh_data

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
            
            # Try to get enriched info if available
            metrics = node.get('__metrics')
            capacity_info = node.get('__capacity', {})
            
            capacity_cpu = capacity_info.get('cpu') or parse_cpu(node['status'].get('capacity', {}).get('cpu'))
            capacity_mem = capacity_info.get('memory_gb') or parse_memory_to_gb(node['status'].get('capacity', {}).get('memory'))
            
            # Simple role detection from labels
            labels = node['metadata'].get('labels', {})
            role = 'worker'
            if 'node-role.kubernetes.io/master' in labels or 'node-role.kubernetes.io/control-plane' in labels:
                role = 'master'
            elif 'node-role.kubernetes.io/infra' in labels:
                role = 'infra'

            return {
                "name": node_name,
                "role": role,
                "labels": labels,
                "annotations": node['metadata'].get('annotations', {}),
                "capacity": {
                    "cpu": capacity_cpu,
                    "memory": capacity_mem
                },
                "usage": {
                    "cpu": metrics.get('cpu_usage', 0) if metrics else 0,
                    "memory": metrics.get('mem_usage_gb', 0) if metrics else 0,
                    "cpu_percent": metrics.get('cpu_percent', 0) if metrics else 0,
                    "mem_percent": metrics.get('mem_percent', 0) if metrics else 0
                },
                "requests_limits": {
                    "cpu_req": 0, # Difficult to calc from snap without all pods
                    "cpu_lim": 0,
                    "mem_req": 0,
                    "mem_lim": 0,
                    "cpu_req_percent": 0,
                    "mem_req_percent": 0
                },
                "events": [],
                "conditions": node['status'].get('conditions', [])
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
            } for e in events],
            "conditions": [{
                "type": c.type,
                "status": c.status,
                "lastHeartbeatTime": c.lastHeartbeatTime,
                "lastTransitionTime": c.lastTransitionTime,
                "reason": c.reason,
                "message": c.message
            } for c in (node.status.conditions or [])]
        }
    except Exception as e:
        print(f"Error fetching node details for {node_name} on {cluster.name}: {e}")
        raise e

def get_machine_details(cluster, machine_name, snapshot_data=None):
    try:
        if snapshot_data:
            machines = snapshot_data.get("machines", [])
            machine = next((m for m in machines if m['metadata']['name'] == machine_name), None)
            if not machine:
                return {"error": "Machine not found in snapshot"}
            
            # Simple metadata from snapshot
            return {
                "name": machine_name,
                "labels": machine['metadata'].get('labels', {}),
                "annotations": machine['metadata'].get('annotations', {}),
                "spec": machine.get('spec', {}),
                "status": machine.get('status', {}),
                "events": []
            }

        dyn_client = get_dynamic_client(cluster)
        
        # 1. Fetch Machine object (search all namespaces)
        machine_api = dyn_client.resources.get(api_version='machine.openshift.io/v1beta1', kind='Machine')
        m_list = machine_api.get().items
        machine = next((m for m in m_list if m.metadata.name == machine_name), None)
        
        if not machine:
            return {"error": f"Machine '{machine_name}' not found"}
        
        m_dict = machine.to_dict()
        namespace = get_val(m_dict, 'metadata.namespace')

        # 2. Fetch Events for this machine in its namespace
        events_list = []
        try:
            event_api = dyn_client.resources.get(api_version='v1', kind='Event')
            events = event_api.get(namespace=namespace, field_selector=f"involvedObject.kind=Machine,involvedObject.name={machine_name}").items
            # Sort events safely
            events = sorted(events, key=lambda e: str(e.lastTimestamp or e.firstTimestamp or ""), reverse=True)[:20]
            events_list = [{
                "type": e.type,
                "reason": e.reason,
                "message": e.message,
                "lastTimestamp": e.lastTimestamp or e.firstTimestamp,
                "count": e.count
            } for e in events]
        except Exception as ee:
            print(f"Non-critical: could not fetch events for machine {machine_name}: {ee}")
        
        # 3. Extract Platform specifics
        provider_spec = get_val(m_dict, 'spec.providerSpec.value') or {}
        platform = provider_spec.get('kind', '')
        
        details = {
            "name": machine_name,
            "namespace": namespace,
            "labels": get_val(m_dict, 'metadata.labels') or {},
            "annotations": get_val(m_dict, 'metadata.annotations') or {},
            "platform": platform,
            "provider_id": get_val(m_dict, 'spec.providerID') or '',
            "phase": get_val(m_dict, 'status.phase') or 'Unknown',
            "vm_size": provider_spec.get('vmSize') or provider_spec.get('instanceType') or '',
            "events": events_list
        }
        
        if platform == "AzureMachineProviderSpec":
            details.update({
                "resource_group": provider_spec.get('resourceGroup', ''),
                "vnet": provider_spec.get('vnet', ''),
                "vnet_resource_group": provider_spec.get('networkResourceGroup', ''),
                "subnet": provider_spec.get('subnet', ''),
                "zone": provider_spec.get('zone', ''),
                "location": provider_spec.get('location', '')
            })
        elif platform == "VSphereMachineProviderSpec":
            workspace = provider_spec.get('workspace', {})
            details.update({
                "vsphere_server": workspace.get('server', ''),
                "datacenter": workspace.get('datacenter', ''),
                "datastore": workspace.get('datastore', ''),
                "folder": workspace.get('folder', ''),
                "resource_pool": workspace.get('resourcePool', ''),
                "cpus": provider_spec.get('numCPUs', ''),
                "memory_mb": provider_spec.get('memoryMiB', ''),
                "disk_gb": provider_spec.get('diskGiB', '')
            })
            
        return details

    except Exception as e:
        print(f"Error fetching machine details for {machine_name} on {cluster.name}: {e}")
        raise e
