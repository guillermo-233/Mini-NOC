import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

import logs_api

ROUTER = '192.168.153.1'
OTHER = '192.168.154.1'


def ts_ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


class TestLogsApi(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        logs_api.DB_FILE = self.path
        logs_api.service_state = lambda name: 'active'      # no depende de systemd
        self.conn = sqlite3.connect(self.path)
        self.conn.execute('''CREATE TABLE syslog_data (id INTEGER PRIMARY KEY AUTOINCREMENT,
                             timestamp TEXT, source_ip TEXT, severity TEXT, message TEXT)''')
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def add(self, minutes_ago, host, severity, message):
        self.conn.execute('INSERT INTO syslog_data (timestamp, source_ip, severity, message) VALUES (?, ?, ?, ?)',
                          (ts_ago(minutes_ago), host, severity, message))
        self.conn.commit()

    def load_sample(self):
        # Mensajes reales del router; el resto, ilustrativos
        self.add(5, ROUTER, 'local0.err', 'login failure for user fakeuser from 192.168.153.4 via ssh')
        self.add(4, ROUTER, 'local0.err', 'login failure for user fakeuser from 192.168.153.4 via ssh')
        self.add(3, ROUTER, 'local0.info', 'user admin logged in from 192.168.100.14 via ssh')
        self.add(2, OTHER, 'local0.warning', 'interface test-down link down')
        self.add(1, OTHER, 'local0.info', 'Prueba de syslog desde el router')
        self.add(3000, ROUTER, 'local0.info', 'evento antiguo fuera de 24 h')

    def counts(self, r):
        return {c['severity']: c['count'] for c in r['counts']}

    def test_latest_first_and_window(self):
        self.load_sample()
        r = logs_api.get_logs(minutes=1440)
        self.assertEqual(r['total_matching'], 5)                       # el evento de hace 3000 min queda fuera
        self.assertEqual(r['data'][0]['message'], 'Prueba de syslog desde el router')
        self.assertEqual(r['status'], 'ok')

    def test_counts_per_severity(self):
        self.load_sample()
        self.assertEqual(self.counts(logs_api.get_logs(minutes=1440)), {'err': 2, 'warning': 1, 'info': 2})

    def test_filter_by_severity_keeps_counts_of_other_levels(self):
        self.load_sample()
        r = logs_api.get_logs(minutes=1440, severity='err')
        self.assertEqual(r['total_matching'], 2)
        self.assertTrue(all(e['severity'] == 'err' for e in r['data']))
        self.assertEqual(self.counts(r), {'err': 2, 'warning': 1, 'info': 2})   # el recuento ignora el filtro de severidad

    def test_severity_aliases_and_multiple_levels(self):
        self.load_sample()
        r = logs_api.get_logs(minutes=1440, severity='error, warn')
        self.assertEqual(r['total_matching'], 3)
        self.assertEqual(r['filters']['severity'], ['err', 'warning'])

    def test_filter_by_host(self):
        self.load_sample()
        r = logs_api.get_logs(minutes=1440, host=OTHER)
        self.assertEqual(r['total_matching'], 2)
        self.assertEqual(self.counts(r), {'warning': 1, 'info': 1})
        self.assertEqual(r['hosts'], [ROUTER, OTHER])          # la lista de hosts no depende del filtro de host

    def test_text_filter_is_case_insensitive_and_literal(self):
        self.load_sample()
        self.assertEqual(logs_api.get_logs(minutes=1440, q='LOGIN FAILURE')['total_matching'], 2)
        self.assertEqual(logs_api.get_logs(minutes=1440, q='100%')['total_matching'], 0)   # % no es comodín
        self.assertEqual(logs_api.get_logs(minutes=1440, q="'; DROP TABLE syslog_data; --")['total_matching'], 0)
        self.conn.execute('SELECT COUNT(*) FROM syslog_data').fetchone()                    # la tabla sigue ahí

    def test_combined_filters(self):
        self.load_sample()
        r = logs_api.get_logs(minutes=1440, host=ROUTER, severity='info', q='admin')
        self.assertEqual(r['total_matching'], 1)

    def test_limit(self):
        self.load_sample()
        r = logs_api.get_logs(minutes=1440, limit=2)
        self.assertEqual(len(r['data']), 2)
        self.assertEqual(r['total_matching'], 5)

    def test_empty_table_says_no_data(self):
        r = logs_api.get_logs()
        self.assertEqual((r['status'], r['data']), ('no_data', []))

    def test_quiet_window_says_no_events(self):
        self.add(3000, ROUTER, 'local0.info', 'evento antiguo')
        r = logs_api.get_logs(minutes=60)
        self.assertEqual(r['status'], 'no_events')
        self.assertIsNotNone(r['freshness'])                           # hay un último evento, aunque fuera de la ventana

    def test_filters_without_match_say_no_match(self):
        self.load_sample()
        r = logs_api.get_logs(minutes=1440, q='texto que no existe')
        self.assertEqual((r['status'], r['total_matching']), ('no_match', 0))

    def test_facility_and_level_are_split(self):
        self.load_sample()
        e = logs_api.get_logs(minutes=1440, severity='warning')['data'][0]
        self.assertEqual((e['facility'], e['severity']), ('local0', 'warning'))

    def test_collector_state_is_reported(self):
        logs_api.service_state = lambda name: 'inactive'
        self.assertEqual(logs_api.get_logs()['collector'], 'inactive')

    def test_invalid_parameters_are_rejected(self):
        for kwargs in ({'severity': 'nada'}, {'host': 'no-es-ip'}, {'minutes': 0}, {'minutes': 99999}):
            with self.assertRaises(HTTPException):
                logs_api.get_logs(**kwargs)


if __name__ == '__main__':
    unittest.main()
