import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

import alert_engine


def add_sample(conn, descr, oper_status):
    conn.execute(
        'INSERT INTO snmp_data (timestamp, if_index, descr, admin_status, oper_status, in_bytes, out_bytes, in_bps, out_bps) '
        'VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0)',
        (datetime.now(timezone.utc).isoformat(), descr, descr, oper_status, oper_status))


class TestInterfaceDown(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        alert_engine.DB_PATH = self.path
        alert_engine.init_db()
        conn = sqlite3.connect(self.path)
        conn.execute('''CREATE TABLE snmp_data (timestamp TEXT, if_index TEXT, descr TEXT, admin_status TEXT,
                        oper_status TEXT, in_bytes INTEGER, out_bytes INTEGER, in_bps REAL, out_bps REAL)''')
        conn.commit()
        self.conn = conn

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def alerts(self):
        return self.conn.execute('SELECT entity, status FROM alerts ORDER BY id').fetchall()

    def test_ignored_interface_does_not_fire(self):
        add_sample(self.conn, 'ether4', '2')
        self.conn.commit()
        alert_engine.check_interface_status()
        self.assertEqual(self.alerts(), [])

    def test_ignored_interface_open_alert_is_resolved(self):
        self.conn.execute("INSERT INTO alerts (rule_name, severity, status, entity, message, start_time) "
                          "VALUES ('Interface Down', 'CRITICAL', 'firing', 'ether4', 'x', '2026-11-08 17:45:41')")
        add_sample(self.conn, 'ether4', '2')
        self.conn.commit()
        alert_engine.check_interface_status()
        self.assertEqual(self.alerts(), [('ether4', 'resolved')])

    def test_other_interface_fires_once_and_resolves(self):
        add_sample(self.conn, 'test-down', '2')
        self.conn.commit()
        alert_engine.check_interface_status()
        alert_engine.check_interface_status()          # segunda evaluación: no duplica
        self.assertEqual(self.alerts(), [('test-down', 'firing')])

        self.conn.execute("DELETE FROM snmp_data")
        add_sample(self.conn, 'test-down', '1')         # vuelve a estar up
        self.conn.commit()
        alert_engine.check_interface_status()
        self.assertEqual(self.alerts(), [('test-down', 'resolved')])


if __name__ == '__main__':
    unittest.main()
