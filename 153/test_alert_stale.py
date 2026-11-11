import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import alert_engine


def iso_ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def flow_ts_ago(seconds):
    t = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return t.strftime('%Y-%m-%d %H:%M:%S.') + '%03d' % (t.microsecond // 1000)


class AlertTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        alert_engine.DB_PATH = self.path
        alert_engine.init_db()
        alert_engine._down_counts.clear()
        for name in ('NOC_STALE_SNMP', 'NOC_STALE_IPFIX', 'NOC_STALE_SYSLOG'):
            os.environ.pop(name, None)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute('CREATE TABLE snmp_data (timestamp TEXT)')
        self.conn.execute('CREATE TABLE flow_data (ts TEXT)')
        self.conn.execute('CREATE TABLE syslog_data (timestamp TEXT)')
        self.conn.commit()

    def tearDown(self):
        for name in ('NOC_STALE_SNMP', 'NOC_STALE_IPFIX', 'NOC_STALE_SYSLOG'):
            os.environ.pop(name, None)
        self.conn.close()
        os.remove(self.path)

    def alerts(self, rule):
        return self.conn.execute('SELECT entity, status FROM alerts WHERE rule_name = ? ORDER BY id', (rule,)).fetchall()

    def message(self, rule):
        return self.conn.execute('SELECT message FROM alerts WHERE rule_name = ? ORDER BY id DESC LIMIT 1', (rule,)).fetchone()[0]


class TestStaleData(AlertTestCase):
    def test_fresh_sources_do_not_alert(self):
        self.conn.execute('INSERT INTO snmp_data VALUES (?)', (iso_ago(10),))
        self.conn.execute('INSERT INTO flow_data VALUES (?)', (flow_ts_ago(30),))   # formato de flujos, hace 30 s
        self.conn.commit()
        alert_engine.check_no_data()
        self.assertEqual(self.alerts('No Data'), [])

    def test_stale_snmp_fires_once_and_resolves(self):
        self.conn.execute('INSERT INTO snmp_data VALUES (?)', (iso_ago(600),))
        self.conn.execute('INSERT INTO flow_data VALUES (?)', (flow_ts_ago(10),))
        self.conn.commit()
        alert_engine.check_no_data()
        alert_engine.check_no_data()                                   # segunda evaluación: no duplica
        self.assertEqual(self.alerts('No Data'), [('SNMP Poller', 'firing')])
        self.assertIn('SNMP', self.message('No Data'))
        self.assertIn('10 min', self.message('No Data'))

        self.conn.execute('INSERT INTO snmp_data VALUES (?)', (iso_ago(5),))   # vuelven los datos
        self.conn.commit()
        alert_engine.check_no_data()
        self.assertEqual(self.alerts('No Data'), [('SNMP Poller', 'resolved')])

    def test_stale_flows_uses_flow_timestamp_format(self):
        self.conn.execute('INSERT INTO snmp_data VALUES (?)', (iso_ago(5),))
        self.conn.execute('INSERT INTO flow_data VALUES (?)', (flow_ts_ago(2 * 86400 + 3 * 3600),))   # como el incidente real: > 1 día
        self.conn.commit()
        alert_engine.check_no_data()
        self.assertEqual(self.alerts('No Data'), [('IPFIX Collector', 'firing')])
        self.assertIn('2 d 3 h', self.message('No Data'))

    def test_empty_tables_alert_for_enabled_sources_only(self):
        alert_engine.check_no_data()
        self.assertEqual(self.alerts('No Data'), [('SNMP Poller', 'firing'), ('IPFIX Collector', 'firing')])
        self.assertIn('ningún dato', self.message('No Data'))

    def test_missing_table_is_treated_as_no_data(self):
        self.conn.execute('DROP TABLE flow_data')
        self.conn.execute('INSERT INTO snmp_data VALUES (?)', (iso_ago(5),))
        self.conn.commit()
        alert_engine.check_no_data()
        self.assertEqual(self.alerts('No Data'), [('IPFIX Collector', 'firing')])

    def test_syslog_is_disabled_by_default(self):
        self.conn.execute('INSERT INTO snmp_data VALUES (?)', (iso_ago(5),))
        self.conn.execute('INSERT INTO flow_data VALUES (?)', (flow_ts_ago(5),))
        self.conn.execute('INSERT INTO syslog_data VALUES (?)', (iso_ago(30 * 86400),))   # silencio de un mes: normal
        self.conn.commit()
        alert_engine.check_no_data()
        self.assertEqual(self.alerts('No Data'), [])

    def test_syslog_can_be_enabled_by_environment(self):
        os.environ['NOC_STALE_SYSLOG'] = '3600'
        self.conn.execute('INSERT INTO snmp_data VALUES (?)', (iso_ago(5),))
        self.conn.execute('INSERT INTO flow_data VALUES (?)', (flow_ts_ago(5),))
        self.conn.execute('INSERT INTO syslog_data VALUES (?)', (iso_ago(2 * 3600),))
        self.conn.commit()
        alert_engine.check_no_data()
        self.assertEqual(self.alerts('No Data'), [('Syslog Collector', 'firing')])

    def test_threshold_is_configurable(self):
        os.environ['NOC_STALE_SNMP'] = '1200'                          # 20 min: 10 min sin datos ya no es obsoleto
        self.conn.execute('INSERT INTO snmp_data VALUES (?)', (iso_ago(600),))
        self.conn.execute('INSERT INTO flow_data VALUES (?)', (flow_ts_ago(5),))
        self.conn.commit()
        alert_engine.check_no_data()
        self.assertEqual(self.alerts('No Data'), [])

    def test_disabling_a_rule_resolves_its_open_alert(self):
        self.conn.execute("INSERT INTO alerts (rule_name, severity, status, entity, message, start_time) "
                          "VALUES ('No Data', 'CRITICAL', 'firing', 'IPFIX Collector', 'x', '2026-11-08 20:00:00')")
        self.conn.execute('INSERT INTO snmp_data VALUES (?)', (iso_ago(5),))
        self.conn.commit()
        os.environ['NOC_STALE_IPFIX'] = 'off'
        alert_engine.check_no_data()
        self.assertEqual(self.alerts('No Data'), [('IPFIX Collector', 'resolved')])


class TestHelpers(unittest.TestCase):
    def test_stale_threshold_parsing(self):
        os.environ.pop('NOC_X', None)
        self.assertEqual(alert_engine.stale_threshold(300, 'NOC_X'), 300)
        for value, expected in (('600', 600), ('off', 0), ('0', 0), ('', 0), ('abc', 300), ('-5', 0)):
            os.environ['NOC_X'] = value
            self.assertEqual(alert_engine.stale_threshold(300, 'NOC_X'), expected, value)
        os.environ.pop('NOC_X', None)

    def test_format_duration(self):
        self.assertEqual(alert_engine.format_duration(45), '45 s')
        self.assertEqual(alert_engine.format_duration(600), '10 min')
        self.assertEqual(alert_engine.format_duration(3900), '65 min')
        self.assertEqual(alert_engine.format_duration(11100), '3 h 5 min')
        self.assertEqual(alert_engine.format_duration(7200), '2 h')
        self.assertEqual(alert_engine.format_duration(93784 + 86400), '2 d 2 h')

    def test_run_checks_isolates_failures(self):
        calls = []

        def boom():
            raise RuntimeError('fallo simulado')

        def ok():
            calls.append('ok')

        alert_engine.run_checks([boom, ok])
        self.assertEqual(calls, ['ok'])                                 # la regla que falla no impide las demás


class TestCollectorDown(AlertTestCase):
    def setUp(self):
        super().setUp()
        self.states = {'snmp-poller': 'active', 'syslog-poller': 'active', 'flow-poller': 'active'}
        alert_engine.service_state = lambda name: self.states[name]

    def test_needs_two_consecutive_observations_then_fires_once(self):
        self.states['syslog-poller'] = 'inactive'
        alert_engine.check_collectors_down()
        self.assertEqual(self.alerts('Collector Down'), [])             # una sola vez puede ser un reinicio
        alert_engine.check_collectors_down()
        alert_engine.check_collectors_down()
        self.assertEqual(self.alerts('Collector Down'), [('syslog-poller', 'firing')])
        self.assertIn('inactive', self.message('Collector Down'))

    def test_resolves_when_service_returns(self):
        self.states['flow-poller'] = 'failed'
        for _ in range(3):
            alert_engine.check_collectors_down()
        self.states['flow-poller'] = 'active'
        alert_engine.check_collectors_down()
        self.assertEqual(self.alerts('Collector Down'), [('flow-poller', 'resolved')])

    def test_short_restart_does_not_alert(self):
        self.states['snmp-poller'] = 'activating'
        alert_engine.check_collectors_down()
        self.states['snmp-poller'] = 'active'
        alert_engine.check_collectors_down()
        self.states['snmp-poller'] = 'activating'
        alert_engine.check_collectors_down()
        self.assertEqual(self.alerts('Collector Down'), [])             # las observaciones no son seguidas

    def test_unknown_state_is_ignored(self):
        self.states['syslog-poller'] = 'unknown'
        for _ in range(4):
            alert_engine.check_collectors_down()
        self.assertEqual(self.alerts('Collector Down'), [])


if __name__ == '__main__':
    unittest.main()
