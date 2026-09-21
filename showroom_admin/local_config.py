"""Load local settings as data, never execute shell configuration."""
import json
import os
from pathlib import Path
import secrets
from getpass import getpass
from werkzeug.security import generate_password_hash

ROOT=Path(__file__).resolve().parent.parent
SETTINGS=(Path(os.environ['SHOWROOM_HOME'])/'settings.json') if os.getenv('SHOWROOM_HOME') else ROOT/'local_settings.json'
ALLOWED={'UI_LANGUAGE','LOG_1C_HOST','LOG_1C_USER','LOG_1C_IDENTITY_FILE','LOG_4C_HOST','LOG_4C_USER','LOG_4C_IDENTITY_FILE','DATA_MODE','ADMIN_USERNAME','ADMIN_PASSWORD_HASH','SESSION_SECRET','COOKIE_SECURE','HOST','PORT',
         'ORACLE_USER','ORACLE_PASSWORD','ORACLE_DSN','ORACLE_CONFIG_DIR','ORACLE_WALLET_LOCATION',
         'ORACLE_WALLET_PASSWORD','ORACLE_CLIENT_LIB_DIR','ORACLE_DB_TIMEZONE','ORACLE_CREDENTIALS_FILE',
         'WRITE_ORACLE_USER','WRITE_ORACLE_PASSWORD','WRITE_ORACLE_CREDENTIALS_FILE','ALLOW_MEMBER_CREATE',
         'SHOWROOM_INDEX_TRANSPORT','SHOWROOM_SSH_HOST','SHOWROOM_INDEX_DIR','SHOWROOM_REMOTE_BACKUP_DIR',
         'MEMBER_STATE_DIR','STALE_SECONDS','SHOWROOM_SSH_USER','SHOWROOM_SSH_IDENTITY_FILE','SHOWROOM_SSH_KNOWN_HOSTS'}


def load_local_settings(load_credentials=True):
    if not SETTINGS.exists():return
    data=json.loads(SETTINGS.read_text())
    if not isinstance(data,dict) or set(data)-ALLOWED:raise ValueError('local_settings.json contains unknown settings')
    if os.name!='nt' and SETTINGS.stat().st_mode & 0o077:raise ValueError('Run chmod 600 local_settings.json before starting')
    for key,value in data.items():
        if not isinstance(value,str):raise ValueError(f'{key} must be a string')
        from .desktop_data import PATH_KEYS
        if key in PATH_KEYS and value and not Path(value).is_absolute():value=str(SETTINGS.parent/value)
        if value:os.environ.setdefault(key,value)
    if not load_credentials:return
    for prefix,filename_key in [('ORACLE_','ORACLE_CREDENTIALS_FILE'),('WRITE_ORACLE_','WRITE_ORACLE_CREDENTIALS_FILE')]:
        filename=os.getenv(filename_key)
        if filename:
            lines=Path(filename).expanduser().read_text().splitlines()
            if len(lines)<2 or not all(v.strip() for v in lines[:2]):raise ValueError(f'{filename_key} requires username and password on separate lines')
            os.environ.setdefault(prefix+'USER',lines[0].strip())
            os.environ.setdefault(prefix+'PASSWORD',lines[1].strip())


def configure():
    data=json.loads(SETTINGS.read_text()) if SETTINGS.exists() else json.loads((ROOT/'local_settings.example.json').read_text())
    print('配置保存在本机 local_settings.json，不会上传。直接回车保留原配置。')
    password=getpass('管理页面登录密码（至少12位；回车保留）: ')
    if password:
        if len(password)<12 or password!=getpass('再次输入登录密码: '):raise ValueError('密码至少12位且两次一致')
        data['ADMIN_PASSWORD_HASH']=generate_password_hash(password)
    wallet=getpass('Oracle Wallet 密码（回车保留）: ')
    if wallet:data['ORACLE_WALLET_PASSWORD']=wallet
    data.setdefault('SESSION_SECRET',secrets.token_hex(32))
    if not data.get('SESSION_SECRET'):data['SESSION_SECRET']=secrets.token_hex(32)
    temporary=SETTINGS.with_suffix('.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(temporary,SETTINGS)
    finally:
        if temporary.exists():temporary.unlink()
    print('配置已保存。连接检查不会新增成员，也不会修改服务器索引。')
