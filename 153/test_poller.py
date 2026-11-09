import unittest
from poller import calculate_rates

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

if __name__ == '__main__':
    unittest.main()
