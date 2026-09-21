import json
import unittest
from unittest.mock import patch
import test_member_creation as fixtures
from showroom_admin.member_creation import CreationError, normalize_youtube, jdex_entry
from showroom_admin.member_editing import MemberEditingService

class MemberEditingTests(unittest.TestCase):
    setUp=fixtures.CreationTests.setUp
    submit=fixtures.CreationTests.submit
    def create(self):
        self.payload['youtube']=dict(title_template='試験',description_template='配信 {upload_time}',category_id='22',privacy_status='unlisted',playlist_id='PL_example',use_primary_account=0,tags=['試験','showroom'])
        self.assertEqual(self.submit()['state'],'complete')
        self.edit=MemberEditingService(self.service)
        return self.edit.context(self.payload['member_id'])
    def preview(self,context,scope='youtube'):
        y=dict(context['member']['youtube'],playlist_id='PL_changed')
        member=dict(self.payload,youtube=y,name_jp='変更 花子')
        return self.edit.preview(self.payload['member_id'],dict(scope=scope,member=member,version=context['version' if scope=='all' else 'upload_version']),'admin')
    def test_create_uploads_and_upload_only_edit_persist(self):
        ctx=self.create();before=self.file.read_bytes()
        self.assertEqual(ctx['member']['youtube']['tags'],['試験','showroom'])
        prepared=self.preview(ctx)
        self.assertEqual(self.edit.submit(prepared['request_id'],'admin')['state'],'complete')
        self.assertEqual(self.edit.submit(prepared['request_id'],'admin')['state'],'complete')
        self.assertEqual(self.file.read_bytes(),before)
        self.assertEqual(self.repo.detail(self.payload['member_id'])['youtube']['playlist_id'],'PL_changed')
        with self.assertRaises(CreationError):self.edit.submit(prepared['request_id'],'other-user')
    def test_basic_edit_preserves_extra_fields_and_retries_lost_reply(self):
        self.create();rows=json.loads(self.file.read_text());rows[-1]['custom']={'keep':1};self.file.write_text(json.dumps(rows))
        ctx=self.edit.context(self.payload['member_id']);prepared=self.preview(ctx,'all')
        original=self.index.replace
        def lost(*args):original(*args);raise OSError('lost reply')
        with patch.object(self.index,'replace',side_effect=lost):
            job=self.edit.submit(prepared['request_id'],'admin');self.assertNotEqual(job['state'],'complete')
        self.assertEqual(self.repo.detail(self.payload['member_id'])['name_jp'],'試験 花子')
        self.assertEqual(self.edit.submit(prepared['request_id'],'admin')['state'],'complete')
        self.assertEqual(self.repo.detail(self.payload['member_id'])['name_jp'],'変更 花子')
        self.assertEqual(json.loads(self.file.read_text())[-1]['custom'],{'keep':1})
        self.assertEqual(len(json.loads(self.file.read_text())),2)
    def test_stale_database_preview_does_not_overwrite(self):
        ctx=self.create();a=self.preview(ctx);b=self.preview(ctx)
        self.edit.submit(a['request_id'],'admin')
        # Newer change to another value makes the old edit conflict.
        current=self.edit.context(self.payload['member_id']);member=dict(current['member']);member['youtube']=dict(member['youtube'],playlist_id='PL_newer')
        c=self.edit.preview(self.payload['member_id'],dict(scope='youtube',member=member,version=current['upload_version']),'admin')
        self.edit.submit(c['request_id'],'admin')
        result=self.edit.submit(b['request_id'],'admin');self.assertNotEqual(result['state'],'complete')
        self.assertIn('其他操作',result['error'])
        self.assertEqual(self.repo.detail(self.payload['member_id'])['youtube']['playlist_id'],'PL_newer')
        with self.assertRaises(CreationError):self.preview(ctx)
    def test_index_conflict_does_not_change_database(self):
        ctx=self.create();job=self.preview(ctx,'all');rows=json.loads(self.file.read_text());rows[-1]['custom']='external';self.file.write_text(json.dumps(rows))
        result=self.edit.submit(job['request_id'],'admin');self.assertNotEqual(result['state'],'complete')
        self.assertEqual(self.repo.detail(self.payload['member_id'])['name_jp'],'試験 花子')
    def test_readonly_mode_and_immutable_identifiers(self):
        ctx=self.create();member=dict(self.payload,name_en='Another Name')
        with self.assertRaises(CreationError):self.edit.preview(self.payload['member_id'],dict(scope='all',member=member,version=ctx['version']),'admin')
        self.service.writer=None
        with self.assertRaises(CreationError):self.preview(ctx)
    def test_seed_upload_edit_without_index(self):
        self.edit=MemberEditingService(self.service);ctx=self.edit.context('demo_member_001')
        self.assertIsNone(ctx['index'])
        job=self.edit.preview('demo_member_001',dict(scope='youtube',version=ctx['upload_version'],member={'youtube':normalize_youtube({'playlist_id':'PL_demo'})}),'admin')
        self.assertEqual(self.edit.submit(job['request_id'],'admin')['state'],'complete')
        self.assertEqual(self.repo.detail('demo_member_001')['youtube']['playlist_id'],'PL_demo')
    def test_upload_validation(self):
        for y in ({'description_template':'{other}'},{'description_template':'{upload_time.__class__}'},{'privacy_status':'bad'},{'playlist_id':'https://example.com'},{'tags':['x'*101]}):
            with self.assertRaises(CreationError):normalize_youtube(y)
        self.assertEqual(normalize_youtube({'description_template':'{{literal}} {upload_time}','tags':['a','a']})['tags'],['a'])

    def test_oracle_edit_rolls_back_upload_failure_and_retries(self):
        from unittest.mock import MagicMock
        ctx=self.create();prepared=self.preview(ctx);before=ctx['member']
        self.repo.mode='oracle';conn=MagicMock();self.service.writer=MagicMock()
        self.service.writer.write_repo._get_pool.return_value.acquire.return_value.__enter__.return_value=conn
        self.repo.query=MagicMock(return_value=[{'id':123}])
        with patch('showroom_admin.member_editing.database_snapshot',return_value=before),patch('showroom_admin.member_editing.save_youtube',side_effect=RuntimeError('failure')):
            job=self.edit.submit(prepared['request_id'],'admin')
        self.assertNotEqual(job['state'],'complete');conn.rollback.assert_called_once();conn.commit.assert_not_called()
        with patch('showroom_admin.member_editing.database_snapshot',return_value=before),patch('showroom_admin.member_editing.save_youtube') as save:
            job=self.edit.submit(prepared['request_id'],'admin')
        self.assertEqual(job['state'],'complete');self.assertEqual(save.call_args.args[1],123);conn.commit.assert_called_once()
    def test_oracle_lost_commit_response_reconciles(self):
        from unittest.mock import MagicMock
        ctx=self.create();prepared=self.preview(ctx);job=self.edit.get(prepared['request_id'],'admin')
        self.repo.mode='oracle';conn=MagicMock();self.service.writer=MagicMock()
        self.service.writer.write_repo._get_pool.return_value.acquire.return_value.__enter__.return_value=conn
        self.repo.query=MagicMock(return_value=[{'id':123}]);conn.commit.side_effect=RuntimeError('response lost')
        with patch('showroom_admin.member_editing.database_snapshot',return_value=job['payload']['before']),patch('showroom_admin.member_editing.save_youtube'):
            self.assertNotEqual(self.edit.submit(prepared['request_id'],'admin')['state'],'complete')
        conn.commit.side_effect=None
        with patch('showroom_admin.member_editing.database_snapshot',return_value=job['payload']['after']),patch('showroom_admin.member_editing.save_youtube') as save:
            self.assertEqual(self.edit.submit(prepared['request_id'],'admin')['state'],'complete')
            save.assert_not_called()
    def test_oracle_create_upload_failure_does_not_commit_member(self):
        from unittest.mock import MagicMock
        from showroom_admin.member_creation import OracleMemberWriter,normalize
        conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value;cur.fetchone.return_value=(1,);cur.var.return_value.getvalue.return_value=[123]
        writer=OracleMemberWriter(MagicMock(),MagicMock());writer.write_repo._get_pool.return_value.acquire.return_value.__enter__.return_value=conn
        writer.check=MagicMock();remember=MagicMock()
        with patch('showroom_admin.member_creation.save_youtube',side_effect=RuntimeError('tag insertion failed')):
            with self.assertRaises(CreationError):writer.stage(normalize(self.payload|{'youtube':{}}),remember)
        conn.commit.assert_not_called();conn.rollback.assert_called_once();remember.assert_not_called()
