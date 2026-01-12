import sys
import os

# Ensure app is in path
sys.path.append(os.getcwd())

from sqlmodel import Session, select
from app.database import engine
from app.models import User
from sqlalchemy.exc import IntegrityError

def reproduce():
    print("Attempting to create a user...")
    with Session(engine) as session:
        try:
            # Check if user exists and delete if so (from previous runs)
            existing = session.exec(select(User).where(User.username == "test_repro_user")).first()
            if existing:
                session.delete(existing)
                session.commit()
            
            # Create user - this should trigger the IntegrityError if is_admin is missing from insert
            new_user = User(username="test_repro_user", role="user")
            
            # verify property works (though irrelevant for DB)
            assert new_user.is_admin == False
            
            session.add(new_user)
            session.commit()
            print("SUCCESS: User created without error (Unexpected if bug exists)")
            
            # Cleanup
            session.refresh(new_user)
            session.delete(new_user)
            session.commit()
            
        except IntegrityError as e:
            print(f"CAUGHT EXPECTED ERROR: {e}")
        except Exception as e:
            print(f"Caught unexpected error: {type(e)}: {e}")

if __name__ == "__main__":
    reproduce()
