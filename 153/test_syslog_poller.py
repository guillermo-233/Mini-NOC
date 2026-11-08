import unittest
from syslog_poller import parse_line, to_utc

REAL = '2026-11-03T19:32:42+00:00 192.168.153.1 MikroTik local0.err login failure for user nobody-here from 192.168.153.4 via ssh'
INFO = '2026-11-03T19:28:05+00:00 192.168.153.1 MikroTik local0.info Prueba de syslog desde el router'


class TestSyslog(unittest.TestCase):
    def test_winter_madrid_is_utc_plus_1(self):
        self.assertEqual(to_utc('2026-11-08T19:07:56+00:00'), '2026-11-08T18:07:56+00:00')

    def test_summer_madrid_is_utc_plus_2(self):
        self.assertEqual(to_utc('2026-07-01T12:00:00+00:00'), '2026-07-01T10:00:00+00:00')

    def test_parse_login_failure(self):
        ts, ip, severity, message = parse_line(REAL)
        self.assertEqual(ts, '2026-11-03T18:32:42+00:00')
        self.assertEqual(ip, '192.168.153.1')
        self.assertEqual(severity, 'local0.err')
        self.assertTrue(message.startswith('login failure for user nobody-here'))

    def test_parse_info_line(self):
        _, _, severity, message = parse_line(INFO)
        self.assertEqual(severity, 'local0.info')
        self.assertEqual(message, 'Prueba de syslog desde el router')

    def test_garbage_line_is_ignored(self):
        self.assertIsNone(parse_line('esto no es una línea de syslog'))
        self.assertIsNone(parse_line(''))


if __name__ == '__main__':
    unittest.main()
