import hmac
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from collections import OrderedDict
from datetime import timedelta
from functools import wraps

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash
from .repository import DemoRepository, OracleRepository, DataUnavailable
from .ssh_index import SSHIndex
from .member_creation import Journal, LocalIndex, DemoMemberWriter, OracleMemberWriter, MemberCreationService, CreationError


class LoginLimiter:
    def __init__(self):
        self.attempts = OrderedDict()
        self.lock = threading.Lock()

    def allowed(self, key):
        with self.lock:
            stamp = time.monotonic()
            attempts = [v for v in self.attempts.get(key, []) if stamp - v < 300]
            if len(attempts) >= 8:
                return False
            attempts.append(stamp)
            self.attempts[key] = attempts
            self.attempts.move_to_end(key)
            if len(self.attempts) > 2000:
                self.attempts.popitem(last=False)
            return True


def create_app(overrides=None, repository=None):
    app = Flask(__name__)
    app.config.update(DATA_MODE=os.getenv("DATA_MODE", "demo"),
        ADMIN_USERNAME=os.getenv("ADMIN_USERNAME", "admin"),
        ADMIN_PASSWORD_HASH=os.getenv("ADMIN_PASSWORD_HASH", ""),
        SECRET_KEY=os.getenv("SESSION_SECRET") or secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=65536,
        STALE_SECONDS=int(os.getenv("STALE_SECONDS", "120")))
    oracle_keys=("USER", "PASSWORD", "DSN", "CONFIG_DIR", "WALLET_LOCATION", "WALLET_PASSWORD", "CLIENT_LIB_DIR")
    app.config.update({"ORACLE_"+k:os.getenv("ORACLE_"+k, "") for k in oracle_keys})
    app.config["ORACLE_DB_TIMEZONE"] = os.getenv("ORACLE_DB_TIMEZONE", "Asia/Tokyo")
    if overrides:
        app.config.update(overrides)
    if app.config["DATA_MODE"] not in ("demo", "oracle"):
        raise ValueError("DATA_MODE must be demo or oracle")
    if not app.config["ADMIN_PASSWORD_HASH"] and not app.config.get("DESKTOP_TOKEN"):
        raise ValueError("ADMIN_PASSWORD_HASH is required; use python -m showroom_admin to start demo mode")
    if app.config["DATA_MODE"] == "oracle":
        for key in ("ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_DSN"):
            if not app.config[key]:
                raise ValueError(f"{key} is required in Oracle mode")
        if not (overrides or {}).get("SECRET_KEY") and not os.getenv("SESSION_SECRET"):
            raise ValueError("SESSION_SECRET is required in Oracle mode")
    repo = repository or (DemoRepository() if app.config["DATA_MODE"] == "demo" else OracleRepository(app.config))
    app.extensions["repository"] = repo
    state_root=Path(app.config.get("MEMBER_STATE_DIR") or os.getenv("MEMBER_STATE_DIR") or Path(app.instance_path)/"onboarding") / app.config['DATA_MODE']
    journal=Journal(state_root)
    if app.config['DATA_MODE']=='demo':
        repo._creation_journal=journal
        index_dir=state_root/'index'
        index_dir.mkdir(parents=True,exist_ok=True)
        for name in ('akb48','ske48','nmb48','hkt48','ngt48','stu48'):
            target=index_dir/(name+'.jdex')
            if not target.exists():
                try:
                    with open(target,'x',encoding='utf-8') as fp:fp.write('[]\n')
                except FileExistsError:pass
        writer=DemoMemberWriter(journal,repo)
    else:
        index_dir=Path(os.getenv('SHOWROOM_INDEX_DIR','/home/ubuntu/showroom/index'))
        writer=None
        if os.getenv('ALLOW_MEMBER_CREATE')=='1':
            if not os.getenv('WRITE_ORACLE_USER') or not os.getenv('WRITE_ORACLE_PASSWORD'):
                raise ValueError('Dedicated WRITE_ORACLE_USER / WRITE_ORACLE_PASSWORD required')
            config=dict(app.config,ORACLE_USER=os.environ['WRITE_ORACLE_USER'],ORACLE_PASSWORD=os.environ['WRITE_ORACLE_PASSWORD'])
            writer=OracleMemberWriter(repo,OracleRepository(config))
    index=LocalIndex(index_dir,state_root/'backups')
    if app.config['DATA_MODE']=='oracle' and os.getenv('SHOWROOM_INDEX_TRANSPORT','local')=='ssh':
        index=SSHIndex(os.getenv('SHOWROOM_SSH_HOST',''),str(index_dir),
                       os.getenv('SHOWROOM_REMOTE_BACKUP_DIR','/home/ubuntu/showroom-admin-backups'))
    elif app.config['DATA_MODE']=='oracle' and os.getenv('SHOWROOM_INDEX_TRANSPORT','local')!='local':
        raise ValueError('SHOWROOM_INDEX_TRANSPORT must be local or ssh')
    creation=MemberCreationService(repo,journal,index,writer)
    app.extensions['member_creation']=creation
    limiter = LoginLimiter()

    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    app.jinja_env.globals["csrf_token"] = csrf

    def authenticated():
        if app.config.get("DESKTOP_TOKEN"):
            return session.get("user")==app.config["ADMIN_USERNAME"] and session.get("desktop") is True
        return session.get("user") == app.config["ADMIN_USERNAME"] and time.time() - session.get("issued", 0) < 28800

    def login_required(fn):
        @wraps(fn)
        def guarded(*args, **kwargs):
            if not authenticated():
                if request.path.startswith("/api/"):
                    return jsonify(error="登录已过期，请重新登录。"), 401
                return redirect(url_for("login"))
            return fn(*args, **kwargs)
        return guarded

    if app.config.get("DESKTOP_TOKEN"):
        from .desktop_routes import register_desktop
        register_desktop(app, login_required)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'"
        if app.config.get("DESKTOP_TOKEN"):
            response.headers["Content-Security-Policy"]=response.headers["Content-Security-Policy"].replace("script-src 'self'", "script-src 'self' 'unsafe-eval'")
        return response

    @app.before_request
    def protect_forms():
        if app.config.get("DESKTOP_TOKEN") and request.path=="/desktop/session":return None
        if request.method == "POST":
            expected = session.get("csrf", "")
            if request.path.startswith('/api/') and not authenticated():
                return jsonify(error="请先登录。"),401
            provided = request.headers.get('X-CSRF-Token') or request.form.get("csrf_token", "")
            if not expected or not hmac.compare_digest(expected.encode(), provided.encode()):
                if request.path.startswith('/api/'):
                    return jsonify(error="页面已过期，请刷新页面后重试。"),400
                return render_template("error.html", message="页面已过期，请返回登录页后重试。"), 400

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if authenticated():
            return redirect(url_for("index"))
        if app.config.get("DESKTOP_TOKEN"):
            return render_template("error.html",message="请从 Showroom 桌面应用打开管理页面。"),403
        error, code = None, 200
        if request.method == "POST":
            if not limiter.allowed(request.remote_addr or "unknown"):
                error, code = "尝试次数过多，请 5 分钟后重试。", 429
            else:
                valid_password = check_password_hash(app.config["ADMIN_PASSWORD_HASH"], request.form.get("password", ""))
                if hmac.compare_digest(request.form.get("username", "").encode(), app.config["ADMIN_USERNAME"].encode()) and valid_password:
                    session.clear()
                    session.update(user=app.config["ADMIN_USERNAME"], issued=time.time(), csrf=secrets.token_urlsafe(32))
                    session.permanent = True
                    return redirect(url_for("index"))
                error, code = "用户名或密码不正确。", 401
        return render_template("login.html", error=error, demo=app.config["DATA_MODE"]=="demo"), code

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def index():
        return render_template("index.html", username=session["user"], mode=app.config["DATA_MODE"],
                               stale_seconds=app.config["STALE_SECONDS"],desktop=bool(app.config.get("DESKTOP_TOKEN")))

    def response(data):
        return jsonify(data=data, mode=app.config["DATA_MODE"], timezone=app.config["ORACLE_DB_TIMEZONE"],
                       stale_seconds=app.config["STALE_SECONDS"])

    from .logs import LogService
    logs=LogService(app.config['DATA_MODE'])

    @app.get('/api/logs/instances')
    @login_required
    def log_instances():return response(logs.instances())

    @app.get('/api/logs/<node>/files')
    @login_required
    def log_files(node):return response(logs.call(node,'files'))

    @app.get('/api/logs/<node>/tail')
    @login_required
    def log_tail(node):
        try:lines=int(request.args.get('lines','500'))
        except ValueError:raise CreationError('行数无效。')
        return response(logs.call(node,'tail',name=request.args.get('name',''),lines=lines))

    @app.get('/api/member-template')
    @login_required
    def member_template():
        from .member_template import defaults
        return response(defaults(repo,creation.index))

    @app.get('/api/preferences')
    @login_required
    def preferences():
        language=session.get('language','zh')
        if app.config.get('DESKTOP_TOKEN'):
            from .desktop_data import default_home,initialize
            language=initialize(default_home()).get('UI_LANGUAGE','zh')
        return response(dict(language=language if language in ('zh','ja') else 'zh'))

    @app.post('/api/preferences')
    @login_required
    def save_preferences():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise CreationError('语言格式无效。')
        language=data.get('language')
        if language not in ('zh','ja'):raise CreationError('语言无效。')
        if app.config.get('DESKTOP_TOKEN'):
            from .desktop_data import default_home,initialize,save
            with app.extensions['desktop_mutex']:
                if app.extensions['desktop_state']['active']>1:raise CreationError('正在保存，请稍后切换语言。',409)
                home=default_home();settings=initialize(home);settings['UI_LANGUAGE']=language;save(home,settings)
        session['language']=language
        return response(dict(language=language))

    @app.get("/api/overview")
    @login_required
    def overview():
        return response(repo.overview())

    @app.get("/api/groups")
    @login_required
    def groups():
        return response(repo.groups())

    @app.get("/api/members")
    @login_required
    def members():
        try:
            page = int(request.args.get("page", "1"))
            size = int(request.args.get("page_size", "12"))
            if not 1 <= page <= 100000 or not 1 <= size <= 100:
                raise ValueError()
        except ValueError:
            return jsonify(error="分页参数无效。"), 400
        enabled = request.args.get("enabled", "")
        search = request.args.get("search", "").strip()
        group = request.args.get("group", "")
        if enabled not in ("", "0", "1") or len(search)>150 or len(group)>100:
            return jsonify(error="筛选参数无效。"), 400
        return response(dict(**repo.members(search, group, enabled, page, size), page=page, page_size=size))

    from .member_editing import MemberEditingService
    editing=MemberEditingService(creation)

    @app.get('/api/members/<member_id>/edit')
    @login_required
    def edit_context(member_id):return response(editing.context(member_id))

    @app.post('/api/members/<member_id>/edit-preview')
    @login_required
    def edit_preview(member_id):return response(editing.preview(member_id,request.get_json(silent=True),session['user']))

    @app.get('/api/member-edits')
    @login_required
    def edit_operations():return response(editing.recent(session['user']))

    @app.post('/api/member-edits/<request_id>/save')
    @login_required
    def save_edit(request_id):
        result=editing.submit(request_id,session['user'])
        return response(result), (200 if result['state']=='complete' else 409)

    @app.get('/api/member-creation/options')
    @login_required
    def creation_options():
        return response(creation.options())

    @app.post('/api/member-creation/preview')
    @login_required
    def creation_preview():
        return response(creation.preview(request.get_json(silent=True)))

    @app.post('/api/members')
    @login_required
    def create_member():
        body=request.get_json(silent=True)
        if not isinstance(body,dict):raise CreationError('请求格式无效。')
        result=creation.submit(body.get('member'),body.get('request_id'),session['user'])
        return response(result), (201 if result['state']=='complete' else 409)

    @app.get('/api/member-creation/operations')
    @login_required
    def creation_operations():
        return response([creation.public(j) for j in journal.recent() if j['created_by']==session['user']])

    @app.post('/api/member-creation/operations/<request_id>/retry')
    @login_required
    def retry_creation(request_id):
        job=journal.get(request_id)
        if not job or job['created_by']!=session['user']:
            return jsonify(error='操作不存在。'),404
        result=creation.submit(job['payload'],request_id,session['user'])
        return response(result), (200 if result['state']=='complete' else 409)

    @app.errorhandler(CreationError)
    def creation_error(exc):
        return jsonify(error=str(exc)),exc.code

    @app.get("/api/members/<member_id>")
    @login_required
    def member(member_id):
        if len(member_id)>150:
            return jsonify(error="成员标识无效。"), 400
        result=repo.detail(member_id)
        if result is None:
            return jsonify(error="没有找到该成员。"), 404
        return response(result)

    @app.get("/api/live-status")
    @login_required
    def live():
        return response(repo.live())

    @app.get("/api/instances")
    @login_required
    def instances():
        return response(repo.instances())

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    @app.errorhandler(DataUnavailable)
    def unavailable(exc):
        # Avoid logging exception text or connection configuration.
        app.logger.warning("Read-only data source unavailable (%s)", type(exc.__cause__).__name__)
        return jsonify(error=str(exc)), 503

    @app.errorhandler(500)
    def internal(_):
        if request.path.startswith("/api/"):
            return jsonify(error="读取失败，请稍后重试。"), 500
        return render_template("error.html", message="页面暂时不可用，请稍后重试。"), 500

    return app
