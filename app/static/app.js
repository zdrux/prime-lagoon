function toggleClusterMenu(clusterId) {

    const submenu = document.getElementById(`submenu-${clusterId}`);

    if (submenu) submenu.classList.toggle('open');

}



function toggleSubmenu(targetId, element) {

    const target = document.getElementById(targetId);

    if (target) {

        const isOpening = !target.classList.contains('open');

        target.classList.toggle('open');



        // Handle trigger state

        if (element) {

            element.classList.toggle('active');

        } else {

            const trigger = document.querySelector(`[data-target="${targetId}"]`);

            if (trigger) trigger.classList.toggle('active');

        }

    }

}



// Global State

window.currentSnapshotTime = localStorage.getItem('currentSnapshotTime') || "";



function formatEST(timestamp, includeSeconds = true) {

    if (!timestamp) return '-';

    let ts = timestamp;

    if (typeof ts === 'string' && !ts.endsWith('Z') && !ts.includes('+')) {

        ts += 'Z';

    }

    const date = new Date(ts);

    const options = {

        timeZone: 'America/New_York',

        year: 'numeric', month: 'numeric', day: 'numeric',

        hour: '2-digit', minute: '2-digit',

        timeZoneName: 'short'

    };

    if (includeSeconds) options.second = '2-digit';

    return date.toLocaleString('en-US', options);

}



function getRemainingCacheTime(timestamp, ttlMinutes) {

    if (!timestamp || !ttlMinutes) return "---";

    let ts = timestamp;

    if (typeof ts === 'string' && !ts.endsWith('Z') && !ts.includes('+')) {

        ts += 'Z';

    }

    const lastUpdate = new Date(ts);

    const now = new Date();

    const diffMs = (lastUpdate.getTime() + ttlMinutes * 60000) - now.getTime();

    if (diffMs <= 0) return "available now";

    const minutes = Math.ceil(diffMs / 60000);

    return `${minutes} minutes`;

}



// Global initialization

document.addEventListener('DOMContentLoaded', () => {

    // Initialize UI state for time travel immediately

    if (window.currentSnapshotTime) {

        document.body.classList.add('historical-mode');

        const indicator = document.getElementById('time-travel-indicator');

        if (indicator) {

            indicator.style.display = 'block';

            let ts = window.currentSnapshotTime;

            if (!ts.endsWith('Z') && !ts.includes('+')) ts += 'Z';

            const formatted = formatEST(ts);

            indicator.innerHTML = `<i class="fas fa-exclamation-triangle"></i> Viewing Snapshot: ${formatted} (EST)`;

        }

    }



    // Load available snapshots

    loadSnapshots();



    // Check if we are on dashboard page and need to load params

    const params = new URLSearchParams(window.location.search);

    const clusterId = params.get('cluster_id');

    const resourceType = params.get('resource_type');



    if (clusterId && resourceType) {

        // Auto expand

        const submenu = document.getElementById(`submenu-${clusterId}`);

        if (submenu) submenu.classList.add('open');

        loadResource(clusterId, resourceType);

    } else {

        // Load summary if on dashboard home

        const summaryDiv = document.getElementById('dashboard-summary');

        if (summaryDiv) {

            loadSummary();

        }

    }

});





async function loadSnapshots() {

    const selector = document.getElementById('snapshot-selector');

    if (!selector) return;



    // Apply scrollable style

    selector.style.maxHeight = '300px';

    selector.style.overflowY = 'auto'; // Might need 'size' attribute or custom dropdown for true scroll control on native select



    // For a standard <select>, 'size' attribute controls height if it's a listbox, 

    // but usually users want a dropdown that scrolls. 

    // A standard dropdown scroll is handled by the browser. 

    // If the user means the 'expanded' list is too long, typically browsers handle this.

    // However, if it's a custom UL/LI dropdown (which it seems it might not be, let's verify HTML),

    // looking at the screenshot it looks like a standard Select. 

    // Standard selects are hard to style for max-height. 

    // BUT the user asked for "pull-down should have a scrollable list".

    // If it's a standard <select>, we can't easily limit the height of the popup options in CSS.

    // We might need to assume the user accepts the browser default or convert to a custom dropdown.

    // Given the constraints and the screenshot showing a relatively standard looking dropdown, 

    // I will stick to standard select for now but format the text.

    // Update: The screenshot shows a list that looks somewhat custom. 

    // Wait, the prompt implies "limited in height". 

    // Let's at least fix the timezone.



    try {

        const res = await fetch('/api/dashboard/snapshots');

        if (res.ok) {

            const timestamps = await res.json();

            selector.innerHTML = ''; // Clear existing



            // Add default "Live" option

            const liveOpt = document.createElement('option');

            liveOpt.value = "";

            liveOpt.innerText = "Live Mode (Real-time)";

            selector.appendChild(liveOpt);



            timestamps.forEach(ts => {

                const opt = document.createElement('option');

                opt.value = ts;



                // Parse and format to EST

                // Add 'Z' to force UTC interpretation if not present

                let utcTs = ts;

                if (!utcTs.endsWith('Z') && !utcTs.includes('+')) utcTs += 'Z';



                opt.innerText = formatEST(utcTs);

                selector.appendChild(opt);

            });



            // Restore selection if exists

            if (window.currentSnapshotTime) {

                selector.value = window.currentSnapshotTime;

            }

        }

    } catch (e) {

        console.error("Failed to load snapshots:", e);

    }

}



function toggleTimeTravel(timestamp) {

    window.currentSnapshotTime = timestamp;

    localStorage.setItem('currentSnapshotTime', timestamp || ""); // Persist



    const indicator = document.getElementById('time-travel-indicator');



    // Update indicator UI

    if (timestamp) {

        if (indicator) {

            indicator.style.display = 'block';

            let ts = timestamp;

            if (!ts.endsWith('Z') && !ts.includes('+')) ts += 'Z';

            const formatted = formatEST(ts);

            indicator.innerHTML = `<i class="fas fa-exclamation-triangle"></i> Viewing Snapshot: ${formatted} (EST)`;

        }

        document.body.classList.add('historical-mode'); // Optional: for global styling

    } else {

        if (indicator) indicator.style.display = 'none';

        document.body.classList.remove('historical-mode');

    }



    // Automatically refresh current view

    refreshCurrentView();

}



/**

 * Intelligently refreshes the data on the current page 

 * based on whether we are in historical or live mode.

 */

function refreshCurrentView() {

    // 1. Dashboard Summary

    const dashboardSummary = document.getElementById('dashboard-summary');

    if (dashboardSummary) {

        loadSummary();

        return;

    }



    // 2. Operator Matrix (uses its own global variable currentSnapshotTime)

    if (document.getElementById('matrix-container')) {

        if (typeof loadMatrix === 'function') {

            loadMatrix();

        } else {

            window.location.reload();

        }

        return;

    }



    // 3. Cluster Details

    if (document.getElementById('cluster-content')) {

        // Find cluster ID and resource from URL

        const params = new URLSearchParams(window.location.search);

        const clusterId = params.get('cluster_id');

        if (clusterId) {

            loadClusterDetails(clusterId);

        } else {

            window.location.reload();

        }

        return;

    }



    // Fallback: Reload the entire page if we don't have a specific refresh handler

    window.location.reload();

}





async function loadSummary(forceRefresh = false) {

    const summaryDiv = document.getElementById('dashboard-summary');

    if (!summaryDiv) return;

    try {

        let url = '/api/dashboard/summary';



        if (window.currentSnapshotTime) {

            url += `?snapshot_time=${encodeURIComponent(window.currentSnapshotTime)}`;

        } else if (forceRefresh) {

            url += `?refresh=true`;

        }



        const res = await fetch(url);

        if (!res.ok) throw new Error("Failed to load summary");

        const data = await res.json();



        let clusters = [];

        let global = {

            total_nodes: 0,

            total_licensed_nodes: 0,

            total_vcpu: 0,

            total_licensed_vcpu: 0,

            total_licenses: 0

        };



        if (Array.isArray(data)) {

            // Case for /simple-clusters

            clusters = data;

        } else {

            // Case for /summary (snapshots)

            clusters = data.clusters || [];

            global = data.global_stats || global;

        }



        window._allClusters = clusters;

        window._dashboardTtl = data.ttl_minutes;

        window._dashboardTimestamp = data.timestamp;



        // Dynamically update Sidebar Service Mesh indicators

        clusters.forEach(c => {

            if (c.id) {

                // Fix: has_service_mesh is nested in stats object

                const hasMesh = c.stats ? (c.stats.has_service_mesh === true) : false;



                const clusterLink = document.querySelector(`.nav-link[data-cluster-id="${c.id}"]`);

                const submenu = document.getElementById(`submenu-${c.id}`);



                // Update Badge

                if (clusterLink) {

                    let badge = clusterLink.querySelector('.badge-sm');

                    if (hasMesh && !badge) {

                        // Add it

                        badge = document.createElement('span');

                        badge.className = 'badge-sm';

                        badge.innerText = 'SM';

                        clusterLink.appendChild(badge);

                    } else if (!hasMesh && badge) {

                        // Remove it

                        badge.remove();

                    }

                }



                // Update Sublink

                if (submenu) {

                    // Find existing by checking text content or specific class we can add?

                    // Let's assume we search for the specific onclick

                    const existingLink = Array.from(submenu.querySelectorAll('a')).find(a => a.onclick && a.onclick.toString().includes('loadServiceMesh'));



                    if (hasMesh && !existingLink) {

                        const link = document.createElement('a');

                        link.href = "#";

                        link.className = "sub-link sub-link-sm";

                        link.onclick = function (e) { loadServiceMesh(c.id); e.stopPropagation(); };

                        link.innerHTML = '<i class="fas fa-project-diagram" style="width:14px; margin-right:4px;"></i> Service Mesh';

                        submenu.prepend(link); // Service Mesh usually top

                    } else if (!hasMesh && existingLink) {

                        existingLink.remove();

                    }

                }

            }

        });



        // Dynamically update Sidebar ArgoCD indicators

        clusters.forEach(c => {

            if (c.id) {

                const hasCD = c.stats ? (c.stats.has_argocd === true) : false;

                const clusterLink = document.querySelector(`.nav-link[data-cluster-id="${c.id}"]`);

                const submenu = document.getElementById(`submenu-${c.id}`);



                if (clusterLink) {

                    let badge = clusterLink.querySelector('.badge-cd');

                    if (hasCD && !badge) {

                        badge = document.createElement('span');

                        badge.className = 'badge-cd';

                        badge.innerText = 'CD';

                        clusterLink.appendChild(badge);

                    } else if (!hasCD && badge) {

                        badge.remove();

                    }

                }



                if (submenu) {

                    const existingLink = Array.from(submenu.querySelectorAll('a')).find(a => a.onclick && a.onclick.toString().includes('loadArgoCD'));

                    if (hasCD && !existingLink) {

                        const link = document.createElement('a');

                        link.href = "#";

                        link.className = "sub-link";

                        link.onclick = function (e) { loadArgoCD(c.id); e.stopPropagation(); };

                        link.innerHTML = '<i class="fas fa-sync" style="width:14px; margin-right:4px;"></i> ArgoCD';

                        // Insert after Service Mesh if exists, or first/second depending on preference?

                        // Simple append is fine, or check order.

                        submenu.appendChild(link);

                    } else if (!hasCD && existingLink) {

                        existingLink.remove();

                    }

                }

            }

        });



        if (clusters.length === 0) {

            summaryDiv.innerHTML = '<div class="card" style="grid-column: 1/-1;">No clusters configured.</div>';

            return;

        }



        summaryDiv.style.display = 'block';
        summaryDiv.innerHTML = `

        <!-- Global Summary Cards -->

        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap:1rem; margin-bottom:1.5rem;">

            <div class="card fade-in interactive-summary-card" onclick="showHistoricalModal()" style="margin:0; text-align:center; padding:0.6rem 0.75rem; border-bottom:3px solid var(--accent-color); cursor:pointer;">

                <div style="font-size:0.65rem; color:var(--text-secondary); margin-bottom:0.2rem; text-transform:uppercase; letter-spacing:1px;">Total Clusters <i class="fas fa-chart-line" style="margin-left:0.3rem; opacity:0.5;"></i></div>

                <div style="font-size:1.1rem; font-weight:700;">${clusters.length}</div>

            </div>

            <div class="card fade-in interactive-summary-card" onclick="showHistoricalModal()" style="margin:0; text-align:center; padding:0.6rem 0.75rem; border-bottom:3px solid var(--success-color); cursor:pointer;">

                <div style="font-size:0.65rem; color:var(--text-secondary); margin-bottom:0.2rem; text-transform:uppercase; letter-spacing:1px;">Total Nodes <i class="fas fa-chart-line" style="margin-left:0.3rem; opacity:0.5;"></i></div>

                <div style="font-size:1.1rem; font-weight:700;">${global.total_nodes} <span style="font-size:0.75rem; opacity:0.6;">(${global.total_licensed_nodes})</span></div>

            </div>

            <div class="card fade-in interactive-summary-card" onclick="showHistoricalModal()" style="margin:0; text-align:center; padding:0.6rem 0.75rem; border-bottom:3px solid #a855f7; cursor:pointer;">

                <div style="font-size:0.65rem; color:var(--text-secondary); margin-bottom:0.2rem; text-transform:uppercase; letter-spacing:1px;">Total vCPUs <i class="fas fa-chart-line" style="margin-left:0.3rem; opacity:0.5;"></i></div>

                <div style="font-size:1.1rem; font-weight:700;">${global.total_vcpu.toFixed(0)} <span style="font-size:0.75rem; opacity:0.6;">(${global.total_licensed_vcpu.toFixed(0)})</span></div>

            </div>

            <div class="card fade-in interactive-summary-card" id="global-card-licenses" 

                 onclick="showHistoricalModal()" 

                 style="margin:0; text-align:center; padding:0.6rem 0.75rem; border-bottom:3px solid var(--accent-color); background: linear-gradient(135deg, var(--card-bg) 0%, rgba(56, 189, 248, 0.05) 100%); cursor:pointer; position:relative;">

                <div style="font-size:0.65rem; color:var(--text-secondary); margin-bottom:0.2rem; text-transform:uppercase; letter-spacing:1px;">

                    Total Licenses <i class="fas fa-chart-line" style="margin-left:0.3rem; opacity:0.5;"></i>

                </div>

                <div id="summary-total-licenses" style="font-size:1.25rem; font-weight:800; color:var(--accent-color);">${global.total_licenses || 0}</div>

                <div style="font-size:0.55rem; opacity:0.5; margin-top:0.2rem;">(Click for Trends)</div>

                <style>

                    .interactive-summary-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.2); }

                    .interactive-summary-card:active { transform: translateY(0); }

                </style>

            </div>

        </div>



        <div class="card fade-in">

            <div class="resource-header" style="padding:0.75rem 1rem; border-bottom:1px solid var(--border-color); display:flex; justify-content:space-between; align-items:center;">

                <div style="display:flex; align-items:center; gap:1.5rem;">

                    <span style="font-weight:700; font-size:1rem;">Cluster Inventory</span>

                    <div style="position:relative;">

                        <i class="fas fa-search" style="position:absolute; left:0.75rem; top:50%; transform:translateY(-50%); font-size:0.8rem; color:var(--text-secondary);"></i>

                        <input type="text" id="cluster-table-search" placeholder="Search clusters..." 

                               style="padding:0.35rem 0.75rem 0.35rem 2rem; border-radius:15px; background:var(--bg-primary); border:1px solid var(--border-color); color:var(--text-primary); font-size:0.8rem; width:220px;"

                               oninput="applyDashboardFilters()">

                    </div>

                    <div id="data-as-of-container" style="display:none; align-items:center; gap:0.4rem; font-size:0.75rem; color:var(--text-secondary); opacity:0.8;">

                        <i class="far fa-clock"></i> Data as of: <span id="data-as-of-time" style="color:var(--accent-color); font-weight:600;">-</span>

                        <span id="data-next-poll" style="font-style:italic; font-size:0.7rem;"></span>

                    </div>

                    <div style="display:flex; gap:0.25rem;" id="dashboard-filter-group">

                        <button class="filter-btn" onclick="toggleDashboardFilter(this, 'DEV')" data-filter="DEV">DEV</button>

                        <button class="filter-btn" onclick="toggleDashboardFilter(this, 'UAT')" data-filter="UAT">UAT</button>

                        <button class="filter-btn" onclick="toggleDashboardFilter(this, 'PROD')" data-filter="PROD">PROD</button>

                        <div style="width:1px; background:var(--border-color); margin:0 0.25rem;"></div>

                        <button class="filter-btn" onclick="toggleDashboardFilter(this, 'AZURE')" data-filter="AZURE">AZURE</button>

                        <button class="filter-btn" onclick="toggleDashboardFilter(this, 'HCI')" data-filter="HCI">HCI</button>

                    </div>

                </div>

                <div style="display:flex; gap:0.4rem;">

                    <button class="btn btn-secondary" style="padding:0.2rem 0.4rem; font-size:0.7rem; opacity:0.8;" onclick="exportTable('Cluster_Inventory', 'excel')">

                        <i class="fas fa-file-excel"></i> Excel

                    </button>

                    <button class="btn btn-secondary" style="padding:0.2rem 0.4rem; font-size:0.7rem; opacity:0.8;" onclick="exportTable('Cluster_Inventory', 'csv')">

                        <i class="fas fa-file-csv"></i> CSV

                    </button>

                </div>

            </div>

            <div class="table-container">

                <table class="data-table" id="cluster-inventory-table">

                    <thead>

                        <tr>

                            <th class="sortable-header" onclick="sortTable('cluster-inventory-table', 0)">

                                <div style="display:flex; align-items:center; gap:0.5rem;">Cluster Name <i class="fas fa-sort sort-icon" style="opacity:0.3; font-size:0.7rem;"></i></div>

                            </th>

                            <th class="sortable-header" onclick="sortTable('cluster-inventory-table', 1)">

                                <div style="display:flex; align-items:center; gap:0.5rem;">Total Nodes <i class="fas fa-sort sort-icon" style="opacity:0.3; font-size:0.7rem;"></i></div>

                            </th>

                            <th class="sortable-header" onclick="sortTable('cluster-inventory-table', 2)">

                                <div style="display:flex; align-items:center; gap:0.5rem;">App Nodes <i class="fas fa-sort sort-icon" style="opacity:0.3; font-size:0.7rem;"></i></div>

                            </th>

                            <th class="sortable-header" onclick="sortTable('cluster-inventory-table', 3)">

                                <div style="display:flex; align-items:center; gap:0.5rem;">Licenses <i class="fas fa-sort sort-icon" style="opacity:0.3; font-size:0.7rem;"></i></div>

                            </th>

                            <th class="sortable-header" onclick="sortTable('cluster-inventory-table', 4)">

                                <div style="display:flex; align-items:center; gap:0.5rem;">Total vCPUs <i class="fas fa-sort sort-icon" style="opacity:0.3; font-size:0.7rem;"></i></div>

                            </th>

                            <th class="sortable-header" onclick="sortTable('cluster-inventory-table', 5)">

                                <div style="display:flex; align-items:center; gap:0.5rem;">Total Licensed vCPUs <i class="fas fa-sort sort-icon" style="opacity:0.3; font-size:0.7rem;"></i></div>

                            </th>

                            <th>Console</th>

                            <th class="sortable-header" onclick="sortTable('cluster-inventory-table', 7)">

                                <div style="display:flex; align-items:center; gap:0.5rem;">Datacenter <i class="fas fa-sort sort-icon" style="opacity:0.3; font-size:0.7rem;"></i></div>

                            </th>

                            <th class="sortable-header" onclick="sortTable('cluster-inventory-table', 8)">

                                <div style="display:flex; align-items:center; gap:0.5rem;">Environment <i class="fas fa-sort sort-icon" style="opacity:0.3; font-size:0.7rem;"></i></div>

                            </th>

                            <th class="sortable-header" onclick="sortTable('cluster-inventory-table', 9)">

                                <div style="display:flex; align-items:center; gap:0.5rem;">Version <i class="fas fa-sort sort-icon" style="opacity:0.3; font-size:0.7rem;"></i></div>

                            </th>

                            <th>Details</th>

                        </tr>

                    </thead>

                    <tbody id="cluster-inventory-body">

                        ${renderClusterRows(clusters)}

                    </tbody>

                </table>

            </div>

        </div>

        `;



        // Initial re-calc

        updateGlobalSummary();



        // Show "Data as of" if timestamp is present

        const dataAsOfContainer = document.getElementById('data-as-of-container');

        const dataAsOfTime = document.getElementById('data-as-of-time');

        const dataNextPoll = document.getElementById('data-next-poll');

        if (dataAsOfContainer && dataAsOfTime && data.timestamp) {

            dataAsOfContainer.style.display = 'flex';

            dataAsOfTime.innerText = formatEST(data.timestamp);



            if (dataNextPoll && data.ttl_minutes) {

                const remaining = getRemainingCacheTime(data.timestamp, data.ttl_minutes);

                dataNextPoll.innerText = `(New data can be polled in: ${remaining})`;

            }

        }



    } catch (e) {

        if (summaryDiv) {

            summaryDiv.innerHTML = `<div class="card" style="color:var(--danger-color);">Error loading summary: ${e.message}</div>`;

        }

    }

}



function updateGlobalSummary() {

    const clusters = window._allClusters || [];

    const stats = {

        total_nodes: 0,

        total_licensed_nodes: 0,

        total_vcpu: 0,

        total_licensed_vcpu: 0,

        total_licenses: 0

    };



    clusters.forEach(c => {

        if (c.stats) {

            const nc = parseInt(c.stats.node_count);

            if (!isNaN(nc)) stats.total_nodes += nc;



            const vc = parseFloat(c.stats.vcpu_count);

            if (!isNaN(vc)) stats.total_vcpu += vc;

        }



        const lnc = parseInt(c.licensed_node_count);

        if (!isNaN(lnc)) stats.total_licensed_nodes += lnc;



        const lvc = parseFloat(c.licensed_vcpu_count);

        if (!isNaN(lvc)) stats.total_licensed_vcpu += lvc;



        if (c.license_info && c.license_info.count !== undefined) {

            const lc = parseInt(c.license_info.count);

            if (!isNaN(lc)) stats.total_licenses += lc;

        }

    });



    // Update the DOM if elements exist

    const elNodes = document.querySelector('[style*="var(--success-color)"] div:nth-child(2)');

    if (elNodes) elNodes.innerHTML = `${stats.total_nodes} <span style="font-size:0.75rem; opacity:0.6;">(${stats.total_licensed_nodes})</span>`;



    const elVcpu = document.querySelector('[style*="#a855f7"] div:nth-child(2)');

    if (elVcpu) {

        const totalVcpu = typeof stats.total_vcpu === 'number' ? stats.total_vcpu.toFixed(0) : '0';

        const licensedVcpu = typeof stats.total_licensed_vcpu === 'number' ? stats.total_licensed_vcpu.toFixed(0) : '0';

        elVcpu.innerHTML = `${totalVcpu} <span style="font-size:0.75rem; opacity:0.6;">(${licensedVcpu})</span>`;

    }



    const elLic = document.getElementById('summary-total-licenses');

    if (elLic) elLic.innerText = stats.total_licenses;

}



function renderClusterRows(clusters) {

    if (clusters.length === 0) {

        return '<tr><td colspan="11" style="text-align:center; padding:2rem; opacity:0.6;">No matching clusters found</td></tr>';

    }

    return clusters.map(c => {

        let statusColor = 'var(--text-secondary)';

        let statusTitle = 'Unknown';

        if (c.status === 'green') { statusColor = 'var(--success-color)'; statusTitle = 'Healthy'; }

        else if (c.status === 'yellow') { statusColor = 'var(--warning-color)'; statusTitle = 'Stale / Polling'; }

        else if (c.status === 'red') { statusColor = 'var(--danger-color)'; statusTitle = 'Error / Degraded'; }

        else if (c.status === 'gray') { statusColor = 'var(--text-secondary)'; statusTitle = 'No Data'; }



        const stats = c.stats || {};

        const licenseInfo = c.license_info || {};



        return `

        <tr id="cluster-row-${c.id}">

            <td style="font-weight:600; color:var(--accent-color);">

                <div style="display:flex; align-items:center;">

                    <span style="cursor:pointer; display:flex; align-items:center;" onclick="showClusterDetails(${c.id}, '${c.name.replace(/'/g, "\\'")}')">

                        <i class="fas fa-circle ${c.status === 'yellow' ? 'fa-pulse' : ''}" style="color:${statusColor}; font-size:0.6rem; margin-right:0.5rem;" title="${statusTitle}"></i>

                        ${c.name}

                    </span>

                    ${stats.upgrade_status && stats.upgrade_status.is_upgrading ?

                `<i class="fas fa-sync fa-spin" style="margin-left:0.5rem; color:#f39c12; font-size:0.8rem; cursor:pointer;" title="Upgrade in progress" onclick="showUpgradeDetails(${c.id})"></i>` : ''}

                </div>

            </td>

            <td style="font-family:monospace; font-size:0.85rem; opacity:0.9;">${stats.node_count !== undefined ? stats.node_count : '<i class="fas fa-spinner fa-spin" style="opacity:0.3;"></i>'}</td>

            <td>

                <span class="badge" style="background:rgba(255,255,255,0.05); color:var(--text-secondary); opacity:0.8;">

                    ${c.licensed_node_count !== undefined ? c.licensed_node_count : '-'}

                </span>

            </td>

            <td>

                <span class="badge badge-purple" 

                        style="cursor:pointer;" 

                        onclick="showLicenseDetails(${c.id}, ${licenseInfo.usage_id || 'null'})"

                        title="View License Breakdown">

                    ${licenseInfo.count !== undefined ? licenseInfo.count : '-'}

                </span>

            </td>

            <td style="font-family:monospace; font-size:0.85rem; opacity:0.9;">${stats.vcpu_count !== undefined ? stats.vcpu_count : '-'}</td>

            <td style="font-family:monospace; font-size:0.85rem; opacity:0.9;">${c.licensed_vcpu_count !== undefined ? c.licensed_vcpu_count : '-'}</td>

            <td>

                ${stats.console_url && stats.console_url !== '#'

                ? `<a href="${stats.console_url}" target="_blank" class="btn btn-primary" style="padding:0.2rem 0.4rem; font-size:0.7rem; border-radius:4px; display:inline-block;" title="Open Console">

                            <i class="fas fa-external-link-alt"></i>

                        </a>`

                : '<span style="opacity:0.5;">-</span>'

            }

            </td>

            <td><span class="badge badge-blue" style="font-size:0.7rem;">${c.datacenter || '-'}</span></td>

            <td><span class="badge badge-green" style="font-size:0.7rem;">${c.environment || '-'}</span></td>

            <td style="font-family:monospace; font-size:0.85rem; opacity:0.9;">${stats.version || '-'}</td>

            <td>

                <button class="btn btn-secondary" style="padding:0.2rem 0.4rem; font-size:0.7rem; opacity:0.8;" onclick="refreshClusterLive(${c.id})" title="Refresh Now">

                    <i class="fas fa-sync-alt"></i>

                </button>

                <button class="btn btn-secondary" style="padding:0.2rem 0.4rem; font-size:0.7rem; opacity:0.8;" onclick="showClusterDetails(${c.id}, '${c.name.replace(/'/g, "\\'")}')">

                    <i class="fas fa-info-circle"></i>

                </button>

            </td >

        </tr >

            `}).join('');

}



/* Dashboard Specific Filtering */

window._activeDashboardFilters = new Set();



function toggleDashboardFilter(btn, filter) {

    btn.classList.toggle('active');

    if (window._activeDashboardFilters.has(filter)) {

        window._activeDashboardFilters.delete(filter);

    } else {

        window._activeDashboardFilters.add(filter);

    }

    applyDashboardFilters();

}



function applyDashboardFilters() {

    const searchInput = document.getElementById('cluster-table-search');

    const searchTerm = searchInput ? searchInput.value.toLowerCase() : "";

    const activeFilters = window._activeDashboardFilters;



    const filtered = window._allClusters.filter(c => {

        // Search check

        const matchesSearch = !searchTerm ||

            c.name.toLowerCase().includes(searchTerm) ||

            (c.datacenter && c.datacenter.toLowerCase().includes(searchTerm)) ||

            (c.environment && c.environment.toLowerCase().includes(searchTerm));



        if (!matchesSearch) return false;



        // Tag checks (Environment and Datacenter)

        if (activeFilters.size === 0) return true;



        const envFilters = ['DEV', 'UAT', 'PROD'].filter(f => activeFilters.has(f));

        const dcFilters = ['AZURE', 'HCI'].filter(f => activeFilters.has(f));



        const matchesEnv = envFilters.length === 0 || (c.environment && envFilters.includes(c.environment.toUpperCase()));

        const matchesDc = dcFilters.length === 0 || (c.datacenter && dcFilters.includes(c.datacenter.toUpperCase()));



        return matchesEnv && matchesDc;

    });



    const body = document.getElementById('cluster-inventory-body');

    if (body) {

        body.innerHTML = renderClusterRows(filtered);

    }



    // Update summary counters to match filtered view? 

    // Usually dashboard summary cards show global, but let's keep it global for now.

}



async function refreshClusterLive(clusterId) {

    const row = document.getElementById(`cluster-row-${clusterId}`);

    if (!row) return;



    // Optional: Show loading state in status icon

    const icon = row.querySelector('.fa-circle');

    if (icon) icon.classList.add('fa-pulse');



    console.log(`[Debug] Refreshing live stats for cluster ${clusterId}...`);

    try {

        const url = `/api/dashboard/${clusterId}/live_stats?ts=${Date.now()}`;

        console.log(`[Debug] Fetching: ${url}`);

        const res = await fetch(url);

        console.log(`[Debug] Response status: ${res.status}`);

        if (res.ok) {

            const data = await res.json();



            // Update Global State

            const idx = window._allClusters.findIndex(c => c.id === clusterId);

            if (idx !== -1) {

                window._allClusters[idx] = {

                    ...window._allClusters[idx],

                    ...data,

                    status: data.status

                };



                // Update DOM strictly by replacing the row

                const temp = document.createElement('tbody');

                temp.innerHTML = renderClusterRows([window._allClusters[idx]]);

                const newRow = temp.firstElementChild;

                row.replaceWith(newRow);



                // Update Total Summary Cards

                updateGlobalSummary();

                console.log(`[Debug] Successfully updated cluster ${clusterId} stats`);

            } else {

                console.warn(`[Debug] Cluster ${clusterId} not found in global list`);

            }

        } else {

            console.error(`[Debug] Fetch failed with status ${res.status}`);

            throw new Error("Failed to refresh");

        }

    } catch (e) {

        console.error("Cluster refresh failed", e);

        if (icon) {

            icon.classList.remove('fa-pulse');

            // Set to red if failed? Or keep yellow?

            // Maybe switch to Red/Yellow pulsing?

            icon.style.color = 'var(--danger-color)';

            icon.title = 'Refresh Failed';



            // Update state to red so filters know

            const idx = window._allClusters.findIndex(c => c.id === clusterId);

            if (idx !== -1) {

                window._allClusters[idx].status = 'red';

            }

        }

    }

}



function filterClusterTable(query) {

    const q = query.toLowerCase().trim();

    const filtered = window._allClusters.filter(c =>

        c.name.toLowerCase().includes(q) ||

        (c.datacenter && c.datacenter.toLowerCase().includes(q)) ||

        (c.environment && c.environment.toLowerCase().includes(q))

    );

    document.getElementById('cluster-inventory-body').innerHTML = renderClusterRows(filtered);

}



// Initial filter state

const activeFilters = {

    DEV: false,

    UAT: false,

    PROD: false,

    AZURE: false,

    HCI: false

};



function toggleFilter(btn, filterName) {

    activeFilters[filterName] = !activeFilters[filterName];



    if (activeFilters[filterName]) {

        btn.classList.add('active');

    } else {

        btn.classList.remove('active');

    }

    filterClusters();

}



function filterClusters() {

    const search = (document.getElementById('cluster-search').value || '').toLowerCase();

    const items = document.querySelectorAll('.cluster-item');



    // Group filters

    const envGroup = ['DEV', 'UAT', 'PROD'];

    const dcGroup = ['AZURE', 'HCI'];



    const anyEnvActive = envGroup.some(f => activeFilters[f]);

    const anyDcActive = dcGroup.some(f => activeFilters[f]);



    items.forEach(item => {

        const name = (item.dataset.name || '').toLowerCase();

        const itemEnv = (item.dataset.env || 'None').toUpperCase();

        const itemDc = (item.dataset.dc || 'None').toUpperCase();



        let nameMatch = !search || name.includes(search);

        let envMatch = !anyEnvActive || activeFilters[itemEnv];

        let dcMatch = !anyDcActive || activeFilters[itemDc];



        item.style.display = (nameMatch && envMatch && dcMatch) ? 'block' : 'none';

    });

}



async function loadResource(clusterId, resourceType, clusterName) {

    const contentDiv = document.getElementById('dashboard-content');



    // Store globally for header rendering and highlighting

    if (clusterName) window.currentClusterName = clusterName;

    window.currentClusterId = clusterId;

    window.currentResourceType = resourceType;



    // Highlight sidebar

    updateSidebarHighlighting(clusterId, resourceType);



    // If not on dashboard page, redirect to dashboard with params

    if (!contentDiv) {

        window.location.href = `/dashboard?cluster_id=${clusterId}&resource_type=${resourceType}`;

        return;

    }



    contentDiv.innerHTML = '<div class="card" style="text-align:center; padding: 2rem;"><i class="fas fa-circle-notch fa-spin"></i> Loading...</div>';



    try {

        let url = `/api/dashboard/${clusterId}/resources/${resourceType}`;

        if (window.currentSnapshotTime) {

            url += `?snapshot_time=${window.currentSnapshotTime}`;

        }

        const response = await fetch(url);

        if (!response.ok) {

            throw new Error(`Error fetching resources: ${response.statusText}`);

        }

        const data = await response.json();

        // Store data globally for filtering ? Or just pass to render

        // Better to attach to callback or closure, but for simplicity:

        window.currentResourceData = data;

        window.currentResourceType = resourceType;

        window.currentClusterId = clusterId; // Track cluster id for modal actions

        renderTable(resourceType, data);

    } catch (error) {

        contentDiv.innerHTML = `<div class="card" style="color: var(--danger-color);"><i class="fas fa-exclamation-triangle"></i> ${error.message}</div>`;

    }

}





/* License Analytics */

/* License Analytics */

let licenseChartInstance = null;

let currentBreakdownTab = 'cluster';



function switchBreakdownTab(tab) {

    currentBreakdownTab = tab;



    // UI Updates

    document.querySelectorAll('.breakdown-tab').forEach(el => el.classList.remove('active'));

    document.getElementById(`tab-${tab}`).classList.add('active');



    document.getElementById('view-cluster').style.display = tab === 'cluster' ? 'block' : 'none';

    document.getElementById('view-mapid').style.display = tab === 'mapid' ? 'block' : 'none';



    // Refresh filters visualization if needed? 

    // Usually filters apply to both or we share state.



    if (tab === 'mapid') {

        loadMapidBreakdown();

    } else {

        loadClusterBreakdown();

    }

}



async function loadMapidBreakdown() {

    const tbody = document.getElementById('mapid-breakdown-body');

    const template = document.getElementById('mapid-row-template');

    const loader = document.getElementById('loader-mapid-breakdown');



    // Helper to get active filters

    const envs = [];

    const dcs = [];

    if (activeBreakdownFilters['DEV']) envs.push('DEV');

    if (activeBreakdownFilters['UAT']) envs.push('UAT');

    if (activeBreakdownFilters['PROD']) envs.push('PROD');

    if (activeBreakdownFilters['AZURE']) dcs.push('AZURE');

    if (activeBreakdownFilters['HCI']) dcs.push('HCI');



    // Always reload for MAPID to get correct aggregation? or cache?

    // Let's reload to be safe and accurate with filters.

    if (loader) loader.style.display = 'block';

    if (tbody) tbody.style.opacity = '0.5';



    let url = '/api/dashboard/mapid-breakdown';

    const params = new URLSearchParams();

    envs.forEach(e => params.append('environment', e)); // API expects single? or list?

    // My API defined: environment: Optional[str]

    // So it supports only ONE? The previous code implied single select or multi?

    // The previous implementation used `toggle` for filters implies multi-select in UI but maybe API limited?

    // Let's check API. `environment: Optional[str] = Query(None)`. This only takes one param usually in FastAPI unless `List[str]`.

    // I should check `dashboard.py`.

    // It takes `environment: Optional[str]`.

    // So multi-select might not be supported backend-side yet.

    // I'll update it to take the FIRST active filter or logic to support filtering.

    // If multiple selected, we might need multiple calls or API update.

    // For now, let's pick the first one or pass nothing if multiple?

    // Actually, `activeBreakdownFilters` allows multiple.

    // For now I'll send the first found or ignore if mixed? 

    // Let's pass the first for now.



    if (envs.length > 0) params.append('environment', envs[0]);

    if (dcs.length > 0) params.append('datacenter', dcs[0]);



    if (params.toString()) url += `?${params.toString()}`;



    try {

        const res = await fetch(url);

        if (!res.ok) throw new Error("Failed to fetch MAPID data");

        const data = await res.json();



        tbody.innerHTML = '';



        if (data.length === 0) {

            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:2rem; opacity:0.6;">No data found</td></tr>';

        } else {

            data.forEach(item => {

                const clone = template.content.cloneNode(true);

                const parent = clone.querySelector('.mapid-parent-row');



                parent.querySelector('.mapid-id').innerText = item.mapid || '-';

                parent.querySelector('.mapid-lob').innerText = item.lob || '-';

                parent.querySelector('.mapid-nodes').innerText = item.total_nodes;

                parent.querySelector('.mapid-licenses').innerText = item.total_licenses;



                // Toggle Logic

                const btn = parent.querySelector('.toggle-details-btn');

                btn.onclick = () => {

                    const childRow = parent.nextElementSibling;

                    if (childRow.style.display === 'none') {

                        childRow.style.display = 'table-row';

                        btn.innerHTML = '<i class="fas fa-chevron-up"></i> Hide';

                    } else {

                        childRow.style.display = 'none';

                        btn.innerHTML = '<i class="fas fa-chevron-down"></i> Clusters';

                    }

                };



                // Populate Child Row

                const childBody = clone.querySelector('.child-tbody');

                if (item.clusters && item.clusters.length > 0) {

                    item.clusters.forEach(c => {

                        // Parent Row (Cluster)

                        const row = document.createElement('tr');

                        row.innerHTML = `

                            <td style="font-weight:600;">${c.name}</td>

                            <td>${c.environment}</td>

                            <td>${c.datacenter}</td>

                            <td>${c.nodes}</td>

                            <td style="font-weight:bold;">

                                <div style="display:flex; justify-content:space-between; align-items:center;">

                                    ${c.licenses}

                                    <button class="btn btn-sm btn-secondary" style="font-size:0.7rem; padding: 0.1rem 0.4rem;" 

                                        onclick="toggleMapidResources(${c.cluster_id}, '${item.mapid}', this)">

                                        <i class="fas fa-chevron-right"></i> Resources

                                    </button>

                                </div>

                            </td>

                        `;

                        childBody.appendChild(row);



                        // Child Row (Resources)

                        const resRow = document.createElement('tr');

                        resRow.style.display = 'none';

                        resRow.style.background = 'rgba(0,0,0,0.1)';

                        resRow.innerHTML = `

                             <td colspan="5" style="padding:0;">

                                <div style="padding:0.5rem 1rem; border-left: 2px solid var(--accent-color); margin: 0.5rem 1rem;">

                                    <div style="text-align:center; padding:1rem;"><i class="fas fa-circle-notch fa-spin"></i> Loading...</div>

                                </div>

                            </td>

                        `;

                        childBody.appendChild(resRow);

                    });

                } else {

                    childBody.innerHTML = '<tr><td colspan="5" style="text-align:center; opacity:0.5;">No contributing clusters found.</td></tr>';

                }



                tbody.appendChild(clone);

            });

        }

    } catch (e) {

        console.error("Error loading MAPID breakdown:", e);

        tbody.innerHTML = `<tr><td colspan="5" style="color:var(--danger-color); text-align:center;">Error: ${e.message}</td></tr>`;

    } finally {

        if (loader) loader.style.display = 'none';

        if (tbody) tbody.style.opacity = '1';

    }

}



// Breakdown Filters

const activeBreakdownFilters = {

    DEV: false, UAT: false, PROD: false,

    AZURE: false, HCI: false

};



function toggleBreakdownFilter(btn, filterName) {

    activeBreakdownFilters[filterName] = !activeBreakdownFilters[filterName];

    if (activeBreakdownFilters[filterName]) {

        btn.classList.add('active');

    } else {

        btn.classList.remove('active');

    }



    // Reload logic depending on tab

    if (currentBreakdownTab === 'cluster') {

        filterBreakdownTable(); // Client side filtering for cluster view (existing logic)

    } else {

        loadMapidBreakdown(); // Server side reload for MAPID with new filters

    }

}







async function loadLicenseAnalytics() {

    const days = document.getElementById('analytics-range').value || 30;



    // Show Loaders

    const loaderTrends = document.getElementById('loader-trends');

    if (loaderTrends) loaderTrends.style.display = 'flex';



    // Legacy loader removed (loader-unmapped)



    // 1. Initialize Tabs (Main Table) - Start Immediately

    switchBreakdownTab('cluster');



    // 2. Load Trends (Parallel)

    loadTrendsDataAsync(days);



    // 3. Load Unmapped Nodes (Parallel)

    // Show scanning state in Status Bar

    const statusBar = document.getElementById('unmapped-status-bar');

    const statusIcon = document.getElementById('unmapped-status-icon');

    const statusTitle = document.getElementById('unmapped-status-title');

    const statusDesc = document.getElementById('unmapped-status-desc');

    const actionBtn = document.getElementById('unmapped-action-btn');



    if (statusBar) {

        statusBar.style.display = 'block';

        statusBar.style.borderLeftColor = 'var(--text-secondary)';

        statusIcon.className = 'fas fa-circle-notch fa-spin';

        statusIcon.style.color = 'var(--text-secondary)';

        statusTitle.innerText = 'Scanning for unmapped resources...';

        statusDesc.innerText = 'Please wait while we check all clusters and projects.';

        if (actionBtn) actionBtn.style.display = 'none';

    }



    loadUnmappedNodesAsync();

}



async function loadTrendsDataAsync(days) {

    try {

        const res = await fetch(`/api/dashboard/mapid/global-trends?days=${days}`);

        if (res.ok) {

            const data = await res.json();

            renderGlobalMapidChart(data);

        }

    } catch (e) {

        console.error("Failed to load trends", e);

    } finally {

        const loader = document.getElementById('loader-trends');

        if (loader) loader.style.display = 'none';

    }

}



async function loadUnmappedNodesAsync() {

    try {

        const res = await fetch(`/api/dashboard/mapid/unmapped-nodes`);

        if (res.ok) {

            const data = await res.json();

            renderUnmappedNodes(data);

        }

    } catch (e) {

        console.error("Failed to load unmapped nodes", e);

    } finally {

        const loader = document.getElementById('loader-unmapped');

        if (loader) loader.style.display = 'none';

    }

}



async function loadClusterBreakdown() {

    const loaderBreakdown = document.getElementById('loader-breakdown');

    const tbody = document.getElementById('breakdown-body');



    // Only fetch if empty (client side cache/filtering model) for clusters

    if (tbody.hasChildNodes() && tbody.children.length > 0) return;



    if (loaderBreakdown) {

        loaderBreakdown.style.display = 'block';

        tbody.innerHTML = '';

    }



    try {

        const res = await fetch(`/api/dashboard/mapid/cluster-breakdown`);

        if (res.ok) {

            const data = await res.json();

            renderBreakdownTable(data);

        }

    } catch (e) {

        console.error("Failed to load breakdown", e);

        tbody.innerHTML = `<tr><td colspan="5" style="color:var(--danger-color); text-align:center;">Error loading clusters: ${e.message}</td></tr>`;

    } finally {

        if (loaderBreakdown) loaderBreakdown.style.display = 'none';

    }

}





function renderGlobalMapidChart(data) {

    const ctx = document.getElementById('global-mapid-chart');

    if (!ctx) return;



    if (licenseChartInstance) {

        licenseChartInstance.destroy();

    }



    // Assign colors dynamically

    const colors = [

        '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',

        '#ec4899', '#6366f1', '#14b8a6', '#f97316', '#a855f7'

    ];



    const datasets = data.datasets.map((d, i) => ({

        label: d.label,

        data: d.data,

        borderColor: colors[i % colors.length],

        backgroundColor: colors[i % colors.length] + '20', // Transparent fill

        borderWidth: 2,

        tension: 0.3,

        fill: false

    }));



    let currentScale = 'logarithmic'; // Default



    window.updateChartScale = function (scaleType) {

        currentScale = scaleType;



        // Update Buttons

        document.getElementById('scale-log').classList.toggle('active', scaleType === 'logarithmic');

        document.getElementById('scale-linear').classList.toggle('active', scaleType === 'linear');



        if (licenseChartInstance) {

            licenseChartInstance.options.scales.y.type = scaleType;

            licenseChartInstance.update();

        }

    };



    licenseChartInstance = new Chart(ctx, {

        type: 'line',

        data: {

            labels: data.labels,

            datasets: datasets

        },

        options: {

            responsive: true,

            maintainAspectRatio: false,

            interaction: {

                mode: 'index',

                intersect: false,

            },

            plugins: {

                legend: {

                    position: 'bottom',

                    labels: { boxWidth: 10, usePointStyle: true }

                },

                tooltip: {

                    mode: 'index',

                    intersect: false,

                    itemSort: (a, b) => b.raw - a.raw

                }

            },

            scales: {

                y: {

                    type: currentScale,

                    beginAtZero: true,

                    title: { display: true, text: 'Total Licenses' }

                },

                x: {

                    grid: { display: false }

                }

            }

        }

    });

}



let unmappedResourcesData = [];

let currentUnmappedFilter = 'all';



function renderUnmappedNodes(data) {

    unmappedResourcesData = data || [];



    const statusBar = document.getElementById('unmapped-status-bar');

    const statusIcon = document.getElementById('unmapped-status-icon');

    const statusTitle = document.getElementById('unmapped-status-title');

    const statusDesc = document.getElementById('unmapped-status-desc');

    const actionBtn = document.getElementById('unmapped-action-btn');



    if (!statusBar) return;

    statusBar.style.display = 'block';



    if (!data || data.length === 0) {

        // Success State

        statusBar.style.borderLeftColor = 'var(--success-color)';

        statusIcon.className = 'fas fa-check-circle';

        statusIcon.style.color = 'var(--success-color)';

        statusTitle.innerText = 'No unmapped resources found';

        statusDesc.innerText = 'All licensed nodes and projects have a valid MAPID label.';

        actionBtn.style.display = 'none';

        return;

    }



    // Warning State

    statusBar.style.borderLeftColor = 'var(--warning-color)';

    statusIcon.className = 'fas fa-exclamation-triangle';

    statusIcon.style.color = 'var(--warning-color)';

    statusTitle.innerText = `${data.length} Unmapped Resources Detected`;

    statusTitle.style.color = 'var(--warning-color)';

    statusDesc.innerText = 'Some licensed resources are missing the mapid label.';

    actionBtn.style.display = 'inline-flex';



    // Initial Render

    setUnmappedFilter('all');

}



function setUnmappedFilter(type) {

    currentUnmappedFilter = type;



    // Update Buttons

    ['all', 'node', 'project'].forEach(t => {

        const btn = document.getElementById(`filter-btn-${t}`);

        if (btn) {

            if (t === type) {

                btn.classList.remove('btn-secondary');

                btn.classList.add('btn-primary');

            } else {

                btn.classList.add('btn-secondary');

                btn.classList.remove('btn-primary');

            }

        }

    });



    renderUnmappedTableRefreshed();

}



// Wrapper for search input

function filterUnmappedModal() {

    renderUnmappedTableRefreshed();

}



function renderUnmappedTableRefreshed() {

    const tbody = document.getElementById('unmapped-nodes-body');

    const searchVal = (document.getElementById('unmapped-search').value || '').toLowerCase();



    if (!tbody) return;



    // Filter Data

    let filtered = unmappedResourcesData.filter(item => {

        // Type Filter

        const isProject = item.node_name.startsWith('[Project]');

        if (currentUnmappedFilter === 'node' && isProject) return false;

        if (currentUnmappedFilter === 'project' && !isProject) return false;



        // Search Filter

        if (searchVal) {

            const text = (item.cluster_name + ' ' + item.node_name + ' ' + item.reason).toLowerCase();

            return text.includes(searchVal);

        }

        return true;

    });



    // Group by Cluster

    const grouped = {};

    filtered.forEach(item => {

        if (!grouped[item.cluster_name]) grouped[item.cluster_name] = [];

        grouped[item.cluster_name].push(item);

    });



    // Generate HTML

    if (Object.keys(grouped).length === 0) {

        tbody.innerHTML = '<tr><td colspan="2" style="text-align:center; padding:1rem;">No results found</td></tr>';

        return;

    }



    let html = '';

    // Sort clusters alphabetically

    Object.keys(grouped).sort().forEach(cluster => {

        // Group Header

        html += `

            <tr style="background: var(--bg-primary); border-top: 2px solid var(--border-color); border-bottom: 2px solid var(--border-color);">

                <td colspan="2" style="font-weight: 700; color: var(--accent-color); padding: 0.75rem;">

                    <i class="fas fa-server" style="opacity:0.7; margin-right:0.5rem;"></i> ${cluster}

                </td>

            </tr>

        `;

        // Items

        grouped[cluster].forEach(n => {

            html += `

                <tr>

                    <td style="font-family:monospace; padding-left: 1.5rem;">${n.node_name}</td>

                    <td style="color:var(--danger-color); font-size:0.9rem;">${n.reason}</td>

                </tr>

            `;

        });

    });



    tbody.innerHTML = html;

}



function renderBreakdownTable(data) {

    const tbody = document.getElementById('breakdown-body');

    if (!tbody) return;

    tbody.innerHTML = '';



    const template = document.getElementById('breakdown-row-template');



    data.forEach(cluster => {

        // Calculate Top MAPID

        const sortedMapids = [...cluster.mapids].sort((a, b) => b.license_count - a.license_count);

        const topMapid = sortedMapids.length > 0 ? sortedMapids[0].mapid : '-';

        const totalLic = cluster.mapids.reduce((sum, m) => sum + m.license_count, 0);



        const clone = template.content.cloneNode(true);

        const parentRow = clone.querySelector('.breakdown-parent-row');

        const childRow = clone.querySelector('.breakdown-child-row');



        // Add metadata for filtering

        parentRow.dataset.name = cluster.cluster_name ? cluster.cluster_name.toLowerCase() : '';

        parentRow.dataset.env = cluster.environment ? cluster.environment.toUpperCase() : 'NONE';

        parentRow.dataset.dc = cluster.datacenter ? cluster.datacenter.toUpperCase() : 'NONE';



        parentRow.querySelector('.cluster-name').innerText = cluster.cluster_name;

        parentRow.querySelector('.top-mapid').innerText = topMapid;

        parentRow.querySelector('.total-licenses').innerText = totalLic;



        // Child Table

        const childBody = childRow.querySelector('.child-tbody');

        if (cluster.mapids.length === 0) {

            childBody.innerHTML = '<tr><td colspan="5" style="text-align:center; opacity:0.6;">No mapped usage</td></tr>';

        } else {

            childBody.innerHTML = sortedMapids.map(m => `

                <tr class="mapid-row-parent">

                    <td style="font-weight:600; color:var(--accent-color);">${m.mapid}</td>

                    <td style="opacity:0.8;">${m.lob || '-'}</td>

                    <td>${m.node_count}</td>

                    <td>${m.vcpu.toFixed(1)}</td>

                    <td style="font-weight:bold;">

                        <div style="display:flex; justify-content:space-between; align-items:center;">

                            ${m.license_count}

                            <button class="btn btn-sm btn-secondary" style="font-size:0.7rem; padding: 0.1rem 0.4rem;" 

                                onclick="toggleMapidResources(${cluster.cluster_id}, '${m.mapid}', this)">

                                <i class="fas fa-chevron-right"></i> Resources

                            </button>

                        </div>

                    </td>

                </tr>

                <tr class="mapid-resources-row" style="display:none; background:rgba(0,0,0,0.1);">

                    <td colspan="5" style="padding:0;">

                        <div style="padding:0.5rem 1rem; border-left: 2px solid var(--accent-color); margin: 0.5rem 1rem;">

                            <div style="text-align:center; padding:1rem;"><i class="fas fa-circle-notch fa-spin"></i> Loading...</div>

                        </div>

                    </td>

                </tr>

            `).join('');

        }



        // Toggle Logic

        const btn = parentRow.querySelector('.toggle-details-btn');

        btn.onclick = () => {

            const isHidden = childRow.style.display === 'none';

            childRow.style.display = isHidden ? 'table-row' : 'none';

            btn.innerHTML = isHidden ? '<i class="fas fa-chevron-up"></i> Hide' : '<i class="fas fa-chevron-down"></i> Breakdown';

        };



        tbody.appendChild(clone);

    });



    window._breakdownData = data; // Store for sorting/filtering if needed later

}



async function toggleMapidResources(clusterId, mapid, btn) {

    const tr = btn.closest('tr');

    const childTr = tr.nextElementSibling;

    const icon = btn.querySelector('i');

    const container = childTr.querySelector('div');



    if (childTr.style.display === 'none') {

        childTr.style.display = 'table-row';

        icon.className = 'fas fa-chevron-down';



        try {

            let url = `/api/dashboard/${clusterId}/mapid/${mapid}/resources`;

            if (window.currentSnapshotTime) {

                url += `?snapshot_time=${window.currentSnapshotTime}`;

            }

            const res = await fetch(url);

            if (!res.ok) throw new Error("Failed");

            const data = await res.json();



            let html = `

                <div style="font-size:0.9rem; padding:1rem;">

                    <h6 style="margin-bottom:0.5rem; color:var(--text-color);">Nodes (${data.nodes.length})</h6>

                    <div style="max-height: 200px; overflow-y: auto; margin-bottom: 1.5rem; background: rgba(0,0,0,0.2); border-radius:4px;">

                        <table class="table table-sm table-borderless" style="margin:0; color:var(--text-color); font-size:0.85rem;">

                            <tbody>

                                ${data.nodes.length ? data.nodes.map(n => `

                                    <tr>

                                        <td style="padding: 0.25rem 0.5rem;"><i class="fas fa-server" style="opacity:0.5; margin-right:5px;"></i> ${n.name}</td>

                                        <td style="padding: 0.25rem 0.5rem; text-align:right; opacity:0.6;">${n.creationTimestamp ? new Date(n.creationTimestamp).toLocaleDateString() : '-'}</td>

                                    </tr>

                                `).join('') : '<tr><td style="padding:0.5rem; opacity:0.5;">No nodes found</td></tr>'}

                            </tbody>

                        </table>

                    </div>

                    

                    <h6 style="margin-bottom:0.5rem; color:var(--text-color);">Namespaces (${data.projects.length})</h6>

                    <div style="max-height: 200px; overflow-y: auto; background: rgba(0,0,0,0.2); border-radius:4px;">

                         <table class="table table-sm table-borderless" style="margin:0; color:var(--text-color); font-size:0.85rem;">

                            <tbody>

                                ${data.projects.length ? data.projects.map(p => `

                                    <tr>

                                        <td style="padding: 0.25rem 0.5rem;"><i class="fas fa-cubes" style="opacity:0.5; margin-right:5px;"></i> ${p.name}</td>

                                        <td style="padding: 0.25rem 0.5rem; text-align:right; opacity:0.6;">${p.requester}</td>

                                    </tr>

                                `).join('') : '<tr><td style="padding:0.5rem; opacity:0.5;">No namespaces found</td></tr>'}

                            </tbody>

                        </table>

                    </div>

                </div>

            `;



            container.innerHTML = html;

        } catch (e) {

            container.innerHTML = `<div style="color:red; padding:0.5rem;">Error loading resources</div>`;

        }

    } else {

        childTr.style.display = 'none';

        icon.className = 'fas fa-chevron-right';

    }

}



function filterBreakdownTable() {

    const q = document.getElementById('breakdown-search').value.toLowerCase();



    // Cluster Tab Filtering

    if (currentBreakdownTab === 'cluster') {

        const rows = document.querySelectorAll('.breakdown-parent-row');

        // Check filters

        const envGroup = ['DEV', 'UAT', 'PROD'];

        const dcGroup = ['AZURE', 'HCI'];



        const anyEnvActive = envGroup.some(f => activeBreakdownFilters[f]);

        const anyDcActive = dcGroup.some(f => activeBreakdownFilters[f]);



        rows.forEach(row => {

            const text = row.innerText.toLowerCase();

            const child = row.nextElementSibling; // The hidden row



            const rowEnv = row.dataset.env || 'NONE';

            const rowDc = row.dataset.dc || 'NONE';



            const nameMatch = text.includes(q);

            const envMatch = !anyEnvActive || activeBreakdownFilters[rowEnv];

            const dcMatch = !anyDcActive || activeBreakdownFilters[rowDc];



            if (nameMatch && envMatch && dcMatch) {

                row.style.display = '';

            } else {

                row.style.display = 'none';

                child.style.display = 'none'; // Hide child too if parent hidden

            }

        });

    }

    // MAPID Tab Filtering

    else {

        // MAPID Env/DC filters are applied server-side via loadMapidBreakdown().

        // Here we only filter by search text on the currently loaded rows.

        const rows = document.querySelectorAll('.mapid-parent-row');

        rows.forEach(row => {

            const text = row.innerText.toLowerCase();

            const child = row.nextElementSibling;



            if (text.includes(q)) {

                row.style.display = '';

            } else {

                row.style.display = 'none';

                child.style.display = 'none';

            }

        });

    }

}





function renderTable(resourceType, data) {

    const contentDiv = document.getElementById('dashboard-content');



    if (!data || data.length === 0) {

        contentDiv.innerHTML = '<div class="card">No resources found.</div>';

        return;

    }



    // Determine columns based on resource type

    // Expanded columns as requested

    let columns = [

        { header: 'Name', path: 'metadata.name' },

        { header: 'Namespace', path: 'metadata.namespace' },

        { header: 'Created', path: 'metadata.creationTimestamp' }

    ];



    if (resourceType === 'nodes') {

        columns = [

            { header: 'Name', path: 'metadata.name' },

            { header: 'Status', path: item => getNested(item, 'status.conditions')?.find(c => c.type === 'Ready')?.status === 'True' ? 'Ready' : 'Not Ready' },

            { header: 'Capacity (vCPU)', path: '__capacity.cpu' },

            { header: 'Capacity (GB)', path: '__capacity.memory_gb' },

            { header: 'Intake #', path: 'metadata.labels.intake_number' },

            { header: 'MAPID', path: 'metadata.labels.mapid' },

            { header: 'LOB', path: 'metadata.labels.lob' },

            { header: 'Roles', path: item => Object.keys(getNested(item, 'metadata.labels') || {}).filter(k => k.startsWith('node-role.kubernetes.io/')).map(k => k.split('/')[1]).join(', ') },

            {

                header: 'CPU Usage',

                path: item => item.__metrics ? renderUsageRing(item.__metrics.cpu_percent, `${item.__metrics.cpu_usage} cores`) : '-'

            },

            {

                header: 'Mem Usage',

                path: item => item.__metrics ? renderUsageRing(item.__metrics.mem_percent, `${item.__metrics.mem_usage_gb} GB`) : '-'

            },

            { header: 'Created', path: 'metadata.creationTimestamp' },

            {

                header: 'Actions', path: item => window.isAdmin ? `

                <button class="btn btn-secondary btn-sm" onclick="showNodeDetails(${window.currentClusterId}, '${item.metadata.name}')">

                    <i class="fas fa-microchip"></i> Details

                </button>

            ` : '-'

            }

        ];

    } else if (resourceType === 'machines') {

        columns = [

            { header: 'Name', path: 'metadata.name' },

            { header: 'Namespace', path: 'metadata.namespace' },

            { header: 'Phase', path: 'status.phase' },

            { header: 'Intake #', path: 'metadata.labels.intake_number' },

            { header: 'MAPID', path: 'metadata.labels.mapid' },

            { header: 'VM Type', path: '__enriched.vm_type' },

            {

                header: 'Subnet', path: item =>

                    getNested(item, 'spec.providerSpec.value.subnet') ||

                    getNested(item, 'spec.providerSpec.value.network.devices[0].networkName') ||

                    '-'

            },

            { header: 'Created', path: 'metadata.creationTimestamp' },

            {

                header: 'Actions', path: item => window.isAdmin ? `

                <button class="btn btn-secondary btn-sm" onclick="showMachineDetails(${window.currentClusterId}, '${item.metadata.name}')">

                    <i class="fas fa-info-circle"></i> Details

                </button>

            ` : '-'

            }

        ];

    } else if (resourceType === 'machinesets') {

        columns = [

            { header: 'Name', path: 'metadata.name' },

            { header: 'Namespace', path: 'metadata.namespace' },

            { header: 'Intake #', path: 'metadata.labels.intake_number' },

            { header: 'MAPID', path: 'metadata.labels.mapid' },

            { header: 'LOB', path: 'metadata.labels.lob' },

            { header: 'Replicas', path: 'spec.replicas' },

            { header: 'Available', path: 'status.availableReplicas' },

            { header: 'Created', path: 'metadata.creationTimestamp' }

        ];

    } else if (resourceType === 'machineautoscalers') {

        columns = [

            { header: 'Name', path: 'metadata.name' },

            { header: 'Target', path: 'spec.scaleTargetRef.name' },

            { header: 'Min', path: 'spec.minReplicas' },

            { header: 'Max', path: 'spec.maxReplicas' },

            { header: 'Created', path: 'metadata.creationTimestamp' }

        ];

    } else if (resourceType === 'projects') {

        columns = [

            { header: 'Name', path: 'metadata.name' },

            { header: 'Status', path: 'status.phase' },

            { header: 'Intake #', path: 'metadata.labels.intake_number' },

            { header: 'MAPID', path: 'metadata.labels.mapid' },

            { header: 'LOB', path: 'metadata.labels.lob' },

            { header: 'Requester', path: 'metadata.annotations.["openshift.io/requester"]' },

            { header: 'Created', path: 'metadata.creationTimestamp' }

        ];

    }





    let titleHtml = `<h1 class="page-title" style="text-transform: capitalize;">${resourceType}</h1>`;

    if (window.currentClusterName) {

        titleHtml = `<div style="display:flex; align-items:baseline;">

            <h1 class="page-title" style="text-transform: capitalize; margin:0;">${resourceType}</h1>

            <i class="fas fa-chevron-right" style="font-size:0.7rem; margin:0 0.8rem; opacity:0.3;"></i> 

            <span style="color: var(--text-secondary); font-weight:400; font-size:0.9rem; margin-right: 0.3rem;">Cluster: </span>

            <span style="color: var(--accent-color); font-weight:500; font-size:0.9rem;">${window.currentClusterName}</span>

        </div>`;

    }



    let html = `

        <div class="page-header">

            ${titleHtml}

            <div style="display:flex; align-items:center; gap:1rem;">

                <div id="data-as-of-container" style="display:none; align-items:center; gap:0.4rem; font-size:0.75rem; color:var(--text-secondary); opacity:0.8;">

                    <i class="far fa-clock"></i> Data as of: <span id="data-as-of-time" style="color:var(--accent-color); font-weight:600;">-</span>

                    <span id="data-next-poll" style="font-style:italic; font-size:0.7rem;"></span>

                </div>

                <input type="text" id="resource-filter" placeholder="Filter table..." class="form-input" style="width:250px;" onkeyup="filterTable()">

                <div style="display:flex; gap:0.5rem;">

                    <button class="btn btn-secondary" title="Export to Excel" onclick="exportTable('${resourceType}', 'excel')">

                        <i class="fas fa-file-excel"></i>

                    </button>

                    <button class="btn btn-secondary" title="Export to CSV" onclick="exportTable('${resourceType}', 'csv')">

                        <i class="fas fa-file-csv"></i>

                    </button>

                </div>

                <span class="badge badge-blue">${data.length} items</span>

            </div>

        </div>

        <div class="card fade-in">

            <div class="table-container">

                <table class="data-table" id="resource-table">

                    <thead>

                        <tr>

                            ${columns.map((col, idx) => `

                                <th class="sortable-header" onclick="sortTable('resource-table', ${idx})">

                                    <div style="display:flex; align-items:center; gap:0.5rem;">

                                        ${col.header}

                                        <i class="fas fa-sort sort-icon" style="opacity:0.3; font-size:0.7rem;"></i>

                                    </div>

                                </th>

                            `).join('')}

                        </tr>

                    </thead>

                    <tbody>

                        ${data.map(item => `

                            <tr>

                                ${columns.map(col => `<td>${getValue(item, col.path)}</td>`).join('')}

                            </tr>

                        `).join('')}

                    </tbody>

                </table>

            </div>

        </div>

    `;

    contentDiv.innerHTML = html;



    // Set status info: Live vs Cached vs Historical

    const container = document.getElementById('data-as-of-container');

    const timeEl = document.getElementById('data-as-of-time');

    const nextEl = document.getElementById('data-next-poll');



    if (container && timeEl && nextEl) {

        if (window.currentSnapshotTime) {

            // Case 1: Time Travel

            container.style.display = 'flex';

            timeEl.innerText = formatEST(window.currentSnapshotTime);

            nextEl.innerText = "(Historical View)";

        } else if (data && data.timestamp) {

            // Case 2: Dashboard Cache

            container.style.display = 'flex';

            timeEl.innerText = formatEST(data.timestamp);

            if (window._dashboardTtl) {

                const remaining = getRemainingCacheTime(data.timestamp, window._dashboardTtl);

                nextEl.innerText = `(New data can be polled in: ${remaining})`;

            }

        } else if (window.currentClusterId) {

            // Case 3: Live Resource Page

            container.style.display = 'flex';

            timeEl.innerHTML = '<span class="badge badge-green" style="font-size:0.7rem;">Live Data</span>';

            timeEl.style.color = 'var(--success-color)';

            nextEl.innerText = "(Internal refresh)";

        } else {

            container.style.display = 'none';

        }

    }

}



function updateSidebarHighlighting(clusterId, resourceType) {

    // Remove all active states

    document.querySelectorAll('.nav-link, .sub-link').forEach(el => el.classList.remove('active'));



    if (clusterId) {

        // Highlight the cluster header

        const clusterHeader = document.querySelector(`.submenu-toggle[data-target="submenu-${clusterId}"]`);

        if (clusterHeader) clusterHeader.classList.add('active');



        // Highlight the specific sub-link

        const submenu = document.getElementById(`submenu-${clusterId}`);

        if (submenu) {

            const subLinks = submenu.querySelectorAll('.sub-link');

            subLinks.forEach(link => {

                const onclick = link.getAttribute('onclick') || '';

                if (onclick.includes(`'${resourceType}'`)) {

                    link.classList.add('active');

                }

            });

        }

    }

}



function sortTable(tableId, colIndex) {

    const table = document.getElementById(tableId);

    if (!table) return;



    const tbody = table.querySelector('tbody');

    const rows = Array.from(tbody.querySelectorAll('tr'));

    const isAsc = table.dataset.sortCol == colIndex && table.dataset.sortDir === 'asc';

    const direction = isAsc ? -1 : 1;



    // Reset icons

    table.querySelectorAll('.sort-icon').forEach(icon => {

        icon.className = 'fas fa-sort sort-icon';

        icon.style.opacity = '0.3';

    });



    // Set new icon

    const activeHeader = table.querySelectorAll('th')[colIndex];

    const activeIcon = activeHeader.querySelector('.sort-icon');

    activeIcon.className = isAsc ? 'fas fa-sort-up sort-icon' : 'fas fa-sort-down sort-icon';

    activeIcon.style.opacity = '1';



    // Sort rows

    const sortedRows = rows.sort((a, b) => {

        const aCol = a.querySelectorAll('td')[colIndex];

        const bCol = b.querySelectorAll('td')[colIndex];



        // Try to get raw value if it's a progress bar or badge

        let aVal = aCol.innerText.trim();

        let bVal = bCol.innerText.trim();



        // Handle numeric sorting (strip non-numeric chars like %, GB, version dots)

        const isNumeric = !isNaN(parseFloat(aVal.replace(/[^0-9.-]/g, ''))) && !isNaN(parseFloat(bVal.replace(/[^0-9.-]/g, '')));



        if (isNumeric && !aVal.includes('.') && !bVal.includes('.')) {

            // Plain number sorting

            const aNum = parseFloat(aVal.replace(/[^0-9.-]/g, ''));

            const bNum = parseFloat(bVal.replace(/[^0-9.-]/g, ''));

            return (aNum - bNum) * direction;

        }



        // Default string sorting

        return aVal.localeCompare(bVal, undefined, { numeric: true, sensitivity: 'base' }) * direction;

    });



    // Update table data attributes

    table.dataset.sortCol = colIndex;

    table.dataset.sortDir = isAsc ? 'desc' : 'asc';



    // Re-append rows

    tbody.append(...sortedRows);

}



function filterTable() {

    const filterField = document.getElementById('resource-filter');

    if (!filterField) return;

    const filter = filterField.value.toLowerCase();

    const table = document.getElementById('resource-table');

    if (!table) return;

    const tr = table.getElementsByTagName('tr');



    for (let i = 1; i < tr.length; i++) {

        let visible = false;

        const tds = tr[i].getElementsByTagName('td');

        for (let j = 0; j < tds.length; j++) {

            if (tds[j]) {

                if (tds[j].textContent.toLowerCase().indexOf(filter) > -1) {

                    visible = true;

                    break;

                }

            }

        }

        tr[i].style.display = visible ? "" : "none";

    }

}



function getValue(item, path) {

    if (typeof path === 'function') {

        return path(item) || '-';

    }

    const val = getNested(item, path);

    return val !== undefined && val !== null ? val : '-';

}



function getNested(obj, path) {

    if (!path) return undefined;

    // Matches either plain fields or bracketed strings ["foo.bar"] or ['foo.bar']

    const parts = path.match(/([^.\[\]]+)|\["([^"\]]+)"\]|\[\'([^\'\]]+)\'\]/g);

    if (!parts) return undefined;



    return parts.reduce((acc, part) => {

        if (acc === undefined || acc === null) return undefined;



        let key = part;

        if (part.startsWith('["') && part.endsWith('"]')) {

            key = part.slice(2, -2);

        } else if (part.startsWith("['") && part.endsWith("']")) {

            key = part.slice(2, -2);

        } else if (part.startsWith('[') && part.endsWith(']')) {

            // Handle indices like [0]

            const index = parseInt(part.slice(1, -1));

            if (!isNaN(index)) return acc[index];

            key = part.slice(1, -1);

        }



        return acc[key];

    }, obj);

}



/* Helper for circular gauges */

function renderUsageRing(percent, tooltip, color = null) {

    const radius = 18;

    const circumference = 2 * Math.PI * radius;

    const offset = circumference - (percent / 100) * circumference;



    if (!color) {

        if (percent >= 80) color = 'var(--danger-color)';

        else if (percent >= 50) color = 'var(--warning-color)';

        else color = 'var(--success-color)';

    }



    return `

        <div class="usage-ring-container" title="${tooltip}">

            <svg class="usage-ring-svg" viewBox="0 0 44 44">

                <circle class="usage-ring-bg" cx="22" cy="22" r="${radius}"></circle>

                <circle class="usage-ring-fill" cx="22" cy="22" r="${radius}"

                        style="stroke:${color}; stroke-dasharray:${circumference}; stroke-dashoffset:${offset};">

                </circle>

            </svg>

            <div class="usage-ring-text" style="color:${color}">${percent}%</div>

        </div>

    `;

}



// License Modal

async function showLicenseDetails(clusterId, usageId) {

    const modal = document.getElementById('license-modal');

    const tbody = document.getElementById('lic-details-body');



    // Reset

    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;">Loading details...</td></tr>';

    document.getElementById('lic-total-nodes').innerText = '-';

    document.getElementById('lic-total-vcpu').innerText = '-';

    document.getElementById('lic-total-count').innerText = '-';



    window.currentClusterLicenseId = clusterId;



    // Reset Trends Toggle

    const wrapper = document.getElementById('cluster-license-chart-wrapper');

    if (wrapper) wrapper.style.height = '0';

    const chevron = document.getElementById('cluster-license-chevron');

    if (chevron) chevron.style.transform = 'rotate(0deg)';

    const text = document.getElementById('trends-toggle-text');

    if (text) text.innerText = 'Show Trends';



    modal.classList.add('open');



    try {

        let url = `/api/dashboard/${clusterId}/license-details/${usageId}`;

        if (window.currentSnapshotTime) {

            url += `?snapshot_time=${window.currentSnapshotTime}`;

        }

        const res = await fetch(url);

        if (!res.ok) throw new Error("Failed to load details");



        const data = await res.json();



        document.getElementById('lic-total-nodes').innerText = data.node_count;

        document.getElementById('lic-total-vcpu').innerText = data.total_vcpu.toFixed(1);

        document.getElementById('lic-total-count').innerText = data.license_count;



        tbody.innerHTML = data.details.map(d => {

            const isInc = d.status === 'INCLUDED';

            const color = isInc ? 'var(--success-color)' : 'var(--danger-color)';



            return `

            <tr style="opacity:${isInc ? 1 : 0.6};">

                <td style="font-family:monospace;">${d.name}</td>

                <td><span class="badge" style="color:${color}; border:1px solid ${color}; background:rgba(255,255,255,0.05);">${d.status}</span></td>

                <td>${d.vcpu}</td>

                <td><strong>${d.licenses}</strong></td>

                <td style="font-size:0.85rem;">${d.reason}</td>

            </tr>

            `;

        }).join('');



    } catch (e) {

        tbody.innerHTML = `<tr><td colspan="5" style="color:red; text-align:center;">Error: ${e.message}</td></tr>`;

    }

}



async function loadClusterTrends(clusterId) {

    const url = `/api/dashboard/trends?cluster_id=${clusterId}&days=30`;

    try {

        const res = await fetch(url);

        const data = await res.json();

        if (data && data.length > 0) {

            renderClusterLicenseChart(data);

        }

    } catch (e) {

        console.error("Failed to load cluster trends:", e);

    }

}



function renderClusterLicenseChart(data) {

    const ctx = document.getElementById('cluster-license-chart').getContext('2d');

    const labels = data.map(d => {

        return formatEST(d.timestamp, false).split(', ')[0] + ' ' + formatEST(d.timestamp, false).split(', ')[1];

    });



    if (clusterLicenseChart) clusterLicenseChart.destroy();



    clusterLicenseChart = new Chart(ctx, {

        type: 'line',

        data: {

            labels: labels,

            datasets: [{

                label: 'Licenses',

                data: data.map(d => d.licenses),

                borderColor: '#a855f7',

                backgroundColor: 'rgba(168, 85, 247, 0.1)',

                fill: true,

                tension: 0.3

            }]

        },

        options: {

            responsive: true,

            maintainAspectRatio: false,

            plugins: {

                legend: { display: false },

                tooltip: {

                    mode: 'index',

                    intersect: false,

                    backgroundColor: 'rgba(15, 23, 42, 0.9)'

                }

            },

            scales: {

                x: {

                    ticks: { display: false },

                    grid: { display: false }

                },

                y: {

                    beginAtZero: true,

                    ticks: { color: '#64748b', font: { size: 10 } },

                    grid: { color: 'rgba(255,255,255,0.05)' }

                }

            }

        }

    });

}



function toggleClusterLicenseTrends() {

    const wrapper = document.getElementById('cluster-license-chart-wrapper');

    const chevron = document.getElementById('cluster-license-chevron');

    const text = document.getElementById('trends-toggle-text');



    if (wrapper.style.height === '0px' || wrapper.style.height === '0') {

        wrapper.style.height = '210px';

        chevron.style.transform = 'rotate(180deg)';

        text.innerText = 'Hide Trends';

        if (window.currentClusterLicenseId) {

            loadClusterTrends(window.currentClusterLicenseId);

        }

    } else {

        wrapper.style.height = '0';

        chevron.style.transform = 'rotate(0deg)';

        text.innerText = 'Show Trends';

    }

}



function closeLicenseModal() {

    document.getElementById('license-modal').classList.remove('open');

}



/**

 * Export table data to Excel or CSV using SheetJS

 * @param {string} filename Base filename for the download

 * @param {string} format 'excel' or 'csv'

 * @param {string} tableId Optional specific table ID to export

 */

function exportTable(filename, format, tableId = null) {

    let table = null;



    if (tableId) {

        table = document.getElementById(tableId);

    }



    if (!table) {

        // Fallback or specific case like audit pages

        const contentDiv = document.getElementById('dashboard-content');

        if (contentDiv) {

            table = contentDiv.querySelector('table');

        }

    }



    if (!table) {

        table = document.querySelector('.data-table');

    }



    if (!table) {

        alert("No table found to export.");

        return;

    }



    // Capture visible rows if filtered

    const wb = XLSX.utils.table_to_book(table, {

        sheet: "Data",

        display: true // Only export visible rows (works with display:none)

    });



    const timestamp = new Date().toISOString().split('T')[0];

    const fullFilename = `${filename}_${timestamp}`;



    if (format === 'excel') {

        XLSX.writeFile(wb, `${fullFilename}.xlsx`);

    } else {

        XLSX.writeFile(wb, `${fullFilename}.csv`, { bookType: 'csv' });

    }

}



async function showClusterDetails(clusterId, clusterName) {

    const modal = document.getElementById('details-modal');

    const body = document.getElementById('details-modal-body');



    modal.classList.add('open');

    body.innerHTML = '<div style="text-align:center; padding:3rem;"><i class="fas fa-circle-notch fa-spin fa-2x"></i><br><br>Fetching technical details...</div>';



    try {

        let url = `/api/dashboard/${clusterId}/details`;

        if (window.currentSnapshotTime) {

            url += `?snapshot_time=${window.currentSnapshotTime}`;

        }

        const res = await fetch(url);

        if (!res.ok) throw new Error("Failed to load cluster details");

        const data = await res.json();



        // Count unhealthy operators

        const unhealthy = data.operators.filter(o => !o.status.available || o.status.degraded).length;



        body.innerHTML = `

            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:1.5rem; margin-bottom:2rem;">

                <div class="card" style="margin:0; padding:1.2rem;">

                    <h4 style="margin-bottom:1rem; color:var(--accent-color);"><i class="fas fa-info-circle"></i> Basic Info</h4>

                    <div style="display:grid; grid-template-columns:100px 1fr; gap:0.5rem; font-size:0.9rem;">

                        <span style="opacity:0.6;">API URL:</span> <code style="word-break:break-all;">${data.api_url}</code>

                        <span style="opacity:0.6;">Console:</span> <a href="${data.console_url}" target="_blank">${data.console_url}</a>

                        <span style="opacity:0.6;">Type:</span> <strong>${data.infrastructure.type}</strong>

                        <span style="opacity:0.6;">Infra Name:</span> <code>${data.infrastructure.infrastructure_name}</code>

                    </div>

                </div>

                <div class="card" style="margin:0; padding:1.2rem;">

                    <h4 style="margin-bottom:1rem; color:var(--accent-color);"><i class="fas fa-code-branch"></i> Version Info</h4>

                    <div style="display:grid; grid-template-columns:100px 1fr; gap:0.5rem; font-size:0.9rem;">

                        <span style="opacity:0.6;">Desired:</span> <strong>${data.version_info.desired_version}</strong>

                        <span style="opacity:0.6;">Cluster ID:</span> <small><code>${data.version_info.cluster_id}</code></small>

                        <span style="opacity:0.6;">History:</span> 

                        <div style="max-height:80px; overflow-y:auto;">

                            ${data.version_info.history.slice(0, 3).map(h => `<div>${h.version} (${h.state})</div>`).join('')}

                        </div>

                    </div>

                </div>

            </div>



            ${window.isAdmin ? `

            <div class="card" style="margin:0;">

                <div style="padding:1rem; border-bottom:1px solid var(--border-color); display:flex; justify-content:space-between; align-items:center;">

                    <h4 style="color:var(--accent-color);"><i class="fas fa-cog"></i> Cluster Operators</h4>

                    <span class="badge ${unhealthy === 0 ? 'badge-green' : 'badge-red'}">

                        ${data.operators.length - unhealthy} / ${data.operators.length} Healthy

                    </span>

                </div>

                <div style="max-height:300px; overflow-y:auto;">

                    <table class="data-table" style="margin:0;">

                        <thead>

                            <tr>

                                <th>Operator</th>

                                <th>Available</th>

                                <th>Progressing</th>

                                <th>Degraded</th>

                                <th>Message</th>

                            </tr>

                        </thead>

                        <tbody>

                            ${data.operators.map(o => `

                                <tr>

                                    <td style="font-weight:600;">${o.name}</td>

                                    <td><i class="fas ${o.status.available ? 'fa-check-circle' : 'fa-times-circle'}" style="color:${o.status.available ? 'var(--success-color)' : 'var(--danger-color)'}"></i></td>

                                    <td><i class="fas ${o.status.progressing ? 'fa-spinner fa-spin' : 'fa-minus'}" style="color:${o.status.progressing ? 'var(--warning-color)' : 'inherit'}"></i></td>

                                    <td><i class="fas ${o.status.degraded ? 'fa-exclamation-triangle' : 'fa-check'}" style="color:${o.status.degraded ? 'var(--danger-color)' : 'var(--success-color)'}"></i></td>

                                    <td><small style="opacity:0.75; font-size:0.8rem; word-break:break-word;">${o.message || '-'}</small></td>

                                </tr>

                            `).join('')}

                        </tbody>

                    </table>

                </div>

            </div>

            ` : ''}





        `;



    } catch (e) {

        body.innerHTML = `<div style="color:var(--danger-color); text-align:center; padding:2rem;"><i class="fas fa-exclamation-triangle fa-2x"></i><br><br>${e.message}</div>`;

    }

}



function closeDetailsModal() {

    document.getElementById('details-modal').classList.remove('open');

}



async function showIngressDetails(clusterId, name) {

    const modal = document.getElementById('ingress-modal');

    const body = document.getElementById('ingress-modal-body');



    modal.classList.add('open');

    body.innerHTML = '<div style="text-align:center; padding:3rem;"><i class="fas fa-circle-notch fa-spin fa-2x"></i><br><br>Fetching ingress details...</div>';



    try {

        let url = `/api/dashboard/${clusterId}/ingress/${name}/details`;

        if (window.currentSnapshotTime) {

            url += `?snapshot_time=${window.currentSnapshotTime}`;

        }

        const response = await fetch(url);

        if (!response.ok) throw new Error("Failed to fetch ingress details");

        const data = await response.json();



        body.innerHTML = `

            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:1.5rem; margin-bottom:2rem;">

                <div class="card" style="margin:0; padding:1.2rem;">

                    <h4 style="margin-bottom:1rem; color:var(--accent-color);"><i class="fas fa-cog"></i> Configuration</h4>

                    <div style="display:grid; grid-template-columns:120px 1fr; gap:0.5rem; font-size:0.9rem;">

                        <span style="opacity:0.6;">Name:</span> <strong>${data.name}</strong>

                        <span style="opacity:0.6;">Domain:</span> <code>${data.spec.domain || '-'}</code>

                        <span style="opacity:0.6;">Replicas:</span> <strong>${data.spec.replicas || 'Default'}</strong>

                        <span style="opacity:0.6;">Endpoint Pub:</span> <span>${data.spec.endpointPublishingStrategy ? data.spec.endpointPublishingStrategy.type : 'Default'}</span>

                    </div>

                </div>

                <div class="card" style="margin:0; padding:1.2rem;">

                    <h4 style="margin-bottom:1rem; color:var(--accent-color);"><i class="fas fa-network-wired"></i> Deployment Placement</h4>

                    <div style="display:grid; grid-template-columns:120px 1fr; gap:0.5rem; font-size:0.9rem;">

                        <span style="opacity:0.6;">Node Selector:</span> <pre style="font-size:0.75rem; margin:0;">${JSON.stringify(data.deployment.node_selector || {}, null, 2)}</pre>

                        <span style="opacity:0.6;">Tolerations:</span> <pre style="font-size:0.75rem; margin:0;">${JSON.stringify(data.deployment.tolerations || [], null, 2)}</pre>

                        <span style="grid-column: 1 / -1; font-size: 0.75rem; opacity: 0.5; margin-top: 0.5rem; border-top: 1px solid var(--border-color); padding-top: 0.3rem;">

                            <i class="fas fa-info-circle"></i> Config fetched from <code>openshift-ingress/${data.deployment.name}</code>

                        </span>

                    </div>

                </div>

            </div>



            <div class="card" style="margin:0; padding:1.2rem; border-left: 4px solid var(--accent-color);">

                <h4 style="margin-bottom:1rem; color:var(--accent-color);"><i class="fas fa-server"></i> Active Router Pods</h4>

                <div class="table-container" style="max-height: 300px; overflow-y: auto;">

                    <table class="data-table" style="font-size: 0.85rem;">

                        <thead>

                            <tr>

                                <th>Pod Name</th>

                                <th>Status</th>

                                <th>Ready</th>

                                <th>Restarts</th>

                                <th>Node</th>

                                <th>Start Time</th>

                            </tr>

                        </thead>

                        <tbody>

                            ${data.pods.length > 0

                ? data.pods.map(p => `

                                    <tr>

                                        <td style="font-family: monospace; font-size: 0.8rem;">${p.name}</td>

                                        <td><span class="badge ${p.status === 'Running' ? 'badge-green' : 'badge-blue'}">${p.status}</span></td>

                                        <td style="text-align:center;"><i class="fas ${p.ready ? 'fa-check-circle' : 'fa-times-circle'}" style="color:${p.ready ? 'var(--success-color)' : 'var(--danger-color)'}"></i></td>

                                        <td style="text-align:center;">${p.restarts}</td>

                                        <td style="font-size: 0.75rem;">${p.node}</td>

                                        <td style="font-size: 0.75rem; white-space: nowrap;">${p.startTime ? formatEST(p.startTime) : '-'}</td>

                                    </tr>

                                `).join('')

                : '<tr><td colspan="6" style="text-align:center; opacity:0.5;">No pods found for this ingress controller.</td></tr>'

            }

                        </tbody>

                    </table>

                </div>

            </div>



        `;

    } catch (error) {

        body.innerHTML = `<div style="color:var(--danger-color); text-align:center; padding:2rem;"><i class="fas fa-exclamation-triangle fa-2x"></i><br><br>${error.message}</div>`;

    }

}



function closeIngressModal() {

    document.getElementById('ingress-modal').classList.remove('open');

}



async function showNodeDetails(clusterId, name) {

    const modal = document.getElementById('node-modal');

    const body = document.getElementById('node-modal-body');



    modal.classList.add('open');

    body.innerHTML = '<div style="text-align:center; padding:3rem;"><i class="fas fa-circle-notch fa-spin fa-2x"></i><br><br>Fetching node details...</div>';



    try {

        let url = `/api/dashboard/${clusterId}/nodes/${name}/details`;

        if (window.currentSnapshotTime) {

            url += `?snapshot_time=${window.currentSnapshotTime}`;

        }

        const response = await fetch(url);

        if (!response.ok) throw new Error("Failed to fetch node details");

        const data = await response.json();



        body.innerHTML = `

            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:1.5rem; margin-bottom:1.5rem;">

                <div class="card" style="margin:0; padding:1.2rem;">

                    <h4 style="margin-bottom:1rem; color:var(--accent-color);"><i class="fas fa-microchip"></i> Resource Usage</h4>

                    <div style="display:grid; grid-template-columns:100px 1fr; gap:0.5rem; font-size:0.9rem;">

                        <span style="opacity:0.6;">CPU (Cores):</span> <strong>${data.usage.cpu.toFixed(2)} / ${data.capacity.cpu}</strong>

                        <div style="grid-column: 1 / -1;">

                            <div class="progress-bar-container" style="height:12px;">

                                <div class="progress-bar" style="width: ${data.usage.cpu_percent}%"></div>

                            </div>

                        </div>

                        <span style="opacity:0.6;">Memory (GB):</span> <strong>${data.usage.memory.toFixed(2)} / ${data.capacity.memory.toFixed(0)}</strong>

                        <div style="grid-column: 1 / -1;">

                            <div class="progress-bar-container" style="height:12px;">

                                <div class="progress-bar" style="width: ${data.usage.mem_percent}%"></div>

                            </div>

                        </div>

                    </div>

                </div>

                <div class="card" style="margin:0; padding:1.2rem;">

                    <h4 style="margin-bottom:1rem; color:var(--accent-color);"><i class="fas fa-chart-pie"></i> Requests & Limits</h4>

                    <div style="display:grid; grid-template-columns:100px 1fr; gap:0.5rem; font-size:0.9rem;">

                        <span style="opacity:0.6;">CPU Req:</span> <strong>${data.requests_limits.cpu_req.toFixed(2)} (${data.requests_limits.cpu_req_percent.toFixed(1)}%)</strong>

                        <span style="opacity:0.6;">CPU Limit:</span> <strong>${data.requests_limits.cpu_lim.toFixed(2)}</strong>

                        <span style="opacity:0.6;">Mem Req:</span> <strong>${data.requests_limits.mem_req.toFixed(2)} GB (${data.requests_limits.mem_req_percent.toFixed(1)}%)</strong>

                        <span style="opacity:0.6;">Mem Limit:</span> <strong>${data.requests_limits.mem_lim.toFixed(2)} GB</strong>

                    </div>

                </div>

            </div>



            <div class="card" style="margin:0; padding:1.2rem; border-left: 4px solid var(--accent-color); margin-bottom:1.5rem;">

                <h4 style="margin-bottom:1rem; color:var(--accent-color);"><i class="fas fa-history"></i> Recent Node Events</h4>

                <div class="table-container" style="max-height: 250px; overflow-y: auto;">

                    <table class="data-table" style="font-size: 0.8rem;">

                        <thead>

                            <tr>

                                <th>Type</th>

                                <th>Reason</th>

                                <th>Message</th>

                                <th>Count</th>

                                <th>Last Seen</th>

                            </tr>

                        </thead>

                        <tbody>

                            ${data.events.length > 0

                ? data.events.map(e => `

                                    <tr>

                                        <td><span class="badge ${e.type === 'Normal' ? 'badge-blue' : 'badge-orange'}">${e.type}</span></td>

                                        <td style="font-weight:600;">${e.reason}</td>

                                        <td style="font-size: 0.75rem;">${e.message}</td>

                                        <td style="text-align:center;">${e.count}</td>

                                        <td style="white-space:nowrap;">${e.lastTimestamp ? formatEST(e.lastTimestamp) : '-'}</td>

                                    </tr>

                                `).join('')

                : '<tr><td colspan="5" style="text-align:center; opacity:0.5;">No recent events found.</td></tr>'

            }

                        </tbody>

                    </table>

                </div>

            </div>



            <div class="card" style="margin:0; padding:1.2rem; border-left: 4px solid #10b981; margin-bottom:1.5rem;">

                <h4 style="margin-bottom:1rem; color:#10b981;"><i class="fas fa-stethoscope"></i> Node Conditions</h4>

                <div class="table-container" style="max-height: 250px; overflow-y: auto;">

                    <table class="data-table" style="font-size: 0.8rem;">

                        <thead>

                            <tr>

                                <th>Type</th>

                                <th>Status</th>

                                <th>Last Heartbeat</th>

                                <th>Last Transition</th>

                                <th>Reason</th>

                                <th>Message</th>

                            </tr>

                        </thead>

                        <tbody>

                            ${data.conditions && data.conditions.length > 0

                ? data.conditions.map(c => `

                                    <tr>

                                        <td style="font-weight:600;">${c.type}</td>

                                        <td>

                                            <span class="badge ${c.status === 'Unknown' ? 'badge-gray' : (c.status === 'True' ? (['Ready', 'Available'].includes(c.type) ? 'badge-green' : 'badge-red') : (['Ready', 'Available'].includes(c.type) ? 'badge-red' : 'badge-green'))}">

                                                ${c.status}

                                            </span>

                                        </td>

                                        <td style="white-space:nowrap; font-size:0.75rem;">${c.lastHeartbeatTime ? formatEST(c.lastHeartbeatTime) : '-'}</td>

                                        <td style="white-space:nowrap; font-size:0.75rem;">${c.lastTransitionTime ? formatEST(c.lastTransitionTime) : '-'}</td>

                                        <td>${c.reason || '-'}</td>

                                        <td style="font-size:0.75rem;">${c.message || '-'}</td>

                                    </tr>

                                `).join('')

                : '<tr><td colspan="6" style="text-align:center; opacity:0.5;">No conditions found.</td></tr>'

            }

                        </tbody>

                    </table>

                </div>

            </div>



            <div style="margin-top:1rem;">

                <h5 style="margin-bottom:0.5rem; opacity:0.7;">Labels</h5>

                <div style="display:flex; gap:0.3rem; flex-wrap:wrap;">

                    ${Object.entries(data.labels).map(([k, v]) => `

                        <span style="font-size:0.7rem; background:rgba(255,255,255,0.05); padding:2px 6px; border-radius:4px; border:1px solid var(--border-color);">

                            ${k}: ${v}

                        </span>

                    `).join('')}

                </div>

            </div>

        `;

    } catch (error) {

        body.innerHTML = `<div style="color:var(--danger-color); text-align:center; padding:2rem;"><i class="fas fa-exclamation-triangle fa-2x"></i><br><br>${error.message}</div>`;

    }

}



function closeNodeModal() {

    document.getElementById('node-modal').classList.remove('open');

}



async function showMachineDetails(clusterId, name) {

    const modal = document.getElementById('machine-modal');

    const body = document.getElementById('machine-modal-body');



    modal.classList.add('open');

    body.innerHTML = '<div style="text-align:center; padding:3rem;"><i class="fas fa-circle-notch fa-spin fa-2x"></i><br><br>Fetching machine details...</div>';



    try {

        let url = `/api/dashboard/${clusterId}/machines/${name}/details`;

        if (window.currentSnapshotTime) {

            url += `?snapshot_time=${window.currentSnapshotTime}`;

        }

        const response = await fetch(url);

        if (!response.ok) throw new Error("Failed to fetch machine details");

        const data = await response.json();

        if (data.error) throw new Error(data.error);



        let platformHtml = '';

        if (data.platform === 'AzureMachineProviderSpec') {

            platformHtml = `

                <div class="card" style="margin:0; padding:1.2rem; border-left: 4px solid #008AD7;">

                    <h4 style="margin-bottom:1rem; color:#008AD7;"><i class="fab fa-microsoft"></i> Azure Metadata</h4>

                    <div style="display:grid; grid-template-columns:140px 1fr; gap:0.5rem; font-size:0.9rem;">

                        <span style="opacity:0.6;">Resource Group:</span> <strong>${data.resource_group}</strong>

                        <span style="opacity:0.6;">VNET:</span> <strong>${data.vnet}</strong>

                        <span style="opacity:0.6;">VNET RG:</span> <strong>${data.vnet_resource_group}</strong>

                        <span style="opacity:0.6;">Subnet:</span> <strong>${data.subnet}</strong>

                        <span style="opacity:0.6;">Zone:</span> <strong>${data.zone}</strong>

                        <span style="opacity:0.6;">Location:</span> <strong>${data.location}</strong>

                        <span style="opacity:0.6;">VM Size:</span> <strong>${data.vm_size}</strong>

                    </div>

                </div>

            `;

        } else if (data.platform === 'VSphereMachineProviderSpec') {

            platformHtml = `

                <div class="card" style="margin:0; padding:1.2rem; border-left: 4px solid #607080;">

                    <h4 style="margin-bottom:1rem; color:#607080;"><i class="fas fa-layer-group"></i> vSphere Metadata</h4>

                    <div style="display:grid; grid-template-columns:140px 1fr; gap:0.5rem; font-size:0.9rem;">

                        <span style="opacity:0.6;">Datacenter:</span> <strong>${data.datacenter}</strong>

                        <span style="opacity:0.6;">Datastore:</span> <strong>${data.datastore}</strong>

                        <span style="opacity:0.6;">Resource Pool:</span> <strong>${data.resource_pool}</strong>

                        <span style="opacity:0.6;">Folder:</span> <strong>${data.folder}</strong>

                        <span style="opacity:0.6;">vCenter Server:</span> <strong>${data.vsphere_server}</strong>

                        <span style="opacity:0.6;">CPUs:</span> <strong>${data.cpus}</strong>

                        <span style="opacity:0.6;">Memory:</span> <strong>${data.memory_mb} MiB</strong>

                        <span style="opacity:0.6;">Disk:</span> <strong>${data.disk_gb} GiB</strong>

                    </div>

                </div>

            `;

        }



        body.innerHTML = `

            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:1.5rem; margin-bottom:1.5rem;">

                <div class="card" style="margin:0; padding:1.2rem;">

                    <h4 style="margin-bottom:1rem; color:var(--accent-color);"><i class="fas fa-info-circle"></i> Machine Info</h4>

                    <div style="display:grid; grid-template-columns:100px 1fr; gap:0.5rem; font-size:0.9rem;">

                        <span style="opacity:0.6;">Name:</span> <strong>${data.name}</strong>

                        <span style="opacity:0.6;">Namespace:</span> <strong>${data.namespace}</strong>

                        <span style="opacity:0.6;">Phase:</span> <span class="badge badge-blue">${data.phase}</span>

                        <span style="opacity:0.6;">Provider ID:</span> <small style="word-break:break-all;"><code>${data.provider_id}</code></small>

                    </div>

                </div>

                ${platformHtml}

            </div>



            <div class="card" style="margin:0; padding:1.2rem; border-left: 4px solid var(--accent-color); margin-bottom:1.5rem;">

                <h4 style="margin-bottom:1rem; color:var(--accent-color);"><i class="fas fa-history"></i> Recent Machine Events</h4>

                <div class="table-container" style="max-height: 250px; overflow-y: auto;">

                    <table class="data-table" style="font-size: 0.8rem;">

                        <thead>

                            <tr>

                                <th>Type</th>

                                <th>Reason</th>

                                <th>Message</th>

                                <th>Count</th>

                                <th>Last Seen</th>

                            </tr>

                        </thead>

                        <tbody>

                            ${data.events && data.events.length > 0

                ? data.events.map(e => `

                                    <tr>

                                        <td><span class="badge ${e.type === 'Normal' ? 'badge-green' : 'badge-red'}">${e.type}</span></td>

                                        <td style="font-weight:600;">${e.reason}</td>

                                        <td style="font-size: 0.75rem;">${e.message}</td>

                                        <td style="text-align:center;">${e.count}</td>

                                        <td style="white-space:nowrap;">${e.lastTimestamp ? formatEST(e.lastTimestamp) : '-'}</td>

                                    </tr>

                                `).join('')

                : '<tr><td colspan="5" style="text-align:center; opacity:0.5;">No recent events found.</td></tr>'

            }

                        </tbody>

                    </table>

                </div>

            </div>



            <div style="margin-top:1rem;">

                <h5 style="margin-bottom:0.5rem; opacity:0.7;">Labels</h5>

                <div style="display:flex; gap:0.3rem; flex-wrap:wrap;">

                    ${Object.entries(data.labels || {}).map(([k, v]) => `

                        <span style="font-size:0.7rem; background:rgba(255,255,255,0.05); padding:2px 6px; border-radius:4px; border:1px solid var(--border-color);">

                            ${k}: ${v}

                        </span>

                    `).join('')}

                </div>

            </div>

        `;

    } catch (error) {

        body.innerHTML = `<div style="color:var(--danger-color); text-align:center; padding:2rem;"><i class="fas fa-exclamation-triangle fa-2x"></i><br><br>${error.message}</div>`;

    }

}



function closeMachineModal() {

    document.getElementById('machine-modal').classList.remove('open');

}



// Historical Trends Modal Logic

let trendsChart = null;

let clusterLicenseChart = null;

window.currentTrendsScale = 'logarithmic';



function setTrendsScale(scale) {

    window.currentTrendsScale = scale;

    // Update UI

    const linBtn = document.getElementById('trends-scale-linear');

    const logBtn = document.getElementById('trends-scale-log');



    if (linBtn && logBtn) {

        if (scale === 'linear') {

            linBtn.classList.add('active');

            linBtn.style.background = 'var(--accent-color)';

            linBtn.style.color = '#0f172a';

            logBtn.classList.remove('active');

            logBtn.style.background = 'transparent';

            logBtn.style.color = 'inherit';

        } else {

            logBtn.classList.add('active');

            logBtn.style.background = 'var(--accent-color)';

            logBtn.style.color = '#0f172a';

            linBtn.classList.remove('active');

            linBtn.style.background = 'transparent';

            linBtn.style.color = 'inherit';

        }

    }

    loadTrendsData();

}



function showHistoricalModal() {

    const modal = document.getElementById('trends-modal');

    if (modal) {

        modal.classList.add('open');

        loadTrendsData();

    }

}



function closeTrendsModal() {

    const modal = document.getElementById('trends-modal');

    if (modal) modal.classList.remove('open');

}



async function loadTrendsData() {

    const days = document.getElementById('trends-days').value;

    const url = `/api/dashboard/trends?days=${days}`;



    try {

        const res = await fetch(url);

        const data = await res.json();



        if (data && Object.keys(data).length > 0) {

            renderTrendsChart(data);

        } else {

            console.warn("No trend data found");

        }

    } catch (e) {

        console.error("Failed to load trends:", e);

    }

}



function renderTrendsChart(data) {

    // 1. Extract all unique timestamps for labels

    const allTimestamps = new Set();

    Object.values(data).forEach(clusterData => {

        clusterData.forEach(d => allTimestamps.add(d.timestamp));

    });



    const sortedTimestamps = Array.from(allTimestamps).sort();

    const labels = sortedTimestamps.map(ts => {

        // Simplified EST label for charts

        return formatEST(ts, false);

    });



    const ctx = document.getElementById('trends-chart').getContext('2d');

    if (trendsChart) trendsChart.destroy();



    // 2. Map data to datasets

    const colors = [

        '#f97316', '#38bdf8', '#10b981', '#a855f7', '#fbbf24',

        '#ef4444', '#6366f1', '#ec4899', '#14b8a6', '#f43f5e',

        '#06b6d4', '#8b5cf6', '#f59e0b', '#10b981', '#6366f1'

    ];



    const datasets = Object.entries(data).map(([clusterName, points], idx) => {

        const color = colors[idx % colors.length];



        const datasetData = sortedTimestamps.map(ts => {

            const match = points.find(p => p.timestamp === ts);

            return match ? match.licenses : null;

        });



        return {

            label: clusterName,

            data: datasetData,

            borderColor: color,

            backgroundColor: color + '1A',

            fill: false,

            tension: 0.3,

            spanGaps: true

        };

    });



    trendsChart = new Chart(ctx, {

        type: 'line',

        data: {

            labels: labels,

            datasets: datasets

        },

        options: {

            responsive: true,

            maintainAspectRatio: false,

            interaction: {

                mode: 'index',

                intersect: false,

            },

            plugins: {

                legend: {

                    position: 'bottom',

                    maxHeight: 100,

                    labels: { color: '#94a3b8', font: { size: 10 }, padding: 10, usePointStyle: true, pointStyle: 'circle' }

                },

                tooltip: {

                    backgroundColor: 'rgba(15, 23, 42, 0.9)',

                    titleColor: '#f8fafc',

                    bodyColor: '#cbd5e1',

                    borderColor: 'rgba(255,255,255,0.1)',

                    borderWidth: 1,

                    callbacks: {

                        label: function (context) {

                            let label = context.dataset.label || '';

                            if (label) label += ': ';

                            if (context.parsed.y !== null) label += context.parsed.y + ' Lic';

                            return label;

                        }

                    }

                }

            },

            scales: {

                x: {

                    ticks: { color: '#64748b', font: { size: 9 }, maxRotation: 45, minRotation: 45 },

                    grid: { color: 'rgba(255,255,255,0.05)' }

                },

                y: {

                    type: window.currentTrendsScale || 'logarithmic',

                    beginAtZero: (window.currentTrendsScale || 'logarithmic') === 'linear',

                    min: (window.currentTrendsScale || 'logarithmic') === 'logarithmic' ? 1 : undefined,

                    ticks: {

                        color: '#64748b',

                        callback: function (value) {

                            if (window.currentTrendsScale === 'logarithmic') {

                                // Chart.js logarithmic scale labels can be messy, format them simply

                                if (value === 1 || value === 10 || value === 100 || value === 1000 || value === 10000) {

                                    return value.toLocaleString();

                                }

                                // For other values skip label to avoid clutter if too many

                                return null;

                            }

                            return value;

                        }

                    },

                    grid: { color: 'rgba(255,255,255,0.05)' },

                    title: { display: true, text: 'Total Licenses', color: '#64748b' }

                }

            }

        }

    });

}



/* Report Generation Logic */

const reportFilters = {

    env: ['DEV', 'UAT', 'PROD'], // Default All

    dc: ['AZURE', 'HCI']

};



// Default Columns

let reportColumns = [

    { name: "Cluster Name", included: true },

    { name: "Environment", included: true },

    { name: "Datacenter", included: true },

    { name: "Node Name", included: true },

    { name: "Node vCPU", included: true },

    { name: "Node Memory (GB)", included: true },

    { name: "Node MAPID", included: true },

    { name: "LOB", included: true },

    { name: "Licenses Consumed", included: true },

    { name: "License Status", included: true }

];



function openReportModal() {

    // Reset filters to default (all off = all included)

    document.querySelectorAll('.report-filter').forEach(btn => btn.classList.remove('active'));



    document.getElementById('report-loading').style.display = 'none';

    document.getElementById('report-advanced').style.display = 'none';

    document.getElementById('report-main-view').style.display = 'block';



    loadReportSettings(); // Load persisted settings

    renderReportColumns();

    previewReport(); // Load everything initially



    document.getElementById('report-modal').classList.add('open');

}



function closeReportModal() {

    document.getElementById('report-modal').classList.remove('open');

}



function toggleReportFilter(btn) {

    btn.classList.toggle('active');

    // Debounce or immediate? Immediate is fine for now

    previewReport();

}





function loadReportSettings() {

    const stored = localStorage.getItem('ocp_report_columns');

    if (stored) {

        try {

            const savedCols = JSON.parse(stored);

            // Verify integrity: ensure all default columns exist

            // This handles cases where we might add new columns in the future

            // We use the saved order and inclusion status, but fall back to defaults for missing ones



            // Create a map of saved cols

            const savedMap = new Map(savedCols.map(c => [c.name, c]));



            // Reconstruct reportColumns respecting saved order if possible, 

            // but for simplicity we'll just check if the saved array has the same length/names? 

            // A simple strategy: use savedCols directly if valid, else merge.

            // Let's just trust valid JSON for now, but maybe ensure names match?

            if (savedCols.length > 0) {

                reportColumns = savedCols;

            }

        } catch (e) {

            console.error("Failed to load report settings", e);

        }

    }

}



function saveReportSettings() {

    localStorage.setItem('ocp_report_columns', JSON.stringify(reportColumns));

}



function renderReportColumns() {

    const list = document.getElementById('column-list');

    list.innerHTML = '';



    reportColumns.forEach((col, idx) => {

        const div = document.createElement('div');

        div.style.display = 'flex';

        div.style.alignItems = 'center';

        div.style.padding = '0.3rem 0';

        div.style.borderBottom = '1px solid var(--border-color)';

        div.style.background = 'transparent';



        div.innerHTML = `

            <input type="checkbox" ${col.included ? 'checked' : ''} onchange="toggleReportColumn(${idx})" style="margin-right:0.5rem; cursor:pointer;">

            <span style="flex:1; opacity:${col.included ? 1 : 0.6}; font-size:0.85rem;">${col.name}</span>

            <div style="display:flex; gap:0.25rem;">

                <button class="btn btn-sm btn-secondary" onclick="moveReportColumn(${idx}, -1)" ${idx === 0 ? 'disabled' : ''} style="padding:0.1rem 0.4rem; font-size:0.7rem;"><i class="fas fa-arrow-up"></i></button>

                <button class="btn btn-sm btn-secondary" onclick="moveReportColumn(${idx}, 1)" ${idx === reportColumns.length - 1 ? 'disabled' : ''} style="padding:0.1rem 0.4rem; font-size:0.7rem;"><i class="fas fa-arrow-down"></i></button>

            </div>

        `;

        list.appendChild(div);

    });

}



function toggleReportColumn(idx) {

    reportColumns[idx].included = !reportColumns[idx].included;

    saveReportSettings();

    renderReportColumns();

}



function moveReportColumn(idx, dir) {

    if (idx + dir < 0 || idx + dir >= reportColumns.length) return;

    const temp = reportColumns[idx];

    reportColumns[idx] = reportColumns[idx + dir];

    reportColumns[idx + dir] = temp;

    saveReportSettings();

    renderReportColumns();

}



async function previewReport() {

    const list = document.getElementById('preview-cluster-list');

    const count = document.getElementById('preview-count');



    // Don't clear list while typing/clicking rapidly, maybe just show spinner overlay?

    // For now simple:

    list.innerHTML = '<div style="text-align:center; padding:1rem;"><i class="fas fa-circle-notch fa-spin"></i> Loading...</div>';



    const envs = [];

    const dcs = [];

    document.querySelectorAll('.report-filter.active').forEach(btn => {

        if (btn.dataset.type === 'env') envs.push(btn.dataset.val);

        if (btn.dataset.type === 'dc') dcs.push(btn.dataset.val);

    });



    try {

        const res = await fetch('/api/reports/preview', {

            method: 'POST',

            headers: { 'Content-Type': 'application/json' },

            body: JSON.stringify({ environments: envs, datacenters: dcs })

        });



        if (res.ok) {

            const data = await res.json();

            count.innerText = data.length;



            if (data.length === 0) {

                list.innerHTML = '<div style="padding:1rem; text-align:center;">No clusters found matching selection.</div>';

            } else {

                list.innerHTML = data.map(c => `

                    <div style="padding:0.2rem 0; border-bottom:1px solid rgba(255,255,255,0.05); display:flex; justify-content:space-between;">

                        <span style="color:var(--accent-color);">${c.name}</span> 

                        <span style="opacity:0.5; font-size:0.75rem; font-family:sans-serif;">${c.environment} / ${c.datacenter}</span>

                    </div>

                `).join('');

            }

        }

    } catch (e) {

        list.innerHTML = `<div style="color:var(--danger-color);">Error loading preview: ${e.message}</div>`;

    }

}



async function generateReport() {

    document.getElementById('report-main-view').style.display = 'none';

    const loader = document.getElementById('report-loading');

    const progressBar = document.getElementById('report-progress-bar');

    const progressText = document.getElementById('report-progress-text');

    const genBtn = document.getElementById('report-gen-btn');



    loader.style.display = 'block';

    genBtn.style.display = 'none';

    progressBar.style.width = '0%';

    progressText.innerText = 'Connecting to server...';



    const envs = [];

    const dcs = [];

    document.querySelectorAll('.report-filter.active').forEach(btn => {

        if (btn.dataset.type === 'env') envs.push(btn.dataset.val);

        if (btn.dataset.type === 'dc') dcs.push(btn.dataset.val);

    });



    // We get total clusters from the preview count

    const totalCount = parseInt(document.getElementById('preview-count').innerText) || 1;

    let clustersProcessed = 0;

    let rowData = [];



    try {

        const response = await fetch('/api/reports/generate', {

            method: 'POST',

            headers: { 'Content-Type': 'application/json' },

            body: JSON.stringify({ environments: envs, datacenters: dcs })

        });



        if (!response.ok) throw new Error("Failed to generate report data.");



        const reader = response.body.getReader();

        const decoder = new TextDecoder();

        let buffer = '';



        while (true) {

            const { done, value } = await reader.read();

            if (done) break;



            buffer += decoder.decode(value, { stream: true });



            // This is a simple but effective way to track progress since we yield one JSON object per row

            // and the streaming response is essentially a large JSON array.

            // We can search for the end of objects "}" followed by a comma or the end of the array.

            // Count number of "Cluster Name" keys since each row has one.

            const matches = buffer.match(/"Cluster Name":/g);

            if (matches) {

                // Approximate progress based on clusters. 

                // Since a cluster has many nodes, let's track "Cluster Name" appearances in the buffer

                // This isn't perfect for exact cluster count because multiple clusters might be in one chunk,

                // but we can use unique cluster names if we want to be precise.



                // For simplicity, let's just count total rows received so far to gauge activity

                const rowCount = matches.length;

                progressText.innerText = `Received ${rowCount} nodes...`;



                // If we want a percentage, we'd need to know total nodes, which we don't.

                // But we know total clusters! Let's count unique cluster names in the buffer.

                // We'll use a regex that captures "Cluster Name": "..."

                const clusterMatches = [...buffer.matchAll(/"Cluster Name":\s*"([^"]+)"/g)];

                const uniqueClusters = new Set(clusterMatches.map(m => m[1])).size;



                const percent = Math.min(Math.round((uniqueClusters / totalCount) * 100), 99);

                progressBar.style.width = percent + '%';

                progressText.innerText = `Processing cluster ${uniqueClusters} of ${totalCount}... (${rowCount} nodes)`;

            }

        }



        // Final parse of the completed buffer

        rowData = JSON.parse(buffer);

        progressBar.style.width = '100%';

        progressText.innerText = 'Finalizing Excel file...';



        if (rowData.length > 0) {

            const finalCols = reportColumns.filter(c => c.included);

            const excelData = rowData.map(row => {

                const newRow = {};

                finalCols.forEach(col => {

                    newRow[col.name] = row[col.name];

                });

                return newRow;

            });



            const ws = XLSX.utils.json_to_sheet(excelData, { header: finalCols.map(c => c.name) });

            const wb = XLSX.utils.book_new();

            XLSX.utils.book_append_sheet(wb, ws, "License_Report");

            XLSX.writeFile(wb, `OCP_License_Report_${new Date().toISOString().slice(0, 10)}.xlsx`);

            closeReportModal();

        } else {

            alert("No data found for report.");

        }



    } catch (e) {

        console.error(e);

        alert("Error generating report: " + e.message);

    } finally {

        loader.style.display = 'none';

        document.getElementById('report-main-view').style.display = 'block';

        genBtn.style.display = 'block';

    }

}

async function restartPod() {

    if (!confirm("CRITICAL ACTION: This will intentionally crash the application process to force a pod restart in OpenShift. The app will be unavailable for 10-30 seconds. Proceed?")) return;



    try {

        const res = await fetch('/api/admin/clusters/restart', { method: 'POST' });

        // We probably won't get a response if it exits fast enough

        alert("Restart signal sent. The application process is exiting...");

        setTimeout(() => window.location.reload(), 5000);

    } catch (e) {

        // Expected error if connection closed

        alert("Restarting... Please reload the page in a few moments.");

        setTimeout(() => window.location.reload(), 3000);

    }

}



let _upgradePollInterval = null;

let _upgradeTimerInterval = null;

let _secondsRemaining = 15;



function showUpgradeDetails(clusterId) {

    // Initial Render

    updateUpgradeModalContent(clusterId);



    // Open Modal

    document.getElementById('upgrade-modal').classList.add('open');



    startUpgradeTimers(clusterId);

}



function startUpgradeTimers(clusterId) {

    stopUpgradeTimers();

    _secondsRemaining = 15;

    updateTimerDisplay();



    // Main Refresh Loop (15s)

    console.log(`[Debug] Starting upgrade timer for cluster ${clusterId}`);

    _upgradePollInterval = setInterval(async () => {

        console.log(`[Debug] Upgrade poll triggered for cluster ${clusterId}`);

        await refreshUpgradeDetails(clusterId);

        _secondsRemaining = 15; // Reset after fetch start

        updateTimerDisplay();

    }, 15000);



    // Countdown Loop (1s)

    _upgradeTimerInterval = setInterval(() => {

        _secondsRemaining--;

        if (_secondsRemaining < 0) _secondsRemaining = 0;

        updateTimerDisplay();

    }, 1000);

}



function stopUpgradeTimers() {

    if (_upgradePollInterval) clearInterval(_upgradePollInterval);

    if (_upgradeTimerInterval) clearInterval(_upgradeTimerInterval);

    _upgradePollInterval = null;

    _upgradeTimerInterval = null;

}



function updateTimerDisplay() {

    const el = document.getElementById('upgrade-timer');

    if (el) el.innerText = `Next refresh in: ${_secondsRemaining}s`;

}



function closeUpgradeModal() {

    stopUpgradeTimers();

    document.getElementById('upgrade-modal').classList.remove('open');

}



async function refreshUpgradeDetails(clusterId) {

    try {

        // reuse the live stats refresh logic

        await refreshClusterLive(clusterId);

        // data in window._allClusters is now updated

        updateUpgradeModalContent(clusterId);

        console.log(`[Debug] Upgrade details refreshed for cluster ${clusterId}`);

    } catch (e) {

        console.error("Auto-refresh failed", e);

    }

}



function updateUpgradeModalContent(clusterId) {

    const cluster = window._allClusters.find(c => c.id === clusterId);

    if (!cluster || !cluster.stats || !cluster.stats.upgrade_status) {

        console.warn(`[Debug] Missing upgrade status for cluster ${clusterId}`, cluster);

        return;

    }



    const status = cluster.stats.upgrade_status;

    document.getElementById('upgrade-cluster-name').innerText = cluster.name;

    document.getElementById('upgrade-current-version').innerText = cluster.stats.version || 'Unknown';

    document.getElementById('upgrade-target-version').innerText = status.target_version;

    document.getElementById('upgrade-message').innerText = status.message;



    // Percentage

    let pct = status.percentage || 0;

    document.getElementById('upgrade-percentage').innerText = `${pct}%`;

    document.getElementById('upgrade-progress-bar').style.width = `${pct}%`;

}



function renderServiceMesh(meshData) {

    if (!meshData || !meshData.is_active) return '';



    const cps = meshData.control_planes || [];

    const members = meshData.membership || [];

    const gateways = meshData.traffic ? meshData.traffic.gateways : [];

    const vservices = meshData.traffic ? meshData.traffic.virtual_services : [];

    const meshSize = meshData.summary ? meshData.summary.mesh_size : 0;



    let html = `

        <div style="margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--border-color);">

            <h3 style="color:var(--accent-color); margin-bottom: 1rem;"><i class="fas fa-project-diagram"></i> Service Mesh Inventory</h3>

            

            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap:1.5rem; margin-bottom:1.5rem;">

                <!-- Control Planes -->

                <div class="card" style="margin:0; padding:1.2rem; border-left: 4px solid #3b82f6;">

                    <h4 style="margin-bottom:1rem; color:#3b82f6;"><i class="fas fa-tower-broadcast"></i> Control Plane</h4>

                    ${cps.length > 0 ? cps.map(cp => `

                        <div style="margin-bottom: 0.8rem; padding-bottom: 0.8rem; border-bottom: 1px solid var(--border-color);">

                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.2rem;">

                                <div style="font-weight:600; font-size:1rem;">${cp.type}</div>

                                <span class="badge ${cp.status === 'Active' || cp.status === 'Healthy' ? 'badge-green' : 'badge-orange'}">${cp.status}</span>

                            </div>

                            <div style="display:grid; grid-template-columns: 80px 1fr; gap:0.3rem; font-size:0.85rem;">

                                <span style="opacity:0.6;">Name:</span> <code style="word-break:break-all;">${cp.name}</code>

                                <span style="opacity:0.6;">Namespace:</span> <code style="word-break:break-all;">${cp.namespace}</code>

                                <span style="opacity:0.6;">Version:</span> <strong>${cp.version}</strong>

                            </div>

                        </div>

                    `).join('') : '<div style="opacity:0.6;">No Control Planes detected</div>'}

                </div>



                <!-- Mesh Stats -->

                <div class="card" style="margin:0; padding:1.2rem; border-left: 4px solid #10b981;">

                    <h4 style="margin-bottom:1rem; color:#10b981;"><i class="fas fa-chart-pie"></i> Mesh Overview</h4>

                    <div style="display:grid; grid-template-columns: 1fr 1fr; gap:1rem; text-align:center;">

                        <div>

                            <div style="font-size:1.5rem; font-weight:700;">${members.length}</div>

                            <div style="font-size:0.8rem; opacity:0.7;">Namespaces</div>

                        </div>

                        <div>

                            <div style="font-size:1.5rem; font-weight:700;">${meshSize}</div>

                            <div style="font-size:0.8rem; opacity:0.7;">Proxied Pods</div>

                        </div>

                        <div>

                            <div style="font-size:1.5rem; font-weight:700;">${gateways.length}</div>

                            <div style="font-size:0.8rem; opacity:0.7;">Gateways</div>

                        </div>

                        <div>

                            <div style="font-size:1.5rem; font-weight:700;">${vservices.length}</div>

                            <div style="font-size:0.8rem; opacity:0.7;">VirtualServices</div>

                        </div>

                        

                        <div style="grid-column: 1 / -1; margin-top: 0.5rem;">

                             <div style="max-height: 100px; overflow-y: auto; background: rgba(0,0,0,0.1); padding: 0.5rem; border-radius: 4px; text-align: left; font-size: 0.75rem; font-family: monospace;">

                                <div style="font-weight:600; margin-bottom:0.3rem; color:var(--text-secondary);">Member Namespaces:</div>

                                ${members.join(', ')}

                            </div>

                        </div>

                    </div>

                </div>

            </div>



            <!-- Traffic Config -->

            <div class="card" style="margin:0; padding:1.2rem;">

                 <h4 style="margin-bottom:1rem; color:var(--text-primary);"><i class="fas fa-network-wired"></i> Traffic Configuration</h4>

                 

                 <div style="margin-bottom: 1.5rem;">

                    <h5 style="opacity:0.8; font-size:0.9rem; margin-bottom:0.5rem;">Gateways</h5>

                    <div class="table-container" style="max-height: 200px; overflow-y: auto;">

                        <table class="data-table" style="font-size:0.8rem; width:100%;">

                            <thead>

                                <tr>

                                    <th>Name</th>

                                    <th>Namespace</th>

                                    <th>Selector</th>

                                    <th>Servers</th>

                                </tr>

                            </thead>

                            <tbody>

                                ${gateways.length > 0 ? gateways.map(g => `

                                    <tr>

                                        <td>${g.name}</td>

                                        <td>${g.namespace}</td>

                                        <td>${Object.keys(g.selector || {}).map(k => `${k}=${g.selector[k]}`).join(', ')}</td>

                                        <td>${(g.servers || []).map(s => `${s.port?.number}/${s.port?.protocol} (${(s.hosts || []).join(', ')})`).join('<br>')}</td>

                                    </tr>

                                `).join('') : '<tr><td colspan="4" style="text-align:center; opacity:0.5;">No Gateways found</td></tr>'}

                            </tbody>

                        </table>

                    </div>

                 </div>



                 <div>

                    <h5 style="opacity:0.8; font-size:0.9rem; margin-bottom:0.5rem;">VirtualServices</h5>

                    <div class="table-container" style="max-height: 200px; overflow-y: auto;">

                        <table class="data-table" style="font-size:0.8rem; width:100%;">

                            <thead>

                                <tr>

                                    <th>Name</th>

                                    <th>Namespace</th>

                                    <th>Hosts</th>

                                    <th>Gateways</th>

                                </tr>

                            </thead>

                            <tbody>

                                ${vservices.length > 0 ? vservices.map(v => `

                                    <tr>

                                        <td>${v.name}</td>

                                        <td>${v.namespace}</td>

                                        <td>${(v.hosts || []).join(', ')}</td>

                                        <td>${(v.gateways || []).join(', ')}</td>

                                    </tr>

                                `).join('') : '<tr><td colspan="4" style="text-align:center; opacity:0.5;">No VirtualServices found</td></tr>'}

                            </tbody>

                        </table>

                    </div>

                 </div>

            </div>

        </div>

    `;



    return html;

}



async function loadServiceMesh(clusterId) {

    try {

        // Show loading state

        const contentDiv = document.querySelector('.main-content');

        contentDiv.innerHTML = `

            <div class="page-header">

                <h1 class="page-title"><i class="fas fa-project-diagram"></i> Service Mesh - Loading...</h1>

            </div>

            <div style="text-align:center; padding: 4rem;">

                <i class="fas fa-circle-notch fa-spin fa-3x" style="color:var(--accent-color);"></i>

            </div>

        `;



        // Highlight logic reuse

        document.querySelectorAll('.nav-link, .sub-link').forEach(l => l.classList.remove('active'));

        // Find the SM link and activate it? Hard to find specific one easily without ID, 

        // but let's at least highlight the cluster parent.

        const clusterLink = document.querySelector(`.nav-link[data-cluster-id="${clusterId}"]`);

        if (clusterLink) {

            // Find parent

            const clusterItem = clusterLink.closest('.cluster-item');

            if (clusterItem) {

                const smLink = clusterItem.querySelector('.sub-link[onclick*="loadServiceMesh"]');

                if (smLink) smLink.classList.add('active');

            }

        }



        // Fetch details (reuse existing endpoint)

        let url = `/api/dashboard/${clusterId}/details`;

        if (window.currentSnapshotTime) {

            url += `?snapshot_time=${encodeURIComponent(window.currentSnapshotTime)}`;

        }



        const response = await fetch(url);

        if (!response.ok) throw new Error("Failed to fetch cluster details");

        const data = await response.json();

        const cluster = data.cluster || {}; // If wrapping exists



        // Extract SM Data

        const meshData = data.service_mesh || (data.stats && data.stats.service_mesh); // Support both structures if flattened



        if (!meshData || !meshData.is_active) {

            contentDiv.innerHTML = `

                <div class="page-header">

                     <h1 class="page-title"><i class="fas fa-project-diagram"></i> ${data.cluster_name || 'Cluster'} Service Mesh</h1>

                </div>

                <div class="card" style="text-align:center; padding:3rem;">

                    <i class="fas fa-ban fa-3x" style="color:var(--text-secondary); opacity:0.3; margin-bottom:1rem;"></i>

                    <h3>Service Mesh Not Enabled</h3>

                    <p style="opacity:0.6;">No active Service Mesh Control Plane was detected on this cluster.</p>

                </div>

            `;

            return;

        }



        renderServiceMeshPage(data, meshData);



    } catch (e) {

        console.error("Load SM Error", e);

        document.querySelector('.main-content').innerHTML = `

            <div style="padding:2rem; color:var(--danger-color); text-align:center;">

                <h3>Error Loading Service Mesh</h3>

                <p>${e.message}</p>

            </div>

        `;

    }

}







function renderServiceMeshPage(clusterData, meshData) {

    const cps = meshData.control_planes || [];

    const members = meshData.membership || [];

    const gateways = meshData.traffic ? meshData.traffic.gateways : [];

    const vservices = meshData.traffic ? meshData.traffic.virtual_services : [];

    const meshSize = meshData.summary ? meshData.summary.mesh_size : 0;

    // Fix: Prioritize cluster_name which comes from get_detailed_stats
    const clusterName = clusterData.cluster_name || clusterData.name || (clusterData.cluster ? clusterData.cluster.name : 'Cluster');



    // color fix: ensure high contrast for version

    // Using a light purple badge to match the theme but ensure visibility

    const versionBadgeStyle = "background:rgba(139, 92, 246, 0.2); color:#e9d5ff; border:1px solid rgba(139, 92, 246, 0.3);";



    // 1. Organize Data by Namespace

    const allNamespaces = new Set([...members]);

    gateways.forEach(g => allNamespaces.add(g.namespace));

    vservices.forEach(v => allNamespaces.add(v.namespace));

    const sortedMembers = Array.from(allNamespaces).sort();



    const html = `

        <div class="page-header" style="margin-bottom: 2rem;">

            <div>

                 <div style="font-size:0.85rem; opacity:0.6; text-transform:uppercase; letter-spacing:1px; margin-bottom:0.2rem;">Service Mesh Dashboard</div>

                 <h1 class="page-title" style="display:flex; align-items:center; gap:0.5rem;">

                    Cluster: <span style="color:var(--accent-color);">${clusterName}</span>

                 </h1>

            </div>

            <!-- Back button removed -->

        </div>



        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap:1.5rem; margin-bottom:2rem;">

            <!-- Control Plane Card -->

            <div class="card" style="margin:0; border-top: 4px solid #3b82f6;">

                <h3 style="margin-bottom:1.2rem; color:#3b82f6; display:flex; align-items:center; gap:0.5rem;">

                    <i class="fas fa-tower-broadcast"></i> Control Plane

                </h3>

                ${cps.map(cp => `

                    <div style="background:rgba(255,255,255,0.03); border-radius:8px; padding:1rem; margin-bottom:1rem;">

                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">

                            <span style="font-weight:700; font-size:1.1rem;">${cp.type}</span>

                            <span class="badge ${cp.status === 'Active' || cp.status === 'Healthy' || cp.status === 'Installed' ? 'badge-green' : 'badge-orange'}">${cp.status}</span>

                        </div>

                        <div style="display:grid; grid-template-columns: auto 1fr; gap:0.5rem 1rem; font-size:0.9rem; opacity:0.9;">

                            <span style="opacity:0.6;">Name:</span> <code>${cp.name}</code>

                            <span style="opacity:0.6;">Namespace:</span> <code>${cp.namespace}</code>

                            <span style="opacity:0.6;">Version:</span> <strong>${cp.version}</strong>

                        </div>

                    </div>

                `).join('')}

            </div>



            <!-- Stats Card -->

            <div class="card" style="margin:0; border-top: 4px solid #10b981;">

                <h3 style="margin-bottom:1.2rem; color:#10b981; display:flex; align-items:center; gap:0.5rem;">

                    <i class="fas fa-chart-pie"></i> Mesh Overview

                </h3>

                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:1.5rem; text-align:center;">

                    <div style="background:rgba(255,255,255,0.03); padding:1rem; border-radius:12px;">

                        <div style="font-size:2rem; font-weight:800;">${sortedMembers.length}</div>

                        <div style="text-transform:uppercase; font-size:0.7rem; opacity:0.6; letter-spacing:1px;">Namespaces</div>

                    </div>

                    <div style="background:rgba(255,255,255,0.03); padding:1rem; border-radius:12px;">

                        <div style="font-size:2rem; font-weight:800;">${meshSize}</div>

                        <div style="text-transform:uppercase; font-size:0.7rem; opacity:0.6; letter-spacing:1px;">Proxied Pods</div>

                    </div>

                     <div style="background:rgba(255,255,255,0.03); padding:1rem; border-radius:12px;">

                        <div style="font-size:2rem; font-weight:800;">${gateways.length}</div>

                        <div style="text-transform:uppercase; font-size:0.7rem; opacity:0.6; letter-spacing:1px;">Gateways</div>

                    </div>

                    <div style="background:rgba(255,255,255,0.03); padding:1rem; border-radius:12px;">

                        <div style="font-size:2rem; font-weight:800;">${vservices.length}</div>

                        <div style="text-transform:uppercase; font-size:0.7rem; opacity:0.6; letter-spacing:1px;">VirtualServices</div>

                    </div>

                </div>

            </div>

        </div>



        <div class="card">

             <h3 style="margin-bottom:1.5rem; display:flex; align-items:center; gap:0.5rem;">

                <i class="fas fa-network-wired"></i> Member Namespaces

            </h3>

            

            <div class="accordion" id="sm-accordion">

                ${sortedMembers.map((ns, idx) => {

        const nsGateways = gateways.filter(g => g.namespace === ns);

        const nsVS = vservices.filter(v => v.namespace === ns);

        const hasConfig = nsGateways.length > 0 || nsVS.length > 0;



        return `

                    <div class="accordion-item" style="background:var(--card-bg); border:1px solid var(--border-color); margin-bottom:0.5rem; border-radius:8px; overflow:hidden;">

                        <div class="accordion-header" onclick="this.parentElement.classList.toggle('active')" style="display:flex; justify-content:space-between; align-items:center; padding:1rem; cursor:pointer; background:rgba(255,255,255,0.02);">

                            <div style="display:flex; align-items:center; gap:0.8rem;">

                                <i class="fas fa-chevron-right accordion-icon" style="transition:transform 0.2s;"></i>

                                <span style="font-weight:600; font-size:1rem;">${ns}</span>

                                ${hasConfig ? `<span class="badge badge-purple" style="font-size:0.75rem; background:rgba(139, 92, 246, 0.2); color:#c4b5fd;">Configured</span>` : ''}

                            </div>

                            <div style="display:flex; gap:1rem; opacity:0.6; font-size:0.85rem;">

                                ${nsGateways.length > 0 ? `<span><i class="fas fa-door-open"></i> ${nsGateways.length} GW</span>` : ''}

                                ${nsVS.length > 0 ? `<span><i class="fas fa-code-branch"></i> ${nsVS.length} VS</span>` : ''}

                            </div>

                        </div>

                        <div class="accordion-body" style="display:none; padding:1.5rem; border-top:1px solid var(--border-color);">

                            ${hasConfig ? `

                                <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap:2rem;">

                                    ${nsGateways.length > 0 ? `

                                        <div>

                                            <h6 style="text-transform:uppercase; font-size:0.75rem; opacity:0.7; margin-bottom:1rem; letter-spacing:0.5px;">Gateways</h6>

                                            <table class="data-table" style="width:100%; font-size:0.9rem;">

                                                <thead>

                                                    <tr>

                                                        <th>Name</th>

                                                        <th>Selector / Servers</th>

                                                    </tr>

                                                </thead>

                                                <tbody>

                                                    ${nsGateways.map(g => `

                                                        <tr>

                                                            <td style="vertical-align:top; width:40%;">

                                                                <div style="font-weight:600; color:var(--accent-color);">${g.name}</div>

                                                            </td>

                                                            <td style="vertical-align:top;">

                                                                <div style="margin-bottom:0.4rem; font-family:monospace; font-size:0.8rem; opacity:0.8;">

                                                                    ${Object.keys(g.selector || {}).map(k => `${k}=${g.selector[k]}`).join(' ')}

                                                                </div>

                                                                <div style="font-size:0.8rem;">

                                                                    ${(g.servers || []).map(s => `<div>${s.port?.number}/${s.port?.protocol} [${(s.hosts || []).join(', ')}]</div>`).join('')}

                                                                </div>

                                                            </td>

                                                        </tr>

                                                    `).join('')}

                                                </tbody>

                                            </table>

                                        </div>

                                    ` : ''}

                                    

                                    ${nsVS.length > 0 ? `

                                        <div>

                                            <h6 style="text-transform:uppercase; font-size:0.75rem; opacity:0.7; margin-bottom:1rem; letter-spacing:0.5px;">Virtual Services</h6>

                                            <table class="data-table" style="width:100%; font-size:0.9rem;">

                                                <thead>

                                                    <tr>

                                                        <th>Name</th>

                                                        <th>Details</th>

                                                    </tr>

                                                </thead>

                                                <tbody>

                                                     ${nsVS.map(v => `

                                                        <tr>

                                                            <td style="vertical-align:top; width:40%;">

                                                                <div style="font-weight:600; color:var(--accent-color);">${v.name}</div>

                                                            </td>

                                                            <td style="vertical-align:top; font-size:0.85rem;">

                                                                <div style="margin-bottom:2px;"><i class="fas fa-globe" style="width:16px; opacity:0.5;"></i> ${(v.hosts || []).join(', ')}</div>

                                                                <div><i class="fas fa-door-open" style="width:16px; opacity:0.5;"></i> ${(v.gateways || []).join(', ')}</div>

                                                            </td>

                                                        </tr>

                                                    `).join('')}

                                                </tbody>

                                            </table>

                                        </div>

                                    ` : ''}

                                </div>

                            ` : `<div style="text-align:center; opacity:0.5; font-style:italic;">No Traffic Configuration (Gateways/VirtualServices) found in this namespace.</div>`}

                        </div>

                    </div>

                    `;

    }).join('')}

            </div>

        </div>

    `;



    document.querySelector('.main-content').innerHTML = html;



    // Simple Accordion Handler CSS

    // We need styles for .accordion-icon

    const styleId = 'sm-accordion-style';

    if (!document.getElementById(styleId)) {

        const style = document.createElement('style');

        style.id = styleId;

        style.innerHTML = `

            .accordion-item.active .accordion-body { display: block !important; }

            .accordion-item.active .accordion-icon { transform: rotate(90deg) !important; }

        `;

        document.head.appendChild(style);

    }

}



async function loadArgoCD(clusterId) {

    const mainContent = document.querySelector('.main-content');

    mainContent.innerHTML = '<div class="loading-spinner"></div>';



    try {

        const response = await fetch(`/api/dashboard/${clusterId}/details`);

        if (!response.ok) throw new Error("Failed to load cluster details");

        const data = await response.json();



        if (!data.argocd || !data.argocd.is_active) {

            mainContent.innerHTML = `

                <div class="fade-in">

                    <div class="card" style="text-align:center; padding:3rem;">

                        <i class="fas fa-sync" style="font-size:3rem; color:var(--text-secondary); margin-bottom:1rem;"></i>

                        <h3>ArgoCD Not Detected</h3>

                        <p style="color:var(--text-secondary);">ArgoCD does not appear to be installed or active on this cluster.</p>

                        <button class="btn btn-primary" onclick="loadDashboard()" style="margin-top:1rem;">Back to Dashboard</button>

                    </div>

                </div>`;

            return;

        }



        renderArgoCDPage(data, clusterId);

    } catch (e) {

        console.error(e);

        mainContent.innerHTML = `<div class="error-state"><i class="fas fa-exclamation-triangle"></i><p>Error loading ArgoCD: ${e.message}</p></div>`;

    }

}



function renderArgoCDPage(clusterData, clusterId) {
    const cd = clusterData.argocd;
    // Fix: Prioritize cluster_name which comes from get_detailed_stats
    const clusterName = clusterData.cluster_name || clusterData.name || (clusterData.cluster ? clusterData.cluster.name : 'Cluster');

    // Stats calculation
    const totalApps = cd.applications ? cd.applications.length : 0;
    const healthyApps = cd.applications ? cd.applications.filter(a => a.health_status === 'Healthy').length : 0;
    const syncedApps = cd.applications ? cd.applications.filter(a => a.sync_status === 'Synced').length : 0;
    const appSets = cd.application_sets ? cd.application_sets.length : 0;

    const instances = cd.instances || [];

    // Group Applications by Project
    const appsByProject = {};
    if (cd.applications) {
        cd.applications.forEach(app => {
            const proj = app.project || 'default';
            if (!appsByProject[proj]) appsByProject[proj] = [];
            appsByProject[proj].push(app);
        });
    }
    const projects = Object.keys(appsByProject).sort();

    const html = `
    <div class="fade-in">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:2rem;">
            <div>
                <h1 style="margin-bottom:0.5rem;">
                    <i class="fas fa-sync" style="color:#f97316; margin-right:0.8rem;"></i>ArgoCD Overview
                </h1>
                <div style="font-size:1.1rem; opacity:0.8; font-family:'Inter', sans-serif;">
                    Cluster: <span style="font-weight:600; color:var(--text-primary);">${clusterName}</span>
                </div>
            </div>
            <div>
                <button class="btn btn-secondary" onclick="loadDashboard()"><i class="fas fa-arrow-left"></i> Dashboard</button>
            </div>
        </div>

        <!-- Top Cards -->
        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap:1.5rem; margin-bottom:2rem;">
            
            <!-- Instances Card -->
            <div class="card">
                <h3 style="margin-bottom:1rem; opacity:0.8; font-size:0.9rem; text-transform:uppercase; letter-spacing:1px;">Controller Instances</h3>
                <div style="display:flex; flex-direction:column; gap:0.8rem;">
                    ${instances.length > 0 ? instances.map(i => `
                        <div style="display:flex; justify-content:space-between; align-items:center; padding-bottom:0.5rem; border-bottom:1px solid var(--border-color);">
                            <div>
                                <div style="font-weight:600;">${i.namespace}</div>
                                <div style="font-size:0.8rem; opacity:0.6;">${i.version}</div>
                            </div>
                            <span class="badge ${i.status === 'Running' || i.status === 'Active' || i.status === 'Available' ? 'badge-green' : 'badge-red'}">${i.status}</span>
                        </div>
                    `).join('') : '<div style="opacity:0.6; font-style:italic;">No instances detected</div>'}
                </div>
            </div>

            <!-- Stats Card -->
            <div class="card">
                <h3 style="margin-bottom:1rem; opacity:0.8; font-size:0.9rem; text-transform:uppercase; letter-spacing:1px;">Application Health</h3>
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:1rem;">
                    <div style="text-align:center; padding:1rem; background:rgba(255,255,255,0.03); border-radius:8px;">
                        <div style="font-size:2rem; font-weight:700; color:var(--accent-color);">${totalApps}</div>
                        <div style="font-size:0.8rem; opacity:0.7;">Total Apps</div>
                    </div>
                    <div style="text-align:center; padding:1rem; background:rgba(255,255,255,0.03); border-radius:8px;">
                        <div style="font-size:2rem; font-weight:700; color:#10b981;">${healthyApps}</div>
                        <div style="font-size:0.8rem; opacity:0.7;">Healthy</div>
                    </div>
                    <div style="text-align:center; padding:1rem; background:rgba(255,255,255,0.03); border-radius:8px;">
                        <div style="font-size:2rem; font-weight:700; color:#3b82f6;">${syncedApps}</div>
                        <div style="font-size:0.8rem; opacity:0.7;">Synced</div>
                    </div>
                    <div style="text-align:center; padding:1rem; background:rgba(255,255,255,0.03); border-radius:8px;">
                        <div style="font-size:2rem; font-weight:700; color:#f59e0b;">${appSets}</div>
                        <div style="font-size:0.8rem; opacity:0.7;">AppSets</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Applications Section -->
        <h3 style="margin-bottom:1rem; font-size:1.2rem;"><i class="fas fa-cubes"></i> Applications</h3>
        
        <!-- Search Bar -->
        <div style="margin-bottom:1.5rem;">
            <input type="text" id="argocd-search" placeholder="Search applications..." 
                onkeyup="filterArgoCDApps()" class="form-input" 
                style="width:100%; max-width:400px; padding:0.6rem; border-radius:6px; background:var(--bg-secondary); border:1px solid var(--border-color); color:var(--text-primary);">
        </div>

        <div id="argocd-projects-container">
            ${projects.map(proj => `
                <div class="card project-group" data-project="${proj}" style="margin-bottom:1.5rem;">
                    <h3 style="margin-bottom:1rem; opacity:0.9; font-size:1rem; border-bottom:1px solid var(--border-color); padding-bottom:0.5rem; display:flex; justify-content:space-between;">
                        <span><i class="fas fa-folder" style="color:#fbbf24; margin-right:0.5rem;"></i> Project: ${proj}</span>
                        <span style="font-size:0.8rem; opacity:0.6; font-weight:400;">${appsByProject[proj].length} Apps</span>
                    </h3>
                    <div style="overflow-x:auto;">
                        <table class="data-table" style="width:100%;">
                            <thead>
                                <tr>
                                    <th>Name</th>
                                    <th>Sync Status</th>
                                    <th>Health</th>
                                    <th>Repo / Path</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${appsByProject[proj].map(app => `
                                    <tr class="clickable-row argocd-app-row" 
                                        data-name="${app.name.toLowerCase()}" 
                                        data-namespace="${app.namespace.toLowerCase()}" 
                                        style="cursor:pointer; transition:background 0.2s;">
                                        <td style="font-weight:600; color:var(--text-primary);" onclick="loadArgoCDAppDetails(${clusterId}, '${app.namespace}', '${app.name}')">
                                            ${app.name}
                                            <div style="font-size:0.75rem; opacity:0.6; font-weight:400;">${app.namespace}</div>
                                        </td>
                                        <td onclick="loadArgoCDAppDetails(${clusterId}, '${app.namespace}', '${app.name}')">
                                            ${app.sync_status === 'Synced'
            ? '<span style="color:#10b981;"><i class="fas fa-check-circle"></i> Synced</span>'
            : (app.sync_status === 'OutOfSync' ? '<span style="color:#f59e0b;"><i class="fas fa-sync-alt"></i> OutOfSync</span>' : `<span style="opacity:0.7;">${app.sync_status}</span>`)}
                                        </td>
                                        <td onclick="loadArgoCDAppDetails(${clusterId}, '${app.namespace}', '${app.name}')">
                                                ${app.health_status === 'Healthy'
            ? '<span style="color:#10b981;"><i class="fas fa-heart"></i> Healthy</span>'
            : (app.health_status === 'Degraded' ? '<span style="color:#ef4444;"><i class="fas fa-heart-broken"></i> Degraded</span>' : `<span style="opacity:0.7;">${app.health_status}</span>`)}
                                        </td>
                                        <td style="font-family:monospace; font-size:0.85rem; opacity:0.8;">
                                            <div style="max-width:300px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${app.repo_url}">
                                                <a href="${app.repo_url}" target="_blank" style="color:var(--accent-color); text-decoration:none; border-bottom:1px dotted var(--accent-color);" onclick="event.stopPropagation();">${app.repo_url}</a>
                                            </div>
                                            <div style="color:var(--text-secondary); opacity:0.8;" onclick="loadArgoCDAppDetails(${clusterId}, '${app.namespace}', '${app.name}')">${app.path}</div>
                                        </td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            `).join('')}
        </div>
        
        ${!cd.applications || cd.applications.length === 0 ? '<div style="padding:2rem; text-align:center; opacity:0.6;">No applications found</div>' : ''}


        <!-- Application Sets -->
        ${cd.application_sets && cd.application_sets.length > 0 ? `
        <div class="card" style="margin-top:2rem;">
            <h3 style="margin-bottom:1.5rem;"><i class="fas fa-layer-group"></i> ApplicationSets</h3>
             <table class="data-table" style="width:100%;">
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Namespace</th>
                            <th>Generators</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${cd.application_sets.map(aset => `
                            <tr>
                                <td style="font-weight:600;">${aset.name}</td>
                                <td>${aset.namespace}</td>
                                <td>
                                    ${aset.generators.map(g => `<span class="badge" style="background:rgba(255,255,255,0.1); margin-right:4px;">${g}</span>`).join('')}
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
            </table>
        </div>
        ` : ''}

    </div>
    
    <!-- Modal Container for App Details -->
    <div id="argocd-app-modal" class="modal">
        <div class="modal-content" style="max-width:900px;">
            <div class="modal-header">
                <h3>Application Details</h3>
                <button class="close-btn" onclick="document.getElementById('argocd-app-modal').classList.remove('open')">&times;</button>
            </div>
            <div id="argocd-app-modal-body">
                <div style="text-align:center; padding:2rem;"><i class="fas fa-circle-notch fa-spin fa-2x"></i></div>
            </div>
        </div>
    </div>
    `;

    document.querySelector('.main-content').innerHTML = html;
}

function filterArgoCDApps() {
    const input = document.getElementById('argocd-search');
    const filter = input.value.toLowerCase();
    const rows = document.getElementsByClassName('argocd-app-row');
    const groups = document.getElementsByClassName('project-group');

    // Filter rows
    for (let i = 0; i < rows.length; i++) {
        const row = rows[i];
        const name = row.getAttribute('data-name');
        const ns = row.getAttribute('data-namespace');
        if (name.includes(filter) || ns.includes(filter)) {
            row.style.display = "";
        } else {
            row.style.display = "none";
        }
    }

    // Hide empty groups
    for (let i = 0; i < groups.length; i++) {
        const group = groups[i];
        const visibleRows = group.querySelectorAll('.argocd-app-row:not([style*="display: none"])');
        if (visibleRows.length > 0) {
            group.style.display = "";
        } else {
            group.style.display = "none";
        }
    }
}

async function loadArgoCDAppDetails(clusterId, namespace, name) {
    const modal = document.getElementById('argocd-app-modal');
    const body = document.getElementById('argocd-app-modal-body');
    modal.classList.add('open');
    body.innerHTML = '<div style="text-align:center; padding:4rem;"><i class="fas fa-circle-notch fa-spin fa-3x" style="color:var(--accent-color);"></i><p style="margin-top:1rem; opacity:0.7;">Fetching live details...</p></div>';

    try {
        const response = await fetch(`/api/dashboard/${clusterId}/argocd/application/${namespace}/${name}`);
        if (!response.ok) throw new Error("Failed to fetch app details");
        const details = await response.json();

        if (details.error) throw new Error(details.error);

        // Render Details
        let html = `
            <div style="display:grid; grid-template-columns: 1.5fr 1fr; gap:2rem;">
                <div>
                    <h2 style="color:var(--accent-color); margin-bottom:0.5rem;">${details.name}</h2>
                    <div style="margin-bottom:1.5rem; opacity:0.8;">Namespace: ${details.namespace} | Project: ${details.project}</div>
                    
                    <div style="background:rgba(255,255,255,0.05); border-radius:8px; padding:1rem; margin-bottom:1.5rem;">
                        <h4 style="margin-bottom:0.8rem; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:0.4rem;">Sync Status</h4>
                        <div style="display:grid; grid-template-columns: auto 1fr; gap:0.5rem 1rem; align-items:center;">
                            <span style="opacity:0.6;">Status:</span> 
                            <span class="${details.sync.status === 'Synced' ? 'text-green' : 'text-orange'}" style="font-weight:bold;">
                                ${details.sync.status === 'Synced' ? '<i class="fas fa-check-circle"></i>' : ''} ${details.sync.status}
                            </span>
                            
                            <span style="opacity:0.6;">Revision:</span> 
                            <code style="font-size:0.85rem;">${details.sync.revision.substring(0, 8)}</code>
                        </div>
                    </div>

                    <div style="background:rgba(255,255,255,0.05); border-radius:8px; padding:1rem; margin-bottom:1.5rem;">
                         <h4 style="margin-bottom:0.8rem; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:0.4rem;">Health</h4>
                         <div style="display:flex; align-items:center; gap:0.5rem;">
                            <span class="${details.health.status === 'Healthy' ? 'text-green' : 'text-red'}" style="font-weight:bold; font-size:1.1rem;">
                                ${details.health.status}
                            </span>
                            ${details.health.message ? `<span style="opacity:0.7; font-size:0.9rem;">- ${details.health.message}</span>` : ''}
                         </div>
                    </div>
                </div>

                <div>
                    <h4 style="margin-bottom:1rem;">Sync History</h4>
                    <div style="display:flex; flex-direction:column; gap:0.8rem;">
                        ${details.history.length > 0 ? details.history.slice().reverse().map(h => `
                            <div style="background:rgba(0,0,0,0.2); padding:0.8rem; border-radius:6px; font-size:0.9rem; border-left: 3px solid var(--border-color);">
                                <div style="display:flex; justify-content:space-between; margin-bottom:0.3rem;">
                                    <span style="opacity:0.6;">${h.deployedAt ? new Date(h.deployedAt).toLocaleString() : 'Unknown'}</span>
                                    <span style="font-weight:600; font-family:monospace;">${h.revision ? h.revision.substring(0, 7) : ''}</span>
                                </div>
                                <div>${h.source ? h.source.repoURL : ''}</div>
                            </div>
                        `).join('') : '<div style="opacity:0.5;">No history available</div>'}
                    </div>
                </div>
            </div>

            ${details.summary.images && details.summary.images.length > 0 ? `
            <div style="margin-top:1.5rem;">
                <h4 style="margin-bottom:1rem;">Container Images</h4>
                <div style="display:flex; flex-wrap:wrap; gap:0.5rem;">
                    ${details.summary.images.map(img => `
                        <span style="background:rgba(255,255,255,0.1); padding:0.3rem 0.6rem; border-radius:4px; font-size:0.85rem; font-family:monospace;">${img}</span>
                    `).join('')}
                </div>
            </div>
            ` : ''}
        `;

        body.innerHTML = html;

    } catch (e) {
        body.innerHTML = `<div class="error-state"><i class="fas fa-exclamation-triangle"></i><p>${e.message}</p></div>`;
    }
}

