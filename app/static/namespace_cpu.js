(() => {
    const endpoint = '/api/reporting/namespace-cpu';
    const get = id => document.getElementById(id);
    const picker = get('cpu-picker');
    const start = get('cpu-start');
    const end = get('cpu-end');
    const selection = new Set();
    let clusters = [];
    let initialized = false;
    let scope;
    let page = 1;
    let request;
    let chart;

    const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 });
    const format = value => value === null ? '—' : number.format(value);
    const hours = Array.from({ length: 24 }, (_, hour) => String(hour).padStart(2, '0') + ':00');
    const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
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
        const query = new URLSearchParams({ start: start.value, end: end.value });
        ids.forEach(checkbox => query.append('cluster_ids', checkbox.value));
        if (event.submitter?.value === 'export') {
            window.location.assign(endpoint + '/export?' + query);
            return;
        }
        scope = query;
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
                    ? 'Collects the previous completed UTC day.'
                    : 'Enable collection in Administration → Cluster Management.';
                submit.disabled = !available.length;
            } catch (error) {
                runStatus.textContent = error.message;
            }
        });

        runForm.addEventListener('submit', async event => {
            event.preventDefault();
            if (running || !clusterPicker.value) return;
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
        get('cpu-hourly-title').textContent = row.namespace + ' · ' + row.cluster + ' · ' + row.date_utc;
        const body = get('cpu-hourly-rows');
        body.replaceChildren();
        hours.forEach((hour, index) => {
            const line = body.insertRow();
            [hour, format(row.hourly_usage[index]), format(row.hourly_quota[index]), row.hourly_mapid[index] || 'Unmapped']
                .forEach(value => appendCell(line, value));
        });
        get('cpu-hourly').showModal();
        if (typeof Chart === 'undefined') {
            body.closest('details').open = true;
            return;
        }
        chart?.destroy();
        chart = new Chart(get('cpu-chart'), {
            type: 'line',
            data: {
                labels: hours,
                datasets: [
                    { label: 'Avg CPU', data: row.hourly_usage, borderColor: '#3b82f6' },
                    { label: 'Avg quota', data: row.hourly_quota, borderColor: '#d97706' },
                ],
            },
            options: { scales: { y: { beginAtZero: true, title: { display: true, text: 'vCPU' } } } },
        });
    }

    async function loadPage(target) {
        request?.abort();
        request = new AbortController();
        const status = get('cpu-status');
        status.textContent = 'Loading report…';
        get('cpu-download-active').style.display = 'none';
        get('cpu-results').hidden = true;
        get('cpu-issues').hidden = true;
        try {
            const data = await read(endpoint + '?' + scope + '&page=' + target, { signal: request.signal });
            page = target;
            const body = get('cpu-result-rows');
            body.replaceChildren();
            for (const row of data.rows) {
                const line = body.insertRow();
                appendCell(line, row.date_utc);
                appendCell(line, [row.cluster, row.environment].filter(Boolean).join(' / '));
                const link = document.createElement('button');
                link.type = 'button';
                link.className = 'btn btn-secondary btn-sm';
                link.textContent = row.namespace;
                link.addEventListener('click', () => showHourly(row));
                line.insertCell().append(link);
                [row.mapid || 'Unmapped', format(row.avg_vcpu), format(row.observed_cpu_vcpu_hours),
                    row.hours_with_data + ' / 24', format(row.avg_quota_vcpu),
                    format(row.observed_quota_vcpu_hours), row.quota_hours_with_data + ' / 24']
                    .forEach(value => appendCell(line, value));
                if (row.quota_status === 'Multiple quota objects' || row.quota_status === 'Varied') {
                    line.lastElementChild.append(' · ' + row.quota_status);
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
            get('cpu-results').hidden = !data.rows.length && page === 1;
            get('cpu-download-active').style.display = data.rows.length ? '' : 'none';
            get('cpu-prev').disabled = page === 1;
            get('cpu-next').disabled = !data.has_more;
            get('cpu-page').textContent = 'Page ' + page;
            const range = scope.get('start') + ' through ' + scope.get('end') + ' (UTC)';
            status.textContent = data.rows.length ? range : 'No collected namespace data for ' + range + '.';
        } catch (error) {
            if (error.name !== 'AbortError') status.textContent = error.message;
        }
    }

    get('cpu-prev').addEventListener('click', () => loadPage(page - 1));
    get('cpu-next').addEventListener('click', () => loadPage(page + 1));
    get('cpu-download-active').addEventListener('click', () => window.location.assign(endpoint + '/export?' + scope));
})();
