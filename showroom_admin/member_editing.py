"""Optimistic, resumable edits. Production writes occur only on explicit submit."""
import hashlib
import json
import re
import uuid
from .member_creation import CreationError, normalize, normalize_youtube, save_youtube, utc

FIELDS=('member_id','name_jp','name_en','group_name','team','room_id','room_url_key','enabled')
IMMUTABLE=('member_id','name_en','room_id')


def snapshot(row):
    if not row:raise CreationError('成员不存在。',404)
    base={k:(int(row[k]) if k=='enabled' else str(row.get(k) or '')) for k in FIELDS}
    y=row.get('youtube')
    if y is not None:
        y={k:(y.get(k) if y.get(k) is not None else default) for k,default in dict(title_template='',description_template='',category_id='22',privacy_status='public',playlist_id='',use_primary_account=0).items()}
        y['tags']=row.get('tags',[])
    base['youtube']=y
    return base


def database_snapshot(repo,conn,member_id,lock=False):
    if lock:
        locked=repo.query(conn,'SELECT ID FROM ADMIN.MEMBERS WHERE MEMBER_ID=:member_id FOR UPDATE WAIT 3',{'member_id':member_id})
        if not locked:raise CreationError('成员不存在。',404)
    rows=repo.query(conn,'SELECT m.ID,m.MEMBER_ID,m.NAME_JP,m.NAME_EN,g.NAME AS GROUP_NAME,m.TEAM,m.ROOM_ID,m.ROOM_URL_KEY,m.ENABLED '
                   'FROM ADMIN.MEMBERS m JOIN ADMIN.GROUPS g ON g.ID=m.GROUP_ID WHERE m.MEMBER_ID=:member_id',{'member_id':member_id})
    if not rows:raise CreationError('成员不存在。',404)
    row=rows[0]
    cfg=repo.query(conn,'SELECT TITLE_TEMPLATE,DESCRIPTION_TEMPLATE,CATEGORY_ID,PRIVACY_STATUS,PLAYLIST_ID,USE_PRIMARY_ACCOUNT FROM ADMIN.YOUTUBE_CONFIGS WHERE MEMBER_ID=:id',{'id':row['id']})
    tags=repo.query(conn,'SELECT TAG FROM ADMIN.YOUTUBE_TAGS WHERE MEMBER_ID=:id ORDER BY SORT_ORDER,ID',{'id':row['id']})
    return snapshot(row|{'youtube':cfg[0] if cfg else None,'tags':[r['tag'] for r in tags]})


def version(base,index):
    return hashlib.sha256(json.dumps([base,index],ensure_ascii=False,sort_keys=True).encode()).hexdigest()


class MemberEditingService:
    def __init__(self,creation):
        self.creation=creation;self.repo=creation.repo;self.journal=creation.journal;self.index=creation.index
        with self.journal.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS edit_operations (request_id TEXT PRIMARY KEY,member_id TEXT NOT NULL,created_by TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)')

    def current(self,member_id):
        if self.repo.mode=='demo':return snapshot(self.repo.detail(member_id))
        with self.repo.read() as conn:return database_snapshot(self.repo,conn,member_id)

    def context(self,member_id):
        base=self.current(member_id);index=None;warning=''
        try:index=self.index.locate(base['room_id'])
        except CreationError as exc:warning=str(exc)
        return dict(member=base,index=index,version=version(base,index),upload_version=version(base,None),groups=self.repo.groups(),
                    can_edit=self.creation.writer is not None,index_warning=warning)

    def preview(self,member_id,data,user):
        if self.creation.writer is None:raise CreationError('真实成员写入尚未开启，请在设置中开启后重新打开应用。',503)
        if not isinstance(data,dict) or data.get('scope') not in ('all','youtube'):raise CreationError('编辑请求格式无效。')
        with self.journal.lock():
            before=self.current(member_id)
            index=self.index.locate(before['room_id']) if data['scope']=='all' else None
            if data.get('version')!=version(before,index):raise CreationError('资料已变化，请重新打开编辑页面后再保存。',409)
            supplied=data.get('member')
            if not isinstance(supplied,dict):raise CreationError('成员资料格式无效。')
            after=dict(before)
            if data['scope']=='all':
                for key in IMMUTABLE:
                    if str(supplied.get(key,''))!=before[key]:raise CreationError('成员 ID、房间号和英文名关联历史记录与视频文件，不支持直接修改。')
                normalized=normalize(supplied|{'index_file':index['filename']})
                if normalized['group_name'] not in self.repo.groups():raise CreationError('请选择已有组合。')
                if self.repo.mode=='oracle' and not normalized['team']:raise CreationError('当前录制项目要求 Team 非空。')
                after.update({k:normalized[k] for k in FIELDS})
                entry=dict(index['entry'])
                for field,key in [('name_jp','jpnName'),('group_name','jpnGroup'),('room_url_key','web_url')]:
                    if before[field]!=after[field]:entry[key]='https://www.showroom-live.com/'+after[field] if field=='room_url_key' else after[field]
                if before['group_name']!=after['group_name']:entry['engGroup']=after['group_name']
                entry.update(jpnTeam=normalized.get('index_team',index['entry'].get('jpnTeam','')),engTeam=normalized['eng_team'],priority=normalized['priority'])
                # Unrelated/custom index fields remain exactly as read.
                index=index|{'after':entry}
            if 'youtube' in supplied:after['youtube']=normalize_youtube(supplied['youtube'])
            if after['youtube'] is None and before['youtube'] is not None:raise CreationError('请编辑或清空具体上传字段，不能删除整份配置。')
            if before==after and (not index or index['entry']==index['after']):raise CreationError('没有需要保存的修改。')
            key=str(uuid.uuid4());payload=dict(before=before,after=after,index=index)
            with self.journal.connect() as db:
                db.execute('INSERT INTO edit_operations VALUES(?,?,?,?,?,?,?,?)',(key,member_id,user,json.dumps(payload,ensure_ascii=False),'prepared',None,utc(),utc()))
            return dict(request_id=key,changes={k:dict(before=before[k],after=after[k]) for k in before if before[k]!=after[k]},
                        index_changes={k:dict(before=index['entry'].get(k),after=v) for k,v in index['after'].items() if index['entry'].get(k)!=v} if index else {},
                        index_changed=bool(index and index['entry']!=index['after']),notice='确认后保存。上传规则由现有脚本执行；不会修改已上传的视频或重启服务。')

    def get(self,key,user):
        with self.journal.connect() as db:row=db.execute('SELECT * FROM edit_operations WHERE request_id=? AND created_by=?',(key,user)).fetchone()
        if not row:raise CreationError('编辑操作不存在。',404)
        job=dict(row);job['payload']=json.loads(job['payload']);return job

    def public(self,job):
        return {k:job[k] for k in ('request_id','member_id','state','error','created_at','updated_at')}

    def recent(self,user):
        with self.journal.connect() as db:rows=db.execute('SELECT request_id FROM edit_operations WHERE created_by=? ORDER BY created_at DESC LIMIT 30',(user,)).fetchall()
        return [self.public(self.get(r[0],user)) for r in rows]

    def update(self,key,state,error=None):
        with self.journal.connect() as db:db.execute('UPDATE edit_operations SET state=?,error=?,updated_at=? WHERE request_id=?',(state,error,utc(),key))

    def apply_index(self,job):
        index=job['payload']['index']
        if index and index['entry']!=index['after']:
            self.index.replace(index['filename'],index['entry'],index['after'],job['request_id'])
            self.update(job['request_id'],'index_written')

    def submit(self,key,user):
        if self.creation.writer is None:raise CreationError('真实成员写入尚未开启。',503)
        with self.journal.lock():
            job=self.get(key,user)
            if job['state']=='complete':return self.public(job)
            before=job['payload']['before'];after=job['payload']['after']
            try:
                if self.repo.mode=='demo':
                    current=self.current(job['member_id'])
                    if current not in (before,after):raise CreationError('成员已被其他操作修改，未覆盖；请重新预览。',409)
                    self.apply_index(job)
                    with self.journal.connect() as db:
                        db.execute('INSERT INTO demo_overrides(member_id,payload) VALUES(?,?) ON CONFLICT(member_id) DO UPDATE SET payload=excluded.payload',(job['member_id'],json.dumps(after,ensure_ascii=False)))
                else:
                    with self.creation.writer.write_repo._get_pool().acquire() as conn:
                        conn.call_timeout=5000
                        try:
                            current=database_snapshot(self.repo,conn,job['member_id'],lock=True)
                            if current not in (before,after):raise CreationError('成员已被其他操作修改，未覆盖；请重新预览。',409)
                            if before['room_url_key']!=after['room_url_key']:
                                rows=self.repo.query(conn,'SELECT ID FROM ADMIN.MEMBERS WHERE ROOM_URL_KEY=:url_key AND MEMBER_ID<>:member_id',dict(url_key=after['room_url_key'],member_id=job['member_id']))
                                if rows:raise CreationError('房间网址标识已被其他成员使用。',409)
                            self.apply_index(job)
                            if current!=after:
                                rows=self.repo.query(conn,'SELECT ID FROM ADMIN.MEMBERS WHERE MEMBER_ID=:member_id',{'member_id':job['member_id']});db_id=rows[0]['id']
                                if any(before[k]!=after[k] for k in FIELDS):
                                    with conn.cursor() as cur:
                                        cur.execute('UPDATE ADMIN.MEMBERS SET NAME_JP=:name_jp,GROUP_ID=(SELECT ID FROM ADMIN.GROUPS WHERE NAME=:group_name),TEAM=:team,ROOM_URL_KEY=:room_url_key,ENABLED=:enabled WHERE ID=:id',
                                                    {k:after[k] for k in ('name_jp','group_name','team','room_url_key','enabled')}|{'id':db_id})
                                if before['youtube']!=after['youtube']:save_youtube(conn,db_id,after['youtube'])
                            conn.commit()
                        except Exception:
                            conn.rollback();raise
                self.update(key,'complete')
            except Exception as exc:
                message=str(exc) if isinstance(exc,CreationError) else '保存未确认，已保留修改记录。请重试原操作；不要重复提交。'
                state=self.get(key,user)['state'];self.update(key,state,message)
            finally:
                if hasattr(self.repo,'_cache'):
                    with self.repo._lock:self.repo._cache.clear()
            return self.public(self.get(key,user))
