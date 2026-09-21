import json
import os
import re
from pathlib import Path
from .desktop_data import PATH_KEYS
from .ssh_index import SSHIndex
from .repository import OracleRepository
from .oracle_client import client_directory


def connection_report(home):
    home=Path(home);data=json.loads((home/'settings.json').read_text());report=[]
    for k in PATH_KEYS:
        if data.get(k):data[k]=str(home/data[k]) if not Path(data[k]).is_absolute() else data[k]
    try:
        # SSH identity paths are stable inside the desktop profile, also across settings edits.
        index=SSHIndex(data.get('SHOWROOM_SSH_HOST',''),data['SHOWROOM_INDEX_DIR'],data['SHOWROOM_REMOTE_BACKUP_DIR'],settings=data)
        probe=index.probe()
        report.append('✓ 3C 连接成功，发现 '+str(len(probe['files']))+' 个索引文件。')
        if not probe['directory_writable'] or not probe['backup_parent_writable']:report.append('⚠ 索引或备份目录权限不足。')
    except Exception:report.append('✗ 3C 连接未通过，请检查主机、用户名、密钥和已信任主机文件。')
    try:
        values=Path(data['ORACLE_CREDENTIALS_FILE']).read_text().splitlines()
        settings={k:data.get(k,'') for k in ('ORACLE_DSN','ORACLE_CONFIG_DIR','ORACLE_WALLET_LOCATION','ORACLE_WALLET_PASSWORD','ORACLE_CLIENT_LIB_DIR','ORACLE_DB_TIMEZONE')}
        settings.update(ORACLE_USER=values[0],ORACLE_PASSWORD=values[1])
        pem=Path(settings['ORACLE_WALLET_LOCATION'])/'ewallet.pem'
        if not client_directory(settings) and pem.exists() and 'ENCRYPTED PRIVATE KEY' in pem.read_text() and not settings['ORACLE_WALLET_PASSWORD']:
            report.append('✗ Wallet 密码尚未填写。')
        else:
            repo=OracleRepository(settings)
            try:
                with repo.read() as conn:repo.query(conn,'SELECT ID FROM ADMIN.MEMBERS WHERE 1=0')
                report.append('✓ Oracle 连接与成员查询通过，未修改任何数据。')
            finally:
                if repo._pool:repo._pool.close()
    except Exception as exc:
        codes=[]; kinds=[]
        while exc:
            kinds.append(type(exc).__name__)
            codes.extend(re.findall(r'(?:ORA|DPI|DPY)-[0-9]+',str(exc)))
            exc=exc.__cause__
        diagnostic=', '.join(dict.fromkeys(codes or kinds))
        report.append('✗ Oracle 连接未通过（'+diagnostic+'），请检查凭证、Wallet 和数据库网络限制。')
    return {'message':'\n'.join(report)+'\n此检查不新增成员、不更新索引，也不重启云端服务。'}
