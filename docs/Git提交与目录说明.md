# Git 提交与目录说明

## 源码结构

```text
showroom-admin/
├── showroom_admin/                 # Python 后台、模板与静态界面
│   ├── static/                     # JavaScript、中日文词条、CSS
│   └── templates/                  # Flask HTML 模板
├── tests/                          # 自动测试
├── tools/                          # Mac 图标生成与应用构建
├── docs/                           # 操作与开发文档
├── deploy/                         # 可选 Linux 服务示例
├── desktop_bootstrap.py            # Mac 桌面启动入口
├── start.sh                        # 源码浏览器启动入口
├── start.command                   # Mac 双击启动浏览器后台
├── configure.command               # Mac 双击配置浏览器后台
├── requirements*.lock              # 固定依赖版本
├── requirements.txt                # 直接依赖范围
├── local_settings.example.json     # 本机配置模板，不含真实凭证
└── .env.example                    # 环境变量参考，不自动加载
```

不移动 Python 包和入口脚本，避免改变导入路径、打包路径及现有启动方式。

## 本机保留、Git 忽略

| 内容 | 位置 |
|---|---|
| 虚拟环境 | `.venv/` |
| 浏览器方式的真实配置 | `local_settings.json` |
| 浏览器方式的操作记录、演示索引 | `instance/` |
| 程序安装包 | `releases/`、`dist/` |
| 构建缓存 | `build/`、`__pycache__/` |
| 桌面 App 配置、凭证、Wallet | `~/Library/Application Support/Showroom Console/`，位于仓库外 |

历史升级备份已整理至项目同级的 `showroom-admin-local-archive/`，不作为源码。旧版应用和备份保留用于恢复。`.gitignore` 同时排除了私钥、Wallet、数据库文件、日志、迁移包和系统缓存。

## 提交与上传

在本项目根目录操作，不要在同级录制项目中执行：

```bash
git status --short
git add .
git diff --cached --stat
git diff --cached
```

检查暂存内容只有源码、测试、文档和无凭证示例，再提交：

```bash
git commit -m "Initial Showroom admin console"
```

在托管平台创建空仓库后，使用自己的仓库地址：

```bash
git remote add origin <你的仓库地址>
git push -u origin HEAD
```

不要使用 `git add -f` 添加被忽略的文件。`.gitignore` 不会移除已提交过的秘密；以后如误提交，需要另外处理历史并更换泄露凭证。本次初始化没有历史提交，也未设置远程地址或上传。

不要直接把整个 Finder 项目文件夹拖到网页上传，因为网页上传不一定遵守 `.gitignore`。使用上面的 Git 命令；只上传 Git 中的文件。发行包可以单独上传至 Release，不把安装包或个人迁移包混入源码。
