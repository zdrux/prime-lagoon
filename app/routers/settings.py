from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from app.database import get_session
from app.models import User, AppConfig
from app.dependencies import admin_required, get_current_user_optional
from typing import Optional, List
from pydantic import BaseModel

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/templates")

class UserUpdate(BaseModel):
    is_admin: bool

class LDAPConfig(BaseModel):
    host: str
    port: int
    use_ssl: bool

class LDAPTestRequest(LDAPConfig):
    test_username: str
    test_password: str

@router.get("", response_class=HTMLResponse)
def settings_redirect():
    return RedirectResponse(url="/settings/users")

@router.get("/users", response_class=HTMLResponse)
def user_management_page(
    request: Request, 
    session: Session = Depends(get_session),
    user: User = Depends(admin_required)
):
    users = session.exec(select(User)).all()
    return templates.TemplateResponse("settings_users.html", {
        "request": request,
        "users": users,
        "user": user,
        "page": "settings_users"
    })

@router.get("/ldap", response_class=HTMLResponse)
def ldap_settings_page(
    request: Request, 
    session: Session = Depends(get_session),
    user: User = Depends(admin_required)
):
    config = session.exec(select(AppConfig)).all()
    cfg_dict = {c.key: c.value for c in config}
    
    return templates.TemplateResponse("settings_ldap.html", {
        "request": request,
        "ldap_host": cfg_dict.get("LDAP_HOST", ""),
        "ldap_port": cfg_dict.get("LDAP_PORT", "389"),
        "ldap_ssl": cfg_dict.get("LDAP_USE_SSL") == "True",
        "ldap_enabled": cfg_dict.get("LDAP_ENABLED") == "True",
        "user": user,
        "page": "settings_ldap"
    })

@router.get("/api/users", response_model=List[User])
def get_users(session: Session = Depends(get_session), user: User = Depends(admin_required)):
    return session.exec(select(User)).all()

@router.post("/api/users/{user_id}/toggle_admin")
def toggle_admin(user_id: int, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    target_user = session.get(User, user_id)
    if not target_user:
        return {"error": "User not found"}
    
    # Don't let users remove their own admin rights if they are the last admin
    if target_user.id == user.id:
        other_admins = session.exec(select(User).where(User.is_admin == True, User.id != user.id)).first()
        if not other_admins:
            return {"error": "Cannot remove your own admin rights as you are the last admin."}

    target_user.is_admin = not target_user.is_admin
    session.add(target_user)
    session.commit()
    return {"ok": True, "is_admin": target_user.is_admin}

@router.post("/api/ldap")
def update_ldap(config: LDAPConfig, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    settings_map = {
        "LDAP_HOST": config.host,
        "LDAP_PORT": str(config.port),
        "LDAP_USE_SSL": str(config.use_ssl)
    }
    
    for key, value in settings_map.items():
        if value is not None:
            db_cfg = session.get(AppConfig, key)
            if not db_cfg:
                db_cfg = AppConfig(key=key, value=value)
            else:
                db_cfg.value = value
            session.add(db_cfg)
    
    session.commit()
    return {"ok": True}

@router.post("/api/ldap/toggle")
def toggle_ldap(enabled: bool, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    db_cfg = session.get(AppConfig, "LDAP_ENABLED")
    if not db_cfg:
        db_cfg = AppConfig(key="LDAP_ENABLED", value=str(enabled))
    else:
        db_cfg.value = str(enabled)
    session.add(db_cfg)
    session.commit()
    return {"ok": True, "ldap_enabled": enabled}

@router.post("/api/ldap/test")
def test_ldap(req: LDAPTestRequest, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    # We temporarily inject these settings for the auth service to use
    # Or just perform the logic directly here. Better to reuse auth service but it expects settings in DB.
    # Let's mock a context or just pass config to a lower level auth function.
    
    from app.services.auth import authenticate_ldap
    
    # Save temporarily to session? No, that's risky. 
    # Let's refactor authenticate_ldap to accept optional override config.
    # For now, I'll just write the test logic here to keep it simple.
    
    from ldap3 import Server, Connection, ALL, Tls
    import ssl
    
    try:
        tls = None
        if req.use_ssl:
            tls = Tls(validate=ssl.CERT_NONE)
        server = Server(req.host, port=req.port, use_ssl=req.use_ssl, tls=tls, get_info=ALL)
        
        conn = Connection(server, user=req.test_username, password=req.test_password)
        if conn.bind():
            return {"ok": True}
        else:
            return {"ok": False, "error": "Invalid test credentials (direct bind)."}
    except Exception as e:
        return {"ok": False, "error": str(e)}
