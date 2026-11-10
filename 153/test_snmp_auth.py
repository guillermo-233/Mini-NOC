import unittest

import snmp_auth

V3 = {'SNMP_VERSION': '3', 'SNMP_USER': 'noc', 'SNMP_AUTH_KEY': 'clave-auth-1', 'SNMP_PRIV_KEY': 'clave-priv-2'}


class TestLoadConfig(unittest.TestCase):
    def test_default_is_v2c_so_nothing_changes_until_v3_is_enabled(self):
        self.assertEqual(snmp_auth.load_config({}), {'version': '2c', 'community': 'public'})

    def test_v3_with_defaults(self):
        cfg = snmp_auth.load_config(V3)
        self.assertEqual((cfg['version'], cfg['user'], cfg['auth_protocol'], cfg['priv_protocol']),
                         ('3', 'noc', 'SHA1', 'AES'))

    def test_protocols_are_case_insensitive(self):
        cfg = snmp_auth.load_config({**V3, 'SNMP_AUTH_PROTOCOL': 'sha256', 'SNMP_PRIV_PROTOCOL': 'des'})
        self.assertEqual((cfg['auth_protocol'], cfg['priv_protocol']), ('SHA256', 'DES'))

    def test_incomplete_v3_fails_instead_of_falling_back_to_v2c(self):
        for missing in ('SNMP_USER', 'SNMP_AUTH_KEY', 'SNMP_PRIV_KEY'):
            env = {k: v for k, v in V3.items() if k != missing}
            with self.assertRaises(ValueError, msg=missing):
                snmp_auth.load_config(env)

    def test_short_keys_rejected(self):
        with self.assertRaises(ValueError):
            snmp_auth.load_config({**V3, 'SNMP_AUTH_KEY': '1234567'})
        with self.assertRaises(ValueError):
            snmp_auth.load_config({**V3, 'SNMP_PRIV_KEY': 'corta'})

    def test_unknown_values_rejected(self):
        for bad in ({'SNMP_VERSION': '1'}, {**V3, 'SNMP_AUTH_PROTOCOL': 'SHA3'}, {**V3, 'SNMP_PRIV_PROTOCOL': 'ROT13'}):
            with self.assertRaises(ValueError):
                snmp_auth.load_config(bad)

    def test_error_messages_never_contain_the_keys(self):
        for env in ({**V3, 'SNMP_AUTH_PROTOCOL': 'SHA3'}, {**V3, 'SNMP_PRIV_KEY': 'corta'}):
            try:
                snmp_auth.load_config(env)
            except ValueError as e:
                for secret in ('clave-auth-1', 'clave-priv-2', 'corta'):
                    self.assertNotIn(secret, str(e))

    def test_describe_does_not_leak_keys(self):
        text = snmp_auth.describe_auth(V3)
        self.assertIn('noc', text)
        self.assertNotIn('clave-auth-1', text)
        self.assertNotIn('clave-priv-2', text)


class TestBuildAuth(unittest.TestCase):
    def setUp(self):
        try:
            import pysnmp.hlapi.v3arch.asyncio  # noqa: F401
        except ImportError:
            self.skipTest('pysnmp no instalado (se ejecuta en el contenedor)')

    def test_v3_builds_usm_user(self):
        from pysnmp.hlapi.v3arch.asyncio import UsmUserData
        self.assertIsInstance(snmp_auth.build_auth(V3), UsmUserData)

    def test_v2c_builds_community(self):
        from pysnmp.hlapi.v3arch.asyncio import CommunityData
        self.assertIsInstance(snmp_auth.build_auth({}), CommunityData)


if __name__ == '__main__':
    unittest.main()
