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
// Global State
window.currentSnapshotTime = localStorage.getItem('currentSnapshotTime') || "";

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
            const formatted = new Date(ts).toLocaleString('en-US', {
                timeZone: 'America/New_York',
                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'
            });
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

                const date = new Date(utcTs);
                opt.innerText = date.toLocaleString('en-US', {
                    timeZone: 'America/New_York',
                    year: 'numeric', month: 'numeric', day: 'numeric',
                    hour: '2-digit', minute: '2-digit', second: '2-digit',
                    timeZoneName: 'short'
                });
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
            const formatted = new Date(ts).toLocaleString('en-US', {
                timeZone: 'America/New_York',
                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'
            });
            indicator.innerHTML = `<i class="fas fa-exclamation-triangle"></i> Viewing Snapshot: ${formatted} (EST)`;
        }
        document.body.classList.add('historical-mode'); // Optional: for global styling
    } else {
        if (indicator) indicator.style.display = 'none';
        document.body.classList.remove('historical-mode');
    }

    // Refresh current view
    // Detect what we are viewing
    const dashboardSummary = document.getElementById('dashboard-summary');
    if (dashboardSummary && dashboardSummary.style.display !== 'none') {
        loadSummary();
        return;
    }

    // If resource table is visible
    const tableTitle = document.getElementById('resource-table-title');
    if (tableTitle && tableTitle.innerText.includes('Inventory')) {
        // Should be covered by loadSummary if it's main inventory, 
        // but if it's a specific resource view:
        // We need to store current clusterId and resourceType in global or URL?
        // URL search params are source of truth for view
        const params = new URLSearchParams(window.location.search);
        const cId = params.get('cluster_id');
        const rType = params.get('resource_type');
        if (cId && rType) {
            loadResource(cId, rType);
        }
    } else {
        // Fallback catch-all for other views
        const params = new URLSearchParams(window.location.search);
        const cId = params.get('cluster_id');
        const rType = params.get('resource_type');
        if (cId && rType) {
            loadResource(cId, rType);
        } else {
            loadSummary();
        }
    }
}

async function loadSummary() {
    const summaryDiv = document.getElementById('dashboard-summary');
    try {
        let url = '/api/dashboard/summary';
        let fastMode = false;

        if (window.currentSnapshotTime) {
            url += `?snapshot_time=${window.currentSnapshotTime}`;
        } else {
            // Live mode: use fast snapshot first
            url += `?mode=fast`;
            fastMode = true;
        }

        const res = await fetch(url);
        if (!res.ok) throw new Error("Failed to load summary");
        const data = await res.json();
        const clusters = data.clusters || [];
        const global = data.global_stats || {};
        window._allClusters = clusters;

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

        // Trigger Live Updates in Background
        if (fastMode) {
            window._allClusters.forEach(c => {
                refreshClusterLive(c.id);
            });
        }

        // Initial re-calc
        updateGlobalSummary();

    } catch (e) {
        summaryDiv.innerHTML = `<div class="card" style="color:var(--danger-color);">Error loading summary: ${e.message}</div>`;
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
    if (elVcpu) elVcpu.innerHTML = `${stats.total_vcpu.toFixed(0)} <span style="font-size:0.75rem; opacity:0.6;">(${stats.total_licensed_vcpu.toFixed(0)})</span>`;

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

        return `
        <tr id="cluster-row-${c.id}">
            <td style="font-weight:600; color:var(--accent-color);">
                <i class="fas fa-circle" style="color:${statusColor}; font-size:0.6rem; margin-right:0.5rem;" title="${statusTitle}"></i>
                ${c.name}
            </td>
            <td style="font-family:monospace; font-size:0.85rem; opacity:0.9;">${c.stats.node_count}</td>
            <td>
                <span class="badge" style="background:rgba(255,255,255,0.05); color:var(--text-secondary); opacity:0.8;">
                    ${c.licensed_node_count}
                </span>
            </td>
            <td>
                <span class="badge badge-purple" 
                        style="cursor:pointer;" 
                        onclick="showLicenseDetails(${c.id}, ${c.license_info.usage_id})"
                        title="View License Breakdown">
                    ${c.license_info.count}
                </span>
            </td>
            <td style="font-family:monospace; font-size:0.85rem; opacity:0.9;">${c.stats.vcpu_count}</td>
            <td style="font-family:monospace; font-size:0.85rem; opacity:0.9;">${c.licensed_vcpu_count}</td>
            <td>
                ${c.stats.console_url && c.stats.console_url !== '#'
                ? `<a href="${c.stats.console_url}" target="_blank" class="btn btn-primary" style="padding:0.2rem 0.4rem; font-size:0.7rem; border-radius:4px; display:inline-block;" title="Open Console">
                            <i class="fas fa-external-link-alt"></i>
                        </a>`
                : '<span style="opacity:0.5;">-</span>'
            }
            </td>
            <td><span class="badge badge-blue" style="font-size:0.7rem;">${c.datacenter || '-'}</span></td>
            <td><span class="badge badge-green" style="font-size:0.7rem;">${c.environment || '-'}</span></td>
            <td style="font-family:monospace; font-size:0.85rem; opacity:0.9;">${c.stats.version || '-'}</td>
            <td>
                <button class="btn btn-secondary" style="padding:0.2rem 0.4rem; font-size:0.7rem; opacity:0.8;" onclick="refreshClusterLive(${c.id})" title="Refresh Now">
                    <i class="fas fa-sync-alt"></i>
                </button>
                <button class="btn btn-secondary" style="padding:0.2rem 0.4rem; font-size:0.7rem; opacity:0.8;" onclick="showClusterDetails(${c.id}, '${c.name}')">
                    <i class="fas fa-info-circle"></i>
                </button>
            </td>
        </tr>
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

    try {
        const res = await fetch(`/api/dashboard/${clusterId}/live_stats`);
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
            }
        } else {
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
                header: 'Actions', path: item => `
                <button class="btn btn-secondary btn-sm" onclick="showNodeDetails(${window.currentClusterId}, '${item.metadata.name}')">
                    <i class="fas fa-microchip"></i> Details
                </button>
            `
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
                header: 'Actions', path: item => `
                <button class="btn btn-secondary btn-sm" onclick="showMachineDetails(${window.currentClusterId}, '${item.metadata.name}')">
                    <i class="fas fa-info-circle"></i> Details
                </button>
            `
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
        const date = new Date(d.timestamp);
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
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
                                        <td style="font-size: 0.75rem; white-space: nowrap;">${p.startTime ? new Date(p.startTime).toLocaleString() : '-'}</td>
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
                                        <td style="white-space:nowrap;">${e.lastTimestamp ? new Date(e.lastTimestamp).toLocaleString() : '-'}</td>
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
                                        <td style="white-space:nowrap;">${e.lastTimestamp ? new Date(e.lastTimestamp).toLocaleString() : '-'}</td>
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

        if (data && data.length > 0) {
            renderTrendsChart(data);
            const last = data[data.length - 1];
            document.getElementById('trend-stat-clusters').innerText = last.clusters;
            document.getElementById('trend-stat-nodes').innerText = `${last.nodes} (${last.licensed_nodes} Lic)`;
            document.getElementById('trend-stat-vcpu').innerText = last.vcpus;
            document.getElementById('trend-stat-licenses').innerText = last.licenses;
        } else {
            console.warn("No trend data found");
        }
    } catch (e) {
        console.error("Failed to load trends:", e);
    }
}

function renderTrendsChart(data) {
    const labels = data.map(d => {
        const date = new Date(d.timestamp);
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    });

    const ctx = document.getElementById('trends-chart').getContext('2d');

    if (trendsChart) trendsChart.destroy();

    trendsChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Clusters',
                    data: data.map(d => d.clusters),
                    borderColor: '#f97316',
                    backgroundColor: 'rgba(249, 115, 22, 0.1)',
                    fill: false,
                    tension: 0.3,
                    yAxisID: 'y'
                },
                {
                    label: 'Nodes',
                    data: data.map(d => d.nodes),
                    borderColor: '#38bdf8',
                    backgroundColor: 'rgba(56, 189, 248, 0.1)',
                    fill: false,
                    tension: 0.3,
                    yAxisID: 'y'
                },
                {
                    label: 'vCPUs',
                    data: data.map(d => d.vcpus),
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.1)',
                    fill: false,
                    tension: 0.3,
                    yAxisID: 'y'
                },
                {
                    label: 'Licenses',
                    data: data.map(d => d.licenses),
                    borderColor: '#a855f7',
                    backgroundColor: 'rgba(168, 85, 247, 0.1)',
                    fill: false,
                    tension: 0.3,
                    yAxisID: 'y'
                }
            ]
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
                    labels: { color: '#94a3b8', font: { size: 11 } }
                },
                tooltip: {
                    backgroundColor: 'rgba(15, 23, 42, 0.9)',
                    titleColor: '#f8fafc',
                    bodyColor: '#cbd5e1',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1
                }
            },
            scales: {
                x: {
                    ticks: { color: '#64748b', font: { size: 9 }, maxRotation: 45, minRotation: 45 },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                },
                y: {
                    beginAtZero: true,
                    ticks: { color: '#64748b' },
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    title: { display: true, text: 'Count', color: '#64748b' }
                }
            }
        }
    });
}
