"""Native desktop entry point shared by macOS and Windows."""
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import threading
from urllib.parse import urlsplit
from .desktop_data import default_home,initialize,export_archive,import_archive
from .member_creation import fcntl


class Bridge:
    def __init__(self,home,app,origin):
        self._home=home;self._app=app;self._origin=origin;self._window=None
        self._restart=False;self._actions=threading.Lock()
    def _trusted(self):
        url=self._window.get_current_url() or ''
        parts=urlsplit(url)
        if f'{parts.scheme}://{parts.netloc}'!=self._origin:raise ValueError('仅允许本机应用窗口执行此操作。')
    def _pause(self):
        with self._app.extensions['desktop_mutex']:
            state=self._app.extensions['desktop_state']
            if state['active'] or state['closing']:raise ValueError('成员正在保存或应用正在处理其他操作，请稍后重试。')
            state['closing']=True
    def _resume(self):
        with self._app.extensions['desktop_mutex']:self._app.extensions['desktop_state']['closing']=False
    def choose_path(self,kind):
        self._trusted()
        if kind not in ('wallet_path','key_path','known_hosts_path','log_1c_key_path','log_4c_key_path'):raise ValueError('不支持的文件选择。')
        import webview
        result=self._window.create_file_dialog(webview.FileDialog.FOLDER if kind=='wallet_path' else webview.FileDialog.OPEN,allow_multiple=False)
        return result[0] if result else ''
    def export_data(self,password):
        self._trusted()
        if not self._actions.acquire(False):return {'error':'其他操作尚未完成。'}
        paused=False
        try:
            if len(password)<12:return {'error':'迁移密码至少 12 位。'}
            import webview
            result=self._window.create_file_dialog(webview.FileDialog.SAVE,save_filename='Showroom-migration.srbackup',file_types=('Showroom backup (*.srbackup)',))
            if not result:return {'cancelled':True}
            path=result if isinstance(result,str) else result[0]
            self._pause();paused=True;export_archive(self._home,path,password)
            return {'message':'已导出加密迁移包，请单独保管迁移密码。','path':path}
        except Exception as exc:return {'error':str(exc) if isinstance(exc,ValueError) else '导出失败，请检查保存位置和可用空间。'}
        finally:
            if paused:self._resume()
            self._actions.release()
    def import_data(self,password):
        self._trusted()
        if not self._actions.acquire(False):return {'error':'其他操作尚未完成。'}
        paused=False;success=False
        try:
            import webview
            result=self._window.create_file_dialog(webview.FileDialog.OPEN,allow_multiple=False,file_types=('Showroom backup (*.srbackup)',))
            if not result:return {'cancelled':True}
            if not self._window.create_confirmation_dialog('导入迁移包','当前本机配置将先备份，再导入所选数据。生产添加会保持关闭，完成后应用会退出，请重新打开。'):return {'cancelled':True}
            self._pause();paused=True;previous=import_archive(self._home,result[0],password);success=True
            threading.Timer(1.5,self._window.destroy).start()
            return {'message':'导入完成。应用即将退出，请重新打开并检查连接。','previous':previous}
        except Exception as exc:return {'error':str(exc) if isinstance(exc,ValueError) else '导入失败，原配置未主动删除；请查看本机备份。'}
        finally:
            if paused and not success:self._resume()
            self._actions.release()
    def connection_check(self):
        self._trusted()
        from .desktop_checks import connection_report
        return connection_report(self._home)
    def quit_app(self):
        self._trusted();self._pause();threading.Timer(.2,self._window.destroy).start();return {'message':'应用正在退出，请重新打开以使用新配置。'}


def configure_path_locale():
    # Finder does not inherit Terminal locale; Oracle encodes client paths with LC_CTYPE.
    # Set once, before any desktop worker threads start, so Chinese app/user paths work.
    if sys.platform == 'darwin':
        import locale
        locale.setlocale(locale.LC_CTYPE, 'en_US.UTF-8')


def main():
    configure_path_locale()
    home=default_home();os.environ['SHOWROOM_HOME']=str(home)
    initialize(home)
    lockpath=home.parent/('.'+home.name+'.desktop.lock')
    app_lock=open(lockpath,'a+')
    try:fcntl.flock(app_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        import webview
        webview.create_window('Showroom 管理',html='<h2>应用已在运行</h2><p>请切换到已经打开的 Showroom 管理窗口。</p>',width=450,height=200)
        webview.start();return
    try:
        from . import local_config
        local_config.SETTINGS=home/'settings.json'
        # Desktop profiles are independent of shell web-server settings.
        for key in local_config.ALLOWED:os.environ.pop(key,None)
        local_config.load_local_settings(load_credentials=False)
        setup_error=''
        if os.getenv('DATA_MODE')=='oracle':
            try:local_config.load_local_settings()
            except (ValueError,OSError):
                setup_error='真实数据库连接配置不完整，当前仅打开演示设置页面；请补齐凭证后重新打开应用。'
                os.environ['DATA_MODE']='demo'
        from .app import create_app
        token=secrets.token_urlsafe(48)
        app=create_app({'DESKTOP_TOKEN':token,'SECRET_KEY':secrets.token_hex(32),'DESKTOP_SETUP_ERROR':setup_error})
        from waitress import create_server
        server=create_server(app,host='127.0.0.1',port=0,threads=4,max_request_body_size=65536)
        origin='http://127.0.0.1:'+str(server.effective_port);app.config['DESKTOP_ORIGIN']=origin
        thread=threading.Thread(target=server.run,name='Local-Admin',daemon=True);thread.start()
        import webview
        bridge=Bridge(home,app,origin)
        window=webview.create_window('Showroom 管理',origin+'/desktop/welcome',js_api=bridge,width=1200,height=840,min_size=(760,600))
        bridge._window=window
        def loaded():
            if window.get_current_url()==origin+'/desktop/welcome':
                target='/#settings' if setup_error else '/#add'
                window.run_js("fetch('/desktop/session',{method:'POST',headers:{'Content-Type':'application/json'},body:"+json.dumps(json.dumps({'token':token}))+"}).then(r=>{if(r.ok)location.replace("+json.dumps(target)+");else document.body.textContent='应用会话初始化失败，请退出后重新打开。';});")
        def closing():
            with app.extensions['desktop_mutex']:
                if app.extensions['desktop_state']['active']:
                    window.create_confirmation_dialog('正在保存成员','请等待保存结果后再退出。');return False
                app.extensions['desktop_state']['closing']=True
        window.events.loaded+=loaded;window.events.closing+=closing
        # No external navigation is generated by the UI. Native bridge methods additionally verify the current origin.
        webview.settings['ALLOW_DOWNLOADS']=False
        try:webview.start(debug=False,private_mode=True,gui='edgechromium' if sys.platform=='win32' else None)
        finally:
            server.close();server.task_dispatcher.shutdown(timeout=5)
    finally:
        fcntl.flock(app_lock,fcntl.LOCK_UN);app_lock.close()


if __name__=='__main__':main()
