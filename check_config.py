from sqlmodel import Session, select
from app.database import engine
from app.models import AppConfig

with Session(engine) as session:
    cfg = session.get(AppConfig, "LICENSE_DEFAULT_INCLUDE")
    if cfg:
        print(f"LICENSE_DEFAULT_INCLUDE value: '{cfg.value}'")
    else:
        print("LICENSE_DEFAULT_INCLUDE not found in database.")
