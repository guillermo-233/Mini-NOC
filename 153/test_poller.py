import sqlite3
import unittest

from poller import calculate_rates, ensure_schema, parse_uptime_ticks, record_device_status


class TestPollerRates(unittest.TestCase):
    def test_rate_calculation_net153(self):
        # Muestras reales de net153 tomadas con 15 s de diferencia
        sample1 = {'3': {'descr': 'net153',
                         'hc_in_octets': '5664186584',
                         'hc_out_octets': '2960408715'}}
        sample2 = {'3': {'descr': 'net153',
                         'hc_in_octets': '5664476409',
                         'hc_out_octets': '2961264338'}}

        rates = calculate_rates(sample1, sample2, interval_seconds=15)

        # IN: (5664476409 - 5664186584) * 8 / 15 = 154573.33 bps
        self.assertAlmostEqual(rates['3']['in_bps'], 154573.33, places=2)
        # OUT: (2961264338 - 2960408715) * 8 / 15 = 456332.27 bps
        self.assertAlmostEqual(rates['3']['out_bps'], 456332.27, places=2)

    def test_uses_real_elapsed_time(self):
        # 1 MB en 15.7 s reales: 1_000_000 * 8 / 15.7 = 509554.14 bps (con 15 s saldría 533333.33, un 4.7 % más)
        sample1 = {'1': {'hc_in_octets': '0', 'hc_out_octets': '0'}}
        sample2 = {'1': {'hc_in_octets': '1000000', 'hc_out_octets': '1000000'}}

        rates = calculate_rates(sample1, sample2, interval_seconds=15.7)

        self.assertAlmostEqual(rates['1']['in_bps'], 509554.14, places=2)
        self.assertAlmostEqual(rates['1']['out_bps'], 509554.14, places=2)

    def test_counter_reset_gives_no_rate(self):
        # Reset del router: el contador de entrada baja. No debe haber tasa (None).
        # El contador de salida sigue subiendo con normalidad: (800 - 500) * 8 / 15 = 160 bps
        sample1 = {'1': {'hc_in_octets': '1000000', 'hc_out_octets': '500'}}
        sample2 = {'1': {'hc_in_octets': '5000', 'hc_out_octets': '800'}}

        rates = calculate_rates(sample1, sample2, interval_seconds=15)

        self.assertIsNone(rates['1']['in_bps'])
        self.assertAlmostEqual(rates['1']['out_bps'], 160.0, places=2)

    def test_idle_interface_is_zero_not_none(self):
        # Sin tráfico (contador igual) la tasa es 0, no None
        sample1 = {'1': {'hc_in_octets': '1000', 'hc_out_octets': '1000'}}
        sample2 = {'1': {'hc_in_octets': '1000', 'hc_out_octets': '1000'}}

        rates = calculate_rates(sample1, sample2, interval_seconds=15)

        self.assertEqual(rates['1']['in_bps'], 0.0)
        self.assertEqual(rates['1']['out_bps'], 0.0)

    def test_non_positive_interval_gives_no_rate(self):
        sample1 = {'1': {'hc_in_octets': '0', 'hc_out_octets': '0'}}
        sample2 = {'1': {'hc_in_octets': '100', 'hc_out_octets': '100'}}

        rates = calculate_rates(sample1, sample2, interval_seconds=0)

        self.assertIsNone(rates['1']['in_bps'])


class TestUptime(unittest.TestCase):
    def test_plain_ticks(self):
        self.assertEqual(parse_uptime_ticks('123456'), 123456)

    def test_formatted_with_days(self):
        # 1 día + 2:03:04 = 93784 s -> 9378400 centésimas, más 5 centésimas
        self.assertEqual(parse_uptime_ticks('1 day, 2:03:04.05'), 9378405)
        self.assertEqual(parse_uptime_ticks('12 days, 0:00:00'), 12 * 86400 * 100)

    def test_formatted_without_days(self):
        self.assertEqual(parse_uptime_ticks('0:00:05.00'), 500)

    def test_garbage_gives_none(self):
        self.assertIsNone(parse_uptime_ticks('no es un uptime'))
        self.assertIsNone(parse_uptime_ticks(''))


class TestDeviceStatus(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        ensure_schema(self.conn)

    def row(self):
        return self.conn.execute(
            'SELECT last_attempt, last_success, reachable, uptime_seconds, consecutive_failures, last_error '
            'FROM snmp_device WHERE device = ?', ('r1',)).fetchone()

    def test_first_success(self):
        record_device_status(self.conn, 'r1', 't1', True, 1000.0, None)
        self.assertEqual(self.row(), ('t1', 't1', 1, 1000.0, 0, None))

    def test_first_attempt_failing(self):
        record_device_status(self.conn, 'r1', 't1', False, None, 'sin respuesta')
        self.assertEqual(self.row(), ('t1', None, 0, None, 1, 'sin respuesta'))

    def test_failure_keeps_last_success_and_uptime_and_counts(self):
        record_device_status(self.conn, 'r1', 't1', True, 1000.0, None)
        record_device_status(self.conn, 'r1', 't2', False, None, 'sin respuesta')
        record_device_status(self.conn, 'r1', 't3', False, None, 'sin respuesta')
        self.assertEqual(self.row(), ('t3', 't1', 0, 1000.0, 2, 'sin respuesta'))

    def test_recovery_resets_failures(self):
        record_device_status(self.conn, 'r1', 't1', False, None, 'sin respuesta')
        record_device_status(self.conn, 'r1', 't2', True, 50.0, None)
        self.assertEqual(self.row(), ('t2', 't2', 1, 50.0, 0, None))

    def test_one_row_per_device(self):
        for t in ('t1', 't2', 't3'):
            record_device_status(self.conn, 'r1', t, True, 1.0, None)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM snmp_device').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
