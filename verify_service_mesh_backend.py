
import sys
import os
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.getcwd())

from app.services.ocp import get_service_mesh_details
from app.models import Cluster

def test_v2_detection():
    print("Testing v2 (Maistra) Detection...")
    
    # Mock Cluster
    cluster = Cluster(id=1, name="test-cluster", api_url="https://api.test", token="token")
    
    with patch("app.services.ocp.get_dynamic_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Mock SMCP v2 Resource
        smcp_resource = MagicMock()
        mock_client.resources.get.return_value = smcp_resource
        
        # Create a mock SMCP object
        smcp_obj = MagicMock()
        smcp_obj.metadata.name = "basic"
        smcp_obj.metadata.namespace = "istio-system"
        smcp_obj.status.get.side_effect = lambda k, d=None: {
            'chartVersion': '2.4.0',
            'conditions': [{'type': 'Ready'}],
            'readiness': {'components': {'pilot': {}, 'ingress': {}}}
        }.get(k, d)
        
        # Setup get() return
        # First call is for v2 SMCP
        def get_side_effect(api_version, kind):
            res = MagicMock()
            if kind == 'ServiceMeshControlPlane':
                res.get.return_value.items = [smcp_obj]
            else:
                res.get.return_value.items = [] # Empty for others
            return res
            
        mock_client.resources.get.side_effect = get_side_effect
        
        # Run
        result = get_service_mesh_details(cluster)
        
        # Assert
        print(f"Result: {result}")
        assert result['is_active'] == True
        assert len(result['control_planes']) == 1
        assert result['control_planes'][0]['type'] == 'Maistra v2'
        assert result['control_planes'][0]['version'] == '2.4.0'
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
        istio_obj.spec.get.return_value = "v1.20.0" # version
        
        def get_side_effect(api_version, kind):
            res = MagicMock()
            if kind == 'Istio': # v3
                res.get.return_value.items = [istio_obj]
            elif kind == 'ServiceMeshControlPlane':
                raise Exception("ResourceNotFoundError") # Simulate missing v2 CRD
            else:
                 res.get.return_value.items = []
            return res
            
        mock_client.resources.get.side_effect = get_side_effect
        
        result = get_service_mesh_details(cluster)
        
        assert result['is_active'] == True
        assert result['control_planes'][0]['type'] == 'Istio v3'
        assert result['control_planes'][0]['version'] == 'v1.20.0'
        print("PASS: v3 Detection")

if __name__ == "__main__":
    try:
        test_v2_detection()
        test_v3_detection()
        print("\nALL BACKEND TESTS PASSED")
    except Exception as e:
        print(f"\nFAILED: {e}")
        import traceback
        traceback.print_exc()
