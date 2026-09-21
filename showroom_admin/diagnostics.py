"""Read-only checks for local-to-OCI wiring."""
import os
from pathlib import Path
from .repository import OracleRepository
from .ssh_index import SSHIndex


def check_connections():
    failures=0
    try:
        index=SSHIndex(os.environ['SHOWROOM_SSH_HOST'],os.environ['SHOWROOM_INDEX_DIR'],
                       os.environ['SHOWROOM_REMOTE_BACKUP_DIR'])
        result=index.probe()
        print(f"[OK] 3C SSH / Python / 索引目录：发现 {len(result['files'])} 个 jdex")
        print('[OK]' if result['directory_writable'] else '[FAIL]', '索引目录写入权限（仅检查，未写文件）')
        print('[OK]' if result['backup_parent_writable'] else '[FAIL]', '备份目录或父目录写入权限（仅检查）')
        failures+=int(not result['directory_writable'])+int(not result['backup_parent_writable'])
    except Exception as exc:
        print('[FAIL] 3C 连接检查失败：'+(str(exc) if type(exc).__name__=='CreationError' else type(exc).__name__))
        failures+=1
    settings={k:os.getenv(k,'') for k in ('ORACLE_USER','ORACLE_PASSWORD','ORACLE_DSN','ORACLE_CONFIG_DIR',
              'ORACLE_WALLET_LOCATION','ORACLE_WALLET_PASSWORD','ORACLE_CLIENT_LIB_DIR')}
    settings['ORACLE_DB_TIMEZONE']=os.getenv('ORACLE_DB_TIMEZONE','Asia/Tokyo')
    wallet=Path(settings['ORACLE_WALLET_LOCATION'])/'ewallet.pem'
    if wallet.exists() and 'ENCRYPTED PRIVATE KEY' in wallet.read_text() and not settings['ORACLE_WALLET_PASSWORD'] and not settings['ORACLE_CLIENT_LIB_DIR']:
        print('[FAIL] Wallet 密码尚未配置；请执行 ./start.sh --configure 在本机输入。')
        return 1
    if not all(settings[k] for k in ('ORACLE_USER','ORACLE_PASSWORD','ORACLE_DSN')):
        print('[FAIL] 缺少数据库用户名、密码或 DSN。');return 1
    try:
        repo=OracleRepository(settings)
        with repo.read() as conn:
            for table in ('GROUPS','MEMBERS','LIVE_STATUS','INSTANCES','MEMBER_INSTANCES','MEMBER_INSTANCES_HISTORY',
                          'SHOWROOM_LIVE_HISTORY','YOUTUBE_CONFIGS','YOUTUBE_TAGS','V_INSTANCE_LOAD'):
                repo.query(conn,f'SELECT * FROM ADMIN.{table} WHERE 1=0')
        print('[OK] Oracle 连接与页面所需表/视图查询权限；未执行 INSERT / UPDATE / DELETE。')
    except Exception:
        print('[FAIL] Oracle 查询失败；核对 Wallet 密码、DSN、网络访问限制及账号查询权限。')
        failures+=1
    print('说明：此检查不验证实际写入、1C 配置刷新或 3C 录制加载；不会启用生产添加。')
    return int(failures>0)
