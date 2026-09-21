import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import oracledb
from showroom_admin.oracle_client import pool_options, client_directory

class OracleClientTests(unittest.TestCase):
    def test_thick_overrides_linux_wallet_location_and_keeps_tls_checks(self):
        with tempfile.TemporaryDirectory(prefix='wallet space ') as tmp:
            Path(tmp,'tnsnames.ora').write_text('sample=(DESCRIPTION=(ADDRESS=(PROTOCOL=TCPS)(HOST=db.example)(PORT=1522))(CONNECT_DATA=(SERVICE_NAME=sample))(SECURITY=(SSL_SERVER_DN_MATCH=yes)))')
            settings=dict(ORACLE_CLIENT_LIB_DIR='/client',ORACLE_CONFIG_DIR=tmp,ORACLE_WALLET_LOCATION=tmp,ORACLE_DSN='sample',ORACLE_WALLET_PASSWORD='unused')
            with patch.object(oracledb,'init_oracle_client'):
                opts=pool_options(settings,oracledb)
            self.assertIn(tmp,opts['dsn'])
            self.assertIn('SSL_SERVER_DN_MATCH=ON',opts['dsn'])
            self.assertNotIn('wallet_password',opts)
    def test_frozen_client_resolves_after_app_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            contents=Path(tmp)/'Other Mac/Showroom.app/Contents'
            client=contents/'Resources/oracle-client';client.mkdir(parents=True)
            (client/'libclntsh.dylib').touch()
            with patch('sys.frozen',True,create=True),patch('sys.executable',str(contents/'MacOS/Showroom')):
                self.assertEqual(client_directory({}),str(client.resolve()))
    def test_thin_preserves_existing_web_configuration(self):
        with patch('sys.frozen',False,create=True):
            self.assertEqual(pool_options({'ORACLE_DSN':'sample','ORACLE_WALLET_PASSWORD':'secret'},oracledb),{'dsn':'sample','wallet_password':'secret'})

    @unittest.skipUnless(__import__('sys').platform == 'darwin', 'Mac desktop locale')
    def test_finder_ascii_locale_accepts_chinese_paths(self):
        import locale
        from showroom_admin.desktop_entry import configure_path_locale
        old=locale.setlocale(locale.LC_CTYPE)
        try:
            locale.setlocale(locale.LC_CTYPE,'C')
            configure_path_locale()
            self.assertEqual('Showroom 管理'.encode(locale.getencoding()),'Showroom 管理'.encode('utf-8'))
        finally:
            locale.setlocale(locale.LC_CTYPE,old)
