from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.database import create_db_and_tables

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield

app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

from app.routers import admin, dashboard, views, audit

app.include_router(views.router)
app.include_router(admin.router)
app.include_router(dashboard.router)
app.include_router(audit.router)
