import sys
import os

# Add the project root to sys.path
sys.path.append(os.getcwd())

from app.services.license import calculate_licenses
from app.models import LicenseRule

# Mock data
nodes = [
    {
        "metadata": {"name": "node-1", "labels": {"node-role.kubernetes.io/worker": ""}},
        "status": {"capacity": {"cpu": "4"}}
    },
    {
        "metadata": {"name": "node-2", "labels": {"node-role.kubernetes.io/master": ""}},
        "status": {"capacity": {"cpu": "8"}}
    }
]

# Case 1: Default EXCLUDE, no rules
print("Testing Case 1: Default EXCLUDE, No Rules")
res1 = calculate_licenses(nodes, [], default_include=False)
print(f"Total Licenses: {res1['total_licenses']} (Expected: 0)")
assert res1['total_licenses'] == 0

# Case 2: Default INCLUDE, no rules
print("\nTesting Case 2: Default INCLUDE, No Rules")
res2 = calculate_licenses(nodes, [], default_include=True)
print(f"Total Licenses: {res2['total_licenses']} (Expected: 6)") # (4/2) + (8/2) = 2 + 4 = 6
assert res2['total_licenses'] == 6

# Case 3: Default INCLUDE, with EXCLUDE rule
print("\nTesting Case 3: Default INCLUDE, with EXCLUDE workers")
rule = LicenseRule(name="Exclude Workers", rule_type="label_match", match_value="node-role.kubernetes.io/worker", action="EXCLUDE")
res3 = calculate_licenses(nodes, [rule], default_include=True)
print(f"Total Licenses: {res3['total_licenses']} (Expected: 4)") # Only master (8/2)
assert res3['total_licenses'] == 4

# Case 4: Default EXCLUDE, with INCLUDE rule
print("\nTesting Case 4: Default EXCLUDE, with INCLUDE workers")
rule2 = LicenseRule(name="Include Workers", rule_type="label_match", match_value="node-role.kubernetes.io/worker", action="INCLUDE")
res4 = calculate_licenses(nodes, [rule2], default_include=False)
print(f"Total Licenses: {res4['total_licenses']} (Expected: 2)") # Only worker (4/2)
assert res4['total_licenses'] == 2

print("\nVerification Successful!")
