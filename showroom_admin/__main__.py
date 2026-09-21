import argparse
import getpass
import os
import secrets
from werkzeug.security import generate_password_hash
from .app import create_app
from .local_config import load_local_settings, configure


def main():
    # Configuration is data only; no source/shell evaluation.
    if "--configure" in __import__("sys").argv:
        configure()
        return
    load_local_settings(load_credentials=False)
    parser = argparse.ArgumentParser(description="Independent Showroom member console")
    parser.add_argument("--configure", action="store_true", help="Configure local passwords interactively")
    parser.add_argument("--check", action="store_true", help="Read-only Oracle and 3C connectivity checks")
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument("--demo", action="store_true", help="Use isolated local demo data")
    modes.add_argument("--oracle", action="store_true", help="Use configured Oracle connection")
    parser.add_argument("--password-hash", action="store_true", help="Prompt for a password and print its scrypt hash")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8091")))
    args = parser.parse_args()
    if args.check:
        load_local_settings()
        from .diagnostics import check_connections
        raise SystemExit(check_connections())
    if args.demo:os.environ["DATA_MODE"]="demo"
    if args.oracle:os.environ["DATA_MODE"]="oracle"
    if args.password_hash:
        password = getpass.getpass("新密码（至少 12 位）: ")
        if len(password)<12 or password!=getpass.getpass("再次输入: "):
            parser.error("密码至少 12 位且两次输入须一致")
        print(generate_password_hash(password))
        return
    if args.host not in ("127.0.0.1", "localhost", "::1") and os.getenv("SHOWROOM_INDEX_TRANSPORT")=="ssh":
        parser.error("本地 SSH 管理配置仅允许监听本机地址")
    mode=os.getenv("DATA_MODE", "demo")
    if mode=="oracle" and not os.getenv("ADMIN_PASSWORD_HASH"):
        parser.error("请先运行 ./start.sh --configure 设置管理页面密码")
    if mode == "demo" and not os.getenv("ADMIN_PASSWORD_HASH"):
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            parser.error("自动生成密码的演示服务只允许绑定本机地址")
        password=secrets.token_urlsafe(15)
        os.environ["ADMIN_PASSWORD_HASH"]=generate_password_hash(password)
        print(f"演示用户名: {os.getenv('ADMIN_USERNAME','admin')}\n本次演示密码: {password}", flush=True)
    if mode=="oracle":load_local_settings()
    app=create_app()
    print(f"Showroom Console ({mode}) · http://{args.host}:{args.port}", flush=True)
    from waitress import serve
    serve(app, host=args.host, port=args.port, threads=4, max_request_body_size=16384)


if __name__ == "__main__":
    main()
