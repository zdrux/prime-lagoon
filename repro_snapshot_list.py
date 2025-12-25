import sys
import os
from sqlmodel import select
from app.database import get_session
from app.models import ClusterSnapshot, Cluster

# Add app to path
sys.path.append(os.getcwd())

def debug_snapshot_list():
    print("Debugging Snapshot List Query...")
    
    # Get session manually since generator
    session_gen = get_session()
    session = next(session_gen)
    
    # 1. Check Raw Counts
    try:
        count_snaps = session.exec(select(ClusterSnapshot)).all()
        print(f"Total ClusterSnapshot rows: {len(count_snaps)}")
        
        count_clusters = session.exec(select(Cluster)).all()
        print(f"Total Cluster rows: {len(count_clusters)}")
    except Exception as e:
        print(f"Error checking counts: {e}")

    # 2. Test Admin Query (as written in admin.py)
    print("\nTesting Admin Query (Outer Join)...")
    try:
        statement = select(ClusterSnapshot, Cluster.name).join(Cluster, isouter=True).order_by(ClusterSnapshot.timestamp.desc()).limit(10)
        results = session.exec(statement).all()
        
        print(f"Query returned {len(results)} rows.")
        for snap, c_name in results:
            print(f" - Snap ID: {snap.id}, Timestamp: {snap.timestamp}, Cluster Name (Live): {c_name}, Captured Name: {snap.captured_name}")
            
    except Exception as e:
        print(f"Query failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_snapshot_list()
