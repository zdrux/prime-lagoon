from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.database import create_db_and_tables

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    # Debug DB Path
    from app.database import DATABASE_URL
    print(f"DEBUG: Active DATABASE_URL = {DATABASE_URL}")
    
    # Start Scheduler
    from app.services.scheduler import start_scheduler
    start_scheduler()
    yield

app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

from app.routers import admin, dashboard, views, audit, auth, settings

app.include_router(views.router)
app.include_router(admin.router)
app.include_router(dashboard.router)
app.include_router(audit.router)
app.include_router(auth.router)
app.include_router(settings.router)
