import json
from typing import Dict, List

from fastapi import HTTPException
from sqlmodel import Session

from app.models import AppConfig, User


PAGE_ACCESS_CONFIG_KEY = "PAGE_ACCESS_CONFIG"

ROLES = ["user", "operator", "admin"]
ROLE_LABELS = {
    "user": "User",
    "operator": "Operator",
    "admin": "Admin",
}

PAGE_DEFINITIONS: List[Dict[str, str]] = [
    {
        "key": "dashboard",
        "label": "All Clusters",
        "description": "Main cluster inventory dashboard.",
        "default_role": "user",
    },
    {
        "key": "cluster_resources",
        "label": "Cluster Resource Subpages",
        "description": "Per-cluster Nodes, Machines, MachineSets, Projects, and Autoscalers views.",
        "default_role": "user",
    },
    {
        "key": "cluster_storage",
        "label": "Cluster PV/PVC Storage",
        "description": "Per-cluster PV/PVC storage page.",
        "default_role": "user",
    },
    {
        "key": "audit_rules",
        "label": "Compliance Rules",
        "description": "Compliance rule library.",
        "default_role": "user",
    },
    {
        "key": "compliance",
        "label": "Run Compliance",
        "description": "Compliance run and results page.",
        "default_role": "user",
    },
    {
        "key": "license_analytics",
        "label": "License Analytics (MAPID)",
        "description": "Fleet-wide license analytics and MAPID attribution.",
        "default_role": "operator",
    },
    {
        "key": "storage_analytics",
        "label": "Storage Analytics",
        "description": "Fleet-wide PV/PVC storage analytics.",
        "default_role": "operator",
    },
    {
        "key": "operators",
        "label": "Cluster Operators",
        "description": "Fleet-wide operator matrix and pending upgrade signals.",
        "default_role": "operator",
    },
    {
        "key": "admin",
        "label": "Cluster Config and Snapshots",
        "description": "Cluster configuration, scheduler, snapshots, and DB stats.",
        "default_role": "operator",
    },
]

PAGE_DEFAULTS = {page["key"]: page["default_role"] for page in PAGE_DEFINITIONS}


def normalize_role(role: str) -> str:
    role = (role or "").lower()
    return role if role in ROLES else "user"


def get_page_access_config(session: Session) -> Dict[str, str]:
    config = session.get(AppConfig, PAGE_ACCESS_CONFIG_KEY)
    saved = {}
    if config and config.value:
        try:
            saved = json.loads(config.value)
        except json.JSONDecodeError:
            saved = {}

    access = PAGE_DEFAULTS.copy()
    for key, value in saved.items():
        if key in access:
            access[key] = normalize_role(value)
    return access


def set_page_access_config(session: Session, access: Dict[str, str]) -> Dict[str, str]:
    clean = PAGE_DEFAULTS.copy()
    for key, value in access.items():
        if key in clean:
            clean[key] = normalize_role(value)

    config = session.get(AppConfig, PAGE_ACCESS_CONFIG_KEY)
    if not config:
        config = AppConfig(key=PAGE_ACCESS_CONFIG_KEY)
    config.value = json.dumps(clean, sort_keys=True)
    session.add(config)
    session.commit()
    return clean


def role_allows(user: User, required_role: str) -> bool:
    if not user:
        return False
    user_role = "admin" if user.is_admin else normalize_role(user.role)
    return ROLES.index(user_role) >= ROLES.index(normalize_role(required_role))


def user_can_access_page(user: User, page_key: str, session: Session) -> bool:
    required_role = get_page_access_config(session).get(page_key, PAGE_DEFAULTS.get(page_key, "user"))
    return role_allows(user, required_role)


def require_page_access(page_key: str, user: User, session: Session) -> User:
    if not user_can_access_page(user, page_key, session):
        required = ROLE_LABELS[get_page_access_config(session).get(page_key, PAGE_DEFAULTS.get(page_key, "user"))]
        raise HTTPException(status_code=403, detail=f"{required} permissions required")
    return user


def template_page_access(user: User, session: Session) -> Dict[str, bool]:
    return {
        page["key"]: user_can_access_page(user, page["key"], session)
        for page in PAGE_DEFINITIONS
    }
