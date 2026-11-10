// Panel de dispositivo del Mini-NOC: alcanzabilidad, tiempo en marcha, último sondeo correcto
// y tabla de interfaces. Sustituye al panel antiguo de "Interfaces de Red".
// Los textos de la API se escriben con textContent (nunca innerHTML).
(function () {
    const REFRESH_MS = 15000;
    const STATUS = {
        reachable: ['Alcanzable', '#198754'],
        unreachable: ['Sin respuesta', '#dc3545'],
        collector_down: ['Recolector parado', '#fd7e14'],
        stale: ['Datos obsoletos', '#fd7e14'],
        unknown: ['Desconocido', '#6c757d']
    };

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

    function fmtDuration(seconds) {
        if (seconds === null || seconds === undefined) return '-';
        let s = Math.floor(seconds);
        const d = Math.floor(s / 86400); s %= 86400;
        const h = Math.floor(s / 3600); s %= 3600;
        const m = Math.floor(s / 60);
        const parts = [];
        if (d) parts.push(d + (d === 1 ? ' día' : ' días'));
        if (d || h) parts.push(h + ' h');
        parts.push(m + ' min');
        return parts.join(' ');
    }

    function fmtAge(seconds) {
        if (seconds === null || seconds === undefined) return '';
        if (seconds < 90) return 'hace ' + seconds + ' s';
        if (seconds < 5400) return 'hace ' + Math.round(seconds / 60) + ' min';
        return 'hace ' + Math.round(seconds / 3600) + ' h';
    }

    function fmtRate(bps) {
        if (bps === null || bps === undefined) return '— (reinicio de contador)';
        if (bps < 1000) return Math.round(bps) + ' bit/s';
        if (bps < 1e6) return (bps / 1e3).toFixed(1) + ' kbit/s';
        return (bps / 1e6).toFixed(2) + ' Mbit/s';
    }

    const panel = el('div', null, 'panel');
    panel.id = 'device-panel';
    const oldTable = document.getElementById('interfaces-table');
    const oldPanel = oldTable ? (oldTable.closest('.panel') || oldTable.parentElement) : null;
    if (oldPanel) {
        oldPanel.style.display = 'none';
        oldPanel.insertAdjacentElement('beforebegin', panel);
    } else {
        document.body.appendChild(panel);
    }
    panel.appendChild(el('h2', 'Dispositivo'));
    const body = document.createElement('div');
    panel.appendChild(body);

    function infoLine(label, value) {
        const p = el('p');
        p.style.margin = '3px 0';
        p.appendChild(el('strong', label + ': '));
        p.appendChild(document.createTextNode(value));
        return p;
    }

    function render(data) {
        body.textContent = '';
        const [label, color] = STATUS[data.status] || [data.status, '#6c757d'];
        const head = document.createElement('div');
        head.style.marginBottom = '8px';
        const badge = el('span', label, 'badge');
        badge.style.backgroundColor = color;
        head.appendChild(badge);
        head.appendChild(document.createTextNode('  ' + (data.device || 'sin dispositivo registrado')));
        body.appendChild(head);

        if (data.message) body.appendChild(el('div', data.message, 'empty-message'));

        if (data.device) {
            body.appendChild(infoLine('Tiempo en marcha (sysUpTime)', fmtDuration(data.uptime_seconds)));
            if (data.boot_time) body.appendChild(infoLine('Arrancó', fmtTime(data.boot_time)));
            body.appendChild(infoLine('Último sondeo correcto',
                data.last_success ? fmtTime(data.last_success) + ' (' + fmtAge(data.success_age_seconds) + ')' : 'ninguno'));
            body.appendChild(infoLine('Último intento',
                fmtTime(data.last_attempt) + ' (' + fmtAge(data.attempt_age_seconds) + ')'));
            if (data.consecutive_failures > 0) {
                body.appendChild(infoLine('Fallos seguidos', data.consecutive_failures + (data.last_error ? ' · ' + data.last_error : '')));
            }
        }

        body.appendChild(el('h3', 'Interfaces'));
        if (!data.interfaces.length) {
            body.appendChild(el('div', 'No hay datos de interfaces: el poller SNMP todavía no ha guardado ninguna muestra.', 'empty-message'));
        } else {
            const table = document.createElement('table');
            const head = document.createElement('tr');
            ['Índice', 'Nombre', 'Admin', 'Estado', 'Entrada', 'Salida', 'Última muestra'].forEach(h => head.appendChild(el('th', h)));
            table.appendChild(head);
            data.interfaces.forEach(i => {
                const tr = document.createElement('tr');
                tr.appendChild(el('td', String(i.if_index)));
                tr.appendChild(el('td', i.name));
                tr.appendChild(el('td', i.admin_status));
                const state = el('td', i.state === 'admin_down' ? 'desactivada' : i.oper_status);
                state.style.fontWeight = 'bold';
                state.style.color = i.state === 'up' ? '#198754' : (i.state === 'down' ? '#dc3545' : '#6c757d');
                tr.appendChild(state);
                tr.appendChild(el('td', fmtRate(i.in_bps)));
                tr.appendChild(el('td', fmtRate(i.out_bps)));
                tr.appendChild(el('td', fmtAge(i.age_seconds) + (i.stale ? ' · sin datos recientes' : '')));
                if (i.stale) tr.style.opacity = '0.6';
                table.appendChild(tr);
            });
            body.appendChild(table);
        }
        body.appendChild(el('div', 'Última muestra recibida: ' + (data.freshness ? fmtTime(data.freshness) : 'ninguna'), 'freshness'));
    }

    async function refresh() {
        try {
            const response = await fetch('/api/device');
            if (!response.ok) throw new Error('HTTP ' + response.status);
            render(await response.json());
        } catch (e) {
            body.textContent = '';
            body.appendChild(el('div', 'No se pudo consultar el estado del dispositivo (' + e.message + '). Comprueba que la API esté en marcha.', 'empty-message'));
        }
    }

    refresh();
    setInterval(refresh, REFRESH_MS);
})();
