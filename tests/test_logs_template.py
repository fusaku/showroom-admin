import copy
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from showroom_admin.log_worker import inventory,tail,parts_for,MAX_BYTES
from showroom_admin.logs import LogService
from showroom_admin.member_template import defaults
from showroom_admin.member_creation import CreationError
from showroom_admin.app import create_app

class LogTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def test_tail_unicode_and_bounds(self):
  p=self.root/'live.log';p.write_text('日本語\n'*100000)
  d=tail(self.root,'live.log',1000)
  self.assertEqual(d['lines'],1000);self.assertTrue(d['limited']);self.assertLessEqual(len(d['text'].encode()),MAX_BYTES)
  self.assertEqual(d['text'],'日本語\n'*1000)
 def test_reject_unsafe_names(self):
  for name in ['../secret','/etc/passwd','a/../b','a//b','a/b/c/d','x.gz','a\\b','x\n']:
   with self.subTest(name=name),self.assertRaises(ValueError):parts_for(name)
 def test_no_symlink_fifo_or_binary(self):
  (self.root/'ok.log').write_text('ok\n');(self.root/'link').symlink_to(self.root/'ok.log')
  (self.root/'dir').symlink_to(self.root,target_is_directory=True);os.mkfifo(self.root/'fifo')
  (self.root/'binary').write_bytes(b'a\x00b')
  for name in ['link','dir/ok.log','fifo','binary']:
   with self.subTest(name=name),self.assertRaises((OSError,ValueError)):tail(self.root,name,5)
  self.assertEqual({x['name'] for x in inventory(self.root)['files']},{'ok.log','binary'})
 def test_rotation_and_history(self):
  (self.root/'backup').mkdir();(self.root/'backup/a.log.1').write_text('old\n');(self.root/'backup/a.gz').write_bytes(b'gzip')
  p=self.root/'a.log';p.write_text('before\n');self.assertEqual(tail(self.root,'a.log',10)['text'],'before\n')
  p.rename(self.root/'a.log.1');p.write_text('after\n');self.assertEqual(tail(self.root,'a.log',10)['text'],'after\n')
  self.assertEqual(len(inventory(self.root)['files']),3)
 def test_service_allowlist(self):
  s=LogService('demo')
  for node,action,args in [('5c','files',{}),('1c','delete',{}),('1c','tail',{'name':'../x','lines':1}),('1c','tail',{'name':'demo.log','lines':True})]:
   with self.assertRaises(CreationError):s.call(node,action,**args)

class TemplateTests(unittest.TestCase):
 def test_actual_values_without_identity_or_playlist(self):
  m=dict(name_jp='白鳥 沙怜',name_en='Shiratori Sari',room_id=507074,group_name='AKB48',team='AKB48 19期生',enabled=0,youtube=dict(playlist_id='private-source-playlist',category_id=22,description_template='白鳥 沙怜',title_template=None),tags=['白鳥沙怜'])
  original=copy.deepcopy(m);repo=SimpleNamespace(mode='oracle',detail=Mock(return_value=m));index=SimpleNamespace(locate=Mock(return_value=dict(filename='akb48.jdex',entry=dict(jpnTeam='19期生',engTeam='19th Generation',priority=27))))
  d=defaults(repo,index)['member'];self.assertEqual(d['youtube']['playlist_id'],'');self.assertEqual(d['priority'],27)
  for key in ['member_id','name_jp','name_en','room_id','room_url_key']:self.assertEqual(d[key],'')
  self.assertEqual(m,original);self.assertEqual(d['youtube']['description_template'],m['youtube']['description_template']);index.locate.assert_called_once_with('507074')

class NewApiTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.app=create_app(dict(TESTING=True,SECRET_KEY='test',DATA_MODE='demo',MEMBER_STATE_DIR=self.tmp.name,ADMIN_PASSWORD_HASH='unused'))
  self.client=self.app.test_client()
 def login(self):
  import time
  with self.client.session_transaction() as s:s.update(user='admin',issued=time.time(),csrf='token')
 def test_auth_and_language(self):
  self.assertEqual(self.client.get('/api/logs/1c/files').status_code,401);self.login()
  self.assertEqual(self.client.post('/api/preferences',json={'language':'ja'}).status_code,400)
  r=self.client.post('/api/preferences',json={'language':'ja'},headers={'X-CSRF-Token':'token'});self.assertEqual(r.status_code,200)
  self.assertEqual(self.client.get('/api/preferences').json['data']['language'],'ja')
  self.assertEqual(self.client.post('/api/preferences',json={'language':'xx'},headers={'X-CSRF-Token':'token'}).status_code,400)
 def test_logs_and_template(self):
  self.login()
  for node in ['1c','3c','4c']:
   self.assertEqual(self.client.get('/api/logs/'+node+'/files').status_code,200)
   self.assertEqual(self.client.get('/api/logs/'+node+'/tail?name=demo.log&lines=100').status_code,200)
  self.assertEqual(self.client.get('/api/logs/1c/tail?name=../x').status_code,400)
  self.assertEqual(self.client.get('/api/logs/1c/tail?name=demo.log&lines=1001').status_code,400)
  r=self.client.get('/api/member-template');self.assertEqual(r.status_code,200);self.assertEqual(r.json['data']['member']['youtube']['playlist_id'],'')
