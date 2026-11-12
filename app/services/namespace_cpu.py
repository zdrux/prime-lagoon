import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlsplit

import requests
from sqlalchemy import delete, text
from sqlmodel import Session, select

from app.database import engine
from app.models import AppConfig, Cluster, NamespaceCPUCollection, NamespaceCPUDaily

logger = logging.getLogger(__name__)

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
    f'avg_over_time((sum by (namespace) ({QUOTA_OBJECTS}) '
    f'and on (namespace) ({QUOTA_COUNT} == 1))[1h:5m])'
)
AMBIGUOUS_QUERY = f'max_over_time(({QUOTA_COUNT})[1h:5m]) > 1'
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


def query_range(client: requests.Session, base_url: str, query: str, midnight: datetime):
    response = client.get(
        f"{base_url.rstrip('/')}/api/v1/query_range",
        params={
            "query": query,
            "start": (midnight + timedelta(hours=1)).isoformat(),
            "end": (midnight + timedelta(days=1)).isoformat(),
            "step": "1h",
            "timeout": "45s",
            "partial_response": "false",
        },
        timeout=(5, 55),
        allow_redirects=False,
        verify=False,
    )
    if response.status_code != 200:
        raise ThanosQueryError(f"Thanos returned HTTP {response.status_code}")
    payload = response.json()
    if payload.get("status") != "success" or payload.get("warnings"):
        raise ThanosQueryError("Thanos did not return a complete query result")
    if payload["data"]["resultType"] != "matrix":
        raise ThanosQueryError("Thanos did not return a range result")
    for series in payload["data"]["result"]:
        for timestamp, raw_value in series["values"]:
            seconds = timestamp - midnight.timestamp()
            if seconds % 3600 or not 3600 <= seconds <= 86400:
                raise ThanosQueryError("Thanos returned an unexpected hour boundary")
            value = float(raw_value)
            if math.isfinite(value):
                yield series["metric"], int(seconds // 3600) - 1, value


def fetch_day(cluster: Cluster, day: date) -> list[NamespaceCPUDaily]:
    midnight = datetime.combine(day, time.min, tzinfo=timezone.utc)
    usage, quota, ambiguous, mapids = {}, {}, {}, {}
    if not valid_thanos_url(cluster.thanos_url):
        raise ThanosQueryError("Configure an HTTPS Thanos base URL")
    with requests.Session() as client:
        client.headers["Authorization"] = f"Bearer {cluster.token}"
        for query, data in ((USAGE_QUERY, usage), (QUOTA_QUERY, quota), (AMBIGUOUS_QUERY, ambiguous)):
            for metric, hour, value in query_range(client, cluster.thanos_url, query, midnight):
                data.setdefault(metric["namespace"], [None] * 24)[hour] = value
        for metric, hour, _ in query_range(client, cluster.thanos_url, MAPID_QUERY, midnight):
            labels = mapids.setdefault(metric["namespace"], [set() for _ in range(24)])
            labels[hour].add(metric["label_mapid"])

    rows = []
    for namespace in usage.keys() | quota.keys() | ambiguous.keys():
        hourly_quota = quota.get(namespace, [None] * 24)
        for hour, count in enumerate(ambiguous.get(namespace, [])):
            if count is not None:
                hourly_quota[hour] = None
        rows.append(NamespaceCPUDaily(
            cluster_id=cluster.id, day=day, namespace=namespace,
            cluster_name=cluster.name, environment=cluster.environment,
            usage_json=json.dumps(usage.get(namespace, [None] * 24)),
            quota_json=json.dumps(hourly_quota),
            mapids_json=json.dumps([
                next(iter(labels)) if len(labels) == 1 else None
                for labels in mapids.get(namespace, [set()] * 24)
            ]),
            ambiguous_quota=namespace in ambiguous,
        ))
    return rows


def collect_day(cluster_id: int, day: date, *, manual: bool = False) -> CollectionResult:
    with Session(engine) as session:
        cluster = session.get(Cluster, cluster_id)
        if not cluster or not cluster.namespace_cpu_enabled:
            return CollectionResult("Skipped", "Namespace CPU collection is disabled for this cluster.")
        if not manual and cluster.namespace_cpu_since and cluster.namespace_cpu_since > day:
            return CollectionResult("Skipped")
        existing = session.get(NamespaceCPUCollection, (cluster_id, day))
        if not manual and existing and existing.status == "Collected":
            return CollectionResult("Collected")
    try:
        rows = fetch_day(cluster, day)
    except Exception as error:
        detail = str(error) if isinstance(error, ThanosQueryError) else type(error).__name__
        logger.warning("Namespace CPU collection failed for cluster %s on %s (%s)", cluster.id, day, detail)
        rows, status = [], "Failed"
    else:
        status, detail = ("Collected", None) if rows else ("No data", None)

    with Session(engine) as session:
        session.execute(text("BEGIN IMMEDIATE"))
        current = session.get(Cluster, cluster.id)
        if not current or not current.namespace_cpu_enabled or (
            current.thanos_url, current.token, current.unique_id, current.namespace_cpu_since
        ) != (cluster.thanos_url, cluster.token, cluster.unique_id, cluster.namespace_cpu_since):
            return CollectionResult("Skipped", "The cluster's monitoring configuration changed during collection.")
        if day < retention_start(session, datetime.now(timezone.utc).date()):
            return CollectionResult("Skipped", "This day is outside snapshot retention.")
        existing = session.get(NamespaceCPUCollection, (cluster.id, day))
        if existing and existing.status == "Collected":
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
        session.merge(NamespaceCPUCollection(cluster_id=cluster.id, day=day, status=status, detail=detail))
        session.commit()
    return CollectionResult(status, detail)


def collect_namespace_cpu():
    today = datetime.now(timezone.utc).date()
    with Session(engine) as session:
        cutoff = retention_start(session, today)
        for model in (NamespaceCPUDaily, NamespaceCPUCollection):
            session.execute(delete(model).where(model.day < cutoff))
        session.commit()
        clusters = session.exec(select(Cluster).where(Cluster.namespace_cpu_enabled == True)).all()
        completed = set(session.exec(select(
            NamespaceCPUCollection.cluster_id, NamespaceCPUCollection.day,
        ).where(NamespaceCPUCollection.status.in_(["Collected", "No data"]))).all())

    for cluster in clusters:
        first = max(cutoff, cluster.namespace_cpu_since or today - timedelta(days=1))
        for offset in range(1, (today - first).days + 1):
            day = today - timedelta(days=offset)
            if (cluster.id, day) not in completed:
                result = collect_day(cluster.id, day)
                if result.status in ("Failed", "Skipped"):
                    break
