// Panel de alertas del Mini-NOC. Se inserta bajo el título y se refresca solo.
// Los textos de la API se escriben con textContent (nunca innerHTML) para evitar inyección de HTML.
(function () {
    const REFRESH_MS = 15000;
    const SEVERITY_COLOR = { CRITICAL: '#dc3545', WARNING: '#fd7e14' };

    function el(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = text;
        if (className) node.className = className;
        return node;
    }

    function fmt(iso) {
        if (!iso) return '-';
        const d = new Date(iso);
        if (isNaN(d)) return iso;
        return d.toLocaleString('es-ES', { timeZone: 'Europe/Madrid' });
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

    function severityCell(severity) {
        const td = document.createElement('td');
        const badge = el('span', severity, 'badge');
        badge.style.backgroundColor = SEVERITY_COLOR[severity] || '#6c757d';
        td.appendChild(badge);
        return td;
    }

    const panel = el('div', null, 'panel');
    panel.id = 'alerts-panel';
    panel.appendChild(el('h2', 'Alertas'));
    const body = document.createElement('div');
    panel.appendChild(body);
    const anchor = document.querySelector('h1');
    if (anchor) anchor.insertAdjacentElement('afterend', panel);
    else document.body.prepend(panel);

    function render(data) {
        body.textContent = '';

        if (data.engine !== 'active') {
            body.appendChild(el('div',
                'El motor de alertas no está en marcha (estado: ' + data.engine + '). ' +
                'La ausencia de alertas NO significa que todo esté bien.', 'empty-message'));
        }

        if (data.active.length === 0) {
            if (data.engine === 'active') body.appendChild(el('p', 'Sin alertas activas.'));
        } else {
            body.appendChild(el('h3', 'Activas (' + data.active.length + ')'));
            body.appendChild(makeTable(
                ['Severidad', 'Regla', 'Entidad', 'Desde', 'Mensaje'],
                data.active.map(a => [severityCell(a.severity), a.rule_name, a.entity, fmt(a.start_time), a.message || ''])
            ));
        }

        if (data.recent_resolved.length > 0) {
            const details = document.createElement('details');
            details.style.marginTop = '10px';
            details.appendChild(el('summary', 'Últimas alertas resueltas (' + data.recent_resolved.length + ')'));
            details.appendChild(makeTable(
                ['Regla', 'Entidad', 'Desde', 'Hasta'],
                data.recent_resolved.map(a => [a.rule_name, a.entity, fmt(a.start_time), fmt(a.end_time)])
            ));
            body.appendChild(details);
        }

        body.appendChild(el('div',
            'Última actividad de alertas: ' + (data.freshness ? fmt(data.freshness) : 'ninguna alerta registrada todavía'),
            'freshness'));
    }

    function showError(message) {
        body.textContent = '';
        body.appendChild(el('div', message, 'empty-message'));
    }

    async function refresh() {
        try {
            const response = await fetch('/api/alerts');
            if (!response.ok) throw new Error('HTTP ' + response.status);
            render(await response.json());
        } catch (e) {
            showError('No se pudo consultar las alertas (' + e.message + '). Comprueba que la API esté en marcha.');
        }
    }

    refresh();
    setInterval(refresh, REFRESH_MS);
})();
