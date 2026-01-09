import requests
import time
from datetime import datetime

BASE_URL = "http://localhost:8000"

def test_cache():
    print("--- Dashboard Cache Verification ---")
    
    # 1. First request - should trigger live fetch and populate cache
    print("Request 1: Fetching live data...")
    start = time.time()
    resp1 = requests.get(f"{BASE_URL}/api/dashboard/summary")
    duration1 = time.time() - start
    data1 = resp1.json()
    ts1 = data1.get('timestamp')
    print(f"Timestamp 1: {ts1}")
    print(f"Duration 1: {duration1:.2f}s")
    
    # 2. Second request - should hit cache immediately
    print("\nRequest 2: Fetching cached data...")
    start = time.time()
    resp2 = requests.get(f"{BASE_URL}/api/dashboard/summary")
    duration2 = time.time() - start
    data2 = resp2.json()
    ts2 = data2.get('timestamp')
    print(f"Timestamp 2: {ts2}")
    print(f"Duration 2: {duration2:.2f}s")
    
    if ts1 == ts2 and duration2 < 0.1:
        print("\nSUCCESS: Cache hit confirmed (same timestamp, sub-100ms response).")
    else:
        print("\nFAILURE: Cache did not behave as expected.")
        print(f"TS match: {ts1 == ts2}")
        print(f"Duration 2: {duration2:.2f}s")

    # 3. Test Bypassing Cache (Time Travel)
    print("\nRequest 3: Time Travel (should bypass cache)...")
    resp3 = requests.get(f"{BASE_URL}/api/dashboard/summary?snapshot_time=2026-01-01T12:00:00")
    data3 = resp3.json()
    ts3 = data3.get('timestamp')
    print(f"Timestamp 3 (Snapshot): {ts3}")
    # 4. Test Global Refresh Bypass
    print("\nRequest 4: Global Refresh (should bypass cache)...")
    start = time.time()
    resp4 = requests.get(f"{BASE_URL}/api/dashboard/summary?refresh=true")
    duration4 = time.time() - start
    data4 = resp4.json()
    ts4 = data4.get('timestamp')
    print(f"Timestamp 4: {ts4}")
    print(f"Duration 4: {duration4:.2f}s")
    
    if ts4 != ts1:
        print("SUCCESS: Global refresh bypassed cache.")
    else:
        print("FAILURE: Global refresh returned cached data.")

if __name__ == "__main__":
    test_cache()
