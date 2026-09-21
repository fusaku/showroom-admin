import tempfile
import unittest
from showroom_admin.app import create_app

class EditAPITests(unittest.TestCase):
    def test_auth_csrf_and_edit_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin='http://127.0.0.1:50123'
            app=create_app({'TESTING':True,'DATA_MODE':'demo','SECRET_KEY':'test','ADMIN_PASSWORD_HASH':'','DESKTOP_TOKEN':'t'*48,'DESKTOP_ORIGIN':origin,'MEMBER_STATE_DIR':tmp})
            client=app.test_client()
            def req(path,**kw):return client.open(path,base_url=origin,**kw)
            self.assertEqual(req('/api/members/demo_member_001/edit').status_code,401)
            req('/desktop/session',method='POST',json={'token':'t'*48})
            context=req('/api/members/demo_member_001/edit').json['data']
            body=dict(scope='youtube',version=context['upload_version'],member={'youtube':{'playlist_id':'PL_saved'}})
            endpoint='/api/members/demo_member_001/edit-preview'
            self.assertEqual(req(endpoint,method='POST',json=body).status_code,400)
            with client.session_transaction(base_url=origin) as s:csrf=s['csrf']
            headers={'X-CSRF-Token':csrf}
            response=req(endpoint,method='POST',json=body,headers=headers);self.assertEqual(response.status_code,200)
            key=response.json['data']['request_id']
            response=req('/api/member-edits/'+key+'/save',method='POST',json={},headers=headers)
            self.assertEqual(response.json['data']['state'],'complete')
            self.assertEqual(app.extensions['desktop_state']['active'],0)
            detail=req('/api/members/demo_member_001').json['data'];self.assertEqual(detail['youtube']['playlist_id'],'PL_saved')
            self.assertEqual(req('/api/member-edits').json['data'][0]['state'],'complete')
