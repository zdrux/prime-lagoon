import csv
import io
import json
import logging
from concurrent.futures import Future
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.database import get_session
from app.dependencies import get_current_user, operator_allowed
from app.models import Cluster, NamespaceCPUCollection, NamespaceCPUDaily, User
from app.services.namespace_cpu import CollectionResult, collect_day, retention_start
from app.services.page_access import require_page_access

router = APIRouter(prefix="/api/reporting/namespace-cpu", tags=["reporting"])
logger = logging.getLogger(__name__)
PAGE_SIZE = 50
manual_runs: dict[tuple[int, date], Future[CollectionResult]] = {}
manual_runs_lock = Lock()


def run_collection(cluster_id: int, day: date, result: Future[CollectionResult]):
    try:
        result.set_result(collect_day(cluster_id, day, manual=True))
    except Exception:
        logger.exception("Manual namespace CPU collection failed for cluster %s", cluster_id)
        result.set_result(CollectionResult("Failed", "Unable to save the collection. Check the application logs."))


@router.post("/run-now/{cluster_id}", status_code=202)
def start_manual_run(
    cluster_id: int, background: BackgroundTasks,
    session: Session = Depends(get_session), user: User = Depends(operator_allowed),
):
    require_page_access("reports", user, session)
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(404, "Cluster not found.")
    if not cluster.namespace_cpu_enabled:
        raise HTTPException(409, "Enable Namespace CPU collection for this cluster first.")
    today = datetime.now(timezone.utc).date()
    day = today - timedelta(days=1)
    if day < retention_start(session, today):
        raise HTTPException(409, "Yesterday is outside snapshot retention.")

    with manual_runs_lock:
        for key, result in list(manual_runs.items()):
            if key[1] < day and result.done():
                del manual_runs[key]
        key = (cluster_id, day)
        result = manual_runs.get(key)
        if result is None or result.done():
            result = Future()
            manual_runs[key] = result
            background.add_task(run_collection, cluster_id, day, result)
    return {"day": day, "cluster_id": cluster_id}


@router.get("/run-now/{cluster_id}")
def get_manual_run(
    cluster_id: int, day: date,
    session: Session = Depends(get_session), user: User = Depends(operator_allowed),
):
    require_page_access("reports", user, session)
    with manual_runs_lock:
        result = manual_runs.get((cluster_id, day))
    if result is None:
        raise HTTPException(404, "This collection is no longer available. Open the report or start another run.")
    if not result.done():
        return {"status": "Running"}
    return result.result()


@router.get("/clusters")
def list_clusters(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    require_page_access("reports", user, session)
    cutoff = retention_start(session, datetime.now(timezone.utc).date())
    historical = select(NamespaceCPUCollection.cluster_id).where(NamespaceCPUCollection.day >= cutoff)
    clusters = session.exec(select(Cluster).where(
        (Cluster.namespace_cpu_enabled == True) | Cluster.id.in_(historical),
    ).order_by(Cluster.name)).all()
    return [
        {"id": c.id, "name": c.name, "environment": c.environment, "datacenter": c.datacenter,
         "collecting": c.namespace_cpu_enabled}
        for c in clusters
    ]


def report_scope(
    start: date, end: date, cluster_ids: Annotated[list[int], Query(min_length=1)],
    session: Session = Depends(get_session), user: User = Depends(get_current_user),
):
    require_page_access("reports", user, session)
    today = datetime.now(timezone.utc).date()
    if start > end or end >= today:
        raise HTTPException(422, "Choose an inclusive date range ending on a completed UTC day.")
    cutoff = retention_start(session, today)
    return max(start, cutoff), end, set(cluster_ids)


def report_query(scope):
    start, end, cluster_ids = scope
    return select(NamespaceCPUDaily).where(
        NamespaceCPUDaily.day.between(start, end), NamespaceCPUDaily.cluster_id.in_(cluster_ids),
    ).order_by(NamespaceCPUDaily.day.desc(), NamespaceCPUDaily.cluster_name,
               NamespaceCPUDaily.namespace, NamespaceCPUDaily.cluster_id)


def summarize(row: NamespaceCPUDaily):
    usage = json.loads(row.usage_json)
    quota = json.loads(row.quota_json)
    labels = json.loads(row.mapids_json)
    mapids = sorted({label for label in labels if label is not None})
    measured = [value for value in usage if value is not None]
    quotas = [value for value in quota if value is not None]
    return {
        "date_utc": row.day, "cluster": row.cluster_name, "environment": row.environment,
        "namespace": row.namespace, "mapid": ", ".join(mapids),
        "avg_vcpu": sum(measured) / len(measured) if measured else None,
        "observed_cpu_vcpu_hours": sum(measured) if measured else None,
        "hours_with_data": len(measured),
        "avg_quota_vcpu": sum(quotas) / len(quotas) if quotas else None,
        "observed_quota_vcpu_hours": sum(quotas) if quotas else None,
        "quota_hours_with_data": len(quotas),
        "quota_status": (
            "Multiple quota objects" if row.ambiguous_quota else
            "No quota data" if not quotas else "Varied" if min(quotas) != max(quotas) else "Stable"
        ),
        "hourly_usage": usage, "hourly_quota": quota, "hourly_mapid": labels,
    }


@router.get("")
def view_report(
    scope=Depends(report_scope), page: int = Query(1, ge=1), session: Session = Depends(get_session),
):
    records = session.exec(report_query(scope).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1)).all()
    start, end, cluster_ids = scope
    collections = session.exec(select(NamespaceCPUCollection, Cluster.name).join(Cluster).where(
        NamespaceCPUCollection.day.between(start, end), NamespaceCPUCollection.cluster_id.in_(cluster_ids),
        NamespaceCPUCollection.status != "Collected",
    ).order_by(NamespaceCPUCollection.day.desc(), Cluster.name)).all()
    return {
        "rows": [summarize(row) for row in records[:PAGE_SIZE]], "has_more": len(records) > PAGE_SIZE,
        "collection_issues": [
            {"day": record.day, "cluster": name, "status": record.status, "detail": record.detail}
            for record, name in collections
        ],
    }


def csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


@router.get("/export")
def export_report(scope=Depends(report_scope), session: Session = Depends(get_session)):
    columns = [
        "date_utc", "cluster", "environment", "namespace", "mapid", "avg_vcpu",
        "observed_cpu_vcpu_hours", "hours_with_data", "avg_quota_vcpu",
        "observed_quota_vcpu_hours", "quota_hours_with_data", "quota_status",
    ]

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns + [f"avg_vcpu_{hour:02d}" for hour in range(24)]
                        + [f"avg_quota_vcpu_{hour:02d}" for hour in range(24)])
        yield buffer.getvalue()
        for row in session.exec(report_query(scope).execution_options(yield_per=250)):
            values = summarize(row)
            buffer.seek(0)
            buffer.truncate()
            writer.writerow([csv_cell(values[column]) for column in columns]
                            + values["hourly_usage"] + values["hourly_quota"])
            yield buffer.getvalue()

    return StreamingResponse(generate(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="namespace-cpu.csv"',
    })
