import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

import flows_api
from flow_poller import resolve_real_ip

WAN = '192.168.100.56'   # dirección WAN del router (la que NUNCA debe aparecer como host interno)


def ts_ago(minutes):
    t = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return t.strftime('%Y-%m-%d %H:%M:%S.') + '%03d' % (t.microsecond // 1000)


class TestFlowsApi(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        flows_api.DB_FILE = self.path
        self.conn = sqlite3.connect(self.path)
        self.conn.execute('''CREATE TABLE flow_data (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, te TEXT, pr TEXT,
            sa TEXT, sp INTEGER, da TEXT, dp INTEGER, pkt INTEGER, byt INTEGER, in_if INTEGER, out_if INTEGER,
            xsa TEXT, xda TEXT, real_src TEXT, real_dst TEXT)''')
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def add(self, minutes_ago, pr, sa, sp, da, dp, byt, xsa, xda, pkt=10):
        ts = ts_ago(minutes_ago)
        self.conn.execute(
            'INSERT INTO flow_data (ts, te, pr, sa, sp, da, dp, pkt, byt, in_if, out_if, xsa, xda, real_src, real_dst) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 2, ?, ?, ?, ?)',
            (ts, ts, pr, sa, sp, da, dp, pkt, byt, xsa, xda, resolve_real_ip(sa, xsa), resolve_real_ip(da, xda)))
        self.conn.commit()

    def load_sample(self):
        # Basado en flujos reales del 8-nov (direcciones, puertos y bytes reales)
        self.add(2, 'TCP', '192.168.154.3', 22, '192.168.100.14', 49530, 8593, WAN, '192.168.100.14')      # SNAT
        self.add(2, 'TCP', '192.168.100.14', 49516, WAN, 22403, 5788, '192.168.100.14', '192.168.154.3')   # DNAT
        self.add(3, 'TCP', '192.168.154.3', 33208, '192.168.153.4', 514, 5032, '192.168.154.3', '192.168.153.4')
        # puerto de origen efímero ilustrativo (en la captura real solo se vio el destino, 58068)
        self.add(4, 'TCP', '192.168.153.3', 60123, '192.168.100.14', 58068, 691969, '192.168.153.3', '192.168.100.14')
        self.add(200, 'UDP', '192.168.154.3', 34405, '8.8.8.8', 53, 100, '192.168.154.3', '8.8.8.8')       # fuera de 60 min

    def test_hosts_are_internal_and_wan_never_appears(self):
        self.load_sample()
        r = flows_api.flows_top(minutes=60, by='hosts')
        hosts = {h['host']: h for h in r['data']}
        self.assertEqual(set(hosts), {'192.168.153.3', '192.168.154.3', '192.168.153.4'})
        self.assertNotIn(WAN, hosts)
        self.assertEqual(r['data'][0]['host'], '192.168.153.3')           # el de más bytes va primero
        h = hosts['192.168.154.3']
        self.assertEqual((h['bytes_out'], h['bytes_in'], h['total_bytes']), (8593 + 5032, 5788, 8593 + 5032 + 5788))
        self.assertEqual(r['status'], 'ok')

    def test_scope_all_includes_external_hosts(self):
        self.load_sample()
        r = flows_api.flows_top(minutes=60, by='hosts', scope='all')
        self.assertIn('192.168.100.14', {h['host'] for h in r['data']})

    def test_window_excludes_old_flows(self):
        self.load_sample()
        r60 = flows_api.flows_top(minutes=60, by='pairs', limit=50)
        r1440 = flows_api.flows_top(minutes=1440, by='pairs', limit=50)
        self.assertNotIn('8.8.8.8', {p['dst'] for p in r60['data']})
        self.assertIn('8.8.8.8', {p['dst'] for p in r1440['data']})

    def test_pairs_use_real_addresses(self):
        self.load_sample()
        r = flows_api.flows_top(minutes=60, by='pairs', limit=50)
        pairs = {(p['src'], p['dst']): p['total_bytes'] for p in r['data']}
        self.assertEqual(pairs[('192.168.154.3', '192.168.100.14')], 8593)
        self.assertEqual(pairs[('192.168.100.14', '192.168.154.3')], 5788)
        self.assertTrue(all(WAN not in pair for pair in pairs))

    def test_ports_group_by_service_port(self):
        self.load_sample()
        r = flows_api.flows_top(minutes=60, by='ports', limit=50)
        ports = {p['port']: p['total_bytes'] for p in r['data']}
        self.assertEqual(ports[58068], 691969)
        self.assertEqual(ports[514], 5032)
        self.assertEqual(ports[22], 8593)
        self.assertEqual(ports[22403], 5788)   # limitación conocida: en DNAT se ve el puerto externo

    def test_service_port_heuristic_limitation(self):
        # Limitación conocida: si el puerto de servicio es MAYOR que el efímero del cliente, se elige el efímero.
        self.add(1, 'TCP', '192.168.153.3', 40000, '192.168.100.14', 58068, 1000, '192.168.153.3', '192.168.100.14')
        r = flows_api.flows_top(minutes=60, by='ports', limit=50)
        self.assertIn(40000, {p['port'] for p in r['data']})

    def test_host_conversations(self):
        self.load_sample()
        r = flows_api.flows_host('192.168.154.3', minutes=60)
        rows = {(c['peer'], c['port']): c for c in r['data']}
        self.assertEqual(rows[('192.168.100.14', 22)]['bytes_out'], 8593)
        self.assertEqual(rows[('192.168.100.14', 22403)]['bytes_in'], 5788)
        self.assertEqual(rows[('192.168.153.4', 514)]['bytes_out'], 5032)
        self.assertEqual(len(r['data']), 3)

    def test_empty_table_says_no_data(self):
        r = flows_api.flows_top(minutes=60, by='hosts')
        self.assertEqual((r['status'], r['data']), ('no_data', []))

    def test_quiet_window_says_no_traffic(self):
        self.load_sample()
        r = flows_api.flows_top(minutes=1, by='pairs')       # el último flujo tiene 2 min
        self.assertEqual((r['status'], r['data']), ('no_traffic', []))

    def test_old_data_says_stale(self):
        self.add(200, 'UDP', '192.168.154.3', 34405, '8.8.8.8', 53, 100, '192.168.154.3', '8.8.8.8')
        r = flows_api.flows_top(minutes=1440, by='pairs')
        self.assertEqual(r['status'], 'stale')
        self.assertEqual(len(r['data']), 1)

    def test_invalid_parameters_are_rejected(self):
        for kwargs in ({'by': 'nada'}, {'scope': 'nada'}, {'minutes': 0}, {'minutes': 99999}):
            with self.assertRaises(HTTPException):
                flows_api.flows_top(**kwargs)
        with self.assertRaises(HTTPException):
            flows_api.flows_host('no-es-una-ip')


if __name__ == '__main__':
    unittest.main()
