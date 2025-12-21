import math
import json
from typing import List, Dict, Any, Optional
from app.services.ocp import parse_cpu

from app.models import LicenseRule

def calculate_licenses(nodes: List[Any], rules: List[LicenseRule] = []) -> Dict[str, Any]:
    """
    Calculates RedHat license usage based on nodes and LicenseRules.
    Logic:
    1. Default status is IGNORED.
    2. If node matches any INCLUDE rule, status becomes INCLUDED.
    3. If node matches any EXCLUDE rule, status becomes EXCLUDED (overrides INCLUDE).
    Formula: ceil(vCPU / 2) per node.
    """
    
    total_nodes = 0
    total_vcpu = 0.0
    total_licenses = 0
    
    details = []
    
    # Pre-process rules
    include_rules = [r for r in rules if r.action == "INCLUDE" and r.is_active]
    exclude_rules = [r for r in rules if r.action == "EXCLUDE" and r.is_active]
    
    for node in nodes:
        name = node.metadata.name
        node_labels = node.metadata.labels or {}
        
        is_included = False
        inclusion_reason = "Default Ignore"
        
        # 1. Check Includes (Union)
        # If no include rules exist, should we include ALL? 
        # Requirement implies "define rules for including". So if no rules, nothing included?
        # Let's assume: If NO include rules are defined, we default to INCLUDE ALL (legacy behavior compatibility & ease of use).
        # OR, we default to EXCLUDE ALL.
        # Given the previous implementation had defaults, let's say:
        # If no rules exist at all, include all? Or force user to create rules.
        # Let's go with: If NO include rules, everything is INCLUDED by default (unless excluded).
        # Wait, the user asked "define multiple rules for *including* nodes". 
        # Let's stick to safe approach: Default is IGNORED unless Included.
        # BUT, for migration, we might want to seed a "Include All" rule.
        # Let's implement: Default IGNORED.
        
        if not include_rules:
             # Fallback: if no include rules are set, maybe we should include everything?
             # For now, let's stick to strict: Must match an include rule.
             # Verification plan added a "Worker" include rule, so strict is good.
             is_included = False
             inclusion_reason = "No matching include rule"
        else:
             for r in include_rules:
                 matched = False
                 if r.rule_type == "name_match":
                     import re
                     # Simple regex or glob? Let's assume Regex for power
                     try:
                         if re.search(r.match_value, name):
                             matched = True
                     except:
                         pass
                 elif r.rule_type == "label_match":
                     # Value format "key=value" or just "key" (exists)
                     if "=" in r.match_value:
                         k, v = r.match_value.split("=", 1)
                         if node_labels.get(k) == v:
                             matched = True
                     else:
                         if r.match_value in node_labels:
                             matched = True
                 
                 if matched:
                     is_included = True
                     inclusion_reason = f"Matched include rule: {r.name}"
                     break
        
        # 2. Check Excludes (Override)
        if is_included:
            for r in exclude_rules:
                 matched = False
                 if r.rule_type == "name_match":
                     import re
                     try:
                         if re.search(r.match_value, name):
                             matched = True
                     except:
                         pass
                 elif r.rule_type == "label_match":
                     if "=" in r.match_value:
                         k, v = r.match_value.split("=", 1)
                         if node_labels.get(k) == v:
                             matched = True
                     else:
                         if r.match_value in node_labels:
                             matched = True
                 
                 if matched:
                     is_included = False
                     inclusion_reason = f"Excluded by rule: {r.name}"
                     break # Stop checking excludes
        
        # 3. Calculate
        if is_included:
             raw_cpu = node.status.capacity.cpu
             vcpu = parse_cpu(raw_cpu)
             licenses = math.ceil(vcpu / 2)
             
             total_nodes += 1
             total_vcpu += vcpu
             total_licenses += licenses
             
             details.append({
                "name": name,
                "status": "INCLUDED",
                "reason": inclusion_reason,
                "vcpu": vcpu,
                "licenses": licenses
            })
        else:
             details.append({
                "name": name,
                "status": "EXCLUDED",
                "reason": inclusion_reason,
                "vcpu": 0,
                "licenses": 0
            })
             
    return {
        "node_count": total_nodes,
        "total_vcpu": total_vcpu,
        "total_licenses": total_licenses,
        "details": details
    }
