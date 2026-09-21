"""Read-only bounded log reader executed over SSH; standard library only."""
import json
import os
import stat
import sys

ROOT='/home/ubuntu/logs'
MAX_BYTES=256*1024
MAX_FILES=500


def parts_for(name):
    if not isinstance(name,str) or not name or len(name)>600 or '\\' in name or any(ord(c)<32 for c in name):raise ValueError('日志文件名无效。')
    parts=name.split('/')
    if name.startswith('/') or any(p in ('','.','..') for p in parts) or len(parts)>3:raise ValueError('不允许访问日志目录以外的位置。')
    if name.endswith(('.gz','.zip','.xz','.bz2')):raise ValueError('暂不读取压缩历史日志。')
    return parts


def open_log(root,name):
    parts=parts_for(name)
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=next_fd
        result=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        if not stat.S_ISREG(os.fstat(result).st_mode):os.close(result);raise ValueError('只能查看普通日志文件。')
        return result
    finally:os.close(fd)


def inventory(root):
    root_fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    os.close(root_fd)
    files=[];scanned=0;clipped=False
    for folder,dirs,names in os.walk(root,followlinks=False):
        relative=os.path.relpath(folder,root)
        depth=0 if relative=='.' else len(relative.split(os.sep))
        dirs[:]=sorted(d for d in dirs if not os.path.islink(os.path.join(folder,d))) if depth<2 else []
        for name in sorted(names):
            scanned+=1
            if scanned>10000:clipped=True;break
            path=os.path.join(folder,name);relative=os.path.relpath(path,root)
            try:
                parts_for(relative);fd=open_log(root,relative)
                try:st=os.fstat(fd)
                finally:os.close(fd)
            except (OSError,ValueError):continue
            files.append(dict(name=relative,size=st.st_size,mtime=st.st_mtime))
        if clipped:break
    if not os.path.isdir(root):raise ValueError('日志目录不存在或不可访问。')
    files.sort(key=lambda r:('/' in r['name'],-r['mtime'],r['name']))
    return dict(files=files[:MAX_FILES],limited=clipped or len(files)>MAX_FILES)


def tail(root,name,lines):
    if isinstance(lines,bool) or not isinstance(lines,int) or not 1<=lines<=1000:raise ValueError('行数必须为 1～1000。')
    fd=open_log(root,name)
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno());offset=max(0,st.st_size-MAX_BYTES)
        f.seek(offset);raw=f.read(MAX_BYTES)
    if offset:
        split=raw.find(b'\n')
        if split>=0:raw=raw[split+1:]
    if b'\x00' in raw:raise ValueError('文件可能为二进制，未显示。')
    text=raw.decode('utf-8',errors='replace');rows=text.splitlines(keepends=True)
    return dict(name=name,text=''.join(rows[-lines:]),size=st.st_size,mtime=st.st_mtime,
                limited=bool(offset or len(rows)>lines),lines=min(len(rows),lines),window_bytes=MAX_BYTES)


def main():
    try:
        request=json.loads(sys.stdin.read(4096))
        if request.get('action')=='files':data=inventory(ROOT)
        elif request.get('action')=='tail':data=tail(ROOT,request.get('name'),request.get('lines',500))
        else:raise ValueError('不支持的日志操作。')
        print(json.dumps({'ok':True,'data':data},ensure_ascii=False))
    except ValueError as exc:print(json.dumps({'ok':False,'error':str(exc),'code':400},ensure_ascii=False))
    except Exception:print(json.dumps({'ok':False,'error':'日志读取失败，文件可能已轮转或权限不足。请刷新文件列表重试。','code':503},ensure_ascii=False))

if __name__=='__main__':main()
