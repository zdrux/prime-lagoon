import asyncio
import json

import httpx
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.dependencies import get_current_user, get_current_user_optional
from app.main import app
from app.models import AppConfig, User
from app.services.page_access import (
    get_page_access_config,
    set_page_access_config,
    template_page_access,
    user_can_access_page,
)


def test_reports_enforces_operator_minimum_for_existing_and_new_settings(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'page-access.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(AppConfig(
            key="PAGE_ACCESS_CONFIG",
            value=json.dumps({"reports": "user"}),
        ))
        session.commit()

        regular_user = User(username="reader", role="user")
        operator = User(username="operator", role="operator")
        admin = User(username="admin", role="admin")

        assert get_page_access_config(session)["reports"] == "operator"
        assert user_can_access_page(regular_user, "reports", session) is False
        assert template_page_access(regular_user, session)["reports"] is False
        assert user_can_access_page(operator, "reports", session) is True
        assert user_can_access_page(admin, "reports", session) is True

        saved = set_page_access_config(session, {"reports": "user"})
        assert saved["reports"] == "operator"
        assert get_page_access_config(session)["reports"] == "operator"


def test_reports_navigation_page_and_api_require_operator(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'reports-access.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    def override_session():
        with Session(engine) as session:
            yield session

    async def request(path):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    app.dependency_overrides[get_session] = override_session
    try:
        regular_user = User(username="reader", role="user")
        app.dependency_overrides[get_current_user_optional] = lambda: regular_user
        app.dependency_overrides[get_current_user] = lambda: regular_user

        dashboard = asyncio.run(request("/dashboard"))
        assert dashboard.status_code == 200
        assert 'href="/reports"' not in dashboard.text
        assert asyncio.run(request("/reports")).status_code == 403
        assert asyncio.run(request("/api/reporting/templates")).status_code == 403

        operator = User(username="operator", role="operator")
        app.dependency_overrides[get_current_user_optional] = lambda: operator
        app.dependency_overrides[get_current_user] = lambda: operator

        dashboard = asyncio.run(request("/dashboard"))
        assert 'href="/reports"' in dashboard.text
        assert 'id="sidebar-administration"' in dashboard.text
        assert 'id="snapshot-selector"' in dashboard.text
        assert "sidebar-admin-submenu open" not in dashboard.text
        administration_position = dashboard.text.index('id="sidebar-administration"')
        dashboard_position = dashboard.text.index('href="/dashboard"', administration_position)
        license_position = dashboard.text.index('href="/license-analytics"')
        assert administration_position < dashboard.text.index('href="/storage-analytics"') < dashboard_position
        assert administration_position < dashboard.text.index('href="/operators"') < dashboard_position
        assert dashboard_position < license_position

        reports_page = asyncio.run(request("/reports"))
        assert reports_page.status_code == 200
        assert "sidebar-admin-submenu open" in reports_page.text
        assert asyncio.run(request("/api/reporting/templates")).status_code == 200
    finally:
        app.dependency_overrides.clear()
