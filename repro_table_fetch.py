from sqlmodel import Session, create_engine, select
from app.models import Cluster
from app.services.ocp import get_dynamic_client
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

DATABASE_URL = "sqlite:///database_v13.db"
engine = create_engine(DATABASE_URL)

def test_fetch_fixed():
    with Session(engine) as session:
        cluster = session.exec(select(Cluster).limit(1)).first()
        if not cluster:
            print("No cluster found.")
            return

        print(f"Testing fetch for cluster: {cluster.name}")
        
        # This header is now the one in ocp.py
        h = 'application/json;as=Table;g=meta.k8s.io;v=v1'
        print(f"\nTesting Fixed Header: {h}")
        try:
            dyn_client = get_dynamic_client(cluster)
            resource_api = dyn_client.resources.get(api_version="operators.coreos.com/v1alpha1", kind="ClusterServiceVersion")
            
            resp = resource_api.get(header_params={'Accept': h}, _request_timeout=30)
            
            resp_dict = resp.to_dict() if hasattr(resp, 'to_dict') else resp
            
            print(f"SUCCESS with {h}")
            if isinstance(resp_dict, dict):
                print(f"Returned Kind: {resp_dict.get('kind')}")
                if resp_dict.get("kind") == "Table":
                    print(f"Rows: {len(resp_dict.get('rows', []))}")
                else:
                    # Verify fallback logic
                    items = resp_dict.get("items", [])
                    print(f"Returned as List, item count: {len(items)}")
            else:
                 print(f"Returned type: {type(resp_dict)}")
                 
        except Exception as e:
            print(f"Failed with {h}: {e}")

if __name__ == "__main__":
    test_fetch_fixed()
