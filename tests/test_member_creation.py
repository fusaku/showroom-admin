import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from showroom_admin.member_creation import Journal, LocalIndex, DemoMemberWriter, MemberCreationService, CreationError, normalize
from showroom_admin.repository import DemoRepository

class CreationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.journal=Journal(root/'state');self.repo=DemoRepository();self.repo._creation_journal=self.journal
        self.directory=root/'index';self.directory.mkdir();self.file=self.directory/'akb48.jdex'
        self.existing={'room_id':'123','engName':'Existing Name','custom':{'preserve':True}}
        self.file.write_text(json.dumps([self.existing]))
        self.index=LocalIndex(self.directory,root/'backups');self.writer=DemoMemberWriter(self.journal,self.repo)
        self.service=MemberCreationService(self.repo,self.journal,self.index,self.writer)
        self.payload=dict(member_id='test_hanako',name_jp='試験 花子',name_en='Test Hanako',group_name=self.repo.groups()[0],team='',eng_team='',room_id='99887766',room_url_key='test_hanako',index_file='akb48.jdex',enabled=True,priority=20)
        self.key=str(uuid.uuid4())
    def submit(self):return self.service.submit(self.payload,self.key,'admin')
    def test_success_persistence_and_idempotence(self):
        self.assertEqual(self.submit()['state'],'complete');self.assertEqual(self.submit()['state'],'complete')
        rows=json.loads(self.file.read_text());self.assertEqual(len(rows),2);self.assertEqual(rows[0],self.existing)
        self.assertEqual(Journal(self.journal.root).get(self.key)['state'],'complete')
        self.assertEqual([r for r in self.repo.all_rows() if r['member_id']=='test_hanako'][0]['enabled'],1)
        self.assertEqual(len(list(self.index.backups.glob('*.bak'))),1)
    def test_index_failure_stays_disabled_and_retry_recovers(self):
        with patch.object(self.index,'append',side_effect=OSError('test')):self.assertEqual(self.submit()['state'],'database_staged')
        with self.journal.connect() as db:self.assertEqual(db.execute('SELECT enabled FROM demo_members').fetchone()[0],0)
        self.assertEqual(self.submit()['state'],'complete');self.assertEqual(len(json.loads(self.file.read_text())),2)
    def test_activation_failure_and_retry(self):
        with patch.object(self.writer,'activate',side_effect=OSError('test')):self.assertEqual(self.submit()['state'],'index_written')
        self.assertEqual(self.submit()['state'],'complete');self.assertEqual(len(json.loads(self.file.read_text())),2)
    def test_bad_index_no_database_write(self):
        self.file.write_text('invalid')
        with self.assertRaises(CreationError):self.submit()
        self.assertEqual(self.journal.recent(),[])
    def test_conflict_and_validation(self):
        for key,value in [('name_en','Name $(touch file)'),('index_file','../other.jdex'),('room_id','0001')]:
            with self.assertRaises(CreationError):normalize(self.payload|{key:value})
        self.submit()
        with self.assertRaises(CreationError):self.service.submit(self.payload|{'name_jp':'変更'},self.key,'admin')
        with self.assertRaises(CreationError):self.service.preview(self.payload)
