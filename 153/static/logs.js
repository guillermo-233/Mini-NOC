// Panel de logs del Mini-NOC: últimos eventos con filtros por host, severidad y texto, y recuento por severidad.
// Sustituye al panel antiguo de syslog. Los textos de la API se escriben con textContent
// (nunca innerHTML): el contenido de un log, como el nombre de usuario de un login fallido, lo controla quien ataca.
(function () {
    const REFRESH_MS = 15000;
    const LIMIT = 50;
    const WINDOWS = [[60, '1 h'], [1440, '24 h'], [10080, '7 días']];
    const COLORS = {
        emerg: '#7b1113', alert: '#a71d2a', crit: '#dc3545', err: '#dc3545',
        warning: '#fd7e14', notice: '#0d6efd', info: '#6c757d', debug: '#adb5bd'
    };
    const state = { minutes: 1440, host: '', q: '', severities: [] };
    let hostsKey = '';

    function el(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = text;
        if (className) node.className = className;
        return node;
    }

    function fmtTime(iso) {
        if (!iso) return '-';
        const d = new Date(iso);
        return isNaN(d) ? iso : d.toLocaleString('es-ES', { timeZone: 'Europe/Madrid' });
    }

    function pill(text, active, color, onClick) {
        const b = el('button', text);
        b.style.cssText = 'padding:4px 10px;margin:0 6px 6px 0;border-radius:12px;cursor:pointer;border:1px solid ' + color + ';' +
            (active ? 'background:' + color + ';color:white;' : 'background:white;color:' + color + ';');
        b.addEventListener('click', onClick);
        return b;
    }

    // ---- Estructura (se construye una sola vez; así no se pierde el foco al escribir) ----
    const panel = el('div', null, 'panel');
    panel.id = 'logs-panel';
    const oldTable = document.getElementById('syslog-table');
    const oldPanel = oldTable ? (oldTable.closest('.panel') || oldTable.parentElement) : null;
    if (oldPanel) {
        oldPanel.style.display = 'none';
        oldPanel.insertAdjacentElement('beforebegin', panel);
    } else {
        document.body.appendChild(panel);
    }

    panel.appendChild(el('h2', 'Eventos (Syslog)'));
    const windowRow = document.createElement('div');
    windowRow.style.marginBottom = '8px';
    const filterRow = document.createElement('div');
    filterRow.style.cssText = 'margin-bottom:8px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;';
    const chipsRow = document.createElement('div');
    chipsRow.style.marginBottom = '4px';
    const alertBox = document.createElement('div');
    const tableBox = document.createElement('div');
    const footer = document.createElement('div');

    const hostSelect = document.createElement('select');
    hostSelect.appendChild(Object.assign(el('option', 'Todos los hosts'), { value: '' }));
    hostSelect.addEventListener('change', () => { state.host = hostSelect.value; refresh(); });

    const textInput = document.createElement('input');
    textInput.type = 'text';
    textInput.placeholder = 'Buscar en el mensaje…';
    textInput.maxLength = 100;
    textInput.style.minWidth = '220px';
    let debounce = null;
    textInput.addEventListener('input', () => {
        clearTimeout(debounce);
        debounce = setTimeout(() => { state.q = textInput.value.trim(); refresh(); }, 400);
    });

    const clearBtn = el('button', 'Limpiar filtros');
    clearBtn.addEventListener('click', () => {
        state.host = ''; state.q = ''; state.severities = [];
        hostSelect.value = ''; textInput.value = '';
        refresh();
    });

    filterRow.appendChild(hostSelect);
    filterRow.appendChild(textInput);
    filterRow.appendChild(clearBtn);
    [windowRow, filterRow, chipsRow, alertBox, tableBox, footer].forEach(n => panel.appendChild(n));

    // ---- Dibujo ----
    function renderWindows() {
        windowRow.textContent = '';
        windowRow.appendChild(el('span', 'Ventana: '));
        WINDOWS.forEach(([m, label]) => windowRow.appendChild(
            pill(label, state.minutes === m, '#0056b3', () => { state.minutes = m; refresh(); })));
    }

    function renderHosts(hosts) {
        const key = hosts.join(',') + '|' + state.host;
        if (key === hostsKey) return;
        hostsKey = key;
        hostSelect.textContent = '';
        hostSelect.appendChild(Object.assign(el('option', 'Todos los hosts'), { value: '' }));
        const all = hosts.slice();
        if (state.host && !all.includes(state.host)) all.push(state.host);
        all.forEach(h => hostSelect.appendChild(Object.assign(el('option', h), { value: h })));
        hostSelect.value = state.host;
    }

    function renderChips(counts) {
        chipsRow.textContent = '';
        chipsRow.appendChild(el('span', 'Severidad: '));
        const shown = counts.slice();
        state.severities.forEach(s => { if (!shown.some(c => c.severity === s)) shown.push({ severity: s, count: 0 }); });
        if (!shown.length) chipsRow.appendChild(el('span', 'sin eventos'));
        shown.forEach(c => {
            const on = state.severities.includes(c.severity);
            chipsRow.appendChild(pill(c.severity + ' (' + c.count + ')', on, COLORS[c.severity] || '#6c757d', () => {
                state.severities = on ? state.severities.filter(s => s !== c.severity) : state.severities.concat([c.severity]);
                refresh();
            }));
        });
    }

    function renderTable(data) {
        tableBox.textContent = '';
        if (!data.data.length) return;
        const table = document.createElement('table');
        const head = document.createElement('tr');
        ['Hora (Madrid)', 'Host', 'Severidad', 'Mensaje'].forEach(h => head.appendChild(el('th', h)));
        table.appendChild(head);
        data.data.forEach(r => {
            const tr = document.createElement('tr');
            tr.appendChild(el('td', fmtTime(r.ts)));
            tr.appendChild(el('td', r.host));
            const sevTd = document.createElement('td');
            const badge = el('span', r.severity, 'badge');
            badge.style.backgroundColor = COLORS[r.severity] || '#6c757d';
            badge.title = r.facility ? 'facility: ' + r.facility : '';
            sevTd.appendChild(badge);
            tr.appendChild(sevTd);
            tr.appendChild(el('td', r.message));
            table.appendChild(tr);
        });
        tableBox.appendChild(table);
    }

    function render(data) {
        renderWindows();
        renderHosts(data.hosts);
        renderChips(data.counts);

        alertBox.textContent = '';
        if (data.collector !== 'active') {
            alertBox.appendChild(el('div', 'El recolector de syslog no está en marcha (estado: ' + data.collector +
                '). Los eventos de abajo pueden estar desactualizados.', 'empty-message'));
        }
        if (data.status !== 'ok' && data.message) alertBox.appendChild(el('div', data.message, 'empty-message'));

        renderTable(data);

        footer.textContent = '';
        if (data.data.length) {
            footer.appendChild(el('div', 'Mostrando ' + data.data.length + ' de ' + data.total_matching + ' eventos que coinciden.', 'freshness'));
        }
        footer.appendChild(el('div', 'Último evento recibido: ' + (data.freshness ? fmtTime(data.freshness) : 'ninguno'), 'freshness'));
    }

    async function refresh() {
        renderWindows();
        const params = new URLSearchParams({ minutes: state.minutes, limit: LIMIT });
        if (state.host) params.set('host', state.host);
        if (state.q) params.set('q', state.q);
        if (state.severities.length) params.set('severity', state.severities.join(','));
        try {
            const response = await fetch('/api/logs?' + params.toString());
            if (!response.ok) throw new Error('HTTP ' + response.status);
            render(await response.json());
        } catch (e) {
            alertBox.textContent = '';
            alertBox.appendChild(el('div', 'No se pudo consultar los logs (' + e.message + '). Comprueba que la API esté en marcha.', 'empty-message'));
        }
    }

    refresh();
    setInterval(refresh, REFRESH_MS);
})();
