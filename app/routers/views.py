from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from app.database import get_session
from app.models import Cluster, AuditRule, AuditBundle, User

from app.dependencies import get_current_user_optional, admin_required, is_ldap_enabled, operator_allowed
import json

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["fromjson"] = json.loads

router = APIRouter(include_in_schema=False)

@router.get("/", response_class=RedirectResponse)
def root():
    return RedirectResponse(url="/dashboard")

@router.get("/admin", response_class=HTMLResponse)
def admin_view(request: Request, tab: str = 'clusters', session: Session = Depends(get_session), user: User = Depends(operator_allowed)):
    from app.models import AppConfig
    clusters = session.exec(select(Cluster).order_by(Cluster.name)).all()
    clusters_by_dc = _group_clusters_with_status(clusters, session)
    
    # Get configs
    poll_int_config = session.get(AppConfig, "POLL_INTERVAL_MINUTES")
    poll_interval = int(poll_int_config.value) if poll_int_config else 15
    
    retention_config = session.get(AppConfig, "SNAPSHOT_RETENTION_DAYS")
    retention_days = int(retention_config.value) if retention_config else 30
    
    dashboard_ttl = session.get(AppConfig, "DASHBOARD_CACHE_TTL_MINUTES")
    dashboard_ttl_val = int(dashboard_ttl.value) if dashboard_ttl else 15

    collect_olm_config = session.get(AppConfig, "SNAPSHOT_COLLECT_OLM")
    collect_olm = collect_olm_config.value.lower() == "true" if collect_olm_config else True

    run_compliance_config = session.get(AppConfig, "SNAPSHOT_COLLECT_COMPLIANCE")
    run_compliance = run_compliance_config.value.lower() == "true" if run_compliance_config else False

    enable_vacuum_config = session.get(AppConfig, "ENABLE_DB_VACUUM")
    enable_vacuum = enable_vacuum_config.value.lower() == "true" if enable_vacuum_config else True
    
    return templates.TemplateResponse("admin.html", {
        "request": request, 
        "clusters": clusters, 
        "clusters_by_dc": clusters_by_dc,
        "page": "admin",
        "active_tab": tab,
        "poll_interval": poll_interval,
        "retention_days": retention_days,
        "dashboard_cache_ttl": dashboard_ttl_val,
        "collect_olm": collect_olm,
        "run_compliance": run_compliance,
        "enable_db_vacuum": enable_vacuum,
        "user": user
    })

@router.get("/audit", response_class=HTMLResponse)
def audit_view(request: Request, session: Session = Depends(get_session), user: User = Depends(get_current_user_optional)):
    if is_ldap_enabled(session) and not user:
        return RedirectResponse(url="/login")
        
    rules = session.exec(select(AuditRule)).all()
    bundles = session.exec(select(AuditBundle)).all()
    
    clusters = session.exec(select(Cluster).order_by(Cluster.name)).all()
    clusters_by_dc = _group_clusters_with_status(clusters, session)
    
    return templates.TemplateResponse("audit.html", {
        "request": request, 
        "rules": rules, 
        "bundles": bundles,
        "clusters": clusters,
        "clusters_by_dc": clusters_by_dc,
        "page": "audit_rules",
        "user": user
    })

@router.get("/compliance", response_class=HTMLResponse)
def compliance_view(request: Request, session: Session = Depends(get_session), user: User = Depends(get_current_user_optional)):
    if is_ldap_enabled(session) and not user:
        return RedirectResponse(url="/login")
        
    clusters = session.exec(select(Cluster).order_by(Cluster.name)).all()
    clusters_by_dc = _group_clusters_with_status(clusters, session)
    
    return templates.TemplateResponse("audit_run.html", {
        "request": request, 
        "page": "compliance",
        "clusters": clusters,
        "clusters_by_dc": clusters_by_dc,
        "user": user
    })

def _group_clusters(clusters):
    by_dc = {}
    for c in clusters:
        dc = c.datacenter if c.datacenter else "Other"
        if dc not in by_dc:
            by_dc[dc] = []
        by_dc[dc].append(c)
    
    # Sort within each DC
    for dc in by_dc:
        by_dc[dc].sort(key=lambda x: x.name.lower())
    
    # Sort DCs but keep Azure/HCI order if possible, or just alpha
    return dict(sorted(by_dc.items()))

def _group_clusters_with_status(clusters, session):
    from app.routers.dashboard import dashboard_cache as d_cache
    from app.models import ClusterSnapshot
    from sqlmodel import select, func
    import json
    
    # 1. Try to get status from Cache first (fastest)
    status_map = {} # cluster_id -> {has_sm: bool, has_cd: bool}
    
    # Check if cache is partially valid? We just check if it has data.
    if d_cache and d_cache.data and "clusters" in d_cache.data:
        for c in d_cache.data["clusters"]:
            stats = c.get("stats") or {}
            # Ensure ID is present
            if "id" in c:
                status_map[c["id"]] = {
                    "has_service_mesh": stats.get("has_service_mesh", False),
                    "has_argocd": stats.get("has_argocd", False)
                }

    # 2. Identify missing clusters (Cache Miss)
    # If cache was empty or some clusters are new, we need to fetch them from DB
    missing_ids = [c.id for c in clusters if c.id not in status_map]
    
    if missing_ids:
        # Batch Fetch Fallback
        # Strategy: Get the LATEST successful snapshot for each missing cluster
        # Subquery: Max ID per Cluster for Success status
        # Note: SQLite optimization - simple GROUP BY max(id) works well enough
        
        try:
            # We want to fetch service_mesh_json and argocd_json for these clusters
            # Optimized Query:
            # SELECT cluster_id, service_mesh_json, argocd_json 
            # FROM clustersnapshot 
            # WHERE id IN (SELECT MAX(id) FROM clustersnapshot WHERE status='Success' AND cluster_id IN (...) GROUP BY cluster_id)
            
            subq = select(func.max(ClusterSnapshot.id))\
                .where(ClusterSnapshot.status == "Success")\
                .where(ClusterSnapshot.cluster_id.in_(missing_ids))\
                .group_by(ClusterSnapshot.cluster_id)
            
            statement = select(ClusterSnapshot.cluster_id, ClusterSnapshot.service_mesh_json, ClusterSnapshot.argocd_json)\
                .where(ClusterSnapshot.id.in_(subq))
                
            results = session.exec(statement).all()
            
            for cid, sm_json, cd_json in results:
                has_sm = False
                has_cd = False
                
                if sm_json:
                    try:
                        if json.loads(sm_json).get("is_active"): has_sm = True
                    except: pass
                
                if cd_json:
                    try:
                        if json.loads(cd_json).get("is_active"): has_cd = True
                    except: pass
                    
                status_map[cid] = {"has_service_mesh": has_sm, "has_argocd": has_cd}
                
        except Exception as e:
            print(f"Error fetching batch status for sidebar: {e}")

    # 3. Build Result
    by_dc = {}
    for c in clusters:
        # Convert to dict
        c_dict = c.model_dump()
        
        # Inject Status
        st = status_map.get(c.id, {"has_service_mesh": False, "has_argocd": False})
        c_dict['has_service_mesh'] = st['has_service_mesh']
        c_dict['has_argocd'] = st['has_argocd']
        
        dc = c.datacenter if c.datacenter else "Other"
        if dc not in by_dc:
            by_dc[dc] = []
        by_dc[dc].append(c_dict)
    
    for dc in by_dc:
        by_dc[dc].sort(key=lambda x: x['name'].lower())
    
    return dict(sorted(by_dc.items()))

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_view(request: Request, session: Session = Depends(get_session), user: User = Depends(get_current_user_optional)):
    if is_ldap_enabled(session) and not user:
        return RedirectResponse(url="/login")

    clusters = session.exec(select(Cluster).order_by(Cluster.name)).all()
    clusters_by_dc = _group_clusters_with_status(clusters, session)
        
    return templates.TemplateResponse("dashboard.html", {
        "request": request, 
        "clusters": clusters,
        "clusters_by_dc": clusters_by_dc,
        "page": "dashboard",
        "user": user
    })
    
@router.get("/operators", response_class=HTMLResponse)
def operators_view(request: Request, session: Session = Depends(get_session), user: User = Depends(operator_allowed)):
    # if is_ldap_enabled and not user, admin_required will already have handled auth via get_current_user
    
    clusters = session.exec(select(Cluster).order_by(Cluster.name)).all()
    clusters_by_dc = _group_clusters_with_status(clusters, session)
    
    return templates.TemplateResponse("operators.html", {
        "request": request, 
        "clusters": clusters,
        "clusters_by_dc": clusters_by_dc,
        "page": "operators",
        "user": user
    })


@router.get("/license-analytics", response_class=HTMLResponse)
def license_analytics_view(request: Request, session: Session = Depends(get_session), user: User = Depends(operator_allowed)):
    if is_ldap_enabled(session) and not user:
        return RedirectResponse(url="/login")
        
    clusters = session.exec(select(Cluster).order_by(Cluster.name)).all()
    clusters_by_dc = _group_clusters_with_status(clusters, session)
    
    return templates.TemplateResponse("license_analytics.html", {
        "request": request, 
        "clusters": clusters,
        "clusters_by_dc": clusters_by_dc,
        "page": "license_analytics",
        "user": user
    })
