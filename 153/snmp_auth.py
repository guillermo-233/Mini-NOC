"""Credenciales SNMP del poller: SNMPv2c (por defecto) o SNMPv3 authPriv.

Se configura por variables de entorno (las carga systemd desde /etc/mini-noc.env, fuera del repositorio):

    SNMP_VERSION=3              "2c" (por defecto) o "3"
    SNMP_COMMUNITY=public       solo v2c
    SNMP_USER=noc               solo v3
    SNMP_AUTH_PROTOCOL=SHA1     SHA1 (por defecto), SHA256 o MD5
    SNMP_AUTH_KEY=...           mínimo 8 caracteres
    SNMP_PRIV_PROTOCOL=AES      AES (por defecto, AES-128) o DES
    SNMP_PRIV_KEY=...           mínimo 8 caracteres

Si la configuración de v3 está incompleta, el poller NO vuelve a v2c en silencio: falla y lo dice en el log.
"""
import os

AUTH_PROTOCOLS = {'SHA1': 'usmHMACSHAAuthProtocol', 'SHA256': 'usmHMAC192SHA256AuthProtocol',
                  'MD5': 'usmHMACMD5AuthProtocol'}
PRIV_PROTOCOLS = {'AES': 'usmAesCfb128Protocol', 'DES': 'usmDESPrivProtocol'}
MIN_KEY_LEN = 8

_ENGINE = None


def load_config(env=None):
    """Lee y valida la configuración. Lanza ValueError (sin incluir nunca las claves en el mensaje)."""
    env = os.environ if env is None else env
    version = env.get('SNMP_VERSION', '2c').strip().lower()
    if version in ('2c', '2'):
        return {'version': '2c', 'community': env.get('SNMP_COMMUNITY', 'public')}
    if version != '3':
        raise ValueError(f"SNMP_VERSION no válida: {version!r} (usa '2c' o '3')")

    user = env.get('SNMP_USER', '').strip()
    if not user:
        raise ValueError('SNMP v3: falta SNMP_USER')
    auth_proto = env.get('SNMP_AUTH_PROTOCOL', 'SHA1').strip().upper()
    priv_proto = env.get('SNMP_PRIV_PROTOCOL', 'AES').strip().upper()
    if auth_proto not in AUTH_PROTOCOLS:
        raise ValueError(f'SNMP v3: SNMP_AUTH_PROTOCOL debe ser una de {sorted(AUTH_PROTOCOLS)}')
    if priv_proto not in PRIV_PROTOCOLS:
        raise ValueError(f'SNMP v3: SNMP_PRIV_PROTOCOL debe ser una de {sorted(PRIV_PROTOCOLS)}')
    auth_key, priv_key = env.get('SNMP_AUTH_KEY', ''), env.get('SNMP_PRIV_KEY', '')
    for name, key in (('SNMP_AUTH_KEY', auth_key), ('SNMP_PRIV_KEY', priv_key)):
        if len(key) < MIN_KEY_LEN:
            raise ValueError(f'SNMP v3: {name} falta o tiene menos de {MIN_KEY_LEN} caracteres')
    return {'version': '3', 'user': user, 'auth_protocol': auth_proto, 'auth_key': auth_key,
            'priv_protocol': priv_proto, 'priv_key': priv_key}


def describe_auth(env=None):
    """Texto para el log al arrancar. Nunca incluye claves."""
    cfg = load_config(env)
    if cfg['version'] == '2c':
        return 'SNMPv2c (community)'
    return f"SNMPv3 authPriv usuario={cfg['user']} auth={cfg['auth_protocol']} priv={cfg['priv_protocol']}"


def build_auth(env=None):
    """Objeto de credenciales de PySNMP 7 (CommunityData o UsmUserData)."""
    import pysnmp.hlapi.v3arch.asyncio as h
    cfg = load_config(env)
    if cfg['version'] == '2c':
        return h.CommunityData(cfg['community'])
    return h.UsmUserData(cfg['user'],
                         authKey=cfg['auth_key'], privKey=cfg['priv_key'],
                         authProtocol=getattr(h, AUTH_PROTOCOLS[cfg['auth_protocol']]),
                         privProtocol=getattr(h, PRIV_PROTOCOLS[cfg['priv_protocol']]))


def get_engine():
    """Un único SnmpEngine para todas las consultas. En v3 conserva el motor descubierto y la sincronización
    de tiempo con el router, en vez de repetirlos en cada walk (5 por ciclo)."""
    global _ENGINE
    if _ENGINE is None:
        from pysnmp.hlapi.v3arch.asyncio import SnmpEngine
        _ENGINE = SnmpEngine()
    return _ENGINE
