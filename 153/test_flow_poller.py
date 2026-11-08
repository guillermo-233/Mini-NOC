import unittest
from flow_poller import parse_line, resolve_real_ip

HEADER = 'firstSeen,lastSeen,proto,srcAddr,srcPort,dstAddr,dstPort,packets,bytes,input,output,srcXIP,dstXIP'
LINE = '2026-10-23 08:51:43.864,2026-10-23 08:51:43.864,17,192.168.100.55,5678,255.255.255.255,5678,1,175,6,0,192.168.100.55,255.255.255.255'

# Basadas en flujos reales (19:31 del 8-nov). Direcciones, puertos y bytes son reales; paquetes e interfaces, ilustrativos.
# SNAT: respuesta SSH de d13-basic-154 hacia el exterior; el router la traduce a su dirección WAN (192.168.100.56).
LINE_SNAT = '2026-11-08 19:31:53.138,2026-11-08 19:31:53.138,6,192.168.154.3,22,192.168.100.14,49530,10,8593,3,2,192.168.100.56,192.168.100.14'
# DNAT: tráfico de entrada a la dirección WAN del router (puerto 22403) que se reenvía a d13-basic-154.
LINE_DNAT = '2026-11-08 19:31:52.738,2026-11-08 19:31:52.738,6,192.168.100.14,49516,192.168.100.56,22403,8,5788,2,3,192.168.100.14,192.168.154.3'


class TestParseLine(unittest.TestCase):
    def test_header_is_ignored(self):
        self.assertIsNone(parse_line(HEADER))

    def test_summary_and_blank_lines_are_ignored(self):
        self.assertIsNone(parse_line(''))
        self.assertIsNone(parse_line('Summary'))
        self.assertIsNone(parse_line('Time window: 2026-10-23 08:51:34 - 2026-10-23 08:52:33'))

    def test_truncated_line_is_ignored(self):
        self.assertIsNone(parse_line(','.join(LINE.split(',')[:12])))

    def test_non_numeric_port_is_ignored(self):
        self.assertIsNone(parse_line(LINE.replace(',5678,255', ',abc,255', 1)))

    def test_normal_line(self):
        row = parse_line(LINE)
        ts, te, pr, sa, sp, da, dp, pkt, byt, in_if, out_if, xsa, xda, real_src, real_dst = row
        self.assertEqual(ts, '2026-10-23 08:51:43.864')
        self.assertEqual(pr, 'UDP')
        self.assertEqual((sa, sp, da, dp), ('192.168.100.55', 5678, '255.255.255.255', 5678))
        self.assertEqual((pkt, byt, in_if, out_if), (1, 175, 6, 0))
        self.assertEqual(real_src, '192.168.100.55')

    def test_snat_flow_is_attributed_to_internal_host(self):
        row = parse_line(LINE_SNAT)
        self.assertEqual(row[13], '192.168.154.3')   # real_src: el host interno, NO la WAN del router
        self.assertEqual(row[14], '192.168.100.14')  # real_dst: externo

    def test_dnat_flow_is_attributed_to_internal_host(self):
        row = parse_line(LINE_DNAT)
        self.assertEqual(row[13], '192.168.100.14')  # real_src: externo
        self.assertEqual(row[14], '192.168.154.3')   # real_dst: el host interno, NO la WAN del router


class TestResolveRealIp(unittest.TestCase):
    def test_no_nat_internal_to_internal(self):
        self.assertEqual(resolve_real_ip('192.168.154.3', '192.168.154.3'), '192.168.154.3')

    def test_snat_keeps_internal_original(self):
        self.assertEqual(resolve_real_ip('192.168.154.3', '192.168.100.56'), '192.168.154.3')

    def test_dnat_uses_internal_translated(self):
        self.assertEqual(resolve_real_ip('192.168.100.56', '192.168.154.3'), '192.168.154.3')

    def test_external_without_translation(self):
        self.assertEqual(resolve_real_ip('192.168.100.14', '192.168.100.14'), '192.168.100.14')

    def test_zero_translation_keeps_original(self):
        self.assertEqual(resolve_real_ip('8.8.8.8', '0.0.0.0'), '8.8.8.8')
        self.assertEqual(resolve_real_ip('8.8.8.8', ''), '8.8.8.8')


if __name__ == '__main__':
    unittest.main()
