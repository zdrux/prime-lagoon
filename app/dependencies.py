from fastapi import Request, HTTPException, Depends
from itsdangerous import URLSafeSerializer
from app.database import get_session
from sqlmodel import Session, select
from app.models import User, AppConfig
from typing import Optional

SECRET_KEY = "antigravity-secret-key" # In production, this should be an env var
serializer = URLSafeSerializer(SECRET_KEY, salt="auth-session")

def is_ldap_enabled(session: Session) -> bool:
    enabled = session.exec(select(AppConfig).where(AppConfig.key == "LDAP_ENABLED")).first()
    return bool(enabled and enabled.value == "True")

def get_current_user_optional(request: Request, session: Session = Depends(get_session)) -> Optional[User]:
    # If LDAP is not configured, everyone is an admin
    if not is_ldap_enabled(session):
        return User(username="anonymous", is_admin=True)
        
    session_id = request.cookies.get("session_id")
    if not session_id:
        return None
        
    try:
        username = serializer.loads(session_id)
        user = session.exec(select(User).where(User.username == username)).first()
        return user
    except:
        return None

def get_current_user(request: Request, user: Optional[User] = Depends(get_current_user_optional)) -> User:
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

def admin_required(user: User = Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin permissions required")
    return user
