document.addEventListener('DOMContentLoaded', () => {
    loadMatrix();
});

let allData = null; // Store for filtering

async function loadMatrix() {
    try {
        const response = await fetch('/api/operators/matrix');
        const data = await response.json();
        allData = data;
        renderMatrix(data);
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
    data.operators.forEach(op => {
        const tr = document.createElement('tr');

        // Name Cell
        const tdName = document.createElement('td');
        tdName.className = 'op-name-col';
        tdName.innerHTML = `
            <div style="font-weight:600;">${op.displayName}</div>
            <div style="font-size:0.75rem; opacity:0.6;">${op.provider}</div>
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

                td.innerHTML = `
                    <div class="ver-pill ${pillClass}" title="${matchTitle}">${install.version}</div>
                    <div style="font-size: 0.7rem; opacity: 0.6; margin-top: 2px;">${install.channel}</div>
                `;
                td.onclick = () => openOpModal(op, c.name);
            } else {
                td.className = 'cell-missing';
                td.innerHTML = '-';
            }
            tr.appendChild(td);
        });
    });

    tableBody.appendChild(tr);
});
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

function toggleFilter(btn, filter) {
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

    // Filter by Search (if search matches cluster name/metadata)
    // We track if the search term *specifically* targets clusters to decide on operator filtering
    const clusterMatchesTerm = term && filteredClusters.some(c =>
        c.name.toLowerCase().includes(term) ||
        (c.datacenter && c.datacenter.toLowerCase().includes(term)) ||
        (c.environment && c.environment.toLowerCase().includes(term))
    );

    if (term && clusterMatchesTerm) {
        // Refine clusters to only those matching the term
        filteredClusters = filteredClusters.filter(c =>
            c.name.toLowerCase().includes(term) ||
            (c.datacenter && c.datacenter.toLowerCase().includes(term)) ||
            (c.environment && c.environment.toLowerCase().includes(term))
        );
    }

    // 2. Determine Visible Operators
    let filteredOps = allData.operators;

    if (term) {
        const opMatches = allData.operators.filter(op =>
            op.displayName.toLowerCase().includes(term) ||
            op.name.toLowerCase().includes(term) ||
            op.provider.toLowerCase().includes(term)
        );

        // Smart Search Logic:
        // - If search matches Operators, show those Operators (and all fitlered clusters).
        // - If search ONLY matches Clusters (and no ops), show ALL Operators (for those clusters).
        // - If search matches BOTH, show intersection.

        const hasOpMatches = opMatches.length > 0;

        if (hasOpMatches) {
            // Term matched operators, so filter rows
            filteredOps = opMatches;
        } else if (clusterMatchesTerm) {
            // Term matched clusters but NO operators -> User is searching for a cluster column
            // Show all operators (filteredOps remains allData.operators)
        } else {
            // Matches nothing
            filteredOps = [];
        }
    }

    renderMatrix({
        clusters: filteredClusters,
        operators: filteredOps
    });
}

// Replace old filter function
function filterOperators() {
    applyFilters();
}

function closeOpModal() {
    modal.style.display = 'none';
}

// Close on outside click
window.onclick = function (event) {
    if (event.target == modal) {
        modal.style.display = "none";
    }
}
