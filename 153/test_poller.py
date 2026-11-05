import unittest
from poller import calculate_rates

class TestPollerRates(unittest.TestCase):
    def test_rate_calculation_net153(self):
        # Datos extraídos de tu primera captura (t=0)
        sample1 = {
            '3': {
                'descr': 'net153',
                'hc_in_octets': '5664186584',
                'hc_out_octets': '2960408715'
            }
        }
        
        # Datos extraídos de ifxtable2.txt (t=15)[cite: 3]
        sample2 = {
            '3': {
                'descr': 'net153',
                'hc_in_octets': '5664476409',
                'hc_out_octets': '2961264338'
            }
        }
        
        rates = calculate_rates(sample1, sample2, interval_seconds=15)
        
        # Verificación IN: (5664476409 - 5664186584) * 8 / 15 = 289825 * 8 / 15 = 154573.33 bps
        self.assertAlmostEqual(rates['3']['in_bps'], 154573.33, places=2)
        
        # Verificación OUT: (2961264338 - 2960408715) * 8 / 15 = 855623 * 8 / 15 = 456332.26 bps
        self.assertAlmostEqual(rates['3']['out_bps'], 456332.27, places=2)

    def test_counter_wrap_around(self):
        # Simulación de un contador de 64 bits reiniciándose
        sample1 = {'1': {'hc_in_octets': str((2**64) - 1000), 'hc_out_octets': '0'}}
        sample2 = {'1': {'hc_in_octets': '5000', 'hc_out_octets': '0'}}
        
        rates = calculate_rates(sample1, sample2, interval_seconds=15)
        
        # Diferencia real de bytes: 1000 hasta el límite + 5000 tras el reinicio = 6000 bytes
        # 6000 bytes * 8 bits / 15 seg = 3200.0 bps
        self.assertEqual(rates['1']['in_bps'], 3200.0)

if __name__ == '__main__':
    unittest.main()
