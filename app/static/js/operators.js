document.addEventListener('DOMContentLoaded', () => {
    loadMatrix();
});

let allData = null; // Store for filtering

async function loadMatrix() {
    try {
        let url = '/api/operators/matrix';
        if (window.currentSnapshotTime) {
            url += `?snapshot_time=${window.currentSnapshotTime}`;
        }
        const response = await fetch(url);
        const data = await response.json();
        allData = data;
        renderMatrix(data);

        if (data.snapshot_time) {
            document.getElementById('snapshot-time-display').innerText = formatTimestamp(data.snapshot_time);
            document.getElementById('snapshot-timestamp-indicator').style.display = 'block';
        } else {
            document.getElementById('snapshot-timestamp-indicator').style.display = 'none';
        }

        document.getElementById('loading-matrix').style.display = 'none';
        document.getElementById('matrix-container').style.display = 'block';
    } catch (error) {
        console.error('Error loading matrix:', error);
        document.getElementById('loading-matrix').innerHTML = `<p style="color:red">Failed to load data: ${error}</p>`;
    }
}

function renderMatrix(data) {
    const tableHead = document.getElementById('matrix-header-row');
    const tableBody = document.getElementById('matrix-body');

    // Clear
    tableHead.innerHTML = '';
    tableBody.innerHTML = '';

    // 1. Header: Operator Name Col + Cluster Cols
    const nameHeader = document.createElement('th');
    nameHeader.className = 'op-name-col';
    nameHeader.textContent = 'Operator Name';
    nameHeader.style.zIndex = '20'; // Higher than rows
    tableHead.appendChild(nameHeader);

    data.clusters.forEach(c => {
        const th = document.createElement('th');
        th.className = 'cluster-header';

        let label = c.name;
        let warningHtml = '';
        if (c.auth_error) {
            warningHtml = `<i class="fas fa-exclamation-triangle" style="color:var(--warning-color); font-size:0.8rem; margin-right:4px;" title="Permission denied for Operator API"></i>`;
        }

        // HTML trick for rotated text
        th.innerHTML = `<div>${warningHtml}<span title="${c.name}">${label}</span></div>`;
        tableHead.appendChild(th);
    });

    // 2. Rows
    if (data.operators.length === 0) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = data.clusters.length + 1;
        td.style.textAlign = 'center';
        td.style.padding = '4rem 2rem';
        td.style.opacity = '0.6';

        // Check if any cluster has data_collected = true
        const anyCollected = data.clusters.some(c => c.data_collected !== false);

        if (!anyCollected) {
            td.innerHTML = `
                <i class="fas fa-info-circle" style="font-size:2rem; margin-bottom:1rem; display:block;"></i>
                <div style="font-size:1.1rem; font-weight:600;">Data Not Collected</div>
                <div style="font-size:0.9rem;">Operator data was not collected for this snapshot run.</div>
            `;
        } else {
            td.innerHTML = `
                <i class="fas fa-check-circle" style="font-size:2rem; margin-bottom:1rem; display:block; color:var(--success-color);"></i>
                <div style="font-size:1.1rem; font-weight:600;">No Operators Found</div>
                <div style="font-size:0.9rem;">All clusters have zero OLM subscriptions matching the criteria.</div>
            `;
        }
        tr.appendChild(td);
        tableBody.appendChild(tr);
        return;
    }

    data.operators.forEach(op => {
        const tr = document.createElement('tr');

        // Name Cell
        const tdName = document.createElement('td');
        tdName.className = 'op-name-col';
        const fleetRisk = getOperatorFleetRisk(op, data.clusters);
        tdName.innerHTML = `
            <div style="font-weight:600;">${op.displayName}</div>
            <div style="font-size:0.72rem; opacity:0.78; margin-top:0.35rem;">
                Fleet OCP headroom ${renderHeadroomBadge(fleetRisk.headroom, fleetRisk.risk)}
            </div>
        `;
        tr.appendChild(tdName);

        // Determine "Consensus" Version for color coding
        const versions = Object.values(op.installations).map(i => i.version);
        const consensus = getMode(versions);

        // Cluster Cells
        data.clusters.forEach(c => {
            const td = document.createElement('td');

            // Check if cluster collected this data
            if (c.data_collected === false) {
                td.className = 'cell-missing';
                td.style.background = 'rgba(0,0,0,0.2)';
                td.innerHTML = '<span style="color:var(--text-secondary); opacity:0.3; font-size:0.7rem;">N/A</span>';
                td.title = "Data not collected for this snapshot";
                tr.appendChild(td);
                return;
            }

            const install = op.installations[c.name];

            if (install) {
                td.className = 'cell-installed';
                const isMatch = install.version === consensus;
                const pillClass = isMatch ? 'ver-match' : 'ver-mismatch';
                const matchTitle = isMatch ? 'Matches fleet consensus' : `Differs from fleet consensus (most common: ${consensus})`;
                const compat = install.openshift_compatibility || { status: 'unknown', reason: 'No compatibility metadata found.' };
                const compatClass = `compat-${compat.status || 'unknown'}`;
                const compatTitle = `OCP compatibility: ${compat.status || 'unknown'} - ${compat.reason || ''}`;
                const headroomHtml = renderHeadroomBadge(compat.minor_headroom, compat.risk || compat.status);

                td.innerHTML = `
                    <div class="ver-pill ${pillClass}" title="${matchTitle}">${install.version}<span class="compat-dot ${compatClass}" title="${compatTitle}"></span></div>
                    <div style="font-size: 0.7rem; opacity: 0.75; margin-top: 4px;">${install.channel} ${headroomHtml}</div>
                `;
                td.onclick = () => openOpModal(op, c.name);
            } else {
                td.className = 'cell-missing';
                td.innerHTML = '-';
            }
            tr.appendChild(td);
        });
        tableBody.appendChild(tr);
    });
}

function getOperatorFleetRisk(op, clusters) {
    const values = [];
    let hasBlocked = false;
    let hasUrgent = false;
    let hasWarning = false;
    let hasUnknown = false;

    clusters.forEach(c => {
        const install = op.installations[c.name];
        if (!install) return;
        const compat = install.openshift_compatibility || {};
        if (compat.risk === 'blocked') hasBlocked = true;
        if (compat.risk === 'urgent') hasUrgent = true;
        if (compat.risk === 'warning') hasWarning = true;
        if (compat.risk === 'unknown' || compat.minor_headroom === null || compat.minor_headroom === undefined) hasUnknown = true;
        if (Number.isFinite(Number(compat.minor_headroom))) values.push(Number(compat.minor_headroom));
    });

    if (hasBlocked) return { risk: 'blocked', headroom: values.length ? Math.min(...values) : null };
    if (hasUrgent) return { risk: 'urgent', headroom: values.length ? Math.min(...values) : 0 };
    if (hasWarning) return { risk: 'warning', headroom: values.length ? Math.min(...values) : 1 };
    if (values.length) return { risk: 'ok', headroom: Math.min(...values) };
    return { risk: hasUnknown ? 'unknown' : 'unknown', headroom: null };
}

function renderHeadroomBadge(value, risk = 'unknown') {
    const normalizedRisk = ['ok', 'warning', 'urgent', 'blocked'].includes(risk) ? risk : 'unknown';
    const label = (value === null || value === undefined || value === '') ? '?' : `${Number(value) >= 0 ? '+' : ''}${value}`;
    const title = label === '?' ? 'No CSV max OpenShift version declared' : `${label} OpenShift minor releases of declared compatibility headroom`;
    return `<span class="headroom-badge headroom-${normalizedRisk}" title="${title}">${label}</span>`;
}

function getMode(array) {
    if (array.length == 0) return null;
    var modeMap = {};
    var maxEl = array[0], maxCount = 1;
    for (var i = 0; i < array.length; i++) {
        var el = array[i];
        if (modeMap[el] == null)
            modeMap[el] = 1;
        else
            modeMap[el]++;
        if (modeMap[el] > maxCount) {
            maxEl = el;
            maxCount = modeMap[el];
        }
    }
    return maxEl;
}

// Modal Logic
const modal = document.getElementById('op-modal');

// Global filters state
let activeTags = new Set();

function toggleOperatorFilter(btn, filter) {
    btn.classList.toggle('active');
    if (activeTags.has(filter)) {
        activeTags.delete(filter);
    } else {
        activeTags.add(filter);
    }
    applyFilters();
}

function applyFilters() {
    if (!allData) return;
    const term = document.getElementById('operator-search').value.toLowerCase();

    // 1. Determine Visible Clusters
    // Filter by Tags (Buttons)
    let filteredClusters = allData.clusters;
    if (activeTags.size > 0) {
        const envFilters = ['DEV', 'UAT', 'PROD'].filter(f => activeTags.has(f));
        const dcFilters = ['AZURE', 'HCI'].filter(f => activeTags.has(f));

        filteredClusters = filteredClusters.filter(c => {
            const envMatch = envFilters.length === 0 || (c.environment && envFilters.includes(c.environment.toUpperCase()));
            const dcMatch = dcFilters.length === 0 || (c.datacenter && dcFilters.includes(c.datacenter.toUpperCase()));
            return envMatch && dcMatch;
        });
    }

    // 2. Determine Visible Operators
    let filteredOps = allData.operators;

    if (term) {
        const opMatches = allData.operators.filter(op =>
            op.displayName.toLowerCase().includes(term) ||
            op.name.toLowerCase().includes(term)
        );

        const hasOpMatches = opMatches.length > 0;

        if (hasOpMatches) {
            // If term matches Operators, show those Operators across all button-filtered clusters.
            filteredOps = opMatches;
        } else {
            // If term matches NO operators, then try matching Cluster metadata
            const clusterMatches = filteredClusters.filter(c =>
                c.name.toLowerCase().includes(term) ||
                (c.datacenter && c.datacenter.toLowerCase().includes(term)) ||
                (c.environment && c.environment.toLowerCase().includes(term))
            );

            if (clusterMatches.length > 0) {
                // User is likely searching for a specific cluster/environment
                filteredClusters = clusterMatches;
                // Since no ops matched, we show all ops (standard behavior for cluster search)
                filteredOps = allData.operators;
            } else {
                // Matches nothing
                filteredOps = [];
            }
        }
    }

    renderMatrix({
        clusters: filteredClusters,
        operators: filteredOps
    });
}

function formatTimestamp(isoStr) {
    if (!isoStr) return '';
    const date = new Date(isoStr);
    return date.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        hour12: true
    });
}

// Replace old filter function
function filterOperators() {
    applyFilters();
}

function openOpModal(op, clusterName) {
    const install = op.installations[clusterName];
    if (!install) return;

    document.getElementById('op-modal-title').innerText = op.displayName;
    document.getElementById('op-modal-name').innerText = op.name;
    document.getElementById('op-modal-cluster').innerText = clusterName;
    document.getElementById('op-modal-namespace').innerText = install.namespace || '-';
    document.getElementById('op-modal-approval').innerText = install.approval || '-';
    document.getElementById('op-modal-version').innerText = install.version || '-';
    document.getElementById('op-modal-channel').innerText = install.channel || '-';
    document.getElementById('op-modal-source').innerText = install.source || '-';

    const compat = install.openshift_compatibility || {};
    const compatStatus = compat.status || 'unknown';
    const compatStatusEl = document.getElementById('op-modal-compat-status');
    compatStatusEl.innerText = compatStatus.toUpperCase();
    compatStatusEl.style.color = compatStatus === 'blocked' ? 'var(--danger-color)' : (compatStatus === 'compatible' ? 'var(--success-color)' : 'var(--text-secondary)');
    document.getElementById('op-modal-compat-max').innerText = compat.max_openshift_version || '-';
    document.getElementById('op-modal-compat-min').innerText = compat.min_openshift_version || '-';
    document.getElementById('op-modal-compat-headroom').innerText = (compat.minor_headroom === null || compat.minor_headroom === undefined) ? 'Unknown' : `${compat.minor_headroom} minor release(s)`;
    document.getElementById('op-modal-compat-range').innerText = compat.openshift_versions || '-';
    document.getElementById('op-modal-compat-reason').innerText = compat.reason || '-';

    const csvDetails = install.csv_details || {};
    document.getElementById('op-modal-namespace-support').innerText = csvDetails.namespace_support || '-';
    document.getElementById('op-modal-install-strategy').innerText = csvDetails.install_strategy || '-';
    document.getElementById('op-modal-rbac-summary').innerText =
        `${csvDetails.namespaced_rule_count || 0} namespaced rule(s), ${csvDetails.cluster_rule_count || 0} cluster rule(s)`;
    const serviceAccounts = csvDetails.service_accounts || [];
    document.getElementById('op-modal-service-accounts').innerText = serviceAccounts.length ? serviceAccounts.join(', ') : '-';
    document.getElementById('op-modal-owned-crds').innerText = csvDetails.owned_crd_count ?? '-';
    document.getElementById('op-modal-related-images').innerText = csvDetails.related_image_count ?? '-';

    const statusPill = document.getElementById('op-modal-status-pill');
    const statusText = document.getElementById('op-modal-status-text');
    statusText.innerText = install.status || 'Unknown';

    // Simple color logic for status
    if (install.status === 'Succeeded') {
        statusPill.style.background = '#10b981'; // Green
    } else if (install.status === 'Failed') {
        statusPill.style.background = '#ef4444'; // Red
    } else {
        statusPill.style.background = '#eab308'; // Warning
    }

    const crdsContainer = document.getElementById('op-modal-crds');
    if (crdsContainer) {
        crdsContainer.innerHTML = '';
        if (install.managed_crds && install.managed_crds.length > 0) {
            install.managed_crds.forEach(crd => {
                const div = document.createElement('div');
                div.style.padding = '0.5rem';
                div.style.background = 'rgba(255,255,255,0.03)';
                div.style.border = '1px solid var(--border-color)';
                div.style.borderRadius = '4px';
                div.innerHTML = `
                    <div style="font-weight:600; font-size:0.85rem;">${crd.displayName || crd.kind}</div>
                    <div style="font-size:0.75rem; opacity:0.6; font-family:monospace;">${crd.name}</div>
                `;
                crdsContainer.appendChild(div);
            });
        } else {
            crdsContainer.innerHTML = '<div style="opacity:0.5; font-style:italic; font-size:0.9rem;">No managed resources reported.</div>';
        }
    }

    modal.classList.add('open');
}

function closeOpModal() {
    modal.classList.remove('open');
}

// Close on outside click
window.onclick = function (event) {
    if (event.target == modal) {
        modal.classList.remove('open');
    }
}
