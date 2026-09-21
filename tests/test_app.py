import re
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime
from werkzeug.security import generate_password_hash
from showroom_admin.app import create_app
from showroom_admin.repository import DemoRepository, OracleRepository, DataUnavailable


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hash = generate_password_hash('test-only-password')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = DemoRepository()
        self.app = create_app({'TESTING':True, 'SECRET_KEY':'test-only', 'DATA_MODE':'demo',
                               'ADMIN_PASSWORD_HASH':self.hash,'MEMBER_STATE_DIR':self.tmp.name}, self.repo)
        self.client = self.app.test_client()

    def csrf(self):
        self.client.get('/login')
        with self.client.session_transaction() as sess:
            return sess['csrf']

    def login(self):
        return self.client.post('/login', data={'csrf_token':self.csrf(), 'username':'admin', 'password':'test-only-password'})

    def test_authentication_and_csrf(self):
        self.assertEqual(self.client.get('/api/members').status_code,401)
        self.assertEqual(self.client.post('/login',data={'username':'admin'}).status_code,400)
        self.assertEqual(self.login().status_code,302)
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.client.post('/logout').status_code,400)
        with self.client.session_transaction() as sess: token=sess['csrf']
        self.assertEqual(self.client.post('/logout',data={'csrf_token':token}).status_code,302)
        self.assertEqual(self.client.get('/api/overview').status_code,401)

    def test_invalid_login_and_limit(self):
        token=self.csrf()
        for _ in range(8):
            self.assertEqual(self.client.post('/login',data={'csrf_token':token,'username':'错误用户','password':'wrong'}).status_code,401)
        self.assertEqual(self.client.post('/login',data={'csrf_token':token,'username':'admin','password':'wrong'}).status_code,429)

    def test_session_expiry(self):
        self.login()
        with self.client.session_transaction() as sess: sess['issued']=0
        self.assertEqual(self.client.get('/api/instances').status_code,401)

    def test_search_filter_pagination(self):
        self.login()
        first=self.client.get('/api/members?page=1').json['data']
        second=self.client.get('/api/members?page=2').json['data']
        self.assertEqual(first['total'],36)
        self.assertEqual(len(first['items']),12)
        self.assertNotEqual(first['items'][0]['member_id'],second['items'][0]['member_id'])
        found=self.client.get('/api/members?search=demo_member_001').json['data']
        self.assertEqual(found['total'],1)
        filtered=self.client.get('/api/members?group=SKE48&enabled=0').json['data']['items']
        self.assertTrue(all(r['group_name']=='SKE48' and r['enabled']==0 for r in filtered))
        self.assertEqual(self.client.get('/api/members?search=not_a_member').json['data']['total'],0)

    def test_member_order_across_pages_and_filters(self):
        self.login()
        self.repo.rows.reverse()
        expected=[1,3,6,9,14]+[i for i in range(1,37) if i not in (1,3,6,9,14)]
        actual=[]
        for page in range(1,4):
            result=self.client.get('/api/members?page='+str(page)).json['data']
            actual.extend(row['id'] for row in result['items'])
        self.assertEqual(actual,expected)
        filtered=self.client.get('/api/members?group=AKB48').json['data']['items']
        self.assertEqual([r['id'] for r in filtered],[1,7,13,19,25,31])
        self.repo.rows[0]['is_live']=None
        self.assertEqual(self.repo.members(page=3)['items'][-1]['id'],36)

    def test_validation_details_and_read_only_routes(self):
        self.login()
        for query in ['page=0','page_size=101','page=no','enabled=2']:
            self.assertEqual(self.client.get('/api/members?'+query).status_code,400)
        detail=self.client.get('/api/members/demo_member_001')
        self.assertEqual(detail.status_code,200)
        self.assertIn('assignments',detail.json['data'])
        self.assertEqual(self.client.get('/api/members/missing').status_code,404)
        self.assertEqual(self.client.patch('/api/members/demo_member_001',json={'enabled':0}).status_code,405)
        self.assertEqual(self.client.delete('/api/members/demo_member_001').status_code,405)

    def test_data_failure_does_not_fall_back_to_demo(self):
        self.login()
        with patch.object(self.repo,'overview',side_effect=DataUnavailable('数据源不可用')):
            result=self.client.get('/api/overview')
            self.assertEqual(result.status_code,503)
            self.assertNotIn('data',result.json)

    def test_security_headers_and_overview(self):
        self.login()
        response=self.client.get('/api/overview')
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
        self.assertEqual(response.json['mode'],'demo')
        self.assertEqual(len(response.json['data']['live']),5)

    def test_oracle_requires_explicit_settings(self):
        with self.assertRaises(ValueError):
            create_app({'DATA_MODE':'oracle','ADMIN_PASSWORD_HASH':self.hash,'ORACLE_USER':''})


class OracleBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.repo=OracleRepository({'ORACLE_DB_TIMEZONE':'Asia/Tokyo'})

    def test_read_transaction_and_rollback(self):
        connection=MagicMock()
        pool=MagicMock()
        pool.acquire.return_value.__enter__.return_value=connection
        self.repo._pool=pool
        with self.repo.read(): pass
        connection.cursor.return_value.__enter__.return_value.execute.assert_called_once_with('SET TRANSACTION READ ONLY')
        connection.rollback.assert_called_once()
        self.assertEqual(connection.call_timeout,5000)

    def test_read_failure_sanitized(self):
        pool=MagicMock()
        pool.acquire.side_effect=RuntimeError('private connection string')
        self.repo._pool=pool
        with self.assertRaises(DataUnavailable) as ctx:
            with self.repo.read(): pass
        self.assertNotIn('private',str(ctx.exception))

    def test_search_uses_binds(self):
        from contextlib import nullcontext
        captured=[]
        def fake_query(conn,sql,binds=None):
            captured.append((sql,binds))
            return [{'total':0}] if 'COUNT(*) AS TOTAL' in sql else []
        value="' OR 1=1 --"
        with patch.object(self.repo,'read',return_value=nullcontext(object())),patch.object(self.repo,'query',side_effect=fake_query):
            self.repo.members(search=value,group='AKB48',enabled='1')
        for sql,binds in captured:
            self.assertNotIn(value,sql)
            self.assertEqual(binds['search'],value.lower())

    def test_cache_isolation(self):
        calls=[]
        def fetch():calls.append(1);return {'items':[1]}
        result=self.repo.cached('key',fetch)
        result['items'].append(2)
        self.assertEqual(self.repo.cached('key',fetch),{'items':[1]})
        self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()
