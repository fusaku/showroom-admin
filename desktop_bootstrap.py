import os
import sys
import traceback
from pathlib import Path

if __name__=='__main__':
    try:
        from showroom_admin.desktop_entry import main
        main()
    except Exception as exc:
        # Do not dump connection parameters or secret-bearing exception strings.
        home=Path(os.getenv('LOCALAPPDATA',str(Path.home()/'.local/share')))/'Showroom Console'
        if sys.platform=='darwin':home=Path.home()/'Library/Application Support/Showroom Console'
        home.mkdir(parents=True,exist_ok=True)
        (home/'startup-error.txt').write_text('启动失败：'+type(exc).__name__+'\n'+'\n'.join(f'{f.filename}:{f.lineno} {f.name}' for f in traceback.extract_tb(exc.__traceback__)),encoding='utf-8')
        if sys.platform=='win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(0,'启动未完成。请检查 WebView2 / .NET 运行环境，并查看用户数据目录的 startup-error.txt。','Showroom 管理',16)
        else:raise
