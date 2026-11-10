import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import device_api


def iso_ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


class TestDeviceApi(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        device_api.DB_FILE = self.path
        device_api.service_state = lambda name: 'active'
        self.conn = sqlite3.connect(self.path)
        self.conn.execute('''CREATE TABLE snmp_data (timestamp TEXT, if_index TEXT, descr TEXT, admin_status TEXT,
                             oper_status TEXT, in_bytes INTEGER, out_bytes INTEGER, in_bps REAL, out_bps REAL)''')
        self.conn.execute('''CREATE TABLE snmp_device (device TEXT PRIMARY KEY, last_attempt TEXT, last_success TEXT,
                             reachable INTEGER, uptime_seconds REAL, consecutive_failures INTEGER, last_error TEXT)''')
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def device(self, attempt_ago=5, success_ago=5, reachable=1, uptime=3600.0, failures=0, error=None):
        self.conn.execute('INSERT OR REPLACE INTO snmp_device VALUES (?, ?, ?, ?, ?, ?, ?)',
                          ('192.168.153.1', iso_ago(attempt_ago), iso_ago(success_ago) if success_ago is not None else None,
                           reachable, uptime, failures, error))
        self.conn.commit()

    def sample(self, seconds_ago, if_index, descr, admin, oper, in_bps=100.0, out_bps=200.0):
        self.conn.execute('INSERT INTO snmp_data VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)',
                          (iso_ago(seconds_ago), if_index, descr, admin, oper, in_bps, out_bps))
        self.conn.commit()

    def test_reachable_with_uptime_and_boot_time(self):
        self.device(uptime=90061.0)                           # 1 día, 1 h, 1 min, 1 s
        r = device_api.get_device()
        self.assertEqual((r['status'], r['reachable'], r['device']), ('reachable', True, '192.168.153.1'))
        self.assertEqual(r['uptime_seconds'], 90061.0)
        boot = datetime.fromisoformat(r['boot_time'])
        expected = datetime.now(timezone.utc) - timedelta(seconds=90061)
        self.assertLess(abs((boot - expected).total_seconds()), 5)
        self.assertLessEqual(r['success_age_seconds'], 7)

    def test_unreachable_keeps_last_success(self):
        self.device(attempt_ago=3, success_ago=300, reachable=0, failures=4, error='sin respuesta SNMP')
        r = device_api.get_device()
        self.assertEqual((r['status'], r['reachable'], r['consecutive_failures']), ('unreachable', False, 4))
        self.assertGreaterEqual(r['success_age_seconds'], 299)
        self.assertIn('4 sondeos', r['message'])

    def test_stale_when_poller_stops_updating(self):
        self.device(attempt_ago=600, success_ago=600)
        self.assertEqual(device_api.get_device()['status'], 'stale')

    def test_collector_down_takes_precedence(self):
        self.device()
        device_api.service_state = lambda name: 'inactive'
        r = device_api.get_device()
        self.assertEqual((r['status'], r['collector']), ('collector_down', 'inactive'))

    def test_unknown_without_any_poll(self):
        r = device_api.get_device()
        self.assertEqual((r['status'], r['device'], r['interfaces']), ('unknown', None, []))

    def test_missing_tables_do_not_break(self):
        self.conn.execute('DROP TABLE snmp_device')
        self.conn.execute('DROP TABLE snmp_data')
        self.conn.commit()
        self.assertEqual(device_api.get_device()['status'], 'unknown')

    def test_interfaces_latest_sample_sorted_by_numeric_index(self):
        # Índices no consecutivos (Lab 1): 3, 4, 10, 12. El 10 va después del 4, no después del 1.
        for idx, name in (('10', 'WAN'), ('3', 'net153'), ('12', 'test-down'), ('4', 'net154')):
            self.sample(60, idx, name, '1', '1', in_bps=1.0)
            self.sample(5, idx, name, '1', '1', in_bps=2.0)       # la última muestra manda
        r = device_api.get_device()
        self.assertEqual([i['if_index'] for i in r['interfaces']], [3, 4, 10, 12])
        self.assertTrue(all(i['in_bps'] == 2.0 for i in r['interfaces']))

    def test_interface_states(self):
        self.sample(5, '1', 'net153', '1', '1')
        self.sample(5, '2', 'ether4', '2', '2')        # desactivada a propósito
        self.sample(5, '3', 'caida', '1', '2')         # debería estar arriba y no lo está
        r = {i['name']: i for i in device_api.get_device()['interfaces']}
        self.assertEqual(r['net153']['state'], 'up')
        self.assertEqual((r['ether4']['state'], r['ether4']['admin_status'], r['ether4']['oper_status']), ('admin_down', 'down', 'down'))
        self.assertEqual(r['caida']['state'], 'down')

    def test_interface_age_and_stale_flag(self):
        self.sample(5, '1', 'fresca', '1', '1')
        self.sample(500, '2', 'vieja', '1', '1')
        r = {i['name']: i for i in device_api.get_device()['interfaces']}
        self.assertFalse(r['fresca']['stale'])
        self.assertTrue(r['vieja']['stale'])
        self.assertGreaterEqual(r['vieja']['age_seconds'], 499)

    def test_reset_rate_is_reported_as_null(self):
        self.sample(5, '1', 'net153', '1', '1', in_bps=None, out_bps=10.0)
        i = device_api.get_device()['interfaces'][0]
        self.assertIsNone(i['in_bps'])
        self.assertEqual(i['out_bps'], 10.0)


if __name__ == '__main__':
    unittest.main()
