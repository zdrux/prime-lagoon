import math
import json
from typing import List, Dict, Any, Optional
from app.services.ocp import parse_cpu, get_val
from app.models import LicenseRule

def calculate_licenses(nodes: List[Any], rules: List[LicenseRule] = [], default_include: bool = False) -> Dict[str, Any]:
    """
    Calculates RedHat license usage based on nodes and LicenseRules.
    Logic:
    1. Base status is determined by default_include (True = INCLUDE ALL, False = EXCLUDE ALL).
    2. If base is EXCLUDE: If node matches any INCLUDE rule, status becomes INCLUDED.
    3. If node is (preliminary) INCLUDED: If node matches any EXCLUDE rule, status becomes EXCLUDED (override).
    Formula: ceil(vCPU / 4) per node.
    """
    
    total_nodes = 0
    total_vcpu = 0.0
    total_licenses = 0
    
    details = []
    
    # Pre-process rules
    include_rules = [r for r in rules if r.action == "INCLUDE" and r.is_active]
    exclude_rules = [r for r in rules if r.action == "EXCLUDE" and r.is_active]
    
    for node in nodes:
        name = get_val(node, 'metadata.name')
        labels = get_val(node, 'metadata.labels')
        node_labels = labels if labels else {} # Handle None
        
        is_included = default_include
        inclusion_reason = "Include All by Default" if default_include else "Exclude All by Default"
        
        # 1. Check Includes (Only if not already included by default)
        if not is_included:
            if not include_rules:
                 inclusion_reason = "No matching include rule"
            else:
                 for r in include_rules:
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
             raw_cpu = get_val(node, 'status.capacity.cpu')
             vcpu = parse_cpu(raw_cpu)
             licenses = math.ceil(vcpu / 4)
             
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
