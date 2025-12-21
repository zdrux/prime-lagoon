from fastapi import APIRouter, Depends, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from app.database import get_session
from app.models import User, AppConfig
from app.services.auth import authenticate_ldap
from app.dependencies import serializer, is_ldap_enabled
import os

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, session: Session = Depends(get_session)):
    if not is_ldap_enabled(session):
        return RedirectResponse(url="/")
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login")
def login(
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session)
):
    if not is_ldap_enabled(session):
        return RedirectResponse(url="/", status_code=303)
        
    if authenticate_ldap(username, password, session):
        # Successful login
        db_user = session.exec(select(User).where(User.username == username)).first()
        if not db_user:
            # First user to login should be made admin
            has_admins = session.exec(select(User).where(User.is_admin == True)).first()
            db_user = User(username=username, is_admin=not bool(has_admins))
            session.add(db_user)
            session.commit()
            session.refresh(db_user)
            
        token = serializer.dumps(username)
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="session_id", value=token, httponly=True)
        return response
    else:
        return RedirectResponse(url="/login?error=invalid", status_code=303)

@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login")
    response.delete_cookie("session_id")
    return response
