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
    
    for node in nodes:
        name = get_val(node, 'metadata.name')
        labels = get_val(node, 'metadata.labels')
        node_labels = labels if labels else {}
        
        is_included = default_include
        inclusion_reason = "Include All by Default" if default_include else "Exclude All by Default"
        
        # Sequential Rule Check: First match wins
        for r in rules:
            if not r.is_active:
                continue
                
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
                is_included = (r.action == "INCLUDE")
                inclusion_reason = f"Matched rule: {r.name} ({r.action})"
                break # First match wins!
        
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

def calculate_mapid_usage(nodes: List[Any], rules: List[LicenseRule] = [], default_include: bool = False) -> List[Dict[str, Any]]:
    """
    Calculates license usage grouped by MAPID label.
    Also captures LOB if present.
    """
    # 1. First Pass: Calculate basic license status for all nodes
    base_calc = calculate_licenses(nodes, rules, default_include)
    details = base_calc["details"]
    
    # Map back to original nodes to get labels? 
    # Actually calculate_licenses returns 'details' with name/status/reason/vcpu/licenses 
    # but not the labels. We need to iterate nodes again or change calculate_licenses.
    # To minimize risk, let's just re-iterate nodes since we have them.
    # We can match by name if needed, or better, just replicate the logic since it's cleaner
    # OR we can just modify calculate_licenses to return labels? 
    # Let's iterate nodes again, using the decision logic (it's fast).
    
    groups = {} # mapid -> {lob, nodes, vcpu, licenses}
    
    # Pre-process details map for O(1) lookup if needed, 
    # but strictly speaking calculate_licenses is deterministic given same inputs.
    # Let's just do the inclusion check inline to be efficient and access labels directly.
    
    for node in nodes:
        name = get_val(node, 'metadata.name')
        labels = get_val(node, 'metadata.labels') or {}
        
        # --- REPEAT INCLUSION LOGIC START ---
        is_included = default_include
        for r in rules:
            if not r.is_active: continue
            matched = False
            if r.rule_type == "name_match":
                import re
                try:
                    if re.search(r.match_value, name): matched = True
                except: pass
            elif r.rule_type == "label_match":
                if "=" in r.match_value:
                    k, v = r.match_value.split("=", 1)
                    if labels.get(k) == v: matched = True
                else:
                    if r.match_value in labels: matched = True
            
            if matched:
                is_included = (r.action == "INCLUDE")
                break
        # --- REPEAT INCLUSION LOGIC END ---
        
        if is_included:
            raw_cpu = get_val(node, 'status.capacity.cpu')
            vcpu = parse_cpu(raw_cpu)
            licenses = math.ceil(vcpu / 4)
            
            mapid = labels.get('mapid', 'Unmapped')
            lob = labels.get('lob', 'Unknown')
            
            if mapid not in groups:
                groups[mapid] = {
                    "mapid": mapid,
                    "lob": lob, # Take first LOB found for this MAPID
                    "node_count": 0,
                    "total_vcpu": 0.0,
                    "license_count": 0
                }
            
            # Aggregate
            groups[mapid]["node_count"] += 1
            groups[mapid]["total_vcpu"] += vcpu
            groups[mapid]["license_count"] += licenses
            
            # Update LOB if "Unknown" and we found one
            if groups[mapid]["lob"] == "Unknown" and lob != "Unknown":
                groups[mapid]["lob"] = lob

    return list(groups.values())
