"""Fixed-instance, read-only SSH log access."""
import os
from pathlib import Path
import re
import shlex
from .ssh_index import ssh_json
from .member_creation import CreationError
from .log_worker import parts_for

LABELS={'1c':'1C · 直播监控','3c':'3C · 录制与上传','4c':'4C · 视频处理与上传'}


class LogService:
    def __init__(self,mode,settings=None):self.mode=mode;self.settings=settings if settings is not None else os.environ
    def connection(self,node):
        if node not in LABELS:raise CreationError('未知实例。',404)
        s=self.settings;prefix='SHOWROOM_SSH_' if node=='3c' else 'LOG_'+node.upper()+'_'
        host=s.get(prefix+'HOST','');user=s.get(prefix+'USER','ubuntu')
        if not host or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*',host):raise CreationError('此实例尚未配置 SSH 地址，请在设置页配置。',503)
        settings=dict(SHOWROOM_SSH_USER=user,SHOWROOM_SSH_IDENTITY_FILE=s.get(prefix+'IDENTITY_FILE',''),SHOWROOM_SSH_KNOWN_HOSTS=s.get('SHOWROOM_SSH_KNOWN_HOSTS',''))
        return host,settings
    def instances(self):
        return [dict(id=node,label=label,configured=self.mode=='demo' or bool(self.settings.get(('SHOWROOM_SSH_' if node=='3c' else 'LOG_'+node.upper()+'_')+'HOST'))) for node,label in LABELS.items()]
    def call(self,node,action,**params):
        if node not in LABELS:raise CreationError('未知实例。',404)
        if action not in ('files','tail'):raise CreationError('不支持的日志操作。')
        if action=='tail':
            try:parts_for(params.get('name'))
            except ValueError as exc:raise CreationError(str(exc)) from exc
            if type(params.get('lines')) is not int or not 1<=params['lines']<=1000:raise CreationError('行数必须为 1～1000。')
        if self.mode=='demo':
            if action=='files':return dict(files=[dict(name='demo.log',size=100,mtime=0)],limited=False)
            if params['name']!='demo.log':raise CreationError('演示日志不存在。',404)
            return dict(name='demo.log',text=f'[DEMO] {LABELS[node]}\n[INFO] 这是演示日志，未连接服务器。\n',size=100,mtime=0,limited=False,lines=2,window_bytes=262144)
        host,settings=self.connection(node)
        worker=Path(__file__).with_name('log_worker.py').read_text()
        return ssh_json(host,'python3 -c '+shlex.quote(worker),dict(action=action,**params),settings,timeout_message='日志读取超时，请稍后刷新。')
