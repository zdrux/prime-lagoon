(() => {
    const endpoint = '/api/reporting/namespace-cpu';
    const get = id => document.getElementById(id);
    let scope;
    let activeCluster = null;
    let searchTimer;
    let page = 1;
    const reportDay = get('cpu-result-day');
    const download = get('cpu-download-active');
    const excel = get('cpu-download-excel');
    const results = get('cpu-results');
    const runSummary = get('cpu-run-summary');
    const runFailures = get('cpu-run-failures');
    let request;
    let chart;

    const number = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const format = value => value === null ? '—' : number.format(value);
    const fieldLabels = JSON.parse(get('cpu-field-labels').textContent);
    const fieldStorageKey = 'ocp_namespace_cpu_report_fields';
    const fieldLists = Array.from(document.querySelectorAll('[data-report-fields]'));
    const reportForm = get('cpu-run-form');
    const selectedFields = form => Array.from(form.querySelectorAll('input[name="cpu-field"]:checked'),
        input => input.value);

    function saveFieldPreferences(list) {
        const preferences = Array.from(list.children, row => [row.dataset.field, row.querySelector('input').checked]);
        try {
            localStorage.setItem(fieldStorageKey, JSON.stringify(preferences));
        } catch {
            // The controls still work when browser storage is unavailable.
        }
    }

    function applyFieldOrder(list, order) {
        const rows = new Map(Array.from(list.children, row => [row.dataset.field, row]));
        list.append(...order.map(field => rows.get(field)));
        for (const row of list.children) {
            row.querySelector('[data-move="-1"]').disabled = !row.previousElementSibling;
            row.querySelector('[data-move="1"]').disabled = !row.nextElementSibling;
        }
    }

    try {
        const saved = JSON.parse(localStorage.getItem(fieldStorageKey));
        if (reportForm && Array.isArray(saved)) {
            const preferences = new Map();
            for (const entry of saved) {
                if (!Array.isArray(entry)) continue;
                const [savedField, checked] = entry;
                const field = savedField === 'observed_cpu_vcpu_hours' ? 'sum_avg_vcpu_hours' : savedField;
                if (Object.hasOwn(fieldLabels, field) && typeof checked === 'boolean') preferences.set(field, checked);
            }
            const renderedOrder = Array.from(reportForm.querySelectorAll('.cpu-field-row'), row => row.dataset.field);
            const order = [...preferences.keys(), ...renderedOrder.filter(field => !preferences.has(field))];
            for (const list of fieldLists) {
                applyFieldOrder(list, order);
                for (const input of list.querySelectorAll('input[name="cpu-field"]')) {
                    if (preferences.has(input.value)) input.checked = preferences.get(input.value);
                }
            }
        }
    } catch {
        // Use the rendered defaults if stored preferences cannot be read.
    }
    const fieldInputs = Array.from(document.querySelectorAll('input[name="cpu-field"]'));
    for (const input of fieldInputs) {
        input.addEventListener('change', () => {
            fieldInputs.filter(other => other.value === input.value)
                .forEach(other => { other.checked = input.checked; });
            saveFieldPreferences(input.closest('[data-report-fields]'));
        });
    }

    for (const list of fieldLists) {
        let dragged;
        let dropTarget;

        function clearDropTarget() {
            dropTarget?.classList.remove('drop-before', 'drop-after');
            dropTarget = null;
        }

        function moveField(row, before) {
            if (row === before) return;
            list.insertBefore(row, before);
            const order = Array.from(list.children, item => item.dataset.field);
            for (const fieldList of fieldLists) {
                applyFieldOrder(fieldList, order);
            }
            saveFieldPreferences(list);
            list.nextElementSibling.textContent = fieldLabels[row.dataset.field] + ' moved to position ' +
                (order.indexOf(row.dataset.field) + 1) + ' of ' + order.length + '.';
        }

        list.addEventListener('click', event => {
            const button = event.target.closest('[data-move]');
            if (!button) return;
            const row = button.closest('.cpu-field-row');
            const before = button.dataset.move === '-1' ? row.previousElementSibling : row.nextElementSibling.nextElementSibling;
            moveField(row, before);
            (button.disabled ? row.querySelector('input') : button).focus();
        });

        list.addEventListener('dragstart', event => {
            if (!event.target.closest('.cpu-field-handle')) return;
            dragged = event.target.closest('.cpu-field-row');
            event.dataTransfer.setData('text/plain', dragged.dataset.field);
            event.dataTransfer.effectAllowed = 'move';
        });
        list.addEventListener('dragover', event => {
            if (!dragged) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
            clearDropTarget();
            const target = event.target.closest('.cpu-field-row');
            if (!target || target === dragged) return;
            dropTarget = target;
            const bounds = target.getBoundingClientRect();
            target.classList.add(event.clientY < bounds.top + bounds.height / 2 ? 'drop-before' : 'drop-after');
        });
        list.addEventListener('drop', event => {
            if (!dragged) return;
            event.preventDefault();
            if (dropTarget) {
                moveField(dragged, dropTarget.classList.contains('drop-before') ? dropTarget : dropTarget.nextElementSibling);
            }
            clearDropTarget();
            dragged = null;
        });
        list.addEventListener('dragend', () => {
            clearDropTarget();
            dragged = null;
        });
    }
    const environment = get('cpu-environment');
    const activeEnvironments = new Set();
    const sortableFields = new Set(['date_et', 'cluster', 'environment', 'namespace', 'mapid',
        'cpu_limit_quota_vcpu', 'avg_vcpu', 'sum_avg_vcpu_hours', 'hours_with_data']);
    let sortField = '';
    let descending = false;

    function renderEnvironmentFilters(values) {
        const focused = environment.contains(document.activeElement) ? document.activeElement.dataset.filter : null;
        environment.replaceChildren();
        for (const value of values) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'filter-btn';
            button.dataset.filter = value;
            button.textContent = value;
            button.classList.toggle('active', activeEnvironments.has(value));
            button.setAttribute('aria-pressed', String(activeEnvironments.has(value)));
            button.addEventListener('click', () => {
                if (activeEnvironments.has(value)) activeEnvironments.delete(value);
                else activeEnvironments.add(value);
                button.classList.toggle('active', activeEnvironments.has(value));
                button.setAttribute('aria-pressed', String(activeEnvironments.has(value)));
                loadPage(1);
            });
            environment.append(button);
            if (focused === value) button.focus();
        }
    }
    const search = get('cpu-search');
    function reportParams() {
        const params = new URLSearchParams(scope);
        if (activeCluster !== null) params.set('result_cluster_id', activeCluster);
        activeEnvironments.forEach(value => params.append('environment', value));
        if (sortField) {
            params.set('sort', sortField);
            params.set('descending', String(descending));
        }
        if (search.value.trim()) params.set('search', search.value.trim());
        return params;
    }

    async function read(url, options) {
        const response = await fetch(url, options);
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            const failure = new Error(typeof error.detail === 'string' ? error.detail : 'Request failed (HTTP ' + response.status + ').');
            failure.status = response.status;
            throw failure;
        }
        return response.json();
    }

    document.querySelectorAll('[data-close]').forEach(button => {
        button.addEventListener('click', () => get(button.dataset.close).close());
    });

    const run = get('cpu-run');
    if (run) {
        const runForm = get('cpu-run-form');
        const clusterList = get('cpu-run-clusters');
        const filters = get('cpu-run-filters');
        const targets = new Set();
        let available = [];

        function matchingClusters() {
            const groups = ['environment', 'datacenter'].map(group => [group, Array.from(
                filters.querySelectorAll('[data-group="' + group + '"].active'), button => button.dataset.value
            )]);
            return available.filter(cluster => groups.every(([group, values]) =>
                !values.length || values.includes((cluster[group] || '').toUpperCase())));
        }

        function updateSelection() {
            get('cpu-run-selection').textContent = targets.size + ' of ' + available.length + ' clusters selected';
            submit.disabled = running || !targets.size;
        }

        function renderTargets() {
            clusterList.replaceChildren();
            for (const cluster of matchingClusters()) {
                const row = document.createElement('label');
                row.className = 'cpu-field-row';
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.checked = targets.has(cluster.id);
                checkbox.addEventListener('change', () => {
                    if (checkbox.checked) targets.add(cluster.id);
                    else targets.delete(cluster.id);
                    updateSelection();
                });
                const name = document.createElement('strong');
                name.style.flex = '1';
                name.textContent = cluster.name;
                row.append(checkbox, name);
                for (const [value, color] of [[cluster.environment, 'green'], [cluster.datacenter, 'blue']]) {
                    if (!value) continue;
                    const badge = document.createElement('span');
                    badge.className = 'badge badge-' + color;
                    badge.textContent = value;
                    row.append(badge);
                }
                clusterList.append(row);
            }
            if (!clusterList.children.length) clusterList.textContent = 'No matching opted-in clusters.';
            updateSelection();
        }

        filters.addEventListener('click', event => {
            const button = event.target.closest('button');
            if (!button || running) return;
            if (button.dataset.group) {
                button.setAttribute('aria-pressed', String(button.classList.toggle('active')));
            } else {
                const chosen = button.dataset.select === 'all' ? available
                    : button.dataset.select === 'matching' ? matchingClusters() : [];
                targets.clear();
                chosen.forEach(cluster => targets.add(cluster.id));
            }
            renderTargets();
        });
        const runStatus = get('cpu-run-status');
        const submit = get('cpu-run-submit');
        const runStart = get('cpu-run-start');
        const runEnd = get('cpu-run-end');
        const runProgress = get('cpu-run-progress');
        const runDays = get('cpu-run-days');
        const datePresets = get('cpu-date-presets');
        let running = false;

        datePresets.addEventListener('click', event => {
            const preset = event.target.closest('button');
            if (!preset || running) return;
            runStart.value = preset.dataset.start;
            runEnd.value = preset.dataset.end;
        });

        async function readRunStatus(url) {
            for (let attempt = 1; ; attempt++) {
                try {
                    return await read(url, { signal: AbortSignal.timeout(15000) });
                } catch (error) {
                    if (attempt === 3 || (error.status && error.status < 500 && ![408, 429].includes(error.status))) {
                        if (error.name === 'TimeoutError') throw new Error('Timed out checking collection status.');
                        throw error;
                    }
                    await new Promise(resolve => setTimeout(resolve, attempt * 1000));
                }
            }
        }

        get('cpu-open-run').addEventListener('click', async () => {
            run.showModal();
            if (running) return;
            submit.disabled = true;
            runStatus.textContent = 'Loading opted-in clusters…';
            try {
                available = (await read(endpoint + '/clusters')).filter(cluster => cluster.collecting);
                for (const id of targets) {
                    if (!available.some(cluster => cluster.id === id)) targets.delete(id);
                }
                renderTargets();
                runStatus.textContent = available.length
                    ? 'Choose the completed Eastern days to collect.'
                    : 'Enable collection in Administration → Cluster Management.';
            } catch (error) {
                runStatus.textContent = error.message;
            }
        });

        runForm.addEventListener('submit', async event => {
            event.preventDefault();
            if (running || !targets.size) return;
            if (runStart.value > runEnd.value) {
                runStatus.textContent = 'Choose an end date on or after the start date.';
                return;
            }
            const fields = selectedFields(runForm);
            if (!fields.length) {
                runStatus.textContent = 'Choose at least one report field.';
                return;
            }
            running = true;
            scope = null;
            clearTimeout(searchTimer);
            request?.abort();
            results.hidden = true;
            download.style.display = 'none';
            excel.hidden = true;
            runSummary.textContent = 'Collecting report…';
            runFailures.replaceChildren();
            runFailures.hidden = true;
            submit.disabled = true;
            const targetClusters = available.filter(cluster => targets.has(cluster.id));
            filters.inert = clusterList.inert = true;
            runStart.disabled = runEnd.disabled = datePresets.inert = true;
            runDays.removeAttribute('value');
            runProgress.hidden = false;
            runStatus.textContent = 'Starting collection…';
            try {
                const dates = new URLSearchParams({ start: runStart.value, end: runEnd.value });
                const days = (runEnd.valueAsNumber - runStart.valueAsNumber) / 86400000 + 1;
                runDays.max = days * targetClusters.length;
                const issues = [];
                const collectedDays = [];
                for (const [index, cluster] of targetClusters.entries()) {
                    const url = endpoint + '/run-now/' + cluster.id + '?' + dates;
                    runStatus.textContent = cluster.name + ' · Starting collection…';
                    let outcome;
                    try {
                        await read(url, { method: 'POST', signal: AbortSignal.timeout(15000) });
                        // Allow four queries per day, each with up to three attempts and backoff.
                        const dayTimeout = 15 * 60 * 1000;
                        let deadline = performance.now() + dayTimeout;
                        do {
                            await new Promise(resolve => setTimeout(resolve, 1500));
                            const status = await readRunStatus(url);
                            if (status.completed > (outcome?.completed ?? 0)) deadline = performance.now() + dayTimeout;
                            outcome = status;
                            runDays.value = index * days + outcome.completed;
                            runStatus.textContent = cluster.name + ' · ' + runDays.value + ' of ' +
                                runDays.max + ' cluster days processed · ' + (outcome.detail || outcome.status);
                            if (outcome.status === 'Running' && performance.now() >= deadline) {
                                throw new Error('Collection stopped making progress. Last status: ' + outcome.detail);
                            }
                        } while (outcome.status === 'Running');
                        if (outcome.status !== 'Collected') issues.push(cluster.name + ' · ' + (outcome.detail || outcome.status));
                    } catch (error) {
                        const reason = error.name === 'TimeoutError' ? 'Timed out starting collection.' : error.message;
                        issues.push(cluster.name + ' · ' + reason);
                    }
                    for (const day of outcome?.collected_days || []) collectedDays.push(cluster.id + ':' + day);
                    runDays.value = (index + 1) * days;
                }

                run.close();
                runSummary.textContent = issues.length ? 'Collection issues for ' + issues.length + ' cluster(s)' : '';
                for (const issue of issues) {
                    const item = document.createElement('li');
                    item.textContent = issue;
                    runFailures.append(item);
                }
                runFailures.hidden = !issues.length;
                if (!collectedDays.length) {
                    if (!issues.length) runSummary.textContent = 'This run collected no report rows.';
                    return;
                }
                scope = new URLSearchParams(dates);
                collectedDays.forEach(day => scope.append('collected_day', day));
                activeCluster = null;
                targetClusters.forEach(cluster => scope.append('cluster_ids', cluster.id));
                reportDay.value = '';
                fields.forEach(field => scope.append('fields', field));
                activeEnvironments.clear();
                search.value = '';
                get('cpu-result-rows').replaceChildren();
                get('cpu-result-head').replaceChildren();
                get('cpu-visible-scope').hidden = true;
                environment.replaceChildren();
                results.hidden = false;
                await loadPage(1);
            } catch (error) {
                runStatus.textContent = error.message;
                runSummary.textContent = error.message;
            } finally {
                running = false;
                filters.inert = clusterList.inert = false;
                updateSelection();
                runStart.disabled = runEnd.disabled = datePresets.inert = false;
                runProgress.hidden = true;
            }
        });
    }

    function appendCell(row, value) {
        const cell = row.insertCell();
        cell.textContent = value;
        return cell;
    }

    function showHourly(row) {
        get('cpu-hourly-title').textContent = row.namespace + ' · ' + row.cluster + ' · ' + row.date_et;
        const body = get('cpu-hourly-rows');
        body.replaceChildren();
        row.hours_et.forEach((hour, index) => {
            const line = body.insertRow();
            [hour, format(row.hourly_usage[index])]
                .forEach(value => appendCell(line, value));
        });
        get('cpu-hourly-quota').textContent = row.cpu_limit_quota_vcpu === null
            ? row.quota_status : 'CPU limits quota at day end: ' + format(row.cpu_limit_quota_vcpu) + ' vCPU';
        get('cpu-hourly-coverage').textContent = row.hours_with_data ? '' : 'No hourly CPU samples for this day.';
        get('cpu-hourly').showModal();
        if (typeof Chart === 'undefined') {
            body.closest('details').open = true;
            return;
        }
        chart?.destroy();
        const usageColor = '#60a5fa';
        const quotaColor = '#fbbf24';
        const textColor = getComputedStyle(get('cpu-hourly')).color;
        const datasets = [{
            label: 'Hourly average CPU', data: row.hourly_usage, yAxisID: 'cpu',
            borderColor: usageColor, backgroundColor: usageColor, pointRadius: 3, pointHoverRadius: 6,
        }];
        if (row.cpu_limit_quota_vcpu !== null) {
            datasets.push({
                label: 'CPU limits quota', data: row.hours_et.map(() => row.cpu_limit_quota_vcpu),
                yAxisID: 'quota', borderColor: quotaColor, borderDash: [6, 4], pointRadius: 0,
            });
        }
        chart = new Chart(get('cpu-chart'), {
            type: 'line',
            data: { labels: row.hours_et, datasets },
            options: {
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { labels: { color: textColor } },
                    tooltip: { callbacks: {
                        label: item => item.dataset.label + ': ' + format(item.parsed.y) + ' vCPU',
                    } },
                },
                scales: {
                    x: { ticks: { color: textColor } },
                    cpu: {
                        type: 'linear', position: 'left', beginAtZero: true, ticks: { color: usageColor, callback: format },
                        title: { display: true, text: 'Hourly average CPU (vCPU)', color: usageColor },
                    },
                    quota: {
                        type: 'linear', position: 'right', display: row.cpu_limit_quota_vcpu !== null,
                        beginAtZero: true, grid: { drawOnChartArea: false }, ticks: { color: quotaColor, callback: format },
                        title: { display: true, text: 'CPU limits quota (vCPU)', color: quotaColor },
                    },
                },
            },
        });
    }

    async function loadPage(target) {
        if (!scope) return;
        clearTimeout(searchTimer);
        request?.abort();
        request = new AbortController();
        const params = reportParams();
        const exportUrl = endpoint + '/export?' + params;
        const day = reportDay.value;
        if (day) params.set('report_day', day);
        params.set('page', target);
        const status = get('cpu-status');
        status.textContent = 'Loading report…';
        download.style.display = 'none';
        excel.hidden = true;
        get('cpu-prev').disabled = get('cpu-next').disabled = true;
        get('cpu-issues').hidden = true;
        try {
            const data = await read(endpoint + '?' + params, { signal: request.signal });
            if (activeCluster !== null && !data.targets.some(target => target.id === activeCluster)) {
                activeCluster = null;
                return loadPage(1);
            }
            page = data.page;
            reportDay.replaceChildren(new Option('All collected days (' + data.days.length + ')', ''));
            for (const entry of data.days) {
                reportDay.add(new Option(entry.day + ' (' + entry.count + ' rows)', entry.day));
            }
            if (day && !data.days.some(entry => entry.day === day)) {
                reportDay.value = '';
                return loadPage(1);
            }
            reportDay.value = day;
            reportDay.disabled = !data.days.length;
            renderEnvironmentFilters(data.environments);
            const body = get('cpu-result-rows');
            body.replaceChildren();
            const fields = params.getAll('fields');
            const header = get('cpu-result-head');
            const focusedSort = header.contains(document.activeElement) ? document.activeElement.dataset.field : null;
            header.replaceChildren();
            for (const field of fields) {
                const cell = document.createElement('th');
                cell.scope = 'col';
                header.append(cell);
                if (!sortableFields.has(field)) {
                    cell.textContent = fieldLabels[field];
                    continue;
                }
                cell.className = 'sortable-header';
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'cpu-sort-button';
                button.dataset.field = field;
                const icon = document.createElement('i');
                icon.className = 'fas sort-icon fa-sort';
                icon.setAttribute('aria-hidden', 'true');
                if (sortField === field) {
                    cell.setAttribute('aria-sort', descending ? 'descending' : 'ascending');
                    icon.className = 'fas sort-icon ' + (descending ? 'fa-sort-down' : 'fa-sort-up');
                }
                button.append(fieldLabels[field], icon);
                button.addEventListener('click', () => {
                    descending = sortField === field ? !descending : false;
                    sortField = field;
                    loadPage(1);
                });
                cell.append(button);
                if (focusedSort === field) button.focus();
            }
            for (const row of data.rows) {
                const line = body.insertRow();
                for (const field of fields) {
                    const value = row[field];
                    if (field === 'namespace' || field.startsWith('hourly_')) {
                        const button = document.createElement('button');
                        button.type = 'button';
                        button.className = 'btn btn-secondary cpu-row-button';
                        button.textContent = field === 'namespace' ? value : 'View hourly values';
                        button.addEventListener('click', () => showHourly(row));
                        line.insertCell().append(button);
                    } else if (field === 'hours_with_data') {
                        appendCell(line, value + ' / ' + row.hours_et.length);
                    } else if (typeof value === 'number' || value === null) {
                        appendCell(line, format(value));
                    } else {
                        appendCell(line, value || (field === 'mapid' ? 'Unmapped' : '—'));
                    }
                }
            }
            const issues = get('cpu-issue-list');
            issues.replaceChildren();
            for (const issue of data.collection_issues) {
                const item = document.createElement('li');
                item.textContent = [issue.day, issue.cluster, issue.status, issue.detail].filter(Boolean).join(' · ');
                issues.append(item);
            }
            get('cpu-issue-count').textContent = data.collection_issues.length + ' cluster-day collection issues';
            get('cpu-issues').hidden = !data.collection_issues.length;
            get('cpu-issues').open = !data.rows.length && data.collection_issues.length > 0;
            download.href = exportUrl;
            download.style.display = data.days.length ? '' : 'none';
            excel.hidden = !data.days.length;
            get('cpu-prev').disabled = page === 1;
            get('cpu-next').disabled = !data.has_more;
            get('cpu-page').textContent = 'Page ' + page + ' of ' + data.page_count;
            const clusterFilters = get('cpu-cluster-filters');
            const focused = clusterFilters.contains(document.activeElement) ? document.activeElement.dataset.cluster : null;
            clusterFilters.replaceChildren();
            const labels = new Map();
            for (const { id, cluster, environment } of data.targets) {
                if (!labels.has(id)) labels.set(id, new Set());
                labels.get(id).add(cluster + (environment ? ' (' + environment + ')' : ''));
            }
            for (const [id, label] of [[null, 'All clusters'], ...Array.from(labels, ([id, names]) => [id, [...names].join(' / ')])]) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'filter-btn';
                button.dataset.cluster = id ?? '';
                button.textContent = label;
                button.classList.toggle('active', id === activeCluster);
                button.setAttribute('aria-pressed', String(id === activeCluster));
                button.addEventListener('click', () => {
                    if (activeCluster === id) return;
                    activeCluster = id;
                    loadPage(1);
                });
                clusterFilters.append(button);
                if (focused === button.dataset.cluster) button.focus();
            }
            get('cpu-visible-scope').hidden = !data.targets.length;
            status.textContent = data.rows.length ? '' : data.collection_issues.length
                ? 'No collected reports match this selection. See the collection details below.'
                : 'No collected reports match this selection.';
        } catch (error) {
            if (error.name !== 'AbortError') status.textContent = error.message;
        }
    }

    get('cpu-prev').addEventListener('click', () => loadPage(page - 1));
    get('cpu-next').addEventListener('click', () => loadPage(page + 1));
    reportDay.addEventListener('change', () => loadPage(1));
    excel.addEventListener('click', async () => {
        const status = get('cpu-status');
        excel.disabled = true;
        try {
            if (!window.XLSX) throw new Error('Excel export is unavailable. Use Download CSV.');
            const url = new URL(download.href);
            const fields = url.searchParams.getAll('fields');
            const includeCluster = fields.includes('cluster');
            if (!includeCluster) url.searchParams.append('fields', 'cluster');
            url.searchParams.set('include_cluster_id', 'true');
            const response = await fetch(url);
            if (!response.ok) throw new Error('Unable to load data for Excel export.');
            const csv = XLSX.read(await response.text(), { type: 'string', raw: true });
            const [headers = [], ...rows] = XLSX.utils.sheet_to_json(csv.Sheets[csv.SheetNames[0]], {
                header: 1, defval: '', blankrows: false,
            });
            const clusterColumn = headers.indexOf('cluster');
            const idColumn = headers.indexOf('cluster_id');
            if (clusterColumn < 0 || idColumn < 0) throw new Error('Unable to identify clusters in the export.');
            const groups = new Map();
            for (const row of rows) {
                const id = row[idColumn];
                if (!groups.has(id)) groups.set(id, { cluster: row[clusterColumn], values: [] });
                groups.get(id).values.push(row);
            }
            if (!groups.size) throw new Error('No report rows are available to export.');
            const workbook = XLSX.utils.book_new();
            const numericColumns = headers.map(header => /^(avg_vcpu(?:_|$)|cpu_quota_vcpu_|cpu_limit_quota_vcpu$|sum_avg_vcpu_hours$|hours_with_data$)/.test(header));
            for (const { cluster, values } of groups.values()) {
                const base = (String(cluster).replace(/[\\/:?*\[\]]/g, ' ').replace(/^'+|'+$/g, '').trim() || 'Cluster').slice(0, 31);
                let name = base;
                let suffix = 2;
                while (workbook.SheetNames.some(existing => existing.toLowerCase() === name.toLowerCase())) {
                    const ending = ' (' + suffix++ + ')';
                    name = base.slice(0, 31 - ending.length) + ending;
                }
                const visible = row => row.filter((_, index) => index !== idColumn && (includeCluster || index !== clusterColumn));
                const sheet = XLSX.utils.aoa_to_sheet([visible(headers), ...values.map(row => visible(
                    row.map((value, index) => numericColumns[index] && value !== '' ? {
                        t: 'n', v: Number(value), z: headers[index] === 'hours_with_data' ? '0' : '0.00',
                    } : value)
                ))]);
                XLSX.utils.book_append_sheet(workbook, sheet, name);
            }
            XLSX.writeFile(workbook, 'namespace-cpu.xlsx');
        } catch (error) {
            status.textContent = error.message;
        } finally {
            excel.disabled = false;
        }
    });
    search.addEventListener('input', () => {
        clearTimeout(searchTimer);
        request?.abort();
        searchTimer = setTimeout(() => loadPage(1), 250);
    });
})();
