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
    let request;
    let chart;

    const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 });
    const format = value => value === null ? '—' : number.format(value);
    const fieldLabels = JSON.parse(get('cpu-field-labels').textContent);
    const dateParts = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Toronto',
        year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
    const datePart = type => dateParts.find(part => part.type === type).value;
    const easternDate = Date.UTC(datePart('year'), Number(datePart('month')) - 1, datePart('day'));
    const yesterday = new Date(easternDate - 86400000).toISOString().slice(0, 10);

    const fieldInputs = Array.from(document.querySelectorAll('input[name="cpu-field"]'));
    for (const input of fieldInputs) {
        input.addEventListener('change', () => {
            fieldInputs.filter(other => other.value === input.value)
                .forEach(other => { other.checked = input.checked; });
        });
    }
    const selectedFields = form => Array.from(form.querySelectorAll('input[name="cpu-field"]:checked'),
        input => input.value);
    const environment = get('cpu-environment');
    const search = get('cpu-search');
    const defaultFields = selectedFields(get('cpu-report-form'));

    function reportParams() {
        const params = new URLSearchParams(scope);
        if (environment.value) params.set('environment', environment.value);
        if (search.value.trim()) params.set('search', search.value.trim());
        if (!params.has('fields')) defaultFields.forEach(field => params.append('fields', field));
        return params;
    }
    start.value = end.value = start.max = end.max = yesterday;

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
                get('cpu-picker-status').textContent = '';
            });
            const info = [cluster.environment, cluster.datacenter, cluster.collecting ? '' : 'historical only']
                .filter(Boolean).join(' / ');
            label.append(checkbox, ' ' + cluster.name + (info ? ' (' + info + ')' : ''));
            list.append(label);
        }
        if (!matches.length) list.textContent = 'No clusters match these filters.';
        get('preview-count').textContent = matches.length;
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
        status.textContent = 'Loading clusters…';
        picker.showModal();
        try {
            clusters = await read(endpoint + '/clusters');
            if (!initialized && clusters.length) {
                clusters.forEach(cluster => selection.add(cluster.id));
                initialized = true;
            }
            renderPicker();
            status.textContent = clusters.length ? '' : 'Enable collection in Administration → Cluster Management.';
        } catch (error) {
            status.textContent = error.message;
        }
    });
    document.querySelectorAll('[data-close]').forEach(button => {
        button.addEventListener('click', () => get(button.dataset.close).close());
    });

    get('cpu-report-form').addEventListener('submit', event => {
        event.preventDefault();
        const ids = Array.from(get('preview-cluster-list').querySelectorAll('input:checked'));
        if (!ids.length || start.value > end.value) {
            get('cpu-picker-status').textContent = ids.length ? 'Choose an end date on or after the start date.' : 'Select at least one matching cluster.';
            return;
        }
        const fields = selectedFields(event.currentTarget);
        if (!fields.length) {
            get('cpu-picker-status').textContent = 'Choose at least one report field.';
            return;
        }
        const query = new URLSearchParams({ start: start.value, end: end.value });
        fields.forEach(field => query.append('fields', field));
        ids.forEach(checkbox => query.append('cluster_ids', checkbox.value));
        if (event.submitter?.value === 'export') {
            window.location.assign(endpoint + '/export?' + query);
            return;
        }
        scope = query;
        environment.value = search.value = '';
        picker.close();
        loadPage(1);
    });

    const run = get('cpu-run');
    if (run) {
        const runForm = get('cpu-run-form');
        const clusterPicker = get('cpu-run-cluster');
        const runStatus = get('cpu-run-status');
        const submit = get('cpu-run-submit');
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
                    ? 'Collects the previous completed Eastern day.'
                    : 'Enable collection in Administration → Cluster Management.';
                submit.disabled = !available.length;
            } catch (error) {
                runStatus.textContent = error.message;
            }
        });

        runForm.addEventListener('submit', async event => {
            event.preventDefault();
            if (running || !clusterPicker.value) return;
            const fields = selectedFields(runForm);
            if (!fields.length) {
                runStatus.textContent = 'Choose at least one report field.';
                return;
            }
            running = true;
            submit.disabled = clusterPicker.disabled = true;
            runStatus.textContent = 'Collecting yesterday’s report from Thanos…';
            try {
                const url = endpoint + '/run-now/' + clusterPicker.value;
                const job = await read(url, { method: 'POST' });
                let outcome;
                do {
                    await new Promise(resolve => setTimeout(resolve, 1500));
                    outcome = await read(url + '?day=' + job.day);
                } while (outcome.status === 'Running');
                if (outcome.status !== 'Collected') {
                    runStatus.textContent = outcome.detail || 'Thanos returned no namespace data for yesterday.';
                    if (!run.open) get('cpu-status').textContent = runStatus.textContent;
                    return;
                }

                const clusterId = Number(job.cluster_id);
                if (job.day > end.max) start.max = end.max = job.day;
                start.value = end.value = job.day;
                selection.clear();
                selection.add(clusterId);
                picker.querySelectorAll('.report-filter.active').forEach(button => {
                    button.classList.remove('active');
                    button.setAttribute('aria-pressed', 'false');
                });
                scope = new URLSearchParams({ start: job.day, end: job.day, cluster_ids: clusterId });
                fields.forEach(field => scope.append('fields', field));
                environment.value = search.value = '';
                run.close();
                await loadPage(1);
            } catch (error) {
                runStatus.textContent = error.message;
                if (!run.open) get('cpu-status').textContent = error.message;
            } finally {
                running = false;
                submit.disabled = clusterPicker.disabled = false;
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
                plugins: { legend: { labels: { color: textColor } } },
                scales: {
                    x: { ticks: { color: textColor } },
                    cpu: {
                        type: 'linear', position: 'left', beginAtZero: true, ticks: { color: usageColor },
                        title: { display: true, text: 'Hourly average CPU (vCPU)', color: usageColor },
                    },
                    quota: {
                        type: 'linear', position: 'right', display: row.cpu_limit_quota_vcpu !== null,
                        beginAtZero: true, grid: { drawOnChartArea: false }, ticks: { color: quotaColor },
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
        params.set('page', target);
        get('cpu-show-all').hidden = !scope.has('start');
        const status = get('cpu-status');
        status.textContent = 'Loading report…';
        get('cpu-download-active').style.display = 'none';
        get('cpu-results').hidden = true;
        get('cpu-issues').hidden = true;
        try {
            const data = await read(endpoint + '?' + params, { signal: request.signal });
            page = target;
            const selectedEnvironment = environment.value;
            environment.replaceChildren(new Option('All environments', ''));
            for (const value of data.environments) environment.add(new Option(value, value));
            environment.value = data.environments.includes(selectedEnvironment) ? selectedEnvironment : '';
            const body = get('cpu-result-rows');
            body.replaceChildren();
            const fields = params.getAll('fields');
            const header = get('cpu-result-head');
            header.replaceChildren();
            for (const field of fields) {
                const cell = document.createElement('th');
                cell.scope = 'col';
                cell.textContent = fieldLabels[field];
                header.append(cell);
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
            get('cpu-results').hidden = false;
            get('cpu-download-active').style.display = data.rows.length ? '' : 'none';
            get('cpu-prev').disabled = page === 1;
            get('cpu-next').disabled = !data.has_more;
            get('cpu-page').textContent = 'Page ' + page;
            status.textContent = data.rows.length ? '' : 'No collected reports match this selection.';
        } catch (error) {
            if (error.name !== 'AbortError') status.textContent = error.message;
        }
    }

    get('cpu-prev').addEventListener('click', () => loadPage(page - 1));
    get('cpu-next').addEventListener('click', () => loadPage(page + 1));
    get('cpu-download-active').addEventListener('click', () => window.location.assign(endpoint + '/export?' + reportParams()));
    environment.addEventListener('change', () => loadPage(1));
    search.addEventListener('input', () => {
        clearTimeout(searchTimer);
        request?.abort();
        searchTimer = setTimeout(() => loadPage(1), 250);
    });
    get('cpu-show-all').addEventListener('click', () => {
        const fields = scope.getAll('fields');
        scope = new URLSearchParams();
        fields.forEach(field => scope.append('fields', field));
        environment.value = search.value = '';
        loadPage(1);
    });
    loadPage(1);
})();
