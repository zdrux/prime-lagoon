import os
from sqlmodel import SQLModel, create_engine, Session

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///database_v13.db")

connect_args = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    
    # Migration: Add 'order' column to LicenseRule if missing
    from sqlmodel import text
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
    except Exception as e:
        print(f"MIGRATION ERROR: {e}")

def get_session():
    with Session(engine) as session:
        yield session
