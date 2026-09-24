"""Build the native app for the current Mac architecture; no user data bundled."""
from pathlib import Path
import os
import platform
import plistlib
import subprocess
import sys
import shutil
root=Path(__file__).resolve().parents[1]
if sys.platform!='darwin':raise SystemExit('Run this build on macOS.')
client=Path(os.environ.get('SHOWROOM_BUILD_ORACLE_CLIENT',''))
if not (client/'libclntsh.dylib').is_file():raise SystemExit('Set SHOWROOM_BUILD_ORACLE_CLIENT to the official macOS ARM64 Instant Client directory.')
subprocess.run([sys.executable,str(root/'tools/make_icon.py')],check=True)
args=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir',
      '--icon',str(root/'build/Showroom.icns'),'--name','Showroom 管理','--osx-bundle-identifier','local.showroom.console',
      '--distpath',str(root/'dist'/('macOS-'+platform.machine())),
      '--workpath',str(root/'build/macOS'),'--specpath',str(root/'build'),
      '--collect-all','webview','--collect-all','oracledb','--hidden-import','waitress']
for module in ('PyQt5','PyQt6','PySide2','PySide6','tkinter'):args+=['--exclude-module',module]
for relative in ('showroom_admin/templates','showroom_admin/static','showroom_admin/member_creation.py','showroom_admin/ssh_index_worker.py','showroom_admin/log_worker.py'):
    p=root/relative;destination=str(p.relative_to(root) if p.is_dir() else p.parent.relative_to(root))
    args+=['--add-data',str(p)+':'+destination]
args+=[str(root/'desktop_bootstrap.py')]
subprocess.run(args,check=True,cwd=root)

app=root/'dist'/('macOS-'+platform.machine())/'Showroom 管理.app'
shutil.copytree(client,app/'Contents/Resources/oracle-client',symlinks=True)
plist=app/'Contents/Info.plist'
data=plistlib.loads(plist.read_bytes());data.update(CFBundleShortVersionString='0.4.6',CFBundleVersion='11',LSMinimumSystemVersion='15.0',NSHighResolutionCapable=True)
plist.write_bytes(plistlib.dumps(data))
subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-',str(app)],check=True)
subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(app)],check=True)
print('App:',app)
