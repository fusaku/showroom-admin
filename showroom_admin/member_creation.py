"""Resumable member + jdex onboarding. Never controls recorder processes."""
from __future__ import annotations

try:
    import fcntl
except ImportError:  # Windows: lock byte zero of the stable lock file.
    import msvcrt
    class fcntl:
        LOCK_EX=1
        LOCK_NB=2
        LOCK_UN=4
        @staticmethod
        def flock(handle, operation):
            handle.seek(0)
            if operation==fcntl.LOCK_UN:
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                if os.fstat(handle.fileno()).st_size==0:
                    handle.write('0');handle.flush();handle.seek(0)
                try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                except OSError as exc:raise BlockingIOError() from exc
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone


class CreationError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def utc():
    return datetime.now(timezone.utc).isoformat()


def normalize_youtube(data):
    if data is None:return None
    if not isinstance(data,dict):raise CreationError('上传配置格式无效。')
    def value(key,default='',limit=5000):
        v=data.get(key,default)
        if not isinstance(v,str) or len(v.encode('utf-8'))>limit or any(ord(c)<32 and c not in '\n\r\t' for c in v):
            raise CreationError('上传配置 '+key+' 过长或格式无效。')
        return v.strip()
    title=value('title_template',limit=400)
    if len(title)>100 or any(c in title for c in '<>'):raise CreationError('标题最多 100 字符，不能包含尖括号。')
    description=value('description_template')
    import string
    try:
        for _,field,spec,conversion in string.Formatter().parse(description):
            if field is not None and (field!='upload_time' or spec or conversion):raise ValueError()
    except ValueError:raise CreationError('描述仅支持 {upload_time}；普通花括号请写成 {{ 和 }}。')
    privacy=value('privacy_status','public',20)
    if privacy not in ('public','unlisted','private'):raise CreationError('公开状态无效。')
    category=value('category_id','22',10)
    if not re.fullmatch(r'[1-9][0-9]{0,9}',category):raise CreationError('分类 ID 必须为正整数。')
    playlist=value('playlist_id',limit=200)
    if playlist and not re.fullmatch(r'[A-Za-z0-9_-]+',playlist):raise CreationError('请填写播放列表 ID，不要填写完整网址。')
    primary=data.get('use_primary_account',1)
    if primary not in (0,1,True,False):raise CreationError('主账号标志无效。')
    tags=data.get('tags',[])
    if not isinstance(tags,list) or len(tags)>100:raise CreationError('标签必须为列表。')
    clean=[]
    for tag in tags:
        if not isinstance(tag,str) or not tag.strip() or len(tag.encode('utf-16-be'))//2>100 or any(ord(c)<32 or c in '<>' for c in tag):raise CreationError('标签不能为空、包含控制字符或超过 100 字符。')
        if tag.strip() not in clean:clean.append(tag.strip())
    if sum(len(t)+3 for t in clean)>500:raise CreationError('标签总长度超过 500 字符。')
    return dict(title_template=title,description_template=description,category_id=category,
                privacy_status=privacy,playlist_id=playlist,use_primary_account=int(primary),tags=clean)


def save_youtube(conn,db_id,youtube):
    if youtube is None:return
    values={k:v for k,v in youtube.items() if k!='tags'}|{'member_id':db_id}
    with conn.cursor() as cur:
        # Bind CLOBs explicitly, including long multibyte descriptions.
        import oracledb
        cur.setinputsizes(title_template=oracledb.DB_TYPE_CLOB,description_template=oracledb.DB_TYPE_CLOB)
        cur.execute('UPDATE ADMIN.YOUTUBE_CONFIGS SET TITLE_TEMPLATE=:title_template, DESCRIPTION_TEMPLATE=:description_template, '
                    'CATEGORY_ID=:category_id, PRIVACY_STATUS=:privacy_status, PLAYLIST_ID=:playlist_id, USE_PRIMARY_ACCOUNT=:use_primary_account WHERE MEMBER_ID=:member_id',values)
        if not cur.rowcount:
            cur.execute('INSERT INTO ADMIN.YOUTUBE_CONFIGS (MEMBER_ID,TITLE_TEMPLATE,DESCRIPTION_TEMPLATE,CATEGORY_ID,PRIVACY_STATUS,PLAYLIST_ID,USE_PRIMARY_ACCOUNT) '
                        'VALUES(:member_id,:title_template,:description_template,:category_id,:privacy_status,:playlist_id,:use_primary_account)',values)
    with conn.cursor() as cur:
        cur.execute('DELETE FROM ADMIN.YOUTUBE_TAGS WHERE MEMBER_ID=:id',{'id':db_id})
        for order,tag in enumerate(youtube['tags']):
            cur.execute('INSERT INTO ADMIN.YOUTUBE_TAGS (MEMBER_ID,TAG,SORT_ORDER) VALUES(:id,:tag,:sort_order)',dict(id=db_id,tag=tag,sort_order=order))


def normalize(data):
    if not isinstance(data, dict):
        raise CreationError("成员资料格式无效。")
    def text(key, limit, required=False):
        value=data.get(key, '')
        if not isinstance(value, str):
            raise CreationError(f"{key} 必须是文本。")
        value=value.strip()
        if (required and not value) or len(value.encode('utf-8'))>limit or any(ord(c)<32 for c in value):
            raise CreationError(f"{key} 为空、过长或包含不支持的字符。")
        return value
    result={
        'member_id':text('member_id',50,True),
        'name_jp':text('name_jp',300,True),
        'name_en':text('name_en',100,True),
        'group_name':text('group_name',50,True),
        'team':text('team',300),
        'eng_team':text('eng_team',100),
        'room_id':text('room_id',18,True),
        'room_url_key':text('room_url_key',200,True),
        'index_file':text('index_file',100,True),
    }
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,49}',result['member_id']):
        raise CreationError('成员 ID 仅支持小写英文字母、数字和下划线，以字母开头，最多 50 位。')
    # recorder currently embeds name_en in a double-quoted shell argument.
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 .'-]*",result['name_en']):
        raise CreationError('英文名只支持字母、数字、空格、点、单引号和连字符；不能包含命令字符。')
    if len(result['name_jp'].encode('utf-16-be'))//2>100 or len(result['team'].encode('utf-16-be'))//2>100:
        raise CreationError('日文姓名或队伍名称超过数据库 100 字符限制。')
    if not re.fullmatch(r'[1-9][0-9]{0,17}',result['room_id']):
        raise CreationError('房间号必须是正整数，不能有前导零。')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',result['room_url_key']):
        raise CreationError('请填写房间 URL 最后的一段，例如 48_Yui_Oguri，不要填写完整网址。')
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.jdex',result['index_file']):
        raise CreationError('索引文件名无效。')
    if data.get('enabled',True) not in (True,False,0,1):
        raise CreationError('启用开关无效。')
    result['enabled']=int(bool(data.get('enabled',True)))
    priority=data.get('priority',20)
    if isinstance(priority,bool) or not isinstance(priority,int) or not 1<=priority<=999:
        raise CreationError('优先级必须是 1～999 的整数。')
    result['priority']=priority
    if 'index_team' in data:result['index_team']=text('index_team',300)
    if 'youtube' in data:result['youtube']=normalize_youtube(data['youtube'])
    return result


def jdex_entry(p):
    return dict(jpnName=p['name_jp'],engName=p['name_en'],jpnGroup=p['group_name'],
                engGroup=p['group_name'],jpnTeam=p.get('index_team',p['team']),engTeam=p['eng_team'],
                priority=p['priority'],room_id=p['room_id'],
                web_url='https://www.showroom-live.com/'+p['room_url_key'])


class Journal:
    def __init__(self, root):
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.root/'member_operations.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS operations(
                  request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_by TEXT NOT NULL,
                  state TEXT NOT NULL, db_id INTEGER, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS demo_overrides(member_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS demo_members(
                  id INTEGER PRIMARY KEY AUTOINCREMENT, member_id TEXT UNIQUE NOT NULL,
                  room_id TEXT UNIQUE NOT NULL, name_en TEXT UNIQUE NOT NULL,
                  payload TEXT NOT NULL, enabled INTEGER NOT NULL, created_at TEXT NOT NULL);
            ''')
        os.chmod(self.path,0o600)

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=5)
        db.row_factory=sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def lock(self):
        # Stable lock inode. Covers all cooperating workers on this host.
        with open(self.root/'member_creation.lock','a') as handle:
            try:
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise CreationError('另一个成员正在保存，请稍后重试。',409)
            try:
                yield
            finally:
                fcntl.flock(handle,fcntl.LOCK_UN)

    def get(self, request_id):
        with self.connect() as db:
            row=db.execute('SELECT * FROM operations WHERE request_id=?',(request_id,)).fetchone()
        if row:
            result=dict(row); result['payload']=json.loads(result['payload']); return result
        return None

    def start(self, key, payload, user):
        with self.connect() as db:
            db.execute('INSERT INTO operations(request_id,payload,created_by,state,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                       (key,json.dumps(payload,ensure_ascii=False,sort_keys=True),user,'prepared',utc(),utc()))

    def update(self, key, **values):
        assert set(values)<= {'state','db_id','error'}
        values['updated_at']=utc()
        with self.connect() as db:
            db.execute('UPDATE operations SET '+','.join(k+'=?' for k in values)+' WHERE request_id=?',
                       [*values.values(),key])

    def recent(self):
        with self.connect() as db:
            keys=[r[0] for r in db.execute('SELECT request_id FROM operations ORDER BY created_at DESC LIMIT 30')]
        return [self.get(k) for k in keys]


class LocalIndex:
    def __init__(self, directory, backups):
        self.directory=Path(directory)
        self.backups=Path(backups)

    def files(self):
        if not self.directory.is_dir():
            raise CreationError('索引目录不可用；请在录制服务器部署后台并确认 SHOWROOM_INDEX_DIR。',503)
        files=[]
        for p in sorted(self.directory.glob('*.jdex')):
            if not p.is_symlink() and p.is_file() and re.fullmatch(r'[A-Za-z0-9_-]+\.jdex',p.name):
                files.append(p.name)
        if not files:
            raise CreationError('索引目录中没有可用的 .jdex 文件。',503)
        return files

    def read(self, filename):
        if filename not in self.files():
            raise CreationError('请从服务器现有 jdex 文件中选择，不能自行指定路径。')
        p=self.directory/filename
        if p.stat().st_size>20*1024*1024:
            raise CreationError(f'{filename} 超过索引读取大小限制。',409)
        raw=p.read_bytes()
        try:
            rows=json.loads(raw.decode('utf-8-sig'))
        except (ValueError,UnicodeError):
            raise CreationError(f'{filename} 不是有效的 JSON，未修改文件。',409)
        if not isinstance(rows,list) or not all(isinstance(r,dict) for r in rows):
            raise CreationError(f'{filename} 必须是对象数组。',409)
        return p, raw, rows

    def check(self, payload, allow_existing=False):
        expected=jdex_entry(payload)
        self.read(payload['index_file'])
        found=False
        for filename in self.files():
            _,_,rows=self.read(filename)
            for row in rows:
                same_room=str(row.get('room_id',row.get('showroom_id','')))==payload['room_id']
                same_name=str(row.get('engName','')).casefold()==payload['name_en'].casefold()
                same_url=str(row.get('web_url','')).rstrip('/').split('/')[-1]==payload['room_url_key']
                if same_room or same_name or same_url:
                    if allow_existing and filename==payload['index_file'] and row==expected and not found:
                        found=True
                    else:
                        raise CreationError(f'{filename} 中已存在该房间、英文名或房间标识；请核对后再添加。',409)
        return found

    @staticmethod
    def _fsync_directory(path):
        if os.name=="nt":return
        fd=os.open(path,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)

    def append(self, payload, request_id):
        if self.check(payload,allow_existing=True):
            return
        path, before, rows=self.read(payload['index_file'])
        rows.append(jdex_entry(payload))
        self._write(path,before,rows,request_id)

    def _write(self,path,before,rows,request_id):
        after=(json.dumps(rows,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
        # Backup is outside index and has no .jdex extension, so it cannot be loaded.
        self.backups.mkdir(parents=True,exist_ok=True,mode=0o700)
        backup=self.backups/(request_id+'-'+path.name+'.bak')
        if not backup.exists():
            with open(backup,'xb') as fp:
                fp.write(before);fp.flush();os.fsync(fp.fileno())
            os.chmod(backup,0o600)
        self._fsync_directory(self.backups)
        fd,temp=tempfile.mkstemp(prefix='.member-',suffix='.tmp',dir=self.directory)
        try:
            with os.fdopen(fd,'wb') as fp:
                fp.write(after);fp.flush();os.fsync(fp.fileno())
            os.chmod(temp,stat.S_IMODE(path.stat().st_mode))
            # Reject edits since this read. All automatic writers should use this service;
            # external editors must not write concurrently with onboarding.
            if path.is_symlink() or path.read_bytes()!=before:
                raise CreationError('索引在保存期间被其他程序修改，请重新检查后重试。',409)
            os.replace(temp,path)
            self._fsync_directory(self.directory)
            if path.read_bytes()!=after:
                raise CreationError('索引写入后校验不一致，需要人工核对。',409)
        finally:
            if os.path.exists(temp):os.unlink(temp)

    def locate(self,room_id):
        matches=[]
        for filename in self.files():
            _,_,rows=self.read(filename)
            for row in rows:
                if str(row.get('room_id',row.get('showroom_id','')))==str(room_id):
                    matches.append(dict(filename=filename,entry=row))
        if len(matches)!=1:raise CreationError('未找到唯一的录制索引条目；仍可单独编辑上传配置。',409)
        return matches[0]

    def replace(self,filename,before_entry,after_entry,request_id):
        room=str(before_entry.get('room_id',before_entry.get('showroom_id','')))
        if room!=str(after_entry.get('room_id',after_entry.get('showroom_id',''))):raise CreationError('编辑不能更换房间号。')
        match=self.locate(room)
        if match['filename']!=filename:raise CreationError('索引位置已改变，请重新加载。',409)
        if match['entry']==after_entry:return
        if match['entry']!=before_entry:raise CreationError('索引已被其他操作修改，请重新加载；未覆盖。',409)
        for name in self.files():
            _,_,entries=self.read(name)
            for entry in entries:
                if name==filename and entry==before_entry:continue
                if (str(entry.get('engName','')).casefold()==str(after_entry.get('engName','')).casefold() or
                    str(entry.get('web_url','')).rstrip('/')==str(after_entry.get('web_url','')).rstrip('/')):
                    raise CreationError('修改后的英文名或网址与其他索引重复。',409)
        path,raw,rows=self.read(filename)
        positions=[i for i,row in enumerate(rows) if row==before_entry]
        if len(positions)!=1:raise CreationError('索引在保存前已改变，请重试。',409)
        rows[positions[0]]=after_entry
        self._write(path,raw,rows,request_id)


class DemoMemberWriter:
    def __init__(self, journal, repo):
        self.journal=journal;self.repo=repo

    def check(self,p):
        for row in self.repo.all_rows():
            if (row['member_id']==p['member_id'] or str(row['room_id'])==p['room_id']
                    or row['name_en'].casefold()==p['name_en'].casefold()
                    or row['room_url_key']==p['room_url_key']):
                raise CreationError('成员 ID、房间号、英文名或房间标识已存在。',409)

    def stage(self,p,remember):
        self.check(p)
        with self.journal.connect() as db:
            cur=db.execute('INSERT INTO demo_members(member_id,room_id,name_en,payload,enabled,created_at) VALUES(?,?,?,?,0,?)',
                (p['member_id'],p['room_id'],p['name_en'],json.dumps(p,ensure_ascii=False),utc()))
            db_id=cur.lastrowid
        # Demo uses a unique member id to reconcile a crash before journal update.
        remember(db_id)
        return db_id

    def verify(self,p,db_id):
        with self.journal.connect() as db:
            row=db.execute('SELECT * FROM demo_members WHERE id=?',(db_id,)).fetchone()
        if not row:return False
        if json.loads(row['payload'])!=p:raise CreationError('成员资料已发生变化，需人工核对。',409)
        return True

    def find_staged(self,p):
        with self.journal.connect() as db:
            row=db.execute('SELECT id,payload FROM demo_members WHERE member_id=?',(p['member_id'],)).fetchone()
        if row and json.loads(row['payload'])==p:return row['id']
        return None

    def activate(self,p,db_id):
        with self.journal.connect() as db:
            db.execute('UPDATE demo_members SET enabled=? WHERE id=?',(p['enabled'],db_id))


class OracleMemberWriter:
    """A separately configured write pool. No DDL, recorder control or remote publishing."""
    def __init__(self,read_repo,write_repo):
        self.repo=read_repo;self.write_repo=write_repo

    def check(self,p):
        with self.repo.read() as conn:
            rows=self.repo.query(conn,'SELECT ID FROM ADMIN.MEMBERS WHERE MEMBER_ID=:member_id OR ROOM_ID=:room_id '
                 'OR LOWER(NAME_EN)=:name_en OR ROOM_URL_KEY=:room_url_key',
                 dict(member_id=p['member_id'],room_id=p['room_id'],name_en=p['name_en'].lower(),room_url_key=p['room_url_key']))
        if rows:raise CreationError('数据库中已存在该成员、房间或英文名。',409)

    def stage(self,p,remember):
        import oracledb
        self.check(p)
        try:
            with self.write_repo._get_pool().acquire() as conn:
                conn.call_timeout=5000
                try:
                    with conn.cursor() as cur:
                        cur.execute('SELECT ID FROM ADMIN.GROUPS WHERE NAME=:name',{'name':p['group_name']})
                        row=cur.fetchone()
                        if not row:raise CreationError('所选组合不存在。',409)
                        new_id=cur.var(oracledb.NUMBER)
                        cur.execute('INSERT INTO ADMIN.MEMBERS '
                            '(MEMBER_ID,GROUP_ID,ROOM_ID,NAME_JP,NAME_EN,TEAM,ENABLED,ROOM_URL_KEY) '
                            'VALUES(:member_id,:group_id,:room_id,:name_jp,:name_en,:team,0,:room_url_key) RETURNING ID INTO :new_id',
                            {k:p[k] for k in ('member_id','room_id','name_jp','name_en','team','room_url_key')}
                            | {'group_id':row[0],'new_id':new_id})
                        db_id=int(new_id.getvalue()[0])
                        save_youtube(conn,db_id,p.get('youtube'))
                        # Persist the allocated ID before commit, enabling reconciliation after a lost response.
                        remember(db_id)
                    conn.commit()
                    return db_id
                except Exception:
                    conn.rollback();raise
        except CreationError:raise
        except Exception as exc:
            raise CreationError('数据库保存未确认；成员操作已记录，请按同一操作重试。',503) from exc

    def verify(self,p,db_id):
        with self.repo.read() as conn:
            rows=self.repo.query(conn,'SELECT m.MEMBER_ID,m.ROOM_ID,m.NAME_JP,m.NAME_EN,m.TEAM,m.ROOM_URL_KEY,g.NAME AS GROUP_NAME '
                'FROM ADMIN.MEMBERS m JOIN ADMIN.GROUPS g ON g.ID=m.GROUP_ID WHERE m.ID=:id',{'id':db_id})
        if not rows:return False
        expected={k:p[k] for k in ('member_id','room_id','name_jp','name_en','team','room_url_key','group_name')}
        actual={k:(rows[0][k] or '') for k in expected}
        if p.get('youtube') is not None:
            from .member_editing import database_snapshot
            with self.repo.read() as conn:current=database_snapshot(self.repo,conn,p['member_id'])
            if current['youtube']!=p['youtube']:raise CreationError('上传配置已改变，请人工核对后继续。',409)
        if actual!=expected:raise CreationError('已暂存成员被其他操作修改，请人工核对，未继续写入。',409)
        return True

    def activate(self,p,db_id):
        try:
            with self.write_repo._get_pool().acquire() as conn:
                conn.call_timeout=5000
                try:
                    with conn.cursor() as cur:
                        cur.execute('SELECT ID FROM ADMIN.MEMBERS WHERE ID=:id FOR UPDATE WAIT 3',{'id':db_id})
                        if not cur.fetchone():raise CreationError('暂存成员已不存在，未设置开关。',409)
                    # Validate on the locked write connection to avoid overwriting changed records.
                    rows=self.repo.query(conn,'SELECT m.MEMBER_ID,m.ROOM_ID,m.NAME_JP,m.NAME_EN,m.TEAM,m.ROOM_URL_KEY,g.NAME AS GROUP_NAME '
                        'FROM ADMIN.MEMBERS m JOIN ADMIN.GROUPS g ON g.ID=m.GROUP_ID WHERE m.ID=:id',{'id':db_id})
                    if any((rows[0][k] or '')!=p[k] for k in ('member_id','room_id','name_jp','name_en','team','room_url_key','group_name')):
                        raise CreationError('成员资料已改变，未设置开关。',409)
                    with conn.cursor() as cur:
                        cur.execute('UPDATE ADMIN.MEMBERS SET ENABLED=:enabled WHERE ID=:id',{'enabled':p['enabled'],'id':db_id})
                    conn.commit()
                except Exception:
                    conn.rollback();raise
        except CreationError:raise
        except Exception as exc:
            raise CreationError('索引已写入，但数据库开关尚未确认，请重试同一操作。',503) from exc
        finally:
            with self.repo._lock:self.repo._cache.clear()


class MemberCreationService:
    def __init__(self, repo, journal, index, writer=None):
        self.repo=repo;self.journal=journal;self.index=index;self.writer=writer

    def options(self):
        error=None;files=[]
        try:files=self.index.files()
        except CreationError as exc:error=str(exc)
        return dict(groups=self.repo.groups(),index_files=files,index_directory=str(self.index.directory),
                    can_create=self.writer is not None and not error,
                    reason=error or (None if self.writer else '真实成员写入尚未开启，请在设置中开启添加与修改后重新打开应用。'),
                    mode=self.repo.mode)

    def preview(self,data):
        p=normalize(data)
        if p['group_name'] not in self.repo.groups():raise CreationError('请选择数据库中已有的组合。')
        if self.writer is None:raise CreationError('真实添加尚未开启，未修改数据库或索引。',503)
        if isinstance(self.writer,OracleMemberWriter) and not p['team']:
            raise CreationError('当前监控程序要求 TEAM 非空，请按同组合已有成员格式填写队伍，并核对 jdex 队伍映射。')
        self.writer.check(p)
        self.index.check(p)
        _,raw,_=self.index.read(p['index_file'])
        return dict(member=p,jdex=jdex_entry(p),index_file=p['index_file'],
                    before_sha256=hashlib.sha256(raw).hexdigest(),
                    notice='先停用暂存 → 写入 jdex → 设置成员开关。保存完成不代表运行中的录制程序已重新加载。')

    def public(self,job):
        if job is None:return None
        return {k:job[k] for k in ('request_id','state','error','created_at','updated_at','db_id')} | {
            'member_id':job['payload']['member_id'],'name_jp':job['payload']['name_jp'],
            'index_file':job['payload']['index_file'],'desired_enabled':job['payload']['enabled'],
            'reload_state':'未确认，未重启录制程序'}

    def submit(self,data,key,user):
        if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9-]{36}',key):
            raise CreationError('操作标识无效，请重新预览。')
        if self.writer is None:raise CreationError('真实添加尚未开启。',503)
        p=normalize(data)
        with self.journal.lock():
            job=self.journal.get(key)
            if job and (job['payload']!=p or job['created_by']!=user):
                raise CreationError('此操作标识已用于其他资料，请重新预览。',409)
            if job and job['state']=='complete':return self.public(job)
            if not job:
                self.preview(p)
                self.journal.start(key,p,user)
                job=self.journal.get(key)
            try:
                self.index.check(p,allow_existing=job['db_id'] is not None)
                db_id=job['db_id']
                if db_id and not self.writer.verify(p,db_id):db_id=None
                if not db_id and isinstance(self.writer,DemoMemberWriter):db_id=self.writer.find_staged(p)
                if not db_id:
                    db_id=self.writer.stage(p,lambda n:self.journal.update(key,db_id=n,state='database_pending'))
                self.journal.update(key,db_id=db_id,state='database_staged',error=None)
                self.index.append(p,key)
                self.journal.update(key,state='index_written')
                self.writer.activate(p,db_id)
                self.journal.update(key,state='complete',error=None)
            except Exception as exc:
                message=str(exc) if isinstance(exc,CreationError) else '当前步骤失败，已保留操作记录；请检查目录权限或数据源后重试。'
                self.journal.update(key,error=message)
            return self.public(self.journal.get(key))
