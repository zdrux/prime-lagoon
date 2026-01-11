
import sys
import os
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.getcwd())

from app.services.ocp import get_service_mesh_details
from app.models import Cluster

def test_v2_detection():
    print("Testing v2 (Maistra) Detection...")
    cluster = Cluster(id=1, name="test-cluster", api_url="https://api.test", token="token")
    with patch("app.services.ocp.get_dynamic_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        smcp_obj = MagicMock()
        smcp_obj.metadata.name = "basic"
        smcp_obj.metadata.namespace = "istio-system"
        smcp_obj.status.get.side_effect = lambda k, d=None: {'chartVersion': '2.4.0', 'conditions': [{'type': 'Ready'}], 'readiness': {'components': {'pilot': {}, 'ingress': {}}}}.get(k, d)
        
        def get_side_effect(api_version, kind):
            res = MagicMock()
            if kind == 'ServiceMeshControlPlane':
                res.get.return_value.items = [smcp_obj]
            else:
                res.get.return_value.items = []
            return res
        mock_client.resources.get.side_effect = get_side_effect
        
        result = get_service_mesh_details(cluster)
        assert result['is_active'] == True
        assert len(result['control_planes']) == 1
        print("PASS: v2 Detection")

def test_v3_detection():
    print("\nTesting v3 (Istio/Sail) Detection...")
    cluster = Cluster(id=1, name="test-cluster-v3", api_url="https://api.test", token="token")
    with patch("app.services.ocp.get_dynamic_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        istio_obj = MagicMock()
        istio_obj.metadata.name = "default"
        istio_obj.metadata.namespace = "istio-system"
        istio_obj.spec.get.return_value = "v1.20.0"
        
        def get_side_effect(api_version, kind):
            res = MagicMock()
            if kind == 'Istio': res.get.return_value.items = [istio_obj]
            elif kind == 'ServiceMeshControlPlane': raise Exception("ResourceNotFoundError")
            else: res.get.return_value.items = []
            return res
        mock_client.resources.get.side_effect = get_side_effect
        
        result = get_service_mesh_details(cluster)
        assert result['is_active'] == True
        print("PASS: v3 Detection")

def test_traffic_serialization():
    print("\nTesting Traffic Serialization (Gateway/VS)...")
    cluster = Cluster(id=1, name="test-cluster", api_url="https://api.test", token="token")
    
    with patch("app.services.ocp.get_dynamic_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Ensure CP detection passes so we reach traffic logic
        smcp_obj = MagicMock()
        smcp_obj.metadata.name = "basic" 
        
        # Mock Gateway with list-like servers
        gw_obj = MagicMock()
        gw_obj.metadata.name = "my-gateway"
        gw_obj.metadata.namespace = "istio-system"
        # Simulate simple dicts for servers and selector
        gw_obj.spec.get.side_effect = lambda k, d=None: {
            'selector': {'istio': 'ingressgateway'},
            'servers': [{'port': {'number': 80, 'name': 'http', 'protocol': 'HTTP'}, 'hosts': ['*']}]
        }.get(k, d)

        # Mock VirtualService
        vs_obj = MagicMock()
        vs_obj.metadata.name = "my-vs"
        vs_obj.metadata.namespace = "default"
        vs_obj.spec.get.side_effect = lambda k, d=None: {
            'hosts': ['my-svc.com'],
            'gateways': ['my-gateway']
        }.get(k, d)

        def get_side_effect(api_version, kind):
            res = MagicMock()
            if kind == 'ServiceMeshControlPlane':
                res.get.return_value.items = [smcp_obj]
            elif kind == 'Gateway':
                res.get.return_value.items = [gw_obj]
            elif kind == 'VirtualService':
                res.get.return_value.items = [vs_obj]
            else:
                 res.get.return_value.items = []
            return res
        mock_client.resources.get.side_effect = get_side_effect
        
        result = get_service_mesh_details(cluster)
        
        # Check Gateways
        assert len(result['traffic']['gateways']) == 1
        gw = result['traffic']['gateways'][0]
        assert gw['name'] == "my-gateway"
        assert isinstance(gw['selector'], dict)
        assert isinstance(gw['servers'], list)
        assert len(gw['servers']) == 1
        assert isinstance(gw['servers'][0], dict)
        assert gw['servers'][0]['port']['number'] == 80
        
        # Check VS
        assert len(result['traffic']['virtual_services']) == 1
        vs = result['traffic']['virtual_services'][0]
        assert isinstance(vs['hosts'], list)
        assert vs['hosts'][0] == 'my-svc.com'
        
        print("PASS: Traffic Serialization")

if __name__ == "__main__":
    try:
        test_v2_detection()
        test_v3_detection()
        test_traffic_serialization()
        print("\nALL BACKEND TESTS PASSED")
    except Exception as e:
        print(f"\nFAILED: {e}")
        import traceback
        traceback.print_exc()
