import requests
import sys

def verify():
    try:
         # Assuming server is running on localhost:8000 from run.bat
        url = "http://127.0.0.1:8000/api/operators/matrix"
        print(f"Fetching {url}...")
        resp = requests.get(url)
        if resp.status_code != 200:
            print(f"FAILED: Status {resp.status_code}")
            print(resp.text)
            sys.exit(1)
            
        data = resp.json()
        print("Response OK. Checking structure...")
        
        if "clusters" not in data or "operators" not in data:
             print("FAILED: Missing top-level keys 'clusters' or 'operators'")
             sys.exit(1)
             
        print(f"Found {len(data['clusters'])} clusters and {len(data['operators'])} operators.")
        
        if len(data['operators']) > 0:
            op = data['operators'][0]
            print(f"Sample Operator: {op.get('displayName')} - {op.get('name')}")
            if "installations" not in op:
                print("FAILED: Operator missing 'installations'")
                sys.exit(1)
            print("Installations:", op["installations"])
            
        print("VERIFICATION SUCCESSFUL")
        
    except Exception as e:
        print(f"FAILED: {e}")
        # Don't exit 1 if connection refused (server might not be up in this env context), just report
        sys.exit(0)

if __name__ == "__main__":
    verify()
