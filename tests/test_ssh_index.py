import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch
from showroom_admin.member_creation import Journal,DemoMemberWriter,MemberCreationService,CreationError
from showroom_admin.repository import DemoRepository
from showroom_admin.ssh_index import SSHIndex
from showroom_admin import local_config

REAL_RUN=subprocess.run

class SSHIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.directory=self.root/'index';self.directory.mkdir()
        self.file=self.directory/'SKE48.jdex';self.original=[{'room_id':'1','engName':'Original Name','extra':True}]
        self.file.write_text(json.dumps(self.original))
        self.remote=SSHIndex('test-3c',str(self.directory),str(self.root/'backups'))
        self.payload=dict(member_id='test_new',name_jp='試験',name_en='Test New',room_id='99999',room_url_key='test_new',group_name='SKE48',team='SKE48 Team S',eng_team='Team S',index_file='SKE48.jdex',priority=20,enabled=True)
        self.seen=[]
    def run_worker(self,args,**kwargs):
        self.seen.append(args)
        self.assertNotIn('shell',kwargs)
        return REAL_RUN([sys.executable,'-B','-c',shlex.split(args[-1])[2]],**kwargs)
    def test_probe_read_write_preserves_backup_and_idempotence(self):
        key=str(uuid.uuid4())
        with patch('showroom_admin.ssh_index.subprocess.run',side_effect=self.run_worker):
            self.assertTrue(self.remote.probe()['directory_writable'])
            self.assertFalse((self.root/'backups').exists())
            self.assertEqual(self.remote.files(),['SKE48.jdex'])
            self.assertEqual(self.remote.read('SKE48.jdex')[2],self.original)
            self.remote.append(self.payload,key);self.remote.append(self.payload,key)
            self.assertTrue(self.remote.check(self.payload,allow_existing=True))
        self.assertEqual(len(json.loads(self.file.read_text())),2)
        backup=self.root/'backups'/f'{key}-SKE48.jdex.bak'
        self.assertEqual(json.loads(backup.read_text()),self.original)
        self.assertIn('StrictHostKeyChecking=yes',self.seen[0]);self.assertIn('BatchMode=yes',self.seen[0])
    def test_lost_ssh_response_resumes_without_duplicate(self):
        journal=Journal(self.root/'state');repo=DemoRepository();repo._creation_journal=journal
        writer=DemoMemberWriter(journal,repo);service=MemberCreationService(repo,journal,self.remote,writer);key=str(uuid.uuid4());lost=[False]
        def flaky(args,**kwargs):
            result=self.run_worker(args,**kwargs)
            if json.loads(kwargs['input'])['action']=='append' and not lost[0]:
                lost[0]=True;raise subprocess.TimeoutExpired(args,35)
            return result
        with patch('showroom_admin.ssh_index.subprocess.run',side_effect=flaky):
            result=service.submit(self.payload,key,'admin');self.assertEqual(result['state'],'database_staged')
            with journal.connect() as db:self.assertEqual(db.execute('SELECT enabled FROM demo_members').fetchone()[0],0)
            result=service.submit(self.payload,key,'admin');self.assertEqual(result['state'],'complete')
        self.assertEqual(len(json.loads(self.file.read_text())),2)
    def test_errors_and_no_shell_injection(self):
        with self.assertRaises(ValueError):SSHIndex('-oProxyCommand=bad','/index','/backups')
        with self.assertRaises(ValueError):SSHIndex('host','/index','/index/backups')
        with patch('showroom_admin.ssh_index.subprocess.run',return_value=subprocess.CompletedProcess([],255,'','sensitive error')):
            with self.assertRaises(CreationError) as c:self.remote.files()
            self.assertNotIn('sensitive',str(c.exception))
        with patch('showroom_admin.ssh_index.subprocess.run',side_effect=self.run_worker):
            with self.assertRaises(CreationError):self.remote.read('../bad.jdex')
    def test_remote_lock_blocks_concurrent_writer(self):
        import fcntl
        backup=self.root/'backups';backup.mkdir()
        with open(backup/'index.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch('showroom_admin.ssh_index.subprocess.run',side_effect=self.run_worker):
                with self.assertRaises(CreationError) as c:self.remote.append(self.payload,str(uuid.uuid4()))
                self.assertEqual(c.exception.code,409)
        self.assertEqual(json.loads(self.file.read_text()),self.original)

class LocalConfigTests(unittest.TestCase):
    def test_configuration_is_data_and_environment_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'local_settings.json';creds=Path(tmp)/'credentials'
            creds.write_text('test_user\nsecret $(not-executed)\n')
            p.write_text(json.dumps({'HOST':'127.0.0.1','PORT':'8091','ORACLE_CREDENTIALS_FILE':str(creds)}));p.chmod(0o600)
            with patch.object(local_config,'SETTINGS',p),patch.dict(os.environ,{'PORT':'8092'},clear=True):
                local_config.load_local_settings()
                self.assertEqual(os.environ['PORT'],'8092');self.assertEqual(os.environ['ORACLE_PASSWORD'],'secret $(not-executed)')
            p.chmod(0o644)
            with patch.object(local_config,'SETTINGS',p):
                with self.assertRaises(ValueError):local_config.load_local_settings()

class SSHIndexEditTests(unittest.TestCase):
    setUp=SSHIndexTests.setUp
    run_worker=SSHIndexTests.run_worker
    def test_replace_worker_preserves_backup_and_is_idempotent(self):
        with patch('showroom_admin.ssh_index.subprocess.run',side_effect=self.run_worker):
            self.remote.append(self.payload,str(uuid.uuid4()))
            before=self.remote.locate(self.payload['room_id']);after=before['entry']|{'jpnName':'変更','custom':'preserved'};key=str(uuid.uuid4())
            self.remote.replace(before['filename'],before['entry'],after,key)
            self.remote.replace(before['filename'],before['entry'],after,key)
            self.assertEqual(self.remote.locate(self.payload['room_id'])['entry'],after)
            with self.assertRaises(CreationError):self.remote.replace(before['filename'],before['entry'],after|{'jpnName':'Other'},str(uuid.uuid4()))
        self.assertEqual(len(json.loads(self.file.read_text())),2)
