// Panel de flujos del Mini-NOC: top talkers por hosts, pares o puertos, y conversaciones de un host.
// Las direcciones son las REALES (tras deshacer el NAT). Sustituye al panel antiguo de Top Talkers.
// Los textos de la API se escriben con textContent (nunca innerHTML) para evitar inyección de HTML.
(function () {
    const REFRESH_MS = 30000;
    const WINDOWS = [[5, '5 min'], [15, '15 min'], [60, '1 h'], [1440, '24 h']];
    const TABS = [['hosts', 'Hosts'], ['pairs', 'Pares de hosts'], ['ports', 'Puertos']];
    const state = { minutes: 60, by: 'hosts', host: null };

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

    function fmtBytes(n) {
        if (n === null || n === undefined) return '-';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let v = Number(n), i = 0;
        while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
        return (i === 0 ? v.toFixed(0) : v.toFixed(1)) + ' ' + units[i];
    }

    function makeTable(headers, rows) {
        const table = document.createElement('table');
        const head = document.createElement('tr');
        headers.forEach(h => head.appendChild(el('th', h)));
        table.appendChild(head);
        rows.forEach(cells => {
            const tr = document.createElement('tr');
            cells.forEach(c => tr.appendChild(c instanceof Node ? c : el('td', c)));
            table.appendChild(tr);
        });
        return table;
    }

    function linkCell(text, onClick) {
        const td = document.createElement('td');
        const button = el('button', text);
        button.style.cssText = 'background:none;border:none;color:#0056b3;cursor:pointer;text-decoration:underline;padding:0;font:inherit;';
        button.title = 'Ver conversaciones de este host';
        button.addEventListener('click', onClick);
        td.appendChild(button);
        return td;
    }

    function styledButton(text, active, onClick) {
        const b = el('button', text);
        b.style.cssText = 'padding:5px 12px;margin-right:6px;border:1px solid #0056b3;border-radius:4px;cursor:pointer;' +
            (active ? 'background:#0056b3;color:white;' : 'background:white;color:#0056b3;');
        b.addEventListener('click', onClick);
        return b;
    }

    // Anclaje: sustituye al panel antiguo de Top Talkers (que agrupaba por IP sin deshacer el NAT)
    const panel = el('div', null, 'panel');
    panel.id = 'flows-panel';
    const oldTable = document.getElementById('ipfix-table');
    const oldPanel = oldTable ? (oldTable.closest('.panel') || oldTable.parentElement) : null;
    if (oldPanel) {
        oldPanel.style.display = 'none';
        oldPanel.insertAdjacentElement('beforebegin', panel);
    } else {
        document.body.appendChild(panel);
    }

    const title = el('h2', 'Flujos (IPFIX)');
    const controls = document.createElement('div');
    const body = document.createElement('div');
    panel.appendChild(title);
    panel.appendChild(controls);
    panel.appendChild(body);

    function renderControls() {
        controls.textContent = '';
        const row1 = document.createElement('div');
        row1.style.marginBottom = '8px';
        row1.appendChild(el('span', 'Ventana: '));
        WINDOWS.forEach(([m, label]) => row1.appendChild(styledButton(label, state.minutes === m, () => {
            state.minutes = m; refresh();
        })));
        controls.appendChild(row1);
        if (!state.host) {
            const row2 = document.createElement('div');
            TABS.forEach(([by, label]) => row2.appendChild(styledButton(label, state.by === by, () => {
                state.by = by; refresh();
            })));
            controls.appendChild(row2);
        }
    }

    function showStatus(data) {
        if (data.status !== 'ok' && data.message) body.appendChild(el('div', data.message, 'empty-message'));
        body.appendChild(el('div',
            'Dato más reciente: ' + (data.freshness ? fmtTime(data.freshness) : 'ninguno'), 'freshness'));
    }

    function renderTop(data) {
        body.textContent = '';
        body.appendChild(el('h3', 'Top ' + TABS.find(t => t[0] === data.by)[1].toLowerCase() +
            ' · últimos ' + (WINDOWS.find(w => w[0] === state.minutes) || [0, state.minutes + ' min'])[1]));
        if (data.data.length) {
            if (data.by === 'hosts') {
                body.appendChild(makeTable(['Host (IP real)', 'Enviado', 'Recibido', 'Total', 'Flujos'],
                    data.data.map(r => [linkCell(r.host, () => { state.host = r.host; refresh(); }),
                        fmtBytes(r.bytes_out), fmtBytes(r.bytes_in), fmtBytes(r.total_bytes), String(r.flows)])));
            } else if (data.by === 'pairs') {
                body.appendChild(makeTable(['Origen', 'Destino', 'Total', 'Paquetes', 'Flujos'],
                    data.data.map(r => [r.src, r.dst, fmtBytes(r.total_bytes), String(r.packets), String(r.flows)])));
            } else {
                body.appendChild(makeTable(['Protocolo', 'Puerto de servicio', 'Total', 'Flujos'],
                    data.data.map(r => [r.protocol, String(r.port), fmtBytes(r.total_bytes), String(r.flows)])));
            }
        }
        showStatus(data);
    }

    function renderHost(data) {
        body.textContent = '';
        const back = styledButton('← Volver al ranking', false, () => { state.host = null; refresh(); });
        body.appendChild(back);
        body.appendChild(el('h3', 'Conversaciones de ' + data.host));
        if (data.data.length) {
            body.appendChild(makeTable(['Interlocutor', 'Protocolo', 'Puerto', 'Enviado', 'Recibido', 'Flujos'],
                data.data.map(r => [r.peer, r.protocol, String(r.port), fmtBytes(r.bytes_out), fmtBytes(r.bytes_in), String(r.flows)])));
        }
        showStatus(data);
    }

    async function refresh() {
        renderControls();
        try {
            const url = state.host
                ? '/api/flows/host/' + encodeURIComponent(state.host) + '?minutes=' + state.minutes
                : '/api/flows/top?minutes=' + state.minutes + '&by=' + state.by + '&limit=10';
            const response = await fetch(url);
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const data = await response.json();
            if (state.host) renderHost(data); else renderTop(data);
        } catch (e) {
            body.textContent = '';
            body.appendChild(el('div', 'No se pudo consultar los flujos (' + e.message + '). Comprueba que la API esté en marcha.', 'empty-message'));
        }
    }

    refresh();
    setInterval(refresh, REFRESH_MS);
})();
