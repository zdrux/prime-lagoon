from sqlmodel import Session, create_engine, text
from app.database import engine

def migrate():
    with Session(engine) as session:
        try:
            # Check if column exists
            session.exec(text("SELECT argocd_json FROM clustersnapshot LIMIT 1"))
            print("Column argocd_json already exists.")
        except Exception:
            print("Column argocd_json missing. Adding it...")
            try:
                session.connection().execute(text("ALTER TABLE clustersnapshot ADD COLUMN argocd_json TEXT"))
                session.commit()
                print("Migration successful.")
            except Exception as e:
                print(f"Migration failed: {e}")

if __name__ == "__main__":
    migrate()
