import sqlite3
import os

DB_FILE = "database_v13.db"

def inspect_db():
    if not os.path.exists(DB_FILE):
        print(f"Database {DB_FILE} not found!")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. List Tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print("Tables:", [t[0] for t in tables])
    
    # 2. Count Snapshots
    try:
        cursor.execute("SELECT count(*) FROM clustersnapshot")
        count = cursor.fetchone()[0]
        print(f"Count in clustersnapshot: {count}")
    except Exception as e:
        print(f"Error querying clustersnapshot: {e}")

    # 3. Simple Select from Snapshots
    try:
        cursor.execute("SELECT id, timestamp, status FROM clustersnapshot LIMIT 5")
        rows = cursor.fetchall()
        for r in rows:
            print(f"Row: {r}")
    except Exception as e:
        print(f"Error selecting from clustersnapshot: {e}")

    conn.close()

if __name__ == "__main__":
    inspect_db()
