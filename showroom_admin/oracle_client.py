"""Resolve bundled Oracle Client without storing machine-specific app paths."""
from pathlib import Path
import sys
import threading

_init_lock = threading.Lock()


def client_directory(settings):
    if settings.get('ORACLE_CLIENT_LIB_DIR'):
        return str(Path(settings['ORACLE_CLIENT_LIB_DIR']).expanduser())
    if getattr(sys, 'frozen', False):
        bundled = Path(sys.executable).resolve().parents[1] / 'Resources/oracle-client'
        if (bundled / 'libclntsh.dylib').is_file():
            return str(bundled)
    return ''


def pool_options(settings, driver):
    directory = client_directory(settings)
    config = settings.get('ORACLE_CONFIG_DIR') or None
    wallet = settings.get('ORACLE_WALLET_LOCATION') or config
    if directory:
        with _init_lock:
            driver.init_oracle_client(lib_dir=directory, config_dir=config)
        # Explicit per-connection wallet path overrides exported Linux sqlnet.ora paths.
        params = driver.ConnectParams(config_dir=config, wallet_location=wallet,
                                      tcp_connect_timeout=5, retry_count=0)
        params.parse_connect_string(settings['ORACLE_DSN'])
        params.set(wallet_location=wallet, tcp_connect_timeout=5, retry_count=0)
        return {'dsn': params.get_connect_string()}
    opts = {'dsn': settings['ORACLE_DSN']}
    for key in ('config_dir', 'wallet_location', 'wallet_password'):
        if settings.get('ORACLE_' + key.upper()):
            opts[key] = settings['ORACLE_' + key.upper()]
    return opts
