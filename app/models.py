from typing import Optional
from datetime import datetime
from sqlmodel import Field, SQLModel, Column, Text
from pydantic import field_validator

class ClusterBase(SQLModel):
    name: str = Field(index=True, unique=True)
    unique_id: Optional[str] = Field(default=None, index=True) # OpenShift Cluster ID
    api_url: str
    token: str
    datacenter: Optional[str] = Field(default="Azure")
    environment: Optional[str] = Field(default="DEV")
    tags: Optional[str] = None # JSON string of KV pairs

class Cluster(ClusterBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

class ClusterCreate(ClusterBase):
    tags: Optional[str] = None # JSON string

class ClusterRead(ClusterBase):
    id: int

    @field_validator('token')
    @classmethod
    def mask_token(cls, v: str) -> str:
        return "********"

class ClusterUpdate(SQLModel):
    name: Optional[str] = None
    unique_id: Optional[str] = None
    api_url: Optional[str] = None
    token: Optional[str] = None
    datacenter: Optional[str] = None
    environment: Optional[str] = None
    tags: Optional[str] = None # JSON string of KV pairs

class AuditBundle(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: Optional[str] = None
    # Bundle defines scope for children
    match_datacenter: Optional[str] = None 
    match_environment: Optional[str] = None
    tags: Optional[str] = None # JSON string required tags

class AuditRule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: Optional[str] = None
    tags: Optional[str] = None # JSON string required tags (if ad-hoc)
    
    # Bundle Link
    bundle_id: Optional[int] = Field(default=None, foreign_key="auditbundle.id")
    
    # Target
    resource_kind: str
    api_version: str
    namespace: Optional[str] = None # If null, cluster scoped or all namespaces? Let's assume cluster scoped or we just fetch 'All'
    
    # Logic
    check_type: str = Field(default="VALIDATION") # "VALIDATION", "EXISTENCE", "FORBIDDANCE"
    field_path: Optional[str] = Field(default="") # spec.foo.bar
    operator: str # "equals", "exists", "contains"
    expected_value: Optional[str] = None
    match_resource_name: Optional[str] = None # New: Target specific resource by name
    
    # Advanced Logic
    condition_logic: str = Field(default="AND") # "AND" or "OR"
    extra_conditions: Optional[str] = None # JSON list of {field_path, operator, expected_value}
    
    # Scope
    match_datacenter: Optional[str] = None # "Azure", "HCI", or None for all
    match_environment: Optional[str] = None
    is_enabled: bool = Field(default=True)

class ComplianceScore(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    cluster_id: int = Field(index=True)
    timestamp: str 
    passed_count: int
    total_count: int
    score: float
    results_json: Optional[str] = None # Detailed list of AuditResult objects

class LicenseUsage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    cluster_id: int = Field(index=True)
    timestamp: str
    node_count: int
    total_vcpu: float
    license_count: int
    details_json: Optional[str] = None # Detailed breakdown for audit

class MapidLicenseUsage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    cluster_id: int = Field(index=True)
    timestamp: str = Field(index=True)
    mapid: str = Field(index=True)
    lob: Optional[str] = None
    node_count: int
    total_vcpu: float
    license_count: int


class LicenseRule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    rule_type: str = Field(default="name_match") # "name_match", "label_match"
    match_value: str # regex for name, or "key=value" for label
    action: str = Field(default="INCLUDE") # "INCLUDE", "EXCLUDE"
    order: int = Field(default=0)
    is_active: bool = Field(default=True)

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    is_admin: bool = Field(default=False)

class AppConfig(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: Optional[str] = None

class ClusterSnapshot(SQLModel, table=True):
    __tablename__ = "clustersnapshot"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    cluster_id: int = Field(foreign_key="cluster.id", index=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)
    status: str = Field(default="Success") # Success, Partial, Failed
    
    # Identity freeze
    captured_name: Optional[str] = None
    captured_unique_id: Optional[str] = None
    
    # Store key metrics for quick lookup/graphs
    node_count: int = Field(default=0)
    vcpu_count: float = Field(default=0.0)
    project_count: int = Field(default=0)
    machineset_count: int = Field(default=0)
    machine_count: int = Field(default=0)
    license_count: int = Field(default=0)
    licensed_node_count: int = Field(default=0)
    
    # Store full data dump
    data_json: str = Field(sa_column=Column(Text)) # Stores compressed/large JSON blob
    service_mesh_json: Optional[str] = Field(default=None, sa_column=Column(Text)) # Stores detected mesh details

class NamespaceExclusionRule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    match_pattern: str # Regex or glob pattern
    is_active: bool = Field(default=True)
    description: Optional[str] = None

