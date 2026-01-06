import logging
from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select
from app.database import engine
from app.models import AppConfig
from app.services.poller import poll_all_clusters

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

def get_scheduler_settings():
    """Reads scheduler settings from DB."""
    settings = {"interval": 15, "enable_vacuum": True}
    try:
        with Session(engine) as session:
            # Interval
            c_int = session.get(AppConfig, "POLL_INTERVAL_MINUTES")
            if c_int: settings["interval"] = int(c_int.value)
            
            # Vacuum
            c_vac = session.get(AppConfig, "ENABLE_DB_VACUUM")
            if c_vac: settings["enable_vacuum"] = (c_vac.value.lower() == 'true')
    except Exception as e:
        logger.error(f"Error reading settings: {e}")
    
    return settings

def start_scheduler():
    """Starts the scheduler with the configured interval."""
    settings = get_scheduler_settings()
    logger.info(f"Initializing scheduler. Interval: {settings['interval']}m, Vacuum: {settings['enable_vacuum']}")
    
    # Add Poller Job
    scheduler.add_job(
        poll_all_clusters, 
        'interval', 
        minutes=settings['interval'], 
        id='cluster_poller',
        replace_existing=True
    )
    
    # Add Maintenance Job
    from app.services.maintenance import run_vacuum_task
    if settings['enable_vacuum']:
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

def refresh_jobs():
    """Updates jobs with new settings from DB."""
    settings = get_scheduler_settings()
    logger.info(f"Refreshing scheduler jobs. Settings: {settings}")
    
    try:
        # 1. Update Poller
        scheduler.reschedule_job('cluster_poller', trigger='interval', minutes=settings['interval'])
        
        # 2. Update Vacuum
        # Check if job exists
        job = scheduler.get_job('db_vacuum')
        from app.services.maintenance import run_vacuum_task
        
        if settings['enable_vacuum']:
            if not job:
                scheduler.add_job(run_vacuum_task, 'cron', day_of_week='sun', hour=0, minute=0, id='db_vacuum')
        else:
            if job:
                scheduler.remove_job('db_vacuum')
                
    except Exception as e:
         logger.error(f"Failed to refresh jobs: {e}")
