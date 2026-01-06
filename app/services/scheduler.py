import logging
from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select
from app.database import engine
from app.models import AppConfig
from app.services.poller import poll_all_clusters

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

def get_poll_interval():
    """Reads the polling interval from DB or returns default."""
    default_interval = 15
    try:
        with Session(engine) as session:
            config = session.get(AppConfig, "POLL_INTERVAL_MINUTES")
            if config:
                return int(config.value)
    except Exception as e:
        logger.error(f"Error reading poll interval: {e}")
    
    return default_interval

def start_scheduler():
    """Starts the scheduler with the configured interval."""
    interval = get_poll_interval()
    logger.info(f"Initializing scheduler with interval: {interval} minutes")
    
    # Add the job
    scheduler.add_job(
        poll_all_clusters, 
        'interval', 
        minutes=interval, 
        id='cluster_poller',
        replace_existing=True
    )
    
    # Add maintenance job (Weekly Vacuum)
    from app.services.maintenance import run_vacuum_task
    scheduler.add_job(
        run_vacuum_task,
        'cron',
        day_of_week='sun',
        hour=0,
        minute=0,
        id='db_vacuum',
        replace_existing=True
    )
    
    if not scheduler.running:
        scheduler.start()

def reschedule_job():
    """Updates the job with the new interval from DB."""
    interval = get_poll_interval()
    logger.info(f"Rescheduling poller to interval: {interval} minutes")
    
    try:
        scheduler.reschedule_job('cluster_poller', trigger='interval', minutes=interval)
    except Exception as e:
         logger.error(f"Failed to reschedule job: {e}")
