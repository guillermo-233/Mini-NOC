import unittest
from flow_poller import parse_line, resolve_real_ip

HEADER = 'firstSeen,lastSeen,proto,srcAddr,srcPort,dstAddr,dstPort,packets,bytes,input,output,srcXIP,dstXIP'
LINE = '2026-10-23 08:51:43.864,2026-10-23 08:51:43.864,17,192.168.100.55,5678,255.255.255.255,5678,1,175,6,0,192.168.100.55,255.255.255.255'


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


class TestResolveRealIp(unittest.TestCase):
    def test_no_translation_keeps_original(self):
        self.assertEqual(resolve_real_ip('10.0.0.5', '0.0.0.0'), '10.0.0.5')

    def test_translation_uses_translated_address(self):
        self.assertEqual(resolve_real_ip('1.2.3.4', '10.0.0.5'), '10.0.0.5')


if __name__ == '__main__':
    unittest.main()
