from sqlmodel import Session, select
from app.database import engine
from app.models import Cluster, ClusterSnapshot
import json
from datetime import datetime

# Mock OLM data
MOCK_SUBS = [
    {
        "metadata": {"name": "compliance-operator", "namespace": "openshift-compliance"},
        "spec": {
            "name": "compliance-operator",
            "channel": "stable",
            "installPlanApproval": "Manual",
            "source": "redhat-operators"
        },
        "status": {"installedCSV": "compliance-operator.v1.2.0"}
    },
    {
        "metadata": {"name": "advanced-cluster-management", "namespace": "open-cluster-management"},
        "spec": {
            "name": "advanced-cluster-management",
            "channel": "release-2.9",
            "installPlanApproval": "Automatic",
            "source": "redhat-operators"
        },
        "status": {"installedCSV": "advanced-cluster-management.v2.9.1"}
    }
]

MOCK_CSVS = [
    {
        "metadata": {"name": "compliance-operator.v1.2.0", "namespace": "openshift-compliance"},
        "spec": {
            "version": "1.2.0",
            "displayName": "Compliance Operator",
            "provider": {"name": "Red Hat"},
            "customresourcedefinitions": {
                "owned": [
                    {"name": "compliancesuites.compliance.openshift.io", "kind": "ComplianceSuite", "displayName": "Compliance Suite"},
                    {"name": "compliancescans.compliance.openshift.io", "kind": "ComplianceScan", "displayName": "Compliance Scan"}
                ]
            }
        },
        "status": {"phase": "Succeeded"}
    },
    {
        "metadata": {"name": "advanced-cluster-management.v2.9.1", "namespace": "open-cluster-management"},
        "spec": {
            "version": "2.9.1",
            "displayName": "Advanced Cluster Management",
            "provider": {"name": "Red Hat"},
            "customresourcedefinitions": {
                "owned": [
                    {"name": "multiclusterhubs.operator.open-cluster-management.io", "kind": "MultiClusterHub", "displayName": "MultiCluster Hub"}
                ]
            }
        },
        "status": {"phase": "Succeeded"}
    }
]

def inject():
    print("Injecting Advanced Mock OLM Data...")
    with Session(engine) as session:
        clusters = session.exec(select(Cluster)).all()
        for cluster in clusters:
            print(f"Adding snapshot for {cluster.name}")
            
            data = {
                "subscriptions": MOCK_SUBS,
                "csvs": MOCK_CSVS,
                "nodes": [],
                "projects": [],
                "machines": [],
                "machinesets": []
            }
            
            snap = ClusterSnapshot(
                cluster_id=cluster.id,
                timestamp=datetime.utcnow(),
                status="Success",
                captured_name=cluster.name,
                data_json=json.dumps(data)
            )
            session.add(snap)
        session.commit()
    print("Done.")

if __name__ == "__main__":
    inject()
