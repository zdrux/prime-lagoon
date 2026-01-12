import sys
import os
sys.path.append(os.getcwd())

from app.models import User

def test_instantiation():
    try:
        # mimics admin.py
        print("Testing User instantiation with is_admin arg...")
        u = User(username="test", role="admin", is_admin=True)
        print(f"User created: {u}")
        print(f"User is_admin property: {u.is_admin}")
        print(f"User is_admin_db field: {u.is_admin_db}")
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    test_instantiation()
