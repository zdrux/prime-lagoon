import sys
import os
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

# Ensure we can import app
sys.path.append(os.getcwd())

from app.main import app
from app.dependencies import get_current_user, get_session
from app.models import User

# Setup Test DB
engine = create_engine(
    "sqlite://", 
    connect_args={"check_same_thread": False}, 
    poolclass=StaticPool
)
SQLModel.metadata.create_all(engine)

def get_session_override():
    with Session(engine) as session:
        yield session

app.dependency_overrides[get_session] = get_session_override

client = TestClient(app)

def test_admin_access():
    print("Testing Admin Access...")
    admin_user = User(username="admin_test", role="admin")
    app.dependency_overrides[get_current_user] = lambda: admin_user
    
    # Test Read
    resp = client.get("/api/admin/clusters/")
    assert resp.status_code == 200, f"Admin Read Failed: {resp.text}"
    
    # Test Write (allowed)
    # We won't actually create because it might trigger background tasks or migrations
    # But we check ensuring we don't get 403.
    # We can check a delete endpoint passing invalid ID, expecting 404 not 403.
    resp = client.delete("/api/admin/clusters/999999")
    assert resp.status_code == 404, f"Admin Write Permission Failed (Expected 404, got {resp.status_code})"
    
    # View Access
    resp = client.get("/admin")
    assert resp.status_code == 200
    
    # Check Settings Page (ensures template syntax is correct)
    resp = client.get("/settings/users")
    assert resp.status_code == 200, f"Settings/Users View Failed (likely template error): {resp.text}"
    print("SUCCESS: Admin Access")

def test_operator_access():
    print("Testing Operator Access...")
    op_user = User(username="op_test", role="operator")
    app.dependency_overrides[get_current_user] = lambda: op_user
    
    # Test Read (allowed)
    resp = client.get("/api/admin/clusters/")
    assert resp.status_code == 200, f"Operator Read Failed: {resp.text}"
    
    # Test Write (forbidden)
    resp = client.delete("/api/admin/clusters/999999")
    assert resp.status_code == 403, f"Operator Write Should be Forbidden (Got {resp.status_code})"
    
    # View Access (allowed)
    resp = client.get("/admin")
    assert resp.status_code == 200, f"Operator View Access Failed: {resp.text}"
    
    # Check if UI hides buttons
    # In Operator view, we expect NOT to see button with onclick="deleteCluster..." and class "btn-danger"
    # Note: Javascript variable IS_ADMIN should be false.
    if 'const IS_ADMIN = false' not in resp.text:
         print("WARNING: IS_ADMIN not correctly set to false in HTML")
         
    if 'onclick="deleteCluster' in resp.text and 'btn-danger' in resp.text:
         # Check context: might be commented out? No.
         # Jinja should remove it.
         print("WARNING: Delete Cluster button found in HTML (Should be removed by Jinja)")
             
    print("SUCCESS: Operator Access")

def test_user_access():
    print("Testing Regular User Access...")
    reg_user = User(username="user_test", role="user")
    app.dependency_overrides[get_current_user] = lambda: reg_user
    
    # Test Read (forbidden - operator_allowed blocks it)
    resp = client.get("/api/admin/clusters/")
    assert resp.status_code == 403, f"User Read Should be Forbidden (Got {resp.status_code})"
    
    print("SUCCESS: User Access")

def test_user_management():
    print("Testing User Management Endpoint...")
    admin_user = User(username="admin_mgr", role="admin")
    sess = Session(engine)
    sess.add(admin_user)
    sess.commit()
    
    # Target User
    target_user = User(username="target_u", role="user")
    sess.add(target_user)
    sess.commit()
    sess.refresh(target_user)
    
    app.dependency_overrides[get_current_user] = lambda: admin_user
    
    # 1. Update Role to Operator
    resp = client.post(f"/settings/api/users/{target_user.id}/role", json={"role": "operator"})
    assert resp.status_code == 200, f"Update Role Failed: {resp.text}"
    assert resp.json()["user"]["role"] == "operator"
    
    # 2. Verify DB state
    sess.refresh(target_user)
    assert target_user.role == "operator"
    assert target_user.is_admin == False
    
    # 3. Update Role to Admin
    resp = client.post(f"/settings/api/users/{target_user.id}/role", json={"role": "admin"})
    assert resp.status_code == 200
    
    sess.refresh(target_user)
    assert target_user.role == "admin"
    assert target_user.is_admin == True
    
    print("SUCCESS: User Management")

if __name__ == "__main__":
    try:
        test_admin_access()
        test_operator_access()
        test_user_access()
        test_user_management()
        print("ALL TESTS PASSED")
    except Exception as e:
        print(f"TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
