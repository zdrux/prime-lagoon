(() => {
    const endpoint = '/api/reporting/namespace-cpu';
    const get = id => document.getElementById(id);
    const picker = get('cpu-picker');
    const start = get('cpu-start');
    const end = get('cpu-end');
    const selection = new Set();
    let clusters = [];
    let initialized = false;
    let scope = new URLSearchParams();
    let searchTimer;
    let page = 1;
    const reportDay = get('cpu-result-day');
    const download = get('cpu-download-active');
    let request;
    let chart;

    const number = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const format = value => value === null ? '—' : number.format(value);
    const fieldLabels = JSON.parse(get('cpu-field-labels').textContent);
    const fieldStorageKey = 'ocp_namespace_cpu_report_fields';
    const fieldLists = Array.from(document.querySelectorAll('.cpu-field-list'));
    const reportForm = get('cpu-report-form');
    const selectedFields = form => Array.from(form.querySelectorAll('input[name="cpu-field"]:checked'),
        input => input.value);
    const defaultFields = selectedFields(reportForm);

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
        if (Array.isArray(saved)) {
            const preferences = new Map(saved.filter(entry => Array.isArray(entry) &&
                Object.hasOwn(fieldLabels, entry[0]) && typeof entry[1] === 'boolean'));
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
    const restoredFields = selectedFields(reportForm);
    const initialFields = restoredFields.length ? restoredFields : defaultFields;

    const fieldInputs = Array.from(document.querySelectorAll('input[name="cpu-field"]'));
    for (const input of fieldInputs) {
        input.addEventListener('change', () => {
            fieldInputs.filter(other => other.value === input.value)
                .forEach(other => { other.checked = input.checked; });
            saveFieldPreferences(input.closest('.cpu-field-list'));
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
        'cpu_limit_quota_vcpu', 'avg_vcpu', 'observed_cpu_vcpu_hours', 'hours_with_data']);
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
    const viewButton = get('cpu-view-report');
    let availabilityRequest;
    let availabilityTimer;

    function pickerParams() {
        const ids = get('preview-cluster-list').querySelectorAll('input:checked');
        const fields = selectedFields(reportForm);
        if (!start.value || !end.value || start.value > end.value ||
            start.value > start.max || end.value > end.max || !ids.length || !fields.length) return null;
        const params = new URLSearchParams({ start: start.value, end: end.value });
        ids.forEach(input => params.append('cluster_ids', input.value));
        fields.forEach(field => params.append('fields', field));
        return params;
    }

    function checkReportAvailability() {
        viewButton.disabled = true;
        availabilityRequest?.abort();
        clearTimeout(availabilityTimer);
        const params = pickerParams();
        const status = get('cpu-picker-status');
        if (!params) {
            status.textContent = 'Choose a valid date range, at least one cluster, and at least one report field.';
            return;
        }
        status.textContent = 'Checking for collected reports…';
        availabilityTimer = setTimeout(async () => {
            const controller = new AbortController();
            availabilityRequest = controller;
            try {
                const data = await read(endpoint + '?' + params, { signal: controller.signal });
                if (controller.signal.aborted) return;
                viewButton.disabled = data.rows.length === 0;
                const issue = data.collection_issues[0];
                status.textContent = data.rows.length ? '' : issue
                    ? [issue.day, issue.cluster, issue.status, issue.detail].filter(Boolean).join(' · ')
                    : 'No collected reports match this selection.';
            } catch (error) {
                if (!controller.signal.aborted) status.textContent = error.message;
            }
        }, 200);
    }

    function reportParams() {
        const params = new URLSearchParams(scope);
        activeEnvironments.forEach(value => params.append('environment', value));
        if (sortField) {
            params.set('sort', sortField);
            params.set('descending', String(descending));
        }
        if (search.value.trim()) params.set('search', search.value.trim());
        if (!params.has('fields')) initialFields.forEach(field => params.append('fields', field));
        return params;
    }

    async function read(url, options) {
        const response = await fetch(url, options);
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(typeof error.detail === 'string' ? error.detail : 'Unable to load the report.');
        }
        return response.json();
    }

    function renderPicker() {
        const filters = type => Array.from(picker.querySelectorAll('.report-filter.active[data-type="' + type + '"]'))
            .map(button => button.dataset.val);
        const environments = filters('env');
        const datacenters = filters('dc');
        const matches = clusters.filter(cluster =>
            (!environments.length || environments.includes((cluster.environment || '').toUpperCase())) &&
            (!datacenters.length || datacenters.includes((cluster.datacenter || '').toUpperCase()))
        );
        const list = get('preview-cluster-list');
        list.replaceChildren();
        for (const cluster of matches) {
            const label = document.createElement('label');
            label.style.display = 'block';
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.value = cluster.id;
            checkbox.checked = selection.has(cluster.id);
            checkbox.addEventListener('change', () => {
                if (checkbox.checked) selection.add(cluster.id);
                else selection.delete(cluster.id);
            });
            const info = [cluster.environment, cluster.datacenter, cluster.collecting ? '' : 'historical only']
                .filter(Boolean).join(' / ');
            label.append(checkbox, ' ' + cluster.name + (info ? ' (' + info + ')' : ''));
            list.append(label);
        }
        if (!matches.length) list.textContent = 'No clusters match these filters.';
        get('preview-count').textContent = matches.length;
        checkReportAvailability();
    }

    window.toggleCpuClusterFilter = button => {
        button.setAttribute('aria-pressed', String(button.classList.toggle('active')));
        renderPicker();
    };

    get('cpu-select-matching').addEventListener('click', () => {
        get('preview-cluster-list').querySelectorAll('input').forEach(input => selection.add(Number(input.value)));
        renderPicker();
    });
    get('cpu-clear-selection').addEventListener('click', () => {
        selection.clear();
        renderPicker();
    });

    get('cpu-open-picker').addEventListener('click', async () => {
        const status = get('cpu-picker-status');
        viewButton.disabled = true;
        get('preview-cluster-list').replaceChildren();
        status.textContent = 'Loading clusters…';
        picker.showModal();
        try {
            clusters = await read(endpoint + '/clusters');
            if (!initialized && clusters.length) {
                clusters.forEach(cluster => selection.add(cluster.id));
                initialized = true;
            }
            renderPicker();
            if (!clusters.length) status.textContent = 'Enable collection in Administration → Cluster Management.';
        } catch (error) {
            status.textContent = error.message;
        }
    });
    document.querySelectorAll('[data-close]').forEach(button => {
        button.addEventListener('click', () => get(button.dataset.close).close());
    });

    reportForm.addEventListener('input', checkReportAvailability);
    picker.addEventListener('close', () => {
        availabilityRequest?.abort();
        clearTimeout(availabilityTimer);
    });
    reportForm.addEventListener('submit', event => {
        event.preventDefault();
        const query = pickerParams();
        if (!query) return;
        if (event.submitter?.value === 'export') {
            window.location.assign(endpoint + '/export?' + query);
            return;
        }
        if (viewButton.disabled) return;
        scope = query;
        reportDay.value = '';
        activeEnvironments.clear();
        search.value = '';
        picker.close();
        loadPage(1);
    });

    const run = get('cpu-run');
    if (run) {
        const runForm = get('cpu-run-form');
        const clusterPicker = get('cpu-run-cluster');
        const runStatus = get('cpu-run-status');
        const submit = get('cpu-run-submit');
        const runStart = get('cpu-run-start');
        const runEnd = get('cpu-run-end');
        const runProgress = get('cpu-run-progress');
        const runDays = get('cpu-run-days');
        let running = false;

        get('cpu-open-run').addEventListener('click', async () => {
            run.showModal();
            if (running) return;
            submit.disabled = true;
            runStatus.textContent = 'Loading opted-in clusters…';
            clusterPicker.replaceChildren(new Option('Select one cluster', ''));
            try {
                const available = (await read(endpoint + '/clusters')).filter(cluster => cluster.collecting);
                for (const cluster of available) {
                    clusterPicker.add(new Option(cluster.name, cluster.id));
                }
                runStatus.textContent = available.length
                    ? 'Choose the completed Eastern days to collect.'
                    : 'Enable collection in Administration → Cluster Management.';
                submit.disabled = !available.length;
            } catch (error) {
                runStatus.textContent = error.message;
            }
        });

        runForm.addEventListener('submit', async event => {
            event.preventDefault();
            if (running || !clusterPicker.value) return;
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
            submit.disabled = clusterPicker.disabled = true;
            runStart.disabled = runEnd.disabled = true;
            runDays.removeAttribute('value');
            runProgress.hidden = false;
            runStatus.textContent = 'Starting collection…';
            try {
                const url = endpoint + '/run-now/' + clusterPicker.value;
                const dates = new URLSearchParams({ start: runStart.value, end: runEnd.value });
                const job = await read(url + '?' + dates, { method: 'POST' });
                let outcome;
                do {
                    await new Promise(resolve => setTimeout(resolve, 1500));
                    outcome = await read(url + '?' + dates);
                    runDays.max = outcome.total;
                    runDays.value = outcome.completed;
                    runStatus.textContent = outcome.completed + ' of ' + outcome.total + ' days processed · ' +
                        (outcome.detail || outcome.status);
                } while (outcome.status === 'Running');
                if (outcome.status === 'Failed') {
                    runStatus.textContent = outcome.detail || 'No reports were collected.';
                    if (!run.open) get('cpu-status').textContent = runStatus.textContent;
                    return;
                }

                const clusterId = Number(job.cluster_id);
                start.value = job.start;
                end.value = job.end;
                selection.clear();
                selection.add(clusterId);
                picker.querySelectorAll('.report-filter.active').forEach(button => {
                    button.classList.remove('active');
                    button.setAttribute('aria-pressed', 'false');
                });
                scope = new URLSearchParams({ start: job.start, end: job.end, cluster_ids: clusterId });
                reportDay.value = '';
                fields.forEach(field => scope.append('fields', field));
                activeEnvironments.clear();
                search.value = '';
                run.close();
                await loadPage(1);
                if (outcome.status === 'Partial') {
                    get('cpu-status').textContent = 'Collection completed with issues · ' + outcome.detail;
                    get('cpu-issues').open = true;
                }
            } catch (error) {
                runStatus.textContent = error.message;
                if (!run.open) get('cpu-status').textContent = error.message;
            } finally {
                running = false;
                submit.disabled = clusterPicker.disabled = false;
                runStart.disabled = runEnd.disabled = false;
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
        clearTimeout(searchTimer);
        request?.abort();
        request = new AbortController();
        const params = reportParams();
        const exportUrl = endpoint + '/export?' + params;
        const day = reportDay.value;
        if (day) params.set('report_day', day);
        params.set('page', target);
        get('cpu-show-all').hidden = !scope.has('start');
        const status = get('cpu-status');
        status.textContent = 'Loading report…';
        download.style.display = 'none';
        get('cpu-prev').disabled = get('cpu-next').disabled = true;
        get('cpu-issues').hidden = true;
        try {
            const data = await read(endpoint + '?' + params, { signal: request.signal });
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
            download.textContent = scope.has('start') ? 'Download range CSV' : 'Download CSV';
            get('cpu-prev').disabled = page === 1;
            get('cpu-next').disabled = !data.has_more;
            const firstRow = data.rows.length ? (page - 1) * data.page_size + 1 : 0;
            const lastRow = firstRow ? firstRow + data.rows.length - 1 : 0;
            get('cpu-page').textContent = 'Page ' + page + ' of ' + data.page_count +
                ' · ' + firstRow + '–' + lastRow + ' of ' + data.total + ' rows';
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
    search.addEventListener('input', () => {
        clearTimeout(searchTimer);
        request?.abort();
        searchTimer = setTimeout(() => loadPage(1), 250);
    });
    get('cpu-show-all').addEventListener('click', () => {
        const fields = scope.getAll('fields');
        scope = new URLSearchParams();
        reportDay.value = '';
        fields.forEach(field => scope.append('fields', field));
        activeEnvironments.clear();
        search.value = '';
        loadPage(1);
    });
    loadPage(1);
})();
