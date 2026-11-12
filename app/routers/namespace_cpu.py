import csv
import io
import json
import logging
from datetime import date, timedelta
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from sqlmodel import Session, select

from app.database import get_session
from app.dependencies import get_current_user, operator_allowed
from app.models import Cluster, NamespaceCPUCollection, NamespaceCPUDaily, User
from app.services.namespace_cpu import CollectionResult, collect_day, eastern_today, report_hours, retention_start
from app.services.page_access import require_page_access

router = APIRouter(prefix="/api/reporting/namespace-cpu", tags=["reporting"])
logger = logging.getLogger(__name__)
PAGE_SIZE = 50
REPORT_FIELDS = {
    "date_et": "Date (ET)",
    "cluster": "Cluster",
    "environment": "Environment",
    "namespace": "Namespace",
    "mapid": "MAPID",
    "cpu_limit_quota_vcpu": "CPU limits",
    "avg_vcpu": "Avg CPU (vCPU)",
    "observed_cpu_vcpu_hours": "Observed CPU (vCPU-hours)",
    "hours_with_data": "CPU hours",
    "quota_status": "Quota status",
    "hourly_usage": "Hourly average CPU",
    "hourly_quota": "Hourly CPU quota",
}
DEFAULT_FIELDS = tuple(field for field in REPORT_FIELDS if not field.startswith("hourly_"))

manual_runs: dict[tuple[int, date, date], dict] = {}
manual_runs_lock = Lock()


def run_collection(cluster_id: int, start: date, end: date, state: dict):
    failures = []
    total = (end - start).days + 1
    collected = 0
    for offset in range(total):
        day = start + timedelta(days=offset)

        def progress(message):
            with manual_runs_lock:
                state["detail"] = f"{day} ET · {message}"

        progress("Starting collection")
        try:
            result = collect_day(cluster_id, day, manual=True, progress=progress)
        except Exception:
            logger.exception("Manual namespace CPU collection failed for cluster %s on %s", cluster_id, day)
            result = CollectionResult("Failed", "Unable to save the collection.")
        if result.status == "Collected":
            collected += 1
        else:
            failures.append(f"{day} · {result.detail or result.status}")
        with manual_runs_lock:
            state["completed"] = offset + 1

    with manual_runs_lock:
        state.update(
            status="Collected" if not failures else "Partial" if collected else "Failed",
            detail="; ".join(failures) or None,
        )


@router.post("/run-now/{cluster_id}", status_code=202)
def start_manual_run(
    cluster_id: int, background: BackgroundTasks,
    session: Session = Depends(get_session), user: User = Depends(operator_allowed),
    start: date | None = None, end: date | None = None,
):
    require_page_access("reports", user, session)
    cluster = session.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(404, "Cluster not found.")
    if not cluster.namespace_cpu_enabled:
        raise HTTPException(409, "Enable Namespace CPU collection for this cluster first.")
    today = eastern_today()
    legacy = start is None and end is None
    if legacy:
        start = end = today - timedelta(days=1)
    if start is None or end is None or start > end or end >= today:
        raise HTTPException(422, "Select a date range ending on a completed Eastern day.")
    cutoff = retention_start(session, today)
    if start < cutoff:
        raise HTTPException(409, f"Select a start date on or after {cutoff} ET to stay within snapshot retention.")

    key = (cluster_id, start, end)
    with manual_runs_lock:
        for old_key, state in list(manual_runs.items()):
            if old_key[2] < cutoff and state["status"] != "Running":
                del manual_runs[old_key]
        for active_key, state in manual_runs.items():
            if active_key[0] == cluster_id and state["status"] == "Running" and active_key != key:
                raise HTTPException(409, "A collection is already running for this cluster.")
        state = manual_runs.get(key)
        if state is None or state["status"] != "Running":
            state = {"status": "Running", "detail": "Waiting to start", "completed": 0,
                     "total": (end - start).days + 1}
            manual_runs[key] = state
            background.add_task(run_collection, cluster_id, start, end, state)
    if legacy:
        return {"day": start, "cluster_id": cluster_id}
    return {"start": start, "end": end, "cluster_id": cluster_id}


@router.get("/run-now/{cluster_id}")
def get_manual_run(
    cluster_id: int, day: date | None = None,
    session: Session = Depends(get_session), user: User = Depends(operator_allowed),
    start: date | None = None, end: date | None = None,
):
    require_page_access("reports", user, session)
    if day is not None:
        start = end = day
    with manual_runs_lock:
        state = manual_runs.get((cluster_id, start, end))
        if state is not None:
            return state.copy()
    raise HTTPException(404, "This collection is no longer available. Open the report or start another run.")


@router.get("/clusters")
def list_clusters(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    require_page_access("reports", user, session)
    cutoff = retention_start(session, eastern_today())
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
    start: date | None = None, end: date | None = None,
    cluster_ids: Annotated[list[int] | None, Query(min_length=1)] = None,
    session: Session = Depends(get_session), user: User = Depends(get_current_user),
):
    require_page_access("reports", user, session)
    today = eastern_today()
    cutoff = retention_start(session, today)
    if start is None and end is None:
        return cutoff, today - timedelta(days=1), cluster_ids
    if start is None or end is None:
        raise HTTPException(422, "Choose both a start date and an end date.")
    if start > end or end >= today:
        raise HTTPException(422, "Choose an inclusive date range ending on a completed Eastern day.")
    return max(start, cutoff), end, cluster_ids


def scope_conditions(model, scope):
    start, end, cluster_ids = scope
    conditions = [model.day.between(start, end)]
    if cluster_ids is not None:
        conditions.append(model.cluster_id.in_(cluster_ids))
    return conditions


def report_query(scope, environments, search: str, sort: str, descending: bool):
    row = NamespaceCPUDaily
    statement = select(row).where(row.eastern_day == True, *scope_conditions(row, scope))
    if environments:
        statement = statement.where(func.upper(row.environment).in_(environments))
    if search.strip():
        term = search.strip().lower()
        mapids = func.json_each(row.mapids_json).table_valued("value")
        statement = statement.where(or_(*(
            func.lower(column).contains(term, autoescape=True)
            for column in (row.cluster_name, row.namespace, row.environment, row.day)
        ), select(1).select_from(mapids).where(
            func.lower(mapids.c.value).contains(term, autoescape=True)
        ).exists()))
    usage = func.json_each(row.usage_json).table_valued("value")
    labels = func.json_each(row.mapids_json).table_valued("value")
    columns = {
        "date_et": row.day,
        "cluster": func.lower(row.cluster_name),
        "environment": func.lower(row.environment),
        "namespace": func.lower(row.namespace),
        "mapid": select(func.min(func.lower(labels.c.value))).scalar_subquery(),
        "cpu_limit_quota_vcpu": func.json_extract(row.quota_json, "$[#-1]"),
        "avg_vcpu": select(func.avg(usage.c.value)).scalar_subquery(),
        "observed_cpu_vcpu_hours": select(func.sum(usage.c.value)).scalar_subquery(),
        "hours_with_data": select(func.count(usage.c.value)).scalar_subquery(),
    }
    column = columns.get(sort)
    if column is not None:
        statement = statement.order_by((column.desc() if descending else column.asc()).nulls_last())
    return statement.order_by(row.day.desc(), row.cluster_name, row.namespace, row.cluster_id)


def summarize(row: NamespaceCPUDaily):
    usage = json.loads(row.usage_json)
    quota = json.loads(row.quota_json)
    labels = json.loads(row.mapids_json)
    mapids = sorted({label for label in labels if label is not None})
    measured = [value for value in usage if value is not None]
    return {
        "date_et": row.day, "cluster": row.cluster_name, "environment": row.environment,
        "namespace": row.namespace, "mapid": ", ".join(mapids),
        "avg_vcpu": sum(measured) / len(measured) if measured else None,
        "observed_cpu_vcpu_hours": sum(measured) if measured else None,
        "hours_with_data": len(measured),
        "cpu_limit_quota_vcpu": quota[-1],
        "quota_status": (
            "Multiple quota objects at day end" if row.ambiguous_quota else
            "No quota at day end" if quota[-1] is None else
            "Changed during day" if any(value is not None and value != quota[-1] for value in quota) else
            "Reported"
        ),
        "hours_et": [hour.strftime("%H:%M %Z") for hour in report_hours(row.day)],
        "hourly_usage": usage, "hourly_quota": quota, "hourly_mapid": labels,
    }


@router.get("")
def view_report(
    scope=Depends(report_scope), page: int = Query(1, ge=1), session: Session = Depends(get_session),
    environment: Annotated[list[str] | None, Query()] = None, search: str = "",
    sort: str = "", descending: bool = False,
):
    selected_envs = [value.upper() for value in environment or [] if value]
    records = session.exec(report_query(scope, selected_envs, search, sort, descending)
                           .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1)).all()
    environments = session.exec(select(func.upper(NamespaceCPUDaily.environment)).where(
        NamespaceCPUDaily.eastern_day == True, *scope_conditions(NamespaceCPUDaily, scope),
    ).distinct()).all()
    issues = select(NamespaceCPUCollection, Cluster.name).join(Cluster).where(
        *scope_conditions(NamespaceCPUCollection, scope),
    ).order_by(NamespaceCPUCollection.day.desc(), Cluster.name)
    if selected_envs:
        issues = issues.where(func.upper(Cluster.environment).in_(selected_envs))
    collections = session.exec(issues.where(
        NamespaceCPUCollection.status != "Collected",
        NamespaceCPUCollection.eastern_day == True,
    )).all()
    legacy = session.exec(issues.where(
        NamespaceCPUCollection.eastern_day == False,
    )).all()
    return {
        "environments": sorted(value for value in environments if value),
        "rows": [summarize(row) for row in records[:PAGE_SIZE]], "has_more": len(records) > PAGE_SIZE,
        "collection_issues": [
            {"day": record.day, "cluster": name, "status": record.status, "detail": record.detail}
            for record, name in collections
        ] + [
            {"day": record.day, "cluster": name, "status": "Awaiting Eastern time collection",
             "detail": record.detail or "This report was collected in UTC and must be collected again."}
            for record, name in legacy
        ],
    }


def csv_cell(value):
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


@router.get("/export")
def export_report(
    scope=Depends(report_scope), session: Session = Depends(get_session),
    fields: Annotated[list[str] | None, Query()] = None,
    environment: Annotated[list[str] | None, Query()] = None, search: str = "",
    sort: str = "", descending: bool = False,
):
    selected = list(dict.fromkeys(fields)) if fields is not None else [*DEFAULT_FIELDS, "hourly_usage", "hourly_quota"]
    if not selected or any(field not in REPORT_FIELDS for field in selected):
        raise HTTPException(422, "Choose valid report fields.")

    start, end, _ = scope
    has_repeated_hour = any(
        len(report_hours(start + timedelta(days=offset))) == 25
        for offset in range((end - start).days + 1)
    )
    hour_keys = [f"{hour:02d}" for hour in range(24)]
    if has_repeated_hour:
        hour_keys.insert(2, "01_repeat")

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        columns = []
        for field in selected:
            if field in ("hourly_usage", "hourly_quota"):
                prefix = "avg_vcpu" if field == "hourly_usage" else "cpu_quota_vcpu"
                columns.extend(f"{prefix}_{hour}_et" for hour in hour_keys)
            else:
                columns.append(field)
        writer.writerow(columns)
        yield buffer.getvalue()
        selected_envs = [value.upper() for value in environment or [] if value]
        for row in session.exec(report_query(scope, selected_envs, search, sort, descending)
                                .execution_options(yield_per=250)):
            values = summarize(row)
            keys = [hour.strftime("%H") + ("_repeat" if hour.fold else "") for hour in report_hours(row.day)]
            cells = []
            for field in selected:
                if field in ("hourly_usage", "hourly_quota"):
                    by_hour = dict(zip(keys, values[field]))
                    cells.extend(csv_cell(by_hour.get(hour)) for hour in hour_keys)
                else:
                    cells.append(csv_cell(values[field]))
            buffer.seek(0)
            buffer.truncate()
            writer.writerow(cells)
            yield buffer.getvalue()

    return StreamingResponse(generate(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="namespace-cpu.csv"',
    })
