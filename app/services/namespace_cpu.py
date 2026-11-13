import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from time import sleep
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import delete, text
from sqlmodel import Session, select

from app.database import engine
from app.models import AppConfig, Cluster, NamespaceCPUCollection, NamespaceCPUDaily

logger = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/Toronto")


def eastern_today() -> date:
    return datetime.now(EASTERN).date()


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=EASTERN)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=EASTERN)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def report_hours(day: date) -> list[datetime]:
    start, end = day_bounds(day)
    count = (end - start) // timedelta(hours=1)
    return [(start + timedelta(hours=hour)).astimezone(EASTERN) for hour in range(count)]


USAGE_QUERY = (
    'avg_over_time((sum by (namespace) (rate(container_cpu_usage_seconds_total'
    '{container!="",image!="",namespace!="",namespace!~"openshift-.*"}[5m])))[1h:5m])'
)
QUOTA_OBJECTS = (
    'max by (namespace, resourcequota) (kube_resourcequota'
    '{resource="limits.cpu",type="hard",namespace!="",namespace!~"openshift-.*"})'
)
QUOTA_COUNT = f'count by (namespace) ({QUOTA_OBJECTS})'
QUOTA_QUERY = (
    f'sum by (namespace) ({QUOTA_OBJECTS}) and on (namespace) ({QUOTA_COUNT} == 1)'
)
AMBIGUOUS_QUERY = f'{QUOTA_COUNT} > 1'
REQUEST_QUOTA_QUERY = QUOTA_QUERY.replace('limits.cpu', 'requests.cpu')
MEMORY_QUERIES = {
    "hourly_memory_usage": ("memory working set",
        'avg_over_time((sum by (namespace) (container_memory_working_set_bytes'
        '{container!="",image!="",namespace!="",namespace!~"openshift-.*"}))[1h:5m])'),
    "hourly_memory_requests": ("memory requests quota", QUOTA_QUERY.replace('limits.cpu', 'requests.memory')),
    "hourly_memory_limits": ("memory limits quota", QUOTA_QUERY.replace('limits.cpu', 'limits.memory')),
}
MAPID_QUERY = (
    'max by (namespace, label_mapid) (kube_namespace_labels'
    '{label_mapid!="",namespace!="",namespace!~"openshift-.*"})'
)


class ThanosQueryError(ValueError):
    pass


@dataclass(frozen=True)
class CollectionResult:
    status: str
    detail: str | None = None


def valid_thanos_url(value: str | None) -> bool:
    try:
        url = urlsplit(value or "")
        return (
            url.scheme == "https" and bool(url.hostname) and url.username is None
            and url.password is None and not url.query and not url.fragment
            and "/api/v1/" not in url.path
            and not any(char.isspace() for char in value or "")
            and (url.port is None or 0 < url.port <= 65535)
        )
    except ValueError:
        return False


def retention_start(session: Session, today: date) -> date:
    config = session.get(AppConfig, "SNAPSHOT_RETENTION_DAYS")
    return today - timedelta(days=int(config.value) if config else 30)


def test_monitoring_connection(base_url: str, token: str) -> None:
    response = requests.get(
        f"{base_url.rstrip('/')}/api/v1/query",
        params={"query": "vector(1)"}, headers={"Authorization": f"Bearer {token}"},
        timeout=(5, 10), allow_redirects=False, verify=False,
    )
    if response.status_code != 200:
        raise ThanosQueryError(f"Thanos returned HTTP {response.status_code}")
    payload = response.json()
    if payload.get("status") != "success" or payload.get("data", {}).get("resultType") != "vector":
        raise ThanosQueryError("Thanos did not return a valid query result")


def query_range(
    client: requests.Session, base_url: str, query: str, start: datetime, end: datetime,
    *, label: str, progress, attempts: int,
):
    for attempt in range(1, attempts + 1):
        if progress:
            progress(f"Querying {label} (attempt {attempt} of {attempts})")
        try:
            response = client.get(
                f"{base_url.rstrip('/')}/api/v1/query_range",
                params={
                    "query": query,
                    "start": (start + timedelta(hours=1)).isoformat(),
                    "end": end.isoformat(),
                    "step": "1h",
                    "timeout": "45s",
                    "partial_response": "false",
                },
                timeout=(5, 55),
                allow_redirects=False,
                verify=False,
            )
        except (requests.Timeout, requests.ConnectionError) as error:
            if attempt == attempts:
                reason = "timed out" if isinstance(error, requests.Timeout) else "could not connect to Thanos"
                attempt_label = "attempt" if attempt == 1 else "attempts"
                raise ThanosQueryError(f"{label} query {reason} after {attempt} {attempt_label}") from error
        else:
            if response.status_code not in (408, 429, 500, 502, 503, 504) or attempt == attempts:
                break
        if progress:
            progress(f"Retrying {label} shortly (attempt {attempt + 1} of {attempts})")
        sleep(attempt)
    if response.status_code != 200:
        raise ThanosQueryError(f"{label} query: Thanos returned HTTP {response.status_code} (attempt {attempt} of {attempts})")
    payload = response.json()
    if payload.get("status") != "success" or payload.get("warnings"):
        raise ThanosQueryError("Thanos did not return a complete query result")
    if payload["data"]["resultType"] != "matrix":
        raise ThanosQueryError("Thanos did not return a range result")
    for series in payload["data"]["result"]:
        for timestamp, raw_value in series["values"]:
            seconds = timestamp - start.timestamp()
            if seconds % 3600 or not 3600 <= seconds <= (end - start).total_seconds():
                raise ThanosQueryError("Thanos returned an unexpected hour boundary")
            value = float(raw_value)
            if math.isfinite(value):
                yield series["metric"], int(seconds // 3600) - 1, value


def fetch_day(cluster: Cluster, day: date, *, progress=None, attempts=1) -> list[NamespaceCPUDaily]:
    start, end = day_bounds(day)
    hours = (end - start) // timedelta(hours=1)
    usage, quota, request_quota, ambiguous, mapids = {}, {}, {}, {}, {}
    memory = {field: {} for field in MEMORY_QUERIES}
    if not valid_thanos_url(cluster.thanos_url):
        raise ThanosQueryError("Configure an HTTPS Thanos base URL")
    with requests.Session() as client:
        client.headers["Authorization"] = f"Bearer {cluster.token}"
        for label, query, data in (
            ("CPU usage", USAGE_QUERY, usage), ("CPU limits quota", QUOTA_QUERY, quota),
            ("CPU requests quota", REQUEST_QUOTA_QUERY, request_quota),
            ("quota conflicts", AMBIGUOUS_QUERY, ambiguous),
        ):
            for metric, hour, value in query_range(client, cluster.thanos_url, query, start, end,
                                                  label=label, progress=progress, attempts=attempts):
                data.setdefault(metric["namespace"], [None] * hours)[hour] = value
        for metric, hour, _ in query_range(client, cluster.thanos_url, MAPID_QUERY, start, end,
                                           label="MAPIDs", progress=progress, attempts=attempts):
            labels = mapids.setdefault(metric["namespace"], [set() for _ in range(hours)])
            labels[hour].add(metric["label_mapid"])
        for field, (label, query) in MEMORY_QUERIES.items():
            for metric, hour, value in query_range(client, cluster.thanos_url, query, start, end,
                                                  label=label, progress=progress, attempts=attempts):
                memory[field].setdefault(metric["namespace"], [None] * hours)[hour] = value / 1024**3

    rows = []
    namespaces = usage.keys() | quota.keys() | request_quota.keys() | ambiguous.keys()
    for samples in memory.values():
        namespaces.update(samples)
    for namespace in namespaces:
        hourly_quota = quota.get(namespace, [None] * hours)
        for hour, count in enumerate(ambiguous.get(namespace, [])):
            if count is not None:
                hourly_quota[hour] = None
        rows.append(NamespaceCPUDaily(
            cluster_id=cluster.id, day=day, eastern_day=True, namespace=namespace,
            cluster_name=cluster.name, environment=cluster.environment,
            usage_json=json.dumps(usage.get(namespace, [None] * hours)),
            quota_json=json.dumps(hourly_quota),
            cpu_request_quota_vcpu=request_quota.get(namespace, [None])[-1],
            memory_json=json.dumps({field: samples[namespace] for field, samples in memory.items()
                                    if namespace in samples}),
            mapids_json=json.dumps([
                next(iter(labels)) if len(labels) == 1 else None
                for labels in mapids.get(namespace, [set()] * hours)
            ]),
            ambiguous_quota=ambiguous.get(namespace, [None])[-1] is not None,
        ))
    return rows


def collect_day(cluster_id: int, day: date, *, manual: bool = False, progress=None) -> CollectionResult:
    with Session(engine) as session:
        cluster = session.get(Cluster, cluster_id)
        if not cluster or not cluster.namespace_cpu_enabled:
            return CollectionResult("Skipped", "Namespace CPU collection is disabled for this cluster.")
        existing = session.get(NamespaceCPUCollection, (cluster_id, day))
        if not manual and existing and existing.eastern_day and existing.status == "Collected":
            return CollectionResult("Collected")
    try:
        rows = fetch_day(cluster, day, progress=progress, attempts=3 if manual else 1)
    except Exception as error:
        detail = str(error) if isinstance(error, ThanosQueryError) else type(error).__name__
        logger.warning("Namespace CPU collection failed for cluster %s on %s (%s)", cluster.id, day, detail)
        rows, status = [], "Failed"
    else:
        status, detail = ("Collected", None) if rows else ("No data", None)

    if progress:
        progress("Saving collection results")
    with Session(engine) as session:
        session.execute(text("BEGIN IMMEDIATE"))
        current = session.get(Cluster, cluster.id)
        if not current or not current.namespace_cpu_enabled or (
            current.thanos_url, current.token, current.unique_id, current.namespace_cpu_since
        ) != (cluster.thanos_url, cluster.token, cluster.unique_id, cluster.namespace_cpu_since):
            return CollectionResult("Skipped", "The cluster's monitoring configuration changed during collection.")
        if day < retention_start(session, eastern_today()):
            return CollectionResult("Skipped", "This day is outside snapshot retention.")
        existing = session.get(NamespaceCPUCollection, (cluster.id, day))
        if existing and not existing.eastern_day and status != "Collected":
            existing.detail = detail or "Thanos returned no namespace data for the Eastern day."
            session.commit()
            return CollectionResult(status, existing.detail)
        if existing and existing.eastern_day and existing.status == "Collected":
            if not manual:
                return CollectionResult("Collected")
            if status != "Collected":
                message = detail or "Thanos returned no namespace data."
                return CollectionResult(status, f"{message} Previously collected data is still available.")
        if status != "Failed":
            session.execute(delete(NamespaceCPUDaily).where(
                NamespaceCPUDaily.cluster_id == cluster.id, NamespaceCPUDaily.day == day,
            ))
            session.add_all(rows)
        session.merge(NamespaceCPUCollection(cluster_id=cluster.id, day=day, eastern_day=True, status=status, detail=detail))
        session.commit()
    return CollectionResult(status, detail)


def namespace_cpu_schedule_enabled(session: Session) -> bool:
    config = session.get(AppConfig, "NAMESPACE_CPU_AUTO_COLLECT")
    return config is None or config.value.lower() == "true"


def collect_namespace_cpu():
    today = eastern_today()
    with Session(engine) as session:
        cutoff = retention_start(session, today)
        for model in (NamespaceCPUDaily, NamespaceCPUCollection):
            session.execute(delete(model).where(model.day < cutoff))
        session.commit()
        if not namespace_cpu_schedule_enabled(session):
            return
        clusters = session.exec(select(Cluster).where(Cluster.namespace_cpu_enabled == True)).all()
        legacy_starts = {}
        for cluster_id, day in session.exec(select(
            NamespaceCPUCollection.cluster_id, NamespaceCPUCollection.day,
        ).where(NamespaceCPUCollection.eastern_day == False)):
            legacy_starts[cluster_id] = min(day, legacy_starts.get(cluster_id, day))
        completed = set(session.exec(select(
            NamespaceCPUCollection.cluster_id, NamespaceCPUCollection.day,
        ).where(NamespaceCPUCollection.eastern_day == True,
                NamespaceCPUCollection.status.in_(["Collected", "No data"]))).all())

    for cluster in clusters:
        first = cluster.namespace_cpu_since or today - timedelta(days=1)
        first = max(cutoff, min(first, legacy_starts.get(cluster.id, first)))
        for offset in range(1, (today - first).days + 1):
            day = today - timedelta(days=offset)
            if (cluster.id, day) not in completed:
                result = collect_day(cluster.id, day)
                if result.status in ("Failed", "Skipped"):
                    break
