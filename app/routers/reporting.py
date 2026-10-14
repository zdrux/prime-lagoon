import csv
import io
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.database import get_session
from app.dependencies import get_current_user, operator_allowed
from app.models import Cluster, ReportRun, ReportRunCluster, ReportTemplate, User
from app.services.page_access import require_page_access
from app.services.reporting import (
    cleanup_expired_report_runs,
    start_report_run,
    validate_rbac_report_config,
)


router = APIRouter(prefix="/api/reporting", tags=["reporting"])


class ReportTemplatePayload(BaseModel):
    name: str
    description: Optional[str] = None
    report_type: str = "rbac_access_membership"
    config: Dict[str, Any]


class ReportRunPayload(BaseModel):
    template_id: Optional[int] = None
    name: Optional[str] = None
    report_type: str = "rbac_access_membership"
    config: Optional[Dict[str, Any]] = None
    cluster_ids: List[int] = Field(default_factory=list)


def _require_reports(user: User, session: Session) -> None:
    require_page_access("reports", user, session)


def _template_dict(template: ReportTemplate) -> Dict[str, Any]:
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "report_type": template.report_type,
        "config": json.loads(template.config_json),
        "created_by": template.created_by,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
        "is_active": template.is_active,
    }


def _run_dict(run: ReportRun, include_config: bool = False) -> Dict[str, Any]:
    result = {
        "id": run.id,
        "template_id": run.template_id,
        "template_name": run.template_name,
        "report_type": run.report_type,
        "requested_by": run.requested_by,
        "status": run.status,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "expires_at": run.expires_at,
        "summary": json.loads(run.summary_json) if run.summary_json else None,
    }
    if include_config:
        result["config"] = json.loads(run.config_json)
        result["cluster_ids"] = json.loads(run.cluster_ids_json)
    return result


@router.get("/clusters")
def list_report_clusters(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _require_reports(user, session)
    clusters = session.exec(select(Cluster).order_by(Cluster.name)).all()
    return [
        {
            "id": cluster.id,
            "name": cluster.name,
            "environment": cluster.environment,
            "datacenter": cluster.datacenter,
            "tags": json.loads(cluster.tags) if cluster.tags else {},
        }
        for cluster in clusters
    ]


@router.get("/templates")
def list_report_templates(
    include_archived: bool = False,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _require_reports(user, session)
    statement = select(ReportTemplate).order_by(ReportTemplate.name)
    if not include_archived:
        statement = statement.where(ReportTemplate.is_active == True)
    return [_template_dict(template) for template in session.exec(statement).all()]


@router.post("/templates")
def create_report_template(
    payload: ReportTemplatePayload,
    session: Session = Depends(get_session),
    user: User = Depends(operator_allowed),
):
    _require_reports(user, session)
    if payload.report_type != "rbac_access_membership":
        raise HTTPException(status_code=400, detail="Unsupported report type")
    try:
        config = validate_rbac_report_config(payload.config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Template name is required")
    existing = session.exec(
        select(ReportTemplate).where(ReportTemplate.name == name, ReportTemplate.is_active == True)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="An active template with this name already exists")
    template = ReportTemplate(
        name=name,
        description=(payload.description or "").strip() or None,
        report_type=payload.report_type,
        config_json=json.dumps(config),
        created_by=user.username,
    )
    session.add(template)
    session.commit()
    session.refresh(template)
    return _template_dict(template)


@router.put("/templates/{template_id}")
def update_report_template(
    template_id: int,
    payload: ReportTemplatePayload,
    session: Session = Depends(get_session),
    user: User = Depends(operator_allowed),
):
    _require_reports(user, session)
    template = session.get(ReportTemplate, template_id)
    if not template or not template.is_active:
        raise HTTPException(status_code=404, detail="Report template not found")
    if payload.report_type != "rbac_access_membership":
        raise HTTPException(status_code=400, detail="Unsupported report type")
    try:
        config = validate_rbac_report_config(payload.config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Template name is required")
    duplicate = session.exec(
        select(ReportTemplate).where(
            ReportTemplate.name == name,
            ReportTemplate.is_active == True,
            ReportTemplate.id != template.id,
        )
    ).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="An active template with this name already exists")
    template.name = name
    template.description = (payload.description or "").strip() or None
    template.config_json = json.dumps(config)
    template.updated_at = datetime.utcnow()
    session.add(template)
    session.commit()
    session.refresh(template)
    return _template_dict(template)


@router.delete("/templates/{template_id}")
def archive_report_template(
    template_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(operator_allowed),
):
    _require_reports(user, session)
    template = session.get(ReportTemplate, template_id)
    if not template or not template.is_active:
        raise HTTPException(status_code=404, detail="Report template not found")
    template.is_active = False
    template.updated_at = datetime.utcnow()
    session.add(template)
    session.commit()
    return {"ok": True}


@router.get("/runs")
def list_report_runs(
    limit: int = Query(default=30, ge=1, le=200),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _require_reports(user, session)
    cleanup_expired_report_runs(session)
    runs = session.exec(select(ReportRun).order_by(ReportRun.created_at.desc()).limit(limit)).all()
    return [_run_dict(run) for run in runs]


@router.post("/runs")
def create_report_run(
    payload: ReportRunPayload,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _require_reports(user, session)
    if not payload.cluster_ids:
        raise HTTPException(status_code=400, detail="Select at least one cluster")
    cluster_ids = list(dict.fromkeys(payload.cluster_ids))
    clusters = session.exec(select(Cluster).where(Cluster.id.in_(cluster_ids))).all()
    if len(clusters) != len(cluster_ids):
        raise HTTPException(status_code=400, detail="One or more selected clusters do not exist")

    template = session.get(ReportTemplate, payload.template_id) if payload.template_id else None
    if payload.template_id and (not template or not template.is_active):
        raise HTTPException(status_code=404, detail="Report template not found")
    report_type = template.report_type if template else payload.report_type
    if report_type != "rbac_access_membership":
        raise HTTPException(status_code=400, detail="Unsupported report type")
    raw_config = payload.config if payload.config is not None else (json.loads(template.config_json) if template else {})
    try:
        config = validate_rbac_report_config(raw_config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run_name = (payload.name or (template.name if template else "Ad-hoc RBAC report")).strip()
    if not run_name:
        raise HTTPException(status_code=400, detail="Report name is required")
    run = ReportRun(
        template_id=template.id if template else None,
        template_name=run_name,
        report_type=report_type,
        config_json=json.dumps(config),
        cluster_ids_json=json.dumps(cluster_ids),
        requested_by=user.username,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    cluster_map = {cluster.id: cluster for cluster in clusters}
    for cluster_id in cluster_ids:
        cluster = cluster_map[cluster_id]
        session.add(ReportRunCluster(
            run_id=run.id,
            cluster_id=cluster.id,
            cluster_name=cluster.name,
        ))
    session.commit()
    start_report_run(run.id)
    return _run_dict(run, include_config=True)


@router.get("/runs/{run_id}")
def get_report_run(
    run_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _require_reports(user, session)
    run = session.get(ReportRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Report run not found")
    result = _run_dict(run, include_config=True)
    cluster_runs = session.exec(
        select(ReportRunCluster).where(ReportRunCluster.run_id == run.id).order_by(ReportRunCluster.cluster_name)
    ).all()
    result["clusters"] = [
        {
            "cluster_id": item.cluster_id,
            "cluster_name": item.cluster_name,
            "status": item.status,
            "row_count": item.row_count,
            "error": item.error,
            "started_at": item.started_at,
            "completed_at": item.completed_at,
        }
        for item in cluster_runs
    ]
    return result


def _report_rows(session: Session, run_id: int) -> List[Dict[str, Any]]:
    cluster_runs = session.exec(
        select(ReportRunCluster).where(ReportRunCluster.run_id == run_id).order_by(ReportRunCluster.cluster_name)
    ).all()
    rows: List[Dict[str, Any]] = []
    for cluster_run in cluster_runs:
        if cluster_run.results_json:
            rows.extend(json.loads(cluster_run.results_json))
    return rows


@router.get("/runs/{run_id}/results")
def get_report_results(
    run_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _require_reports(user, session)
    if not session.get(ReportRun, run_id):
        raise HTTPException(status_code=404, detail="Report run not found")
    return _report_rows(session, run_id)


def _csv_safe(value: Any) -> Any:
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value)
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


@router.get("/runs/{run_id}/export")
def export_report_results(
    run_id: int,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _require_reports(user, session)
    run = session.get(ReportRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Report run not found")
    rows = _report_rows(session, run_id)
    safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in run.template_name)[:80]
    if format == "json":
        body = json.dumps(rows, indent=2)
        return StreamingResponse(
            iter([body]),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_{run.id}.json"'},
        )

    columns = [
        "cluster", "environment", "datacenter", "scope", "namespace", "binding_kind",
        "binding_name", "cluster_role", "subject_kind", "subject_name", "total_users",
        "matching_users", "nonmatching_users", "finding", "detail",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_safe(row.get(key)) for key in columns})
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_{run.id}.csv"'},
    )
