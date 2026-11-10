import os
import stat
import tempfile
import unittest

import auth
import common


class TestPasswords(unittest.TestCase):
    def test_hash_is_salted_and_verifies(self):
        h1, h2 = auth.hash_password('correct horse battery'), auth.hash_password('correct horse battery')
        self.assertNotEqual(h1, h2)                       # sal distinta cada vez
        self.assertNotIn('correct horse', h1)             # nunca en claro
        self.assertTrue(auth.verify_password('correct horse battery', h1))
        self.assertFalse(auth.verify_password('correct horse batterz', h1))

    def test_malformed_stored_hash_is_rejected(self):
        for bad in ('', 'plain', 'md5$1$2$3$aa$bb', 'scrypt$x$y$z$zz$qq', None):
            self.assertFalse(auth.verify_password('x', bad))


class TestTokens(unittest.TestCase):
    def setUp(self):
        self.secret = b'k' * 32
        self.pw = auth.hash_password('una-clave-larga-1')
        self.lookup = lambda name: self.pw if name == 'ana' else None

    def test_valid_token(self):
        t = auth.make_token(self.secret, 'ana', self.pw, now=1000, lifetime=60)
        self.assertEqual(auth.verify_token(self.secret, t, self.lookup, now=1030), 'ana')

    def test_expired_token(self):
        t = auth.make_token(self.secret, 'ana', self.pw, now=1000, lifetime=60)
        self.assertIsNone(auth.verify_token(self.secret, t, self.lookup, now=1060))

    def test_tampered_token(self):
        t = auth.make_token(self.secret, 'ana', self.pw, now=1000, lifetime=60)
        payload, sig = t.rsplit('.', 1)
        self.assertIsNone(auth.verify_token(self.secret, payload + '.' + '0' * len(sig), self.lookup, now=1001))
        self.assertIsNone(auth.verify_token(self.secret, 'x' + t, self.lookup, now=1001))

    def test_token_signed_with_other_secret(self):
        t = auth.make_token(b'z' * 32, 'ana', self.pw, now=1000, lifetime=60)
        self.assertIsNone(auth.verify_token(self.secret, t, self.lookup, now=1001))

    def test_garbage_tokens(self):
        for bad in ('', 'abc', '.', 'a.b', 'e30.00', None):
            self.assertIsNone(auth.verify_token(self.secret, bad, self.lookup, now=1))

    def test_deleted_user_loses_session(self):
        t = auth.make_token(self.secret, 'ana', self.pw, now=1000, lifetime=60)
        self.assertIsNone(auth.verify_token(self.secret, t, lambda n: None, now=1001))

    def test_password_change_invalidates_session(self):
        t = auth.make_token(self.secret, 'ana', self.pw, now=1000, lifetime=60)
        new_pw = auth.hash_password('otra-clave-larga-2')
        self.assertIsNone(auth.verify_token(self.secret, t, lambda n: new_pw, now=1001))


class TestLimiter(unittest.TestCase):
    def test_locks_after_max_failures_and_recovers(self):
        lim = auth.LoginLimiter(max_fails=3, window=60, lock=120)
        for i in range(2):
            lim.failure('1.1.1.1', now=100 + i)
        self.assertEqual(lim.retry_after('1.1.1.1', now=103), 0)
        lim.failure('1.1.1.1', now=103)                         # 3er fallo: bloqueo
        self.assertGreater(lim.retry_after('1.1.1.1', now=104), 0)
        self.assertEqual(lim.retry_after('2.2.2.2', now=104), 0)  # otra IP no afectada
        self.assertEqual(lim.retry_after('1.1.1.1', now=300), 0)  # el bloqueo termina

    def test_old_failures_expire_and_success_resets(self):
        lim = auth.LoginLimiter(max_fails=3, window=60, lock=120)
        lim.failure('a', now=0)
        lim.failure('a', now=1)
        lim.failure('a', now=100)                               # los dos primeros ya caducaron
        self.assertEqual(lim.retry_after('a', now=101), 0)
        lim.failure('a', now=101)
        lim.success('a')
        lim.failure('a', now=102)
        self.assertEqual(lim.retry_after('a', now=103), 0)


class TestDbUsers(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.conn = common.connect(self.path)
        common.init_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        for ext in ('', '-wal', '-shm'):
            if os.path.exists(self.path + ext):
                os.remove(self.path + ext)

    def test_create_authenticate_change_delete(self):
        self.assertEqual(auth.count_users(self.conn), 0)
        auth.set_user(self.conn, 'ana', 'una-clave-larga-1')
        self.assertTrue(auth.authenticate(self.conn, 'ana', 'una-clave-larga-1'))
        self.assertFalse(auth.authenticate(self.conn, 'ana', 'mala'))
        self.assertFalse(auth.authenticate(self.conn, 'nadie', 'una-clave-larga-1'))   # usuario inexistente
        auth.set_user(self.conn, 'ana', 'otra-clave-larga-2')                          # cambio de contraseña
        self.assertFalse(auth.authenticate(self.conn, 'ana', 'una-clave-larga-1'))
        self.assertTrue(auth.authenticate(self.conn, 'ana', 'otra-clave-larga-2'))
        self.assertEqual(auth.count_users(self.conn), 1)
        self.assertTrue(auth.delete_user(self.conn, 'ana'))
        self.assertFalse(auth.delete_user(self.conn, 'ana'))

    def test_stored_hash_is_not_plaintext(self):
        auth.set_user(self.conn, 'ana', 'una-clave-larga-1')
        self.assertNotIn('una-clave-larga-1', auth.get_pw_hash(self.conn, 'ana'))

    def test_validation(self):
        with self.assertRaises(ValueError):
            auth.set_user(self.conn, 'ana', 'corta')
        for bad in ('', 'a b', "a'; DROP TABLE users;--", 'x' * 33, '../x'):
            with self.assertRaises(ValueError):
                auth.set_user(self.conn, bad, 'una-clave-larga-1')

    def test_sql_injection_in_login_is_just_a_wrong_user(self):
        auth.set_user(self.conn, 'ana', 'una-clave-larga-1')
        self.assertFalse(auth.authenticate(self.conn, "ana' OR '1'='1", 'x'))


class TestSecretFile(unittest.TestCase):
    def test_created_once_with_private_permissions(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, 'secret')
        s1 = auth.load_secret(p)
        self.assertEqual(len(s1), 32)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(auth.load_secret(p), s1)           # persiste entre reinicios


class TestDecide(unittest.TestCase):
    def setUp(self):
        self.secret = b'k' * 32
        self.pw = auth.hash_password('una-clave-larga-1')
        self.lookup = lambda n: self.pw if n == 'ana' else None
        self.token = auth.make_token(self.secret, 'ana', self.pw, now=1000, lifetime=60)

    def d(self, path, token, users=True, now=1001):
        return auth.decide(path, token, self.secret, self.lookup, users, now)[0]

    def test_public_paths_need_no_session(self):
        for p in ('/login.html', '/api/login'):
            self.assertEqual(self.d(p, None), 'allow')

    def test_everything_else_is_protected(self):
        for p in ('/', '/index.html', '/api/health', '/api/flows/top-hosts', '/alerts.js', '/chart.min.js',
                  '/LOGIN.HTML', '/login.html/', '//login.html', '/api/login/x'):
            self.assertEqual(self.d(p, None), 'deny', p)

    def test_valid_session_allows(self):
        self.assertEqual(self.d('/api/health', self.token), 'allow')

    def test_expired_or_forged_session_denied(self):
        self.assertEqual(self.d('/api/health', self.token, now=2000), 'deny')
        self.assertEqual(self.d('/api/health', self.token + 'x'), 'deny')

    def test_no_users_means_nobody_gets_in(self):
        self.assertEqual(self.d('/api/health', self.token, users=False), 'deny')


if __name__ == '__main__':
    unittest.main()
