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
            const install = op.installations[c.name];

            if (install) {
                td.className = 'cell-installed';
                const isMatch = install.version === consensus;
                const pillClass = isMatch ? 'ver-match' : 'ver-mismatch';

                td.innerHTML = `<span class="ver-pill ${pillClass}">${install.version}</span>`;
                td.onclick = () => openOpModal(op);
            } else {
                td.className = 'cell-missing';
                td.innerHTML = '-';
            }
            tr.appendChild(td);
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

function filterOperators() {
    const term = document.getElementById('operator-search').value.toLowerCase();
    if (!allData) return;

    const filteredOps = allData.operators.filter(op =>
        op.displayName.toLowerCase().includes(term) ||
        op.name.toLowerCase().includes(term) ||
        op.provider.toLowerCase().includes(term)
    );

    renderMatrix({
        clusters: allData.clusters,
        operators: filteredOps
    });
}

// Modal Logic
const modal = document.getElementById('op-modal');

function openOpModal(opData) {
    document.getElementById('op-modal-title').textContent = opData.displayName;
    document.getElementById('op-modal-provider').textContent = opData.provider;
    document.getElementById('op-modal-name').textContent = opData.name;

    const tbody = document.getElementById('op-modal-table-body');
    tbody.innerHTML = '';

    // Convert installations map to list for easier sorting
    const installs = Object.entries(opData.installations).map(([cluster, info]) => ({
        cluster,
        ...info
    }));

    installs.sort((a, b) => a.cluster.localeCompare(b.cluster));

    installs.forEach(i => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td style="font-weight:500;">${i.cluster}</td>
            <td>${i.version}</td>
            <td>
                <span style="font-size:0.75rem; padding:2px 6px; border-radius:4px; 
                      background:${i.status === 'Succeeded' ? 'var(--success-color)' : 'var(--warning-color)'}; 
                      color:#fff; opacity:0.8;">
                    ${i.status}
                </span>
            </td>
            <td>${i.channel}</td>
        `;
        tbody.appendChild(tr);
    });

    modal.style.display = 'flex';
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
