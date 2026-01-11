import os
from sqlmodel import SQLModel, create_engine, Session, text

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///database_v13.db")

connect_args = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

def create_db_and_tables():
    # Enable WAL mode for better concurrency
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL;"))
        conn.commit()

    SQLModel.metadata.create_all(engine)
    
    # Migration: Add 'order' column to LicenseRule if missing
    try:
        with engine.connect() as conn:
            # Check if column exists (SQLite specific)
            res = conn.execute(text("PRAGMA table_info(licenserule)"))
            columns = [row[1] for row in res.fetchall()]
            
            if columns and "order" not in columns:
                print("MIGRATION: Adding 'order' column to licenserule table...")
                conn.execute(text('ALTER TABLE licenserule ADD COLUMN "order" INTEGER DEFAULT 0'))
                conn.commit()
                print("MIGRATION: Success.")
            # Migration 2: Add analytics columns to clustersnapshot if missing
            res = conn.execute(text("PRAGMA table_info(clustersnapshot)"))
            columns = [row[1] for row in res.fetchall()]
            if columns:
                to_add = {
                    "project_count": 'INTEGER DEFAULT 0',
                    "machineset_count": 'INTEGER DEFAULT 0',
                    "machine_count": 'INTEGER DEFAULT 0',
                    "license_count": 'INTEGER DEFAULT 0',
                    "licensed_node_count": 'INTEGER DEFAULT 0'
                }
                for col, sql_type in to_add.items():
                    if col not in columns:
                        print(f"MIGRATION: Adding '{col}' column to clustersnapshot table...")
                        conn.execute(text(f'ALTER TABLE clustersnapshot ADD COLUMN "{col}" {sql_type}'))
                
                # New Migration for ServiceMesh/ArgoCD
                new_cols = {
                    "service_mesh_json": "TEXT",
                    "argocd_json": "TEXT"
                }
                for col, sql_type in new_cols.items():
                    if col not in columns:
                         print(f"MIGRATION: Adding '{col}' column to clustersnapshot table...")
                         conn.execute(text(f'ALTER TABLE clustersnapshot ADD COLUMN "{col}" {sql_type}'))

                conn.commit()
            
            # Migration 3: Add 'is_enabled' column to auditrule if missing
            res = conn.execute(text("PRAGMA table_info(auditrule)"))
            columns = [row[1] for row in res.fetchall()]
            if columns and "is_enabled" not in columns:
                print("MIGRATION: Adding 'is_enabled' column to auditrule table...")
                conn.execute(text('ALTER TABLE auditrule ADD COLUMN "is_enabled" BOOLEAN DEFAULT 1'))
                conn.commit()
                print("MIGRATION: Success.")
            
            # Migration 4: Add DASHBOARD_CACHE_TTL_MINUTES default if missing
            res = conn.execute(text("SELECT value FROM appconfig WHERE key = 'DASHBOARD_CACHE_TTL_MINUTES'"))
            if not res.fetchone():
                print("MIGRATION: Adding DASHBOARD_CACHE_TTL_MINUTES default...")
                conn.execute(text("INSERT INTO appconfig (key, value) VALUES ('DASHBOARD_CACHE_TTL_MINUTES', '15')"))
                conn.commit()

    except Exception as e:
        print(f"MIGRATION ERROR: {e}")

def get_session():
    with Session(engine) as session:
        yield session
