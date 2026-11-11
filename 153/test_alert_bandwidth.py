import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import alert_engine

MBIT = 1_000_000
ENV = ('NOC_BW_THRESHOLD_BPS', 'NOC_BW_MINUTES')


def iso_ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


class TestHighBandwidth(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        alert_engine.DB_PATH = self.path
        alert_engine.init_db()
        for name in ENV:
            os.environ.pop(name, None)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute('CREATE TABLE snmp_data (timestamp TEXT, descr TEXT, in_bps REAL, out_bps REAL)')
        self.conn.commit()

    def tearDown(self):
        for name in ENV:
            os.environ.pop(name, None)
        self.conn.close()
        os.remove(self.path)

    def samples(self, descr, in_values, out_values, spacing=15.7):
        """Una muestra cada `spacing` segundos hacia atrás desde ahora."""
        for i, (a, b) in enumerate(zip(in_values, out_values)):
            self.conn.execute('INSERT INTO snmp_data VALUES (?, ?, ?, ?)', (iso_ago(5 + i * spacing), descr, a, b))
        self.conn.commit()

    def alerts(self):
        return self.conn.execute('SELECT entity, status FROM alerts WHERE rule_name = ? ORDER BY id', ('High Bandwidth',)).fetchall()

    def message(self):
        return self.conn.execute('SELECT message FROM alerts ORDER BY id DESC LIMIT 1').fetchone()[0]

    def test_sustained_traffic_fires_once(self):
        self.samples('net154', [1000] * 7, [90 * MBIT] * 7)            # 7 muestras (~110 s) por encima de 80 Mbit/s
        alert_engine.check_high_bandwidth()
        alert_engine.check_high_bandwidth()                              # segunda evaluación: no duplica
        self.assertEqual(self.alerts(), [('net154', 'firing')])
        self.assertIn('salida', self.message())
        self.assertIn('90.00', self.message())

    def test_short_burst_does_not_fire_although_average_is_high(self):
        # La media sería ~88 Mbit/s (la regla antigua habría disparado), pero solo una muestra supera el umbral
        self.samples('net154', [0] * 7, [1 * MBIT] * 6 + [600 * MBIT])
        alert_engine.check_high_bandwidth()
        self.assertEqual(self.alerts(), [])

    def test_too_few_samples_does_not_conclude_anything(self):
        self.samples('net154', [0, 0], [90 * MBIT, 90 * MBIT])           # solo 2 muestras: el poller falló
        alert_engine.check_high_bandwidth()
        self.assertEqual(self.alerts(), [])

    def test_old_samples_outside_window_are_ignored(self):
        self.samples('net154', [0] * 7, [90 * MBIT] * 7, spacing=15.7)
        self.conn.execute("UPDATE snmp_data SET timestamp = ?", (iso_ago(600),))   # todo ocurrió hace 10 min
        self.conn.commit()
        alert_engine.check_high_bandwidth()
        self.assertEqual(self.alerts(), [])

    def test_reset_samples_are_not_counted_as_traffic(self):
        self.samples('net154', [0] * 8, [90 * MBIT, None, 90 * MBIT, 90 * MBIT, None, 90 * MBIT, 90 * MBIT, 90 * MBIT])
        alert_engine.check_high_bandwidth()
        self.assertEqual(self.alerts(), [('net154', 'firing')])

    def test_each_interface_is_independent(self):
        self.samples('net153', [1 * MBIT] * 7, [1 * MBIT] * 7)
        self.samples('net154', [90 * MBIT] * 7, [1 * MBIT] * 7)
        alert_engine.check_high_bandwidth()
        self.assertEqual(self.alerts(), [('net154', 'firing')])
        self.assertIn('entrada', self.message())

    def test_resolves_when_average_falls_below_threshold(self):
        self.samples('net154', [0] * 7, [90 * MBIT] * 7)
        alert_engine.check_high_bandwidth()
        self.conn.execute('DELETE FROM snmp_data')
        self.conn.commit()
        self.samples('net154', [0] * 7, [2 * MBIT] * 7)
        alert_engine.check_high_bandwidth()
        self.assertEqual(self.alerts(), [('net154', 'resolved')])

    def test_stays_firing_while_average_is_still_above_threshold(self):
        self.samples('net154', [0] * 7, [90 * MBIT] * 7)
        alert_engine.check_high_bandwidth()
        self.conn.execute('DELETE FROM snmp_data')
        self.conn.commit()
        self.samples('net154', [0] * 7, [70 * MBIT, 100 * MBIT, 100 * MBIT, 100 * MBIT, 100 * MBIT, 100 * MBIT, 100 * MBIT])
        alert_engine.check_high_bandwidth()                               # un valor bajo el umbral, pero la media sigue alta
        self.assertEqual(self.alerts(), [('net154', 'firing')])

    def test_keeps_state_when_there_is_no_data(self):
        self.samples('net154', [0] * 7, [90 * MBIT] * 7)
        alert_engine.check_high_bandwidth()
        self.conn.execute('DELETE FROM snmp_data')
        self.conn.commit()
        alert_engine.check_high_bandwidth()                               # sin muestras no se resuelve
        self.assertEqual(self.alerts(), [('net154', 'firing')])

    def test_threshold_and_duration_are_configurable(self):
        os.environ['NOC_BW_THRESHOLD_BPS'] = str(5 * MBIT)
        os.environ['NOC_BW_MINUTES'] = '1'
        self.samples('net154', [0] * 4, [8_800_000] * 4)                  # 4 muestras (~60 s) a 8.8 Mbit/s, como la descarga de prueba
        alert_engine.check_high_bandwidth()
        self.assertEqual(self.alerts(), [('net154', 'firing')])
        self.assertIn('1 min', self.message())

    def test_default_threshold_ignores_the_test_download(self):
        self.samples('net154', [0] * 7, [8_800_000] * 7)                  # 8.8 Mbit/s no llega a 80 Mbit/s
        alert_engine.check_high_bandwidth()
        self.assertEqual(self.alerts(), [])

    def test_invalid_environment_falls_back_to_defaults(self):
        os.environ['NOC_BW_THRESHOLD_BPS'] = 'mucho'
        os.environ['NOC_BW_MINUTES'] = '-3'
        self.assertEqual(alert_engine.positive_int_env('NOC_BW_THRESHOLD_BPS', 80_000_000), 80_000_000)
        self.assertEqual(alert_engine.positive_int_env('NOC_BW_MINUTES', 2), 2)


if __name__ == '__main__':
    unittest.main()
