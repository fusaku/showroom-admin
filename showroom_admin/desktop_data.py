"""Portable desktop data: private files, atomic settings, encrypted migration."""
from contextlib import ExitStack, closing
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from .member_creation import Journal

PATH_KEYS={'LOG_1C_IDENTITY_FILE','LOG_4C_IDENTITY_FILE','ORACLE_CREDENTIALS_FILE','WRITE_ORACLE_CREDENTIALS_FILE','ORACLE_CONFIG_DIR',
           'ORACLE_WALLET_LOCATION','MEMBER_STATE_DIR','SHOWROOM_SSH_IDENTITY_FILE','SHOWROOM_SSH_KNOWN_HOSTS'}
MAX_ARCHIVE=256*1024*1024
MAGIC=b'SRADM1\n'


def default_home():
    import sys
    if os.getenv('SHOWROOM_HOME'):return Path(os.environ['SHOWROOM_HOME']).expanduser()
    if sys.platform=='darwin':return Path.home()/'Library/Application Support/Showroom Console'
    return Path(os.getenv('LOCALAPPDATA',str(Path.home()/'.local/share')))/'Showroom Console'


def write_private(path,raw):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,tmp=tempfile.mkstemp(prefix='.save-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o600);os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def save(home,data):
    write_private(Path(home)/'settings.json',(json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode())


def initialize(home):
    home=Path(home);home.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not (home/'settings.json').exists():
        save(home,dict(UI_LANGUAGE='zh',LOG_1C_HOST='',LOG_1C_USER='ubuntu',LOG_1C_IDENTITY_FILE='secrets/1c.key',LOG_4C_HOST='',LOG_4C_USER='ubuntu',LOG_4C_IDENTITY_FILE='secrets/4c.key',DATA_MODE='demo',ADMIN_USERNAME='admin',HOST='127.0.0.1',PORT='8091',
            SESSION_SECRET=secrets.token_hex(32),COOKIE_SECURE='0',ALLOW_MEMBER_CREATE='0',
            ORACLE_DSN='srdb_high',ORACLE_DB_TIMEZONE='Asia/Tokyo',ORACLE_WALLET_PASSWORD='',
            ORACLE_CREDENTIALS_FILE='secrets/database.key',WRITE_ORACLE_CREDENTIALS_FILE='secrets/database.key',
            ORACLE_CONFIG_DIR='wallet',ORACLE_WALLET_LOCATION='wallet',
            SHOWROOM_INDEX_TRANSPORT='ssh',SHOWROOM_SSH_HOST='',SHOWROOM_SSH_USER='ubuntu',
            SHOWROOM_SSH_IDENTITY_FILE='secrets/3c.key',SHOWROOM_SSH_KNOWN_HOSTS='secrets/known_hosts',
            SHOWROOM_INDEX_DIR='/home/ubuntu/showroom/index',SHOWROOM_REMOTE_BACKUP_DIR='/home/ubuntu/showroom-admin-backups',
            MEMBER_STATE_DIR='state/onboarding'))
    return json.loads((home/'settings.json').read_text())


def copy_secret(source,target):
    source=Path(source).expanduser()
    if not source.is_file() or source.is_symlink():raise ValueError('请选择普通文件，不支持符号链接。')
    if source.stat().st_size>16*1024*1024:raise ValueError('配置文件过大。')
    write_private(target,source.read_bytes())


def copy_wallet(source,target):
    source=Path(source).expanduser();target=Path(target)
    if not source.is_dir() or source.is_symlink():raise ValueError('请选择 Wallet 文件夹。')
    if not (source/'tnsnames.ora').is_file() or not any((source/f).is_file() for f in ('ewallet.pem','cwallet.sso')):raise ValueError('Wallet 需要 tnsnames.ora 和 cwallet.sso 或 ewallet.pem。')
    files=[p for p in source.iterdir() if p.is_file() and not p.is_symlink()]
    if sum(p.stat().st_size for p in files)>32*1024*1024:raise ValueError('Wallet 文件夹过大。')
    for p in files:copy_secret(p,target/p.name)


def migrate_legacy(config_path,home,ssh_key=None,known_hosts=None):
    """Explicit one-time local migration; never called by application startup."""
    config_path=Path(config_path);home=Path(home)
    if (home/'settings.json').exists():raise ValueError('桌面数据已经存在，停止以免覆盖。')
    old=json.loads(config_path.read_text());new=initialize(home)
    for k in ('ADMIN_USERNAME','ORACLE_DSN','ORACLE_DB_TIMEZONE','ORACLE_WALLET_PASSWORD','SHOWROOM_SSH_HOST',
              'SHOWROOM_INDEX_DIR','SHOWROOM_REMOTE_BACKUP_DIR'):
        if old.get(k):new[k]=old[k]
    for k,name in [('ORACLE_CREDENTIALS_FILE','database.key'),('WRITE_ORACLE_CREDENTIALS_FILE','database-write.key')]:
        if old.get(k):copy_secret(old[k],home/'secrets'/name);new[k]='secrets/'+name
    if old.get('ORACLE_CONFIG_DIR'):copy_wallet(old['ORACLE_CONFIG_DIR'],home/'wallet')
    if ssh_key:copy_secret(ssh_key,home/'secrets/3c.key')
    if known_hosts:copy_secret(known_hosts,home/'secrets/known_hosts')
    state=Path(old.get('MEMBER_STATE_DIR',config_path.parent/'instance/onboarding'))
    if state.exists():
        with ExitStack() as stack:
            for mode in ('demo','oracle'):
                if (state/mode/'member_operations.sqlite3').exists():stack.enter_context(Journal(state/mode).lock())
            shutil.copytree(state,home/'state/onboarding',dirs_exist_ok=True)
    # No admin password and no live writes are imported into the desktop profile.
    new['ALLOW_MEMBER_CREATE']='0';new['DATA_MODE']='demo';save(home,new)


def _key(password,salt):
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    if not isinstance(password,str) or len(password)<12:raise ValueError('迁移密码至少 12 位。')
    return PBKDF2HMAC(algorithm=hashes.SHA256(),length=32,salt=salt,iterations=600000).derive(password.encode())


def export_archive(home,destination,password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    home=Path(home);buffer=io.BytesIO();data=initialize(home)
    if Path(destination).resolve().is_relative_to(home.resolve()):raise ValueError('请把迁移包保存到应用数据目录以外，例如文稿文件夹。')
    for k in PATH_KEYS:
        if data.get(k) and (Path(data[k]).is_absolute() or '..' in Path(data[k]).parts):
            raise ValueError('迁移配置含外部绝对路径，请先在应用设置中导入相关文件。')
    with ExitStack() as stack:
        for mode in ('demo','oracle'):
            root=home/'state/onboarding'/mode
            if (root/'member_operations.sqlite3').exists():stack.enter_context(Journal(root).lock())
        paths=[home/'settings.json']
        for name in ('secrets','wallet','state'):
            if (home/name).exists():paths.extend(p for p in (home/name).rglob('*') if p.is_file())
        if sum(p.stat().st_size for p in paths)>MAX_ARCHIVE:raise ValueError('迁移数据超过 256 MB，请先整理备份。')
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
            for p in paths:
                if p.is_symlink():raise ValueError('迁移目录含符号链接，已停止导出。')
                if p.name.endswith(('.lock','-wal','-shm','-journal')):continue
                if p.suffix=='.sqlite3':
                    with tempfile.TemporaryDirectory() as temp:
                        snapshot=Path(temp)/'snapshot.sqlite3'
                        with closing(sqlite3.connect(p)) as source,closing(sqlite3.connect(snapshot)) as target:source.backup(target)
                        archive.writestr(str(p.relative_to(home)),snapshot.read_bytes())
                else:archive.writestr(str(p.relative_to(home)),p.read_bytes())
    salt=os.urandom(16);nonce=os.urandom(12)
    raw=MAGIC+salt+nonce+AESGCM(_key(password,salt)).encrypt(nonce,buffer.getvalue(),MAGIC)
    write_private(destination,raw)


def import_archive(home,source,password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    home=Path(home);source=Path(source)
    if source.stat().st_size>MAX_ARCHIVE+1024*1024:raise ValueError('迁移文件过大。')
    raw=source.read_bytes()
    if not raw.startswith(MAGIC) or len(raw)<51:raise ValueError('不是有效迁移文件。')
    n=len(MAGIC);salt=raw[n:n+16];nonce=raw[n+16:n+28]
    try:decoded=AESGCM(_key(password,salt)).decrypt(nonce,raw[n+28:],MAGIC)
    except InvalidTag:raise ValueError('迁移密码不正确，或迁移文件已经损坏。')
    home.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.showroom-import-',dir=home.parent) as staging:
        staging=Path(staging);names=set()
        with zipfile.ZipFile(io.BytesIO(decoded)) as archive:
            entries=archive.infolist()
            if len(entries)>10000 or sum(i.file_size for i in entries)>MAX_ARCHIVE:raise ValueError('迁移内容过大。')
            for info in entries:
                p=Path(info.filename)
                if p.is_absolute() or '..' in p.parts or not p.parts or '\\' in info.filename or info.filename in names:
                    raise ValueError('迁移文件含无效路径。')
                if info.filename!='settings.json' and p.parts[0] not in ('secrets','wallet','state'):raise ValueError('迁移文件含未知内容。')
                if stat.S_ISLNK(info.external_attr>>16) or info.is_dir():raise ValueError('迁移文件不支持链接或目录条目。')
                names.add(info.filename);write_private(staging/p,archive.read(info))
        if 'settings.json' not in names:raise ValueError('缺少迁移配置。')
        data=json.loads((staging/'settings.json').read_text())
        from .local_config import ALLOWED
        if not isinstance(data,dict) or set(data)-ALLOWED or not all(isinstance(v,str) for v in data.values()):raise ValueError('迁移配置格式无效。')
        for k in PATH_KEYS:
            if data.get(k) and (Path(data[k]).is_absolute() or '..' in Path(data[k]).parts):raise ValueError('迁移配置必须使用相对路径。')
        # Portability never silently re-enables production writes or carries sessions.
        data['DATA_MODE']='demo';data['ALLOW_MEMBER_CREATE']='0';data['SESSION_SECRET']=secrets.token_hex(32)
        save(staging,data)
        previous=None
        if home.exists():
            previous=home.with_name(home.name+'-before-import-'+secrets.token_hex(4));os.replace(home,previous)
        try:os.replace(staging,home)
        except Exception:
            if previous:os.replace(previous,home)
            raise
    return str(previous) if previous else None
