import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from showroom_admin.app import create_app
from showroom_admin.desktop_data import initialize,save,export_archive,import_archive,write_private
from showroom_admin.member_creation import Journal

class DesktopAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.origin='http://127.0.0.1:50123';self.token='t'*48
        self.app=create_app({'TESTING':True,'DATA_MODE':'demo','SECRET_KEY':'test',
            'ADMIN_PASSWORD_HASH':'','DESKTOP_TOKEN':self.token,'DESKTOP_ORIGIN':self.origin,'MEMBER_STATE_DIR':self.tmp.name})
        self.client=self.app.test_client()
    def request(self,path,**kw):return self.client.open(path,base_url=self.origin,**kw)
    def login(self):return self.request('/desktop/session',method='POST',json={'token':self.token})
    def test_no_password_but_capability_required(self):
        self.assertEqual(self.request('/api/members').status_code,401)
        self.assertEqual(self.request('/desktop/session',method='POST',json={'token':'bad'}).status_code,403)
        self.assertEqual(self.login().status_code,200)
        self.assertEqual(self.request('/api/members').status_code,200)
        self.assertEqual(self.login().status_code,403)
        page=self.request('/').get_data(as_text=True)
        self.assertIn('设置与迁移',page);self.assertNotIn('action="/logout"',page)
    def test_host_origin_and_csrf_protection(self):
        self.assertEqual(self.client.get('/desktop/welcome',base_url='http://evil.example:50123').status_code,403)
        self.assertEqual(self.request('/desktop/session',method='POST',json={'token':self.token},headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.login().status_code,200)
        self.assertEqual(self.request('/api/members',method='POST',json={}).status_code,400)
        self.assertEqual(self.app.extensions['desktop_state']['active'],0)
        self.app.extensions['desktop_state']['closing']=True
        self.assertEqual(self.request('/api/members').status_code,503)
    def test_settings_never_return_secrets(self):
        home=Path(self.tmp.name)/'profile';data=initialize(home);data['ORACLE_WALLET_PASSWORD']='private-wallet-password';save(home,data)
        self.login()
        with patch.dict(os.environ,{'SHOWROOM_HOME':str(home)}):
            response=self.request('/api/desktop/settings')
        self.assertNotIn('private-wallet-password',response.get_data(as_text=True))
        self.assertTrue(response.json['data']['wallet_password_saved'])

class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_encrypted_round_trip_to_new_home(self):
        old=self.root/'old user';data=initialize(old);data.update(DATA_MODE='oracle',ALLOW_MEMBER_CREATE='1',ORACLE_WALLET_PASSWORD='sample secret',UI_LANGUAGE='ja',LOG_1C_HOST='monitor.example',LOG_4C_HOST='process.example');save(old,data)
        write_private(old/'secrets/1c.key',b'one-key-fixture');write_private(old/'secrets/4c.key',b'four-key-fixture')
        write_private(old/'secrets/3c.key',b'private-key-fixture');write_private(old/'wallet/ewallet.pem',b'wallet-fixture')
        journal=Journal(old/'state/onboarding/oracle');journal.start('test-operation',{'member_id':'sample'},'admin')
        archive=self.root/'transfer.srbackup';export_archive(old,archive,'migration-password-123')
        self.assertNotIn(b'private-key-fixture',archive.read_bytes());self.assertNotIn(b'sample secret',archive.read_bytes())
        new=self.root/'different user';previous_data=initialize(new);write_private(new/'secrets/previous',b'keep')
        previous=import_archive(new,archive,'migration-password-123')
        self.assertTrue((Path(previous)/'secrets/previous').exists())
        restored=initialize(new);self.assertEqual(restored['DATA_MODE'],'demo');self.assertEqual(restored['ALLOW_MEMBER_CREATE'],'0')
        self.assertEqual(restored['UI_LANGUAGE'],'ja');self.assertEqual(restored['LOG_1C_HOST'],'monitor.example')
        self.assertEqual((new/'secrets/1c.key').read_bytes(),b'one-key-fixture');self.assertEqual((new/'secrets/4c.key').read_bytes(),b'four-key-fixture')
        self.assertEqual(restored['ORACLE_WALLET_PASSWORD'],'sample secret');self.assertNotEqual(restored['SESSION_SECRET'],data['SESSION_SECRET'])
        self.assertEqual((new/'secrets/3c.key').read_bytes(),b'private-key-fixture')
        self.assertEqual(Journal(new/'state/onboarding/oracle').get('test-operation')['payload']['member_id'],'sample')
    def test_bad_password_or_tampering_preserves_destination(self):
        old=self.root/'source';initialize(old);archive=self.root/'data.srbackup';export_archive(old,archive,'correct-password-123')
        new=self.root/'destination';initialize(new);before=(new/'settings.json').read_bytes()
        with self.assertRaises(ValueError):import_archive(new,archive,'wrong-password-123')
        self.assertEqual((new/'settings.json').read_bytes(),before)
        raw=bytearray(archive.read_bytes());raw[-1]^=1;archive.write_bytes(raw)
        with self.assertRaises(ValueError):import_archive(new,archive,'correct-password-123')
        self.assertEqual((new/'settings.json').read_bytes(),before)
    def test_external_path_not_silently_exported(self):
        home=self.root/'home';data=initialize(home);data['ORACLE_CONFIG_DIR']='/other-computer/wallet';save(home,data)
        with self.assertRaises(ValueError):export_archive(home,self.root/'bad.srbackup','migration-password-123')
