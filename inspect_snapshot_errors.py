from sqlmodel import Session, create_engine, select
from app.models import ClusterSnapshot
import json
import os

DATABASE_URL = "sqlite:///database_v13.db"
engine = create_engine(DATABASE_URL)

def inspect_latest_snapshot():
    with Session(engine) as session:
        # Get latest snapshot
        snap = session.exec(select(ClusterSnapshot).order_by(ClusterSnapshot.timestamp.desc()).limit(1)).first()
        if not snap:
            print("No snapshots found.")
            return

        print(f"Snapshot ID: {snap.id}")
        print(f"Timestamp: {snap.timestamp}")
        print(f"Status: {snap.status}")
        
        if snap.data_json:
            try:
                data = json.loads(snap.data_json)
                errors = data.get("__errors", {})
                if errors:
                    print("\nErrors found in snapshot:")
                    print(json.dumps(errors, indent=2))
                else:
                    print("\nNo '__errors' key found in data_json.")
                    
                csvs = data.get("csvs", [])
                print(f"\nCSVs count: {len(csvs)}")
                if not csvs:
                    print("CSVs list is empty.")
                    
                subs = data.get("subscriptions", [])
                print(f"Subscriptions count: {len(subs)}")
                
            except json.JSONDecodeError:
                print("Failed to decode data_json.")
        else:
            print("data_json is empty.")

if __name__ == "__main__":
    inspect_latest_snapshot()
