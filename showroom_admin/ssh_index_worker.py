# Executed after the stdlib-only member_creation module over SSH; no file installation.
import base64


def remote_main():
    try:
        req=json.loads(sys.stdin.read(32768))
        index=LocalIndex(req['directory'],req['backups'])
        action=req['action']
        if action=='files':data=index.files()
        elif action=='probe':
            data=dict(files=index.files(),directory_writable=os.access(index.directory,os.W_OK|os.X_OK),
                      backup_parent_writable=os.access(index.backups if index.backups.exists() else index.backups.parent,os.W_OK|os.X_OK))
        elif action=='read':
            _,raw,_=index.read(req['filename']);data=dict(raw=base64.b64encode(raw).decode('ascii'))
        elif action=='check':data=index.check(normalize(req['payload']),bool(req.get('allow_existing')))
        elif action=='locate':data=index.locate(req['room_id'])
        elif action in ('append','replace'):
            key=req['request_id']
            if action=='append':payload=normalize(req['payload'])
            if not re.fullmatch(r'[a-f0-9-]{36}',key):raise CreationError('操作标识无效。')
            index.backups.mkdir(parents=True,exist_ok=True,mode=0o700)
            # Stable remote lock also coordinates different management computers.
            with open(index.backups/'index.lock','a') as handle:
                try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:raise CreationError('另一台管理后台正在更新索引，请稍后重试。',409)
                try:
                    if action=='append':index.append(payload,key)
                    else:index.replace(req['filename'],req['before_entry'],req['after_entry'],key)
                finally:fcntl.flock(handle,fcntl.LOCK_UN)
            data=True
        else:raise CreationError('不支持的索引操作。')
        print(json.dumps(dict(ok=True,data=data),ensure_ascii=False))
    except CreationError as exc:
        print(json.dumps(dict(ok=False,error=str(exc),code=exc.code),ensure_ascii=False))
    except Exception:
        print(json.dumps(dict(ok=False,error='远端索引操作失败，请检查目录权限和文件格式。',code=503),ensure_ascii=False))

import sys
remote_main()
