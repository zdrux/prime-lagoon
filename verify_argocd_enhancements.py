
import sys
import os
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.getcwd())

from app.services.ocp import get_argocd_application_details
from app.models import Cluster

def test_get_argocd_application_details():
    print("Testing get_argocd_application_details...")
    
    # Mock Cluster
    cluster = Cluster(
        id=1,
        name="test-cluster",
        api_url="https://api.test.com:6443",
        token="test-token"
    )
    
    # Mock Dynamic Client
    with patch('app.services.ocp.get_dynamic_client') as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        mock_app_api = MagicMock()
        mock_client.resources.get.return_value = mock_app_api
        
        # Mock Application Resource
        mock_app = MagicMock()
        mock_app.metadata.name = "test-app"
        mock_app.metadata.namespace = "test-ns"
        
        mock_app.spec.project = "test-project"
        
        # Mock Status
        mock_app.status.summary.images = ["image:v1", "image:v2"]
        mock_app.status.summary.externalURLs = ["http://app.com"]
        
        mock_app.status.sync.status = "Synced"
        mock_app.status.sync.revision = "abcdef123456"
        
        mock_app.status.health.status = "Healthy"
        mock_app.status.health.message = "OK"
        
        # Mock History
        mock_history_item = MagicMock()
        mock_history_item.to_dict.return_value = {
            "revision": "abcdef",
            "deployedAt": "2023-01-01T12:00:00Z",
            "source": {"repoURL": "http://git.com"}
        }
        mock_app.status.history = [mock_history_item]
        
        mock_app_api.get.return_value = mock_app
        
        # Execute
        details = get_argocd_application_details(cluster, "test-ns", "test-app")
        
        # Assertions
        assert details['name'] == "test-app"
        assert details['project'] == "test-project"
        assert "image:v1" in details['summary']['images']
        assert details['sync']['status'] == "Synced"
        assert details['health']['status'] == "Healthy"
        assert len(details['history']) == 1
        
        print("SUCCESS: Full Data test passed.")
        
        # --- TEST 2: Missing/None Fields ---
        print("Testing with None/Missing fields...")
        mock_app_none = MagicMock()
        mock_app_none.metadata.name = "none-app"
        mock_app_none.metadata.namespace = "test-ns"
        mock_app_none.spec = None # None spec
        mock_app_none.status.summary = None # None summary
        mock_app_none.status.sync = None
        mock_app_none.status.health = None
        mock_app_none.status.history = None # None history
        mock_app_none.status.conditions = None
        mock_app_none.status.operationState = None

        mock_app_api.get.return_value = mock_app_none

        details_none = get_argocd_application_details(cluster, "test-ns", "none-app")
        assert details_none['name'] == "none-app"
        assert details_none['project'] == "default"
        assert details_none['summary']['images'] == []
        assert details_none['history'] == []
        assert details_none['conditions'] == []
        
        print("SUCCESS: None/Missing fields test passed.")

        # --- TEST 3: Partial Fields (Empty lists) ---
        print("Testing with Empty Lists...")
        mock_app_empty = MagicMock()
        mock_app_empty.metadata.name = "empty-app"
        mock_app_empty.metadata.namespace = "test-ns"
        mock_app_empty.status.summary.images = []
        mock_app_empty.status.history = []
        mock_app_empty.status.conditions = []
        
        mock_app_api.get.return_value = mock_app_empty
        
        details_empty = get_argocd_application_details(cluster, "test-ns", "empty-app")
        assert details_empty['summary']['images'] == []
        assert details_empty['history'] == []
        
        print("SUCCESS: Empty Lists test passed.")

if __name__ == "__main__":
    test_get_argocd_application_details()
