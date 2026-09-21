import hmac
import json
import secrets
import threading
import time
from datetime import timedelta
from pathlib import Path
from flask import request,session,jsonify,render_template,g
from .desktop_data import default_home,initialize,save,copy_secret,copy_wallet,write_private
from .member_creation import CreationError


def register_desktop(app,login_required):
    token=app.config['DESKTOP_TOKEN'];lock=threading.Lock();consumed=False
    if len(token)<32:raise ValueError('Desktop token must have at least 32 characters')
    app.config['PERMANENT_SESSION_LIFETIME']=timedelta(days=30)
    state={'active':0,'closing':False};app.extensions['desktop_state']=state
    app.extensions['desktop_mutex']=threading.RLock()

    @app.before_request
    def local_only():
        origin=app.config.get('DESKTOP_ORIGIN','')
        if request.remote_addr not in ('127.0.0.1','::1') or request.host_url.rstrip('/')!=origin:
            return jsonify(error='仅允许桌面应用的本机地址访问。'),403
        if request.headers.get('Origin') and request.headers['Origin']!=origin:
            return jsonify(error='不允许跨站请求。'),403
        with app.extensions['desktop_mutex']:
            if state['closing'] and request.path.startswith('/api/'):
                return jsonify(error='应用正在迁移或退出，请重新打开。'),503
            if request.method=='POST' and request.path.startswith('/api/') and not request.path.startswith('/api/desktop/'):
                state['active']+=1;g.desktop_write=True

    @app.teardown_request
    def finish_request(exc):
        if getattr(g,'desktop_write',False):
            with app.extensions['desktop_mutex']:state['active']-=1

    @app.get('/desktop/welcome')
    def welcome():return render_template('error.html',message='正在打开本地管理工作台…')

    @app.post('/desktop/session')
    def bootstrap():
        nonlocal consumed
        body=request.get_json(silent=True) or {}
        provided=body.get('token','') if isinstance(body,dict) else ''
        with lock:
            if consumed or not isinstance(provided,str) or not hmac.compare_digest(provided.encode(),token.encode()):
                return jsonify(error='桌面会话无效。'),403
            consumed=True
        session.clear();session.update(user=app.config['ADMIN_USERNAME'],desktop=True,issued=time.time(),csrf=secrets.token_urlsafe(32))
        return jsonify(ok=True)

    @app.get('/api/desktop/settings')
    @login_required
    def settings():
        data=initialize(default_home())
        public={k:data.get(k,'') for k in ('LOG_1C_HOST','LOG_1C_USER','LOG_4C_HOST','LOG_4C_USER','DATA_MODE','ORACLE_DSN','SHOWROOM_SSH_HOST','SHOWROOM_SSH_USER','SHOWROOM_INDEX_DIR','SHOWROOM_REMOTE_BACKUP_DIR','ALLOW_MEMBER_CREATE')}
        root=default_home()
        from .oracle_client import client_directory
        public['wallet_auto_login']=bool(client_directory(data))
        public.update(log_1c_key_saved=(root/'secrets/1c.key').exists(),log_4c_key_saved=(root/'secrets/4c.key').exists(),home=str(root),active_mode=app.config['DATA_MODE'],wallet_password_saved=bool(data.get('ORACLE_WALLET_PASSWORD')),
            wallet_saved=any((root/'wallet'/name).exists() for name in ('ewallet.pem','cwallet.sso')),key_saved=(root/'secrets/3c.key').exists(),
            database_saved=(root/'secrets/database.key').exists(),known_hosts_saved=(root/'secrets/known_hosts').exists(),
            setup_error=app.config.get('DESKTOP_SETUP_ERROR',''))
        return jsonify(data=public)

    @app.post('/api/desktop/settings')
    @login_required
    def save_settings():
        body=request.get_json(silent=True)
        if not isinstance(body,dict):raise CreationError('配置格式无效。')
        home=default_home();data=initialize(home)
        allowed={'LOG_1C_HOST','LOG_1C_USER','LOG_4C_HOST','LOG_4C_USER','DATA_MODE','ORACLE_DSN','SHOWROOM_SSH_HOST','SHOWROOM_SSH_USER','SHOWROOM_INDEX_DIR','SHOWROOM_REMOTE_BACKUP_DIR','ALLOW_MEMBER_CREATE','wallet_password','database_user','database_password','wallet_path','key_path','known_hosts_path','log_1c_key_path','log_4c_key_path'}
        if set(body)-allowed or any(not isinstance(v,str) or len(v)>4096 or '\x00' in v for v in body.values()):raise CreationError('配置字段无效。')
        if body.get('DATA_MODE') not in ('demo','oracle') or body.get('ALLOW_MEMBER_CREATE') not in ('0','1'):raise CreationError('运行模式或添加开关无效。')
        with app.extensions['desktop_mutex']:
            if app.extensions['desktop_state']['active']:raise CreationError('成员正在保存，请稍后修改配置。',409)
            for k in allowed-{'wallet_password','database_user','database_password','wallet_path','key_path','known_hosts_path','log_1c_key_path','log_4c_key_path'}:
                if k in body:data[k]=body[k].strip()
            if data['SHOWROOM_SSH_HOST']:
                from .ssh_index import SSHIndex
                SSHIndex(data['SHOWROOM_SSH_HOST'],data['SHOWROOM_INDEX_DIR'],data['SHOWROOM_REMOTE_BACKUP_DIR'])
            import re
            for prefix in ('LOG_1C_','LOG_4C_','SHOWROOM_SSH_'):
                host=data.get(prefix+'HOST','');user=data.get(prefix+'USER','')
                if host and not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*',host):raise CreationError('SSH 地址格式无效。')
                if user and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*',user):raise CreationError('SSH 用户名格式无效。')
            try:
                for node in ('1c','4c'):
                    if body.get('log_'+node+'_key_path'):
                        copy_secret(body['log_'+node+'_key_path'],home/'secrets'/(node+'.key'))
                        data['LOG_'+node.upper()+'_IDENTITY_FILE']='secrets/'+node+'.key'
                if body.get('wallet_path'):copy_wallet(body['wallet_path'],home/'wallet')
                if body.get('key_path'):copy_secret(body['key_path'],home/'secrets/3c.key')
                if body.get('known_hosts_path'):copy_secret(body['known_hosts_path'],home/'secrets/known_hosts')
                if body.get('database_user') or body.get('database_password'):
                    if not body.get('database_user') or not body.get('database_password'):raise ValueError('数据库账号和密码需要一起填写。')
                    if '\n' in body['database_user']+body['database_password'] or '\r' in body['database_user']+body['database_password']:raise ValueError('账号密码不能包含换行。')
                    write_private(home/'secrets/database.key',(body['database_user']+'\n'+body['database_password']+'\n').encode())
                    data['WRITE_ORACLE_CREDENTIALS_FILE']='secrets/database.key'
                if body.get('wallet_password'):data['ORACLE_WALLET_PASSWORD']=body['wallet_password']
                save(home,data)
            except (ValueError,OSError):raise CreationError('配置保存失败，请核对文件、路径和账号密码是否完整。')
        return jsonify(data={'message':'已保存到本机。重新打开应用后生效。'})
