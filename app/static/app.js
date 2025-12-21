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

        if (data.length === 0) {
            summaryDiv.innerHTML = '<div class="card" style="grid-column: 1/-1;">No clusters configured.</div>';
            return;
        }

        // Render as a table as requested
        summaryDiv.style.display = 'block'; // Remove grid
        summaryDiv.innerHTML = `
        <div class="card fade-in">
            <div class="resource-header" style="padding:1rem; border-bottom:1px solid var(--border-color); font-weight:700; font-size:1.1rem;">
                Cluster Inventory
            </div>
            <div class="table-container">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Cluster Name</th>
                            <th>Total Nodes</th>
                            <th>Total vCPUs</th>
                            <th>Licenses</th>
                            <th>Console</th>
                            <th>Datacenter</th>
                            <th>Environment</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data.map(c => `
                            <tr>
                                <td style="font-weight:600; color:var(--accent-color);">${c.name}</td>
                                <td>${c.stats.node_count}</td>
                                <td>${c.stats.vcpu_count}</td>
                                <td>
                                    <span class="badge badge-purple" 
                                          style="cursor:pointer;" 
                                          onclick="showLicenseDetails(${c.id}, ${c.license_info.usage_id})">
                                        ${c.license_info.count}
                                    </span>
                                </td>
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
            { header: 'Roles', path: item => Object.keys(getNested(item, 'metadata.labels') || {}).filter(k => k.startsWith('node-role.kubernetes.io/')).map(k => k.split('/')[1]).join(', ') },
            { header: 'Arch', path: 'status.nodeInfo.architecture' },
            { header: 'OS Image', path: 'status.nodeInfo.osImage' },
            { header: 'Kubelet', path: 'status.nodeInfo.kubeletVersion' },
            { header: 'Created', path: 'metadata.creationTimestamp' }
        ];
    } else if (resourceType === 'machines') {
        columns = [
            { header: 'Name', path: 'metadata.name' },
            { header: 'Namespace', path: 'metadata.namespace' },
            { header: 'Phase', path: 'status.phase' },
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
            { header: 'Replicas', path: 'spec.replicas' },
            { header: 'Available', path: 'status.availableReplicas' },
            { header: 'Created', path: 'metadata.creationTimestamp' }
        ];
    } else if (resourceType === 'projects') {
        columns = [
            { header: 'Name', path: 'metadata.name' },
            { header: 'Status', path: 'status.phase' },
            { header: 'Requester', path: 'metadata.annotations.["openshift.io/requester"]' },
            { header: 'Created', path: 'metadata.creationTimestamp' }
        ];
    }


    let html = `
        <div class="page-header">
            <h1 class="page-title" style="text-transform: capitalize;">${resourceType}</h1>
            <div style="display:flex; align-items:center; gap:1rem;">
                <input type="text" id="resource-filter" placeholder="Filter table..." class="form-input" style="width:250px;" onkeyup="filterTable()">
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
        if (!acc) return undefined;
        if (part.startsWith('["') && part.endsWith('"]')) {
            part = part.slice(2, -2);
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
