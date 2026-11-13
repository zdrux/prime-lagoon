import csv
import io
import json
import logging
from datetime import date, timedelta
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, tuple_
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
    "cpu_request_quota_vcpu": "CPU requests",
    "avg_vcpu": "Avg CPU (vCPU)",
    "sum_avg_vcpu_hours": "Sum of avg vCPU hours",
    "hours_with_data": "CPU hours",
    "quota_status": "Quota status",
    "hourly_usage": "Hourly average CPU",
    "hourly_quota": "Hourly CPU quota",
    "hourly_memory_usage": "Hourly average memory (GiB)",
    "memory_requests_gib": "Memory requests (GiB)",
    "memory_limits_gib": "Memory limits (GiB)",
}
HOURLY_EXPORT_PREFIXES = {
    "hourly_usage": "avg_vcpu",
    "hourly_quota": "cpu_quota_vcpu",
    "hourly_memory_usage": "avg_memory_gib",
}
MEMORY_QUOTA_FIELDS = {
    "memory_requests_gib": "hourly_memory_requests",
    "memory_limits_gib": "hourly_memory_limits",
}
DEFAULT_FIELDS = tuple(field for field in REPORT_FIELDS
                       if not field.startswith("hourly_")
                       and field != "cpu_request_quota_vcpu" and field not in MEMORY_QUOTA_FIELDS)

manual_runs: dict[tuple[int, date, date], dict] = {}
manual_runs_lock = Lock()


def run_collection(cluster_id: int, start: date, end: date, state: dict):
    failures = []
    collected_days = []
    total = (end - start).days + 1
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
            collected_days.append(day.isoformat())
        else:
            failures.append(f"{day} · {result.detail or result.status}")
        with manual_runs_lock:
            state["completed"] = offset + 1
            state["collected_days"] = collected_days.copy()

    with manual_runs_lock:
        state.update(
            status="Collected" if not failures else "Partial" if collected_days else "Failed",
            detail="; ".join(failures) or None,
            collected_days=collected_days,
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
    collected_day: Annotated[list[str] | None, Query()] = None,
    session: Session = Depends(get_session), user: User = Depends(get_current_user),
):
    require_page_access("reports", user, session)
    today = eastern_today()
    cutoff = retention_start(session, today)
    pairs = None
    if collected_day is not None:
        pairs = []
        for value in collected_day:
            try:
                cluster_id, day = value.split(":", 1)
                pairs.append((int(cluster_id), date.fromisoformat(day)))
            except ValueError:
                raise HTTPException(422, "Invalid collected cluster and day.") from None
    if start is None and end is None:
        return cutoff, today - timedelta(days=1), cluster_ids, pairs
    if start is None or end is None:
        raise HTTPException(422, "Choose both a start date and an end date.")
    if start > end or end >= today:
        raise HTTPException(422, "Choose an inclusive date range ending on a completed Eastern day.")
    return max(start, cutoff), end, cluster_ids, pairs


def scope_conditions(model, scope):
    start, end, cluster_ids, pairs = scope
    conditions = [model.day.between(start, end)]
    if cluster_ids is not None:
        conditions.append(model.cluster_id.in_(cluster_ids))
    if pairs is not None:
        conditions.append(tuple_(model.cluster_id, model.day).in_(pairs))
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
    usage = func.json_each(row.usage_json).table_valued("key", "value")
    labels = func.json_each(row.mapids_json).table_valued("value")
    columns = {
        "date_et": row.day,
        "cluster": func.lower(row.cluster_name),
        "environment": func.lower(row.environment),
        "namespace": func.lower(row.namespace),
        "mapid": select(func.min(func.lower(labels.c.value))).scalar_subquery(),
        "cpu_limit_quota_vcpu": func.json_extract(row.quota_json, "$[#-1]"),
        "cpu_request_quota_vcpu": row.cpu_request_quota_vcpu,
        "avg_vcpu": select(func.avg(usage.c.value)).scalar_subquery(),
        # A 25-hour Eastern day repeats 01:00 at index 2.
        "sum_avg_vcpu_hours": select(func.sum(usage.c.value)).where(or_(
            func.json_array_length(row.usage_json) != 25, usage.c.key != 2,
        )).scalar_subquery(),
        "hours_with_data": select(func.count(usage.c.value)).scalar_subquery(),
    }
    columns.update({field: func.json_extract(row.memory_json, f"$.{key}[#-1]")
                    for field, key in MEMORY_QUOTA_FIELDS.items()})
    column = columns.get("sum_avg_vcpu_hours" if sort == "observed_cpu_vcpu_hours" else sort)
    if column is not None:
        statement = statement.order_by((column.desc() if descending else column.asc()).nulls_last())
    return statement.order_by(row.day.desc(), row.cluster_name, row.namespace, row.cluster_id)


def summarize(row: NamespaceCPUDaily):
    usage = json.loads(row.usage_json)
    quota = json.loads(row.quota_json)
    labels = json.loads(row.mapids_json)
    mapids = sorted({label for label in labels if label is not None})
    measured = [value for value in usage if value is not None]
    hours = report_hours(row.day)
    memory = json.loads(row.memory_json) if row.memory_json else {}
    exported_usage = [value for hour, value in zip(hours, usage) if not hour.fold and value is not None]
    cpu_hours = sum(exported_usage) if exported_usage else None
    return {
        "date_et": row.day, "cluster": row.cluster_name, "environment": row.environment,
        "namespace": row.namespace, "mapid": ", ".join(mapids),
        "avg_vcpu": sum(measured) / len(measured) if measured else None,
        "sum_avg_vcpu_hours": cpu_hours,
        "observed_cpu_vcpu_hours": cpu_hours,
        "hours_with_data": len(measured),
        "cpu_limit_quota_vcpu": quota[-1],
        "cpu_request_quota_vcpu": row.cpu_request_quota_vcpu,
        "quota_status": (
            "Multiple quota objects at day end" if row.ambiguous_quota else
            "No quota at day end" if quota[-1] is None else
            "Changed during day" if any(value is not None and value != quota[-1] for value in quota) else
            "Reported"
        ),
        "hours_et": [hour.strftime("%H:%M %Z") for hour in hours],
        "hourly_usage": usage, "hourly_quota": quota, "hourly_mapid": labels,
        "hourly_memory_usage": memory.get("hourly_memory_usage", [None] * len(hours)),
        **{field: memory.get(key, [None])[-1] for field, key in MEMORY_QUOTA_FIELDS.items()},
    }


@router.get("")
def view_report(
    scope=Depends(report_scope), page: int = Query(1, ge=1), session: Session = Depends(get_session),
    environment: Annotated[list[str] | None, Query()] = None, search: str = "",
    sort: str = "", descending: bool = False, report_day: date | None = None,
    result_cluster_id: int | None = None,
):
    selected_envs = [value.upper() for value in environment or [] if value]
    query = report_query(scope, selected_envs, search, sort, descending)
    target_columns = (NamespaceCPUDaily.cluster_name, NamespaceCPUDaily.environment, NamespaceCPUDaily.cluster_id)
    targets = session.execute(query.order_by(None).with_only_columns(*target_columns)
                              .distinct().order_by(*target_columns)).all()
    if result_cluster_id is not None:
        query = query.where(NamespaceCPUDaily.cluster_id == result_cluster_id)
    matches = query.order_by(None).with_only_columns(NamespaceCPUDaily.day).subquery()
    day_counts = session.exec(select(matches.c.day, func.count()).group_by(matches.c.day)
                              .order_by(matches.c.day.desc())).all()
    total = sum(count for day, count in day_counts if report_day is None or day == report_day)
    if report_day is not None:
        query = query.where(NamespaceCPUDaily.day == report_day)
    page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, page_count)
    records = session.exec(query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()
    environments = session.exec(select(func.upper(NamespaceCPUDaily.environment)).where(
        NamespaceCPUDaily.eastern_day == True, *scope_conditions(NamespaceCPUDaily, scope),
    ).distinct()).all()
    issues = select(NamespaceCPUCollection, Cluster.name).join(Cluster).where(
        *scope_conditions(NamespaceCPUCollection, scope),
    ).order_by(NamespaceCPUCollection.day.desc(), Cluster.name)
    if selected_envs:
        issues = issues.where(func.upper(Cluster.environment).in_(selected_envs))
    if result_cluster_id is not None:
        issues = issues.where(Cluster.id == result_cluster_id)
    collections = session.exec(issues.where(
        NamespaceCPUCollection.status != "Collected",
        NamespaceCPUCollection.eastern_day == True,
    )).all()
    legacy = session.exec(issues.where(
        NamespaceCPUCollection.eastern_day == False,
    )).all()
    return {
        "environments": sorted(value for value in environments if value),
        "targets": [{"cluster": cluster, "environment": environment, "id": cluster_id}
                    for cluster, environment, cluster_id in targets],
        "rows": [summarize(row) for row in records], "has_more": page < page_count,
        "page": page, "page_count": page_count, "total": total, "page_size": PAGE_SIZE,
        "days": [{"day": day, "count": count} for day, count in day_counts],
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
    sort: str = "", descending: bool = False, include_cluster_id: bool = False,
    result_cluster_id: int | None = None,
):
    if fields is not None:
        selected = list(dict.fromkeys("sum_avg_vcpu_hours" if field == "observed_cpu_vcpu_hours" else field
                                      for field in fields))
    else:
        selected = [*DEFAULT_FIELDS, "hourly_usage", "hourly_quota"]
    if not selected or any(field not in REPORT_FIELDS for field in selected):
        raise HTTPException(422, "Choose valid report fields.")

    hour_keys = [f"{hour:02d}" for hour in range(24)]

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        columns = []
        for field in selected:
            if field in HOURLY_EXPORT_PREFIXES:
                prefix = HOURLY_EXPORT_PREFIXES[field]
                columns.extend(f"{prefix}_{hour}_et" for hour in hour_keys)
            else:
                columns.append(field)
        writer.writerow([*columns, "cluster_id"] if include_cluster_id else columns)
        yield buffer.getvalue()
        selected_envs = [value.upper() for value in environment or [] if value]
        query = report_query(scope, selected_envs, search, sort, descending)
        if result_cluster_id is not None:
            query = query.where(NamespaceCPUDaily.cluster_id == result_cluster_id)
        for row in session.exec(query.execution_options(yield_per=250)):
            values = summarize(row)
            hours = report_hours(row.day)
            cells = []
            for field in selected:
                if field in HOURLY_EXPORT_PREFIXES:
                    by_hour = {hour.strftime("%H"): value for hour, value in zip(hours, values[field])
                               if not hour.fold}
                    cells.extend(csv_cell(by_hour.get(hour)) for hour in hour_keys)
                else:
                    cells.append(csv_cell(values[field]))
            buffer.seek(0)
            buffer.truncate()
            writer.writerow([*cells, row.cluster_id] if include_cluster_id else cells)
            yield buffer.getvalue()

    return StreamingResponse(generate(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="namespace-cpu.csv"',
    })
