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

// Global initialization
document.addEventListener('DOMContentLoaded', () => {
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

async function loadSummary() {
    const summaryDiv = document.getElementById('dashboard-summary');
    try {
        const res = await fetch('/api/dashboard/summary');
        if (!res.ok) throw new Error("Failed to load summary");
        const data = await res.json();
        const clusters = data.clusters || [];
        const global = data.global_stats || {};

        if (clusters.length === 0) {
            summaryDiv.innerHTML = '<div class="card" style="grid-column: 1/-1;">No clusters configured.</div>';
            return;
        }

        summaryDiv.style.display = 'block';
        summaryDiv.innerHTML = `
        <!-- Global Summary Cards -->
        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:1.5rem; margin-bottom:2rem;">
            <div class="card fade-in" style="margin:0; text-align:center; padding:1.5rem; border-bottom:4px solid var(--accent-color);">
                <div style="font-size:0.85rem; color:var(--text-secondary); margin-bottom:0.5rem; text-transform:uppercase; letter-spacing:1px;">Total Clusters</div>
                <div style="font-size:2rem; font-weight:700;">${clusters.length}</div>
            </div>
            <div class="card fade-in" style="margin:0; text-align:center; padding:1.5rem; border-bottom:4px solid var(--success-color);">
                <div style="font-size:0.85rem; color:var(--text-secondary); margin-bottom:0.5rem; text-transform:uppercase; letter-spacing:1px;">Total Nodes</div>
                <div style="font-size:2rem; font-weight:700;">${global.total_nodes} <span style="font-size:0.9rem; opacity:0.6;">(${global.total_licensed_nodes} Lic)</span></div>
            </div>
            <div class="card fade-in" style="margin:0; text-align:center; padding:1.5rem; border-bottom:4px solid #a855f7;">
                <div style="font-size:0.85rem; color:var(--text-secondary); margin-bottom:0.5rem; text-transform:uppercase; letter-spacing:1px;">Total vCPUs</div>
                <div style="font-size:2rem; font-weight:700;">${global.total_vcpu.toFixed(0)} <span style="font-size:0.9rem; opacity:0.6;">(${global.total_licensed_vcpu.toFixed(0)} Lic)</span></div>
            </div>
            <div class="card fade-in" style="margin:0; text-align:center; padding:1.5rem; border-bottom:4px solid var(--accent-color); background: linear-gradient(135deg, var(--card-bg) 0%, rgba(56, 189, 248, 0.05) 100%);">
                <div style="font-size:0.85rem; color:var(--text-secondary); margin-bottom:0.5rem; text-transform:uppercase; letter-spacing:1px;">Total Licenses</div>
                <div style="font-size:2.5rem; font-weight:800; color:var(--accent-color);">${global.total_licenses}</div>
            </div>
        </div>

        <div class="card fade-in">
            <div class="resource-header" style="padding:1rem; border-bottom:1px solid var(--border-color); display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; font-size:1.1rem;">Cluster Inventory</span>
                <div style="display:flex; gap:0.5rem;">
                    <button class="btn btn-secondary" style="padding:0.3rem 0.6rem; font-size:0.8rem;" onclick="exportTable('Cluster_Inventory', 'excel')">
                        <i class="fas fa-file-excel"></i> Excel
                    </button>
                    <button class="btn btn-secondary" style="padding:0.3rem 0.6rem; font-size:0.8rem;" onclick="exportTable('Cluster_Inventory', 'csv')">
                        <i class="fas fa-file-csv"></i> CSV
                    </button>
                </div>
            </div>
            <div class="table-container">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Cluster Name</th>
                            <th>Total Nodes</th>
                            <th>Licensed Nodes</th>
                            <th>Total vCPUs</th>
                            <th>Total Licensed vCPUs</th>
                            <th>Console</th>
                            <th>Datacenter</th>
                            <th>Environment</th>
                            <th>Version</th>
                            <th>Details</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${clusters.map(c => `
                            <tr>
                                <td style="font-weight:600; color:var(--accent-color);">${c.name}</td>
                                <td>${c.stats.node_count}</td>
                                <td>
                                    <span class="badge badge-purple" 
                                          style="cursor:pointer;" 
                                          onclick="showLicenseDetails(${c.id}, ${c.license_info.usage_id})">
                                        ${c.licensed_node_count}
                                    </span>
                                </td>
                                <td>${c.stats.vcpu_count}</td>
                                <td>${c.licensed_vcpu_count}</td>
                                <td>
                                    ${c.stats.console_url && c.stats.console_url !== '#'
                ? `<a href="${c.stats.console_url}" target="_blank" class="btn btn-primary" style="padding:0.25rem 0.6rem; border-radius:4px; display:inline-block;" title="Open Console">
                                             <i class="fas fa-external-link-alt"></i>
                                           </a>`
                : '<span style="opacity:0.5;">-</span>'
            }
                                </td>
                                <td><span class="badge badge-blue">${c.datacenter || '-'}</span></td>
                                <td><span class="badge badge-green">${c.environment || '-'}</span></td>
                                <td style="font-family:monospace; font-size:0.9rem; opacity:0.9;">${c.stats.version || '-'}</td>
                                <td>
                                    <button class="btn btn-secondary" style="padding:0.25rem 0.6rem;" onclick="showClusterDetails(${c.id}, '${c.name}')">
                                        <i class="fas fa-info-circle"></i>
                                    </button>
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        </div>
        `;

    } catch (e) {
        summaryDiv.innerHTML = `<div class="card" style="color:var(--danger-color);">Error loading summary: ${e.message}</div>`;
    }
}

// Initial filter state
const activeFilters = {
    DEV: true,
    UAT: true,
    PROD: true
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
    const search = document.getElementById('cluster-search').value.toLowerCase();

    // We no longer have DC filter dropdown, just Env checkboxes and search
    // But we are grouping by DC in the UI now.

    const items = document.querySelectorAll('.cluster-item');
    items.forEach(item => {
        const name = item.dataset.name;
        // itemDc is not used for primary filtering anymore as they are grouped, 
        // but we could still filter if we wanted.
        const itemEnv = item.dataset.env || 'None';

        let match = true;
        if (search && !name.includes(search)) match = false;

        // Filter by Env Checkboxes
        if (!activeFilters[itemEnv]) match = false;

        item.style.display = match ? 'block' : 'none';
    });
}

async function loadResource(clusterId, resourceType) {
    const contentDiv = document.getElementById('dashboard-content');

    // If not on dashboard page, redirect to dashboard with params
    if (!contentDiv) {
        window.location.href = `/dashboard?cluster_id=${clusterId}&resource_type=${resourceType}`;
        return;
    }

    contentDiv.innerHTML = '<div class="card" style="text-align:center; padding: 2rem;"><i class="fas fa-circle-notch fa-spin"></i> Loading...</div>';

    try {
        const response = await fetch(`/api/dashboard/${clusterId}/resources/${resourceType}`);
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
            { header: 'Intake #', path: 'metadata.labels.intake_number' },
            { header: 'MAPID', path: 'metadata.labels.mapid' },
            { header: 'LOB', path: 'metadata.labels.lob' },
            { header: 'Roles', path: item => Object.keys(getNested(item, 'metadata.labels') || {}).filter(k => k.startsWith('node-role.kubernetes.io/')).map(k => k.split('/')[1]).join(', ') },
            { header: 'Arch', path: 'status.nodeInfo.architecture' },
            {
                header: 'CPU Usage',
                path: item => item.__metrics ? `
                    <div class="progress-bar-container" title="${item.__metrics.cpu_usage} cores">
                        <div class="progress-bar" style="width: ${item.__metrics.cpu_percent}%"></div>
                        <span class="progress-text">${item.__metrics.cpu_percent}%</span>
                    </div>
                ` : '-'
            },
            {
                header: 'Mem Usage',
                path: item => item.__metrics ? `
                    <div class="progress-bar-container" title="${item.__metrics.mem_usage_gb} GB">
                        <div class="progress-bar" style="width: ${item.__metrics.mem_percent}%"></div>
                        <span class="progress-text">${item.__metrics.mem_percent}%</span>
                    </div>
                ` : '-'
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
            { header: 'LOB', path: 'metadata.labels.lob' },
            { header: 'VM Type', path: 'metadata.labels.["machine.openshift.io/instance-type"]' },
            { header: 'CPU (Cores)', path: '__enriched.cpu' },
            {
                header: 'Memory (GB)', path: item => {
                    const gb = getNested(item, '__enriched.memory_gb');
                    return gb !== undefined ? gb + ' GB' : '-';
                }
            },
            { header: 'Created', path: 'metadata.creationTimestamp' }
        ];
    } else if (resourceType === 'machinesets') {
        columns = [
            { header: 'Name', path: 'metadata.name' },
            { header: 'Namespace', path: 'metadata.namespace' },
            { header: 'Intake #', path: 'metadata.labels.intake_number' },
            { header: 'MAPID', path: 'metadata.labels.mapid' },
            { header: 'LOB', path: 'metadata.labels.lob' },
            { header: 'Replicas', path: 'spec.replicas' },
            { header: 'Subnet', path: 'spec.template.spec.providerSpec.value.network.devices[0].networkName' },
            { header: 'VM Size', path: 'spec.template.spec.providerSpec.value.vmSize' },
            { header: 'Available', path: 'status.availableReplicas' },
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
    } else if (resourceType === 'ingresscontrollers') {
        columns = [
            { header: 'Name', path: 'metadata.name' },
            { header: 'Domain', path: 'status.domain' },
            { header: 'Namespace', path: 'metadata.namespace' },
            { header: 'Status', path: item => getNested(item, 'status.conditions')?.find(c => c.type === 'Available')?.status === 'True' ? 'Available' : 'Unavailable' },
            { header: 'Replicas', path: 'spec.replicas' },
            { header: 'Created', path: 'metadata.creationTimestamp' },
            {
                header: 'Actions', path: item => `
                <button class="btn btn-secondary btn-sm" onclick="showIngressDetails(${window.currentClusterId}, '${item.metadata.name}')">
                    <i class="fas fa-info-circle"></i> View Details
                </button>
            `
            }
        ];
    }


    let html = `
        <div class="page-header">
            <h1 class="page-title" style="text-transform: capitalize;">${resourceType}</h1>
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
                            ${columns.map(col => `<th>${col.header}</th>`).join('')}
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

function filterTable() {
    const filter = document.getElementById('resource-filter').value.toLowerCase();
    const table = document.getElementById('resource-table');
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
    return path.split('.').reduce((acc, part) => {
        if (acc === undefined || acc === null) return undefined;

        // Handle bracket notation: ["foo.bar/baz"]
        if (part.startsWith('["') && part.endsWith('"]')) {
            return acc[part.slice(2, -2)];
        }

        // Handle array index notation: foo[0]
        const arrayMatch = part.match(/^(.+)\[(\d+)\]$/);
        if (arrayMatch) {
            const key = arrayMatch[1];
            const index = parseInt(arrayMatch[2]);
            return acc[key] ? acc[key][index] : undefined;
        }

        return acc[part];
    }, obj);
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

    modal.classList.add('open');

    try {
        const res = await fetch(`/api/dashboard/${clusterId}/license-details/${usageId}`);
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

function closeLicenseModal() {
    document.getElementById('license-modal').classList.remove('open');
}

/**
 * Export table data to Excel or CSV using SheetJS
 * @param {string} filename Base filename for the download
 * @param {string} format 'excel' or 'csv'
 */
function exportTable(filename, format) {
    // We try to find the active table in the dashboard content or specific ID
    const contentDiv = document.getElementById('dashboard-content');
    let table = null;

    if (contentDiv) {
        table = contentDiv.querySelector('table');
    }

    if (!table) {
        // Fallback or specific case like audit pages
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
        const res = await fetch(`/api/dashboard/${clusterId}/details`);
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
        const response = await fetch(`/api/dashboard/${clusterId}/ingress/${name}/details`);
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
        const response = await fetch(`/api/dashboard/${clusterId}/nodes/${name}/details`);
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
