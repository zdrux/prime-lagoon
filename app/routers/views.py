from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from app.database import get_session
from app.models import Cluster, AuditRule, AuditBundle

import json

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["fromjson"] = json.loads

router = APIRouter(include_in_schema=False)

@router.get("/", response_class=RedirectResponse)
def root():
    return RedirectResponse(url="/dashboard")

@router.get("/admin", response_class=HTMLResponse)
def admin_view(request: Request, session: Session = Depends(get_session)):
    clusters = session.exec(select(Cluster)).all()
    clusters_by_dc = _group_clusters(clusters)
    return templates.TemplateResponse("admin.html", {
        "request": request, 
        "clusters": clusters, 
        "clusters_by_dc": clusters_by_dc,
        "page": "admin"
    })

@router.get("/audit", response_class=HTMLResponse)
def audit_view(request: Request, session: Session = Depends(get_session)):
    rules = session.exec(select(AuditRule)).all()
    bundles = session.exec(select(AuditBundle)).all()
    
    clusters = session.exec(select(Cluster)).all()
    clusters_by_dc = _group_clusters(clusters)
    
    return templates.TemplateResponse("audit.html", {
        "request": request, 
        "rules": rules, 
        "bundles": bundles,
        "clusters": clusters,
        "clusters_by_dc": clusters_by_dc,
        "page": "audit_rules"
    })

@router.get("/compliance", response_class=HTMLResponse)
def compliance_view(request: Request, session: Session = Depends(get_session)):
    clusters = session.exec(select(Cluster)).all()
    clusters_by_dc = _group_clusters(clusters)
    
    return templates.TemplateResponse("audit_run.html", {
        "request": request, 
        "page": "compliance",
        "clusters": clusters,
        "clusters_by_dc": clusters_by_dc
    })

def _group_clusters(clusters):
    by_dc = {}
    for c in clusters:
        dc = c.datacenter if c.datacenter else "Other"
        if dc not in by_dc:
            by_dc[dc] = []
        by_dc[dc].append(c)
    return by_dc

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_view(request: Request, session: Session = Depends(get_session)):
    clusters = session.exec(select(Cluster)).all()
    clusters_by_dc = _group_clusters(clusters)
        
    # Ensure Azure and HCI keys exist for consistent ordering if desired, or just pass dict
    # Let's just pass the dict and iterate in template
    return templates.TemplateResponse("dashboard.html", {
        "request": request, 
        "clusters": clusters, # Keep flat list for other uses if needed
        "clusters_by_dc": clusters_by_dc,
        "page": "dashboard"
    })
