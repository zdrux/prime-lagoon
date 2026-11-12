from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Cluster, ClusterSnapshot
from app.routers.dashboard import get_available_snapshots, get_snapshot_for_cluster
from app.services import poller
from app.time_utils import parse_snapshot_time


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'snapshots.db').as_posix()}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Cluster(id=1, name="test", api_url="https://example.invalid", token="unused"))
        session.commit()
    yield engine
    engine.dispose()


@pytest.mark.parametrize("value", ["2026-10-13T12:00:00", "2026-10-13 12:00:00Z",
                                  "2026-10-13T08:00:00-04:00", "2026-10-13T14:00:00+02:00"])
def test_snapshot_input_is_utc(value):
    assert parse_snapshot_time(value) == datetime(2026, 10, 13, 12, tzinfo=timezone.utc)


def test_legacy_rows_remain_available_and_selectable(engine):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    recent = now - timedelta(days=15)
    with Session(engine) as session:
        # Simulate rows written by the old naive-UTC application.
        for stamp in [recent, recent - timedelta(seconds=60), now - timedelta(days=40)]:
            snap = ClusterSnapshot(cluster_id=1, timestamp=stamp, data_json="{}")
            session.add(snap)
            session.flush()
            session.execute(text("UPDATE clustersnapshot SET timestamp = :stamp WHERE id = :id"),
                            {"stamp": stamp.strftime("%Y-%m-%d %H:%M:%S.000000"), "id": snap.id})
        session.commit()
        choices = get_available_snapshots(session)
        assert choices == [recent.strftime("%Y-%m-%dT%H:%M:%S")]
        snap = get_snapshot_for_cluster(session, 1, parse_snapshot_time(choices[0]))
        assert snap.timestamp == recent
        assert snap.timestamp.utcoffset() == timedelta(0)
        poller.cleanup_old_snapshots(session)
        assert len(session.exec(select(ClusterSnapshot)).all()) == 2


@pytest.mark.parametrize("stamp", [None, datetime(2026, 10, 13, 12),
                                   datetime(2026, 10, 13, 8, tzinfo=timezone(timedelta(hours=-4)))])
def test_poller_commits_snapshot_with_utc_timestamp(engine, monkeypatch, stamp):
    monkeypatch.setattr(poller, "engine", engine)
    monkeypatch.setattr(poller, "fetch_resources", lambda *args, **kwargs: [])
    monkeypatch.setattr(poller, "get_service_mesh_details", lambda *args: {})
    monkeypatch.setattr(poller, "get_argocd_details", lambda *args: {})
    poller.poll_cluster(1, [], run_timestamp=stamp)
    with Session(engine) as session:
        snap = session.exec(select(ClusterSnapshot)).one()
        assert snap.status == "Success"
        assert snap.timestamp.utcoffset() == timedelta(0)
        if stamp is not None:
            assert snap.timestamp == datetime(2026, 10, 13, 12, tzinfo=timezone.utc)


def test_snapshot_default_is_aware():
    assert ClusterSnapshot(cluster_id=1, data_json="{}").timestamp.utcoffset() == timedelta(0)
