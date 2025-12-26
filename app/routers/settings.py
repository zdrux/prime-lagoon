from fastapi import APIRouter, Depends, Request, Form
from datetime import datetime
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from app.database import get_session
from app.models import User, AppConfig
from app.dependencies import admin_required, get_current_user_optional
from typing import Optional, List, Any
from pydantic import BaseModel
import json
from app.models import Cluster, AppConfig, LicenseRule
from app.services.ocp import fetch_resources
from app.services.license import calculate_licenses

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/templates")

class UserUpdate(BaseModel):
    is_admin: bool

class LDAPConfig(BaseModel):
    host: str
    port: int
    use_ssl: bool
    auth_type: str = "SIMPLE" # SIMPLE or NTLM
    domain_prefix: Optional[str] = None

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
    clusters = session.exec(select(Cluster)).all()
    
    # Group clusters by Datacenter for the sidebar
    clusters_by_dc = {}
    for c in clusters:
        dc = c.datacenter or "Uncategorized"
        if dc not in clusters_by_dc:
            clusters_by_dc[dc] = []
        clusters_by_dc[dc].append(c)
        
    return templates.TemplateResponse("settings_users.html", {
        "request": request,
        "users": users,
        "user": user,
        "page": "settings_users",
        "clusters_by_dc": clusters_by_dc
    })

@router.get("/ldap", response_class=HTMLResponse)
def ldap_settings_page(
    request: Request, 
    session: Session = Depends(get_session),
    user: User = Depends(admin_required)
):
    config = session.exec(select(AppConfig)).all()
    cfg_dict = {c.key: c.value for c in config}
    
    clusters = session.exec(select(Cluster)).all()
    # Group clusters by Datacenter for the sidebar
    clusters_by_dc = {}
    for c in clusters:
        dc = c.datacenter or "Uncategorized"
        if dc not in clusters_by_dc:
            clusters_by_dc[dc] = []
        clusters_by_dc[dc].append(c)
    
    return templates.TemplateResponse("settings_ldap.html", {
        "request": request,
        "ldap_host": cfg_dict.get("LDAP_HOST", ""),
        "ldap_port": cfg_dict.get("LDAP_PORT", "389"),
        "ldap_ssl": cfg_dict.get("LDAP_USE_SSL") == "True",
        "ldap_enabled": cfg_dict.get("LDAP_ENABLED") == "True",
        "ldap_auth_type": cfg_dict.get("LDAP_AUTH_TYPE", "SIMPLE"),
        "ldap_domain": cfg_dict.get("LDAP_USER_DOMAIN", ""),
        "user": user,
        "page": "settings_ldap",
        "clusters_by_dc": clusters_by_dc
    })

@router.get("/db-stats", response_class=HTMLResponse)
def db_stats_page(
    request: Request, 
    session: Session = Depends(get_session),
    user: User = Depends(admin_required)
):
    clusters = session.exec(select(Cluster)).all()
    # Group clusters by Datacenter for the sidebar
    clusters_by_dc = {}
    for c in clusters:
        dc = c.datacenter or "Uncategorized"
        if dc not in clusters_by_dc:
            clusters_by_dc[dc] = []
        clusters_by_dc[dc].append(c)
        
    return templates.TemplateResponse("settings_db_stats.html", {
        "request": request,
        "user": user,
        "page": "settings_db_stats",
        "clusters_by_dc": clusters_by_dc
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
        "LDAP_USE_SSL": str(config.use_ssl),
        "LDAP_AUTH_TYPE": config.auth_type,
        "LDAP_USER_DOMAIN": config.domain_prefix
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
    
    from ldap3 import Server, Connection, ALL, Tls, NTLM
    import ssl
    
    try:
        tls = None
        if req.use_ssl:
            tls = Tls(validate=ssl.CERT_NONE)
        server = Server(req.host, port=req.port, use_ssl=req.use_ssl, tls=tls, get_info=ALL)
        
        user_str = req.test_username
        if req.domain_prefix:
            user_str = f"{req.domain_prefix}\\{req.test_username}"
            
        auth_type = req.auth_type
        
        if auth_type == "NTLM":
            conn = Connection(server, user=user_str, password=req.test_password, authentication=NTLM)
        else:
            conn = Connection(server, user=user_str, password=req.test_password)
            
        if conn.bind():
            return {"ok": True}
        else:
            return {"ok": False, "error": "Invalid test credentials (direct bind)."}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# --- License Settings ---

class LicenseConfigModel(BaseModel):
    rules_json: str

class LicensePreviewRequest(BaseModel):
    cluster_id: int
    rules_json: str

@router.get("/license", response_class=HTMLResponse)
def license_settings_page(
    request: Request, 
    session: Session = Depends(get_session),
    user: User = Depends(admin_required)
):
    today = datetime.now().strftime("%Y-%m-%d")
    clusters = session.exec(select(Cluster)).all()
    rules = session.exec(select(LicenseRule).order_by(LicenseRule.id)).all()
    
    # Group clusters by Datacenter for the sidebar
    clusters_by_dc = {}
    for c in clusters:
        dc = c.datacenter or "Uncategorized"
        if dc not in clusters_by_dc:
            clusters_by_dc[dc] = []
        clusters_by_dc[dc].append(c)
    
    return templates.TemplateResponse("settings_license.html", {
        "request": request,
        "user": user,
        "page": "settings_license",
        "rules": rules,
        "clusters": clusters,
        "clusters_by_dc": clusters_by_dc,
        "default_include": (session.get(AppConfig, "LICENSE_DEFAULT_INCLUDE") or AppConfig(value="False")).value == "True"
    })

class LicenseRuleCreate(BaseModel):
    name: str
    rule_type: str
    match_value: str
    action: str

@router.post("/api/license/rules")
def create_license_rule(rule: LicenseRuleCreate, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    db_rule = LicenseRule(
        name=rule.name,
        rule_type=rule.rule_type,
        match_value=rule.match_value,
        action=rule.action
    )
    session.add(db_rule)
    session.commit()
    session.refresh(db_rule)
    return {"ok": True, "rule": db_rule}

@router.put("/api/license/rules/{rule_id}")
def update_license_rule(rule_id: int, updated_rule: LicenseRuleCreate, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    db_rule = session.get(LicenseRule, rule_id)
    if not db_rule:
        return {"ok": False, "error": "Rule not found"}
    
    db_rule.name = updated_rule.name
    db_rule.rule_type = updated_rule.rule_type
    db_rule.match_value = updated_rule.match_value
    db_rule.action = updated_rule.action
    
    session.add(db_rule)
    session.commit()
    session.refresh(db_rule)
    return {"ok": True, "rule": db_rule}

@router.delete("/api/license/rules/{rule_id}")
def delete_license_rule(rule_id: int, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    rule = session.get(LicenseRule, rule_id)
    if not rule:
        return {"ok": False, "error": "Rule not found"}
    session.delete(rule)
    session.commit()
    return {"ok": True}

class ConfigUpdate(BaseModel):
    value: str

@router.post("/api/license/config/default")
def update_license_default_config(req: ConfigUpdate, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    key = "LICENSE_DEFAULT_INCLUDE"
    db_cfg = session.get(AppConfig, key)
    if not db_cfg:
        db_cfg = AppConfig(key=key, value=req.value)
    else:
        db_cfg.value = req.value
    session.add(db_cfg)
    session.commit()
    return {"ok": True}

class LicensePreviewRequest(BaseModel):
    cluster_id: int
    # Optional temporary rules for preview
    temp_rules: Optional[List[LicenseRuleCreate]] = None
    default_include: Optional[bool] = None

@router.post("/api/license/preview")
def preview_license_config(req: LicensePreviewRequest, session: Session = Depends(get_session), user: User = Depends(admin_required)):
    cluster = session.get(Cluster, req.cluster_id)
    if not cluster:
        return {"ok": False, "error": "Cluster not found"}
        
    try:
        nodes = fetch_resources(cluster, "v1", "Node")
        
        # Decide which rules to use
        if req.temp_rules is not None:
             # Convert Pydantic to Model
             rules = [
                 LicenseRule(name=r.name, rule_type=r.rule_type, match_value=r.match_value, action=r.action) 
                 for r in req.temp_rules
             ]
        else:
             # Use DB rules
             rules = session.exec(select(LicenseRule).where(LicenseRule.is_active == True)).all()
             
        if req.default_include is not None:
            default_include = req.default_include
        else:
            default_include = (session.get(AppConfig, "LICENSE_DEFAULT_INCLUDE") or AppConfig(value="False")).value == "True"

        result = calculate_licenses(nodes, rules, default_include=default_include)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}
