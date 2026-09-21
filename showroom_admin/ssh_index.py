"""Remote index transport; only the configured host/path are accepted."""
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
from .member_creation import CreationError


class SSHIndex:
    def __init__(self, host, directory, backups, settings=None):
        if not host or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*', host):
            raise ValueError('SHOWROOM_SSH_HOST must be an SSH host alias or user@host')
        if not directory.startswith('/') or not backups.startswith('/'):
            raise ValueError('Remote index and backup paths must be absolute')
        if Path(backups).is_relative_to(Path(directory)):
            raise ValueError('Remote backups must be outside the index directory')
        self.settings=settings
        self.host=host
        self.remote_directory=directory
        self.directory=f'{host}:{directory}'
        self.backups=backups

    def _call(self, action, **values):
        # Source is fixed application code, never obtained from the request or server.
        module=Path(__file__).with_name('member_creation.py').read_text()
        worker=Path(__file__).with_name('ssh_index_worker.py').read_text()
        command='python3 -c '+shlex.quote(module+'\n'+worker)
        request=dict(action=action,directory=self.remote_directory,backups=self.backups,**values)
        return ssh_json(self.host,command,request,self.settings)

    def files(self):return self._call('files')
    def check(self,payload,allow_existing=False):
        return self._call('check',payload=payload,allow_existing=allow_existing)
    def read(self,filename):
        data=self._call('read',filename=filename)
        raw=base64.b64decode(data['raw'],validate=True)
        return Path(self.remote_directory)/filename,raw,json.loads(raw.decode('utf-8-sig'))
    def append(self,payload,request_id):
        return self._call('append',payload=payload,request_id=request_id)
    def probe(self):return self._call('probe')

    def locate(self,room_id):return self._call('locate',room_id=room_id)
    def replace(self,filename,before_entry,after_entry,request_id):
        return self._call('replace',filename=filename,before_entry=before_entry,after_entry=after_entry,request_id=request_id)


def ssh_json(host,command,request,settings=None,timeout_message=None):
    extra=[]
    settings=settings if settings is not None else os.environ
    user=settings.get('SHOWROOM_SSH_USER','')
    if user and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*',user):raise CreationError('SSH 用户名格式无效。')
    if user:extra+=['-l',user]
    identity=settings.get('SHOWROOM_SSH_IDENTITY_FILE')
    known=settings.get('SHOWROOM_SSH_KNOWN_HOSTS')
    if identity:extra+=['-i',identity,'-o','IdentitiesOnly=yes']
    if known:
        escaped=str(Path(known).as_posix()).replace('"','\\"')
        extra+=['-o','UserKnownHostsFile="'+escaped+'"']
    if identity and known:extra=['-F','NUL' if os.name=='nt' else '/dev/null']+extra
    args=['/usr/bin/ssh' if os.name!='nt' else 'ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
          '-o','ConnectTimeout=8','-o','ConnectionAttempts=1','-o','ServerAliveInterval=5',
          '-o','ServerAliveCountMax=2',*extra,host,command]
    try:
        result=subprocess.run(args,input=json.dumps(request),text=True,encoding="utf-8",capture_output=True,timeout=35,check=False)
    except subprocess.TimeoutExpired as exc:
        raise CreationError(timeout_message or '服务器连接超时，远端写入结果可能尚未确认；请重试原操作，不要重复添加。',503) from exc
    except OSError as exc:
        raise CreationError('无法启动 SSH，请确认本机已安装 OpenSSH。',503) from exc
    if result.returncode:
        raise CreationError('服务器 SSH 执行失败；请用手顺书中的连接检查命令核对密钥、主机指纹和 Python。',503)
    try:
        answer=json.loads(result.stdout)
    except ValueError as exc:
        raise CreationError('服务器 返回内容无效；检查远端登录脚本是否输出了额外文字。',503) from exc
    if not isinstance(answer,dict) or 'ok' not in answer:
        raise CreationError('服务器 返回格式无效。',503)
    if not answer['ok']:
        raise CreationError(answer.get('error','服务器 索引操作失败。'),answer.get('code',503))
    return answer['data']
