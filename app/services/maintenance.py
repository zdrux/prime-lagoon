import logging
import time
from sqlalchemy import text
from sqlmodel import Session
from app.database import engine

logger = logging.getLogger(__name__)

def run_vacuum_task():
    """
    Executes a VACUUM command on the SQLite database to reclaim unused space.
    This operation can be time-consuming and blocking, so it should be run 
    in a background thread or process.
    """
    start_time = time.time()
    logger.info("Starting database optimization (VACUUM)...")
    
    try:
        with Session(engine) as session:
            session.execute(text("VACUUM"))
            session.commit()
            
        duration = time.time() - start_time
        logger.info(f"Database optimization completed successfully in {duration:.2f} seconds.")
    except Exception as e:
        logger.error(f"Database optimization failed: {e}")
