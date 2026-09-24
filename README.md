# Showroom 管理

独立的 Python / Flask 成员管理工具，当前版本 **0.4.6**。主要使用方式为 Mac 桌面应用，与直播监控、录制和上传程序分开运行。

## 功能

- 中文／日本語切换，保存语言偏好。
- 添加成员：预览确认后，写入 Oracle 成员及 YouTube 配置、标签，并更新 3C 的 jdex 索引。
- 修改成员资料、录制配置和上传配置；支持仅修改上传配置。
- 新成员默认模板来自「白鳥 沙怜」；身份信息及播放列表 ID 留空，说明和标签中的姓名随输入替换。
- 独立日志工作台：通过 SSH 读取 1C／3C／4C 的 `/home/ubuntu/logs`，支持筛选和自动刷新。
- 加密导出／导入本机连接配置与操作记录，方便换 Mac。

成员 ID、房间号、英文名在编辑时保持只读。不提供删除成员、手动录制启停或云端服务重启。上传账号、公开状态等实际行为由现有上传脚本决定，详见手顺书。

## 使用与文档

- [Mac 桌面版使用与迁移手顺书](docs/桌面版使用与迁移手顺书.md)
- [源码与浏览器运行手顺书](docs/本地运行手顺书.md)
- [Git 提交与目录说明](docs/Git提交与目录说明.md)

当前 Mac 安装包支持 **Apple Silicon / macOS 15+**，包含 Python 和 Oracle Instant Client；无需另装 Python或输入管理页面密码。应用只监听本机随机端口。Windows 暂未打包；Intel Mac 未验证。安装包为本地自签名，未做 Apple Developer ID 公证。

首次启动及迁移导入后使用演示模式，真实写入默认关闭。个人数据保存在 `~/Library/Application Support/Showroom Console/`，不属于源码仓库。复制源码或 App 不会迁移个人配置，应使用应用内加密迁移功能。

## 源码运行

浏览器演示方式（Python 3.11+；锁定依赖主要在 macOS ARM64 / Python 3.13 验证）：

```bash
./start.sh --demo
```

首次运行创建 `.venv` 并安装 `requirements.lock`；登录地址及临时演示密码显示在终端。默认地址为 `http://127.0.0.1:8091/#add`。停止时按 Ctrl+C。

真实连接使用 `local_settings.example.json` 作为模板，详见源码手顺书。`.env.example` 仅说明环境变量，不会自动加载。不要把个人配置直接写入示例文件。

## Mac 开发与打包

在 Apple Silicon Mac 上执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-desktop-mac.lock
.venv/bin/python -B -m unittest discover -s tests -v
# 可选：从源码运行桌面界面，默认使用桌面应用的个人数据目录
.venv/bin/python desktop_bootstrap.py
```

如需隔离演示开发，指定一个新的私有数据目录：

```bash
SHOWROOM_HOME="$HOME/Library/Application Support/Showroom Console Dev" .venv/bin/python desktop_bootstrap.py
```

打包前，另行准备官方 Oracle Instant Client Basic 23.26.2 ARM64 解压目录（包含 `libclntsh.dylib` 及许可文件）：

```bash
SHOWROOM_BUILD_ORACLE_CLIENT=/absolute/path/instantclient .venv/bin/python tools/build_mac.py
```

产物位于 `dist/macOS-arm64/Showroom 管理.app`。构建不会打包用户配置、Wallet 或私钥。依赖文件分别用于：`requirements.txt` 声明直接依赖范围，`requirements.lock` 固定浏览器运行依赖，`requirements-desktop-mac.lock` 固定 Mac 桌面和构建依赖。

## 与云端的关系

| 组件 | 职责 |
|---|---|
| 本机管理应用 | 成员及上传配置管理、索引更新、日志查看 |
| 1C | 检测直播，更新数据库状态 |
| 3C | showroom 录制、索引文件、YouTube 上传 |
| 4C | 视频处理、AI 总结、YouTube 上传 |

应用不执行数据库 DDL。读取和写入连接分别配置；只有显式开启 `ALLOW_MEMBER_CREATE` 才启用真实添加／修改。数据库与远端文件不是同一个事务；中断后保留操作记录并重试原操作。关闭本机应用不会停止云端任务。

## 验证范围

当前 53 项自动测试覆盖成员添加／修改、冲突恢复、访问保护、SSH 操作、日志读取边界及加密迁移。已验证打包后的桌面界面、真实 Oracle 读取及三台服务器日志读取；新增和修改流程使用隔离演示数据验证，没有通过测试写入真实生产成员。

`deploy/showroom-admin.service.example` 仅为可选 Linux 部署参考，当前 Mac 用法不需要安装该服务。安装包可单独作为 Git 托管平台的 Release 附件，源码 Git 仓库不收录构建产物。

成员目录按数据库 `MEMBERS.ID` 升序排列；最新直播状态 `IS_LIVE=1` 的成员优先显示，直播成员内部同样按 ID 升序。排序在分页之前完成，搜索和筛选后仍沿用这一规则。

成员目录的“直播中／配信中”使用红色圆点和浅红底标签突出显示；未直播、未知或检测过期的状态保持普通文字，避免把过期记录标成当前直播。

成员目录中已启用成员的最后检测时间显示为“几秒前／几分钟前／几小时前／几天前”，中日文自动切换；鼠标悬停可查看完整日期时间。相对时间以本次列表刷新时刻计算，显示后保持不变，下次刷新列表时重新计算。

已停用成员直接显示最后检测的完整日期时间（日本时间），不显示“几天前”；没有检测记录时显示“—”。

成员详情分为“资料与上传”和“直播记录”两个页签。直播记录以时间线卡片展示最近 20 场（开始时间倒序、日本时间）的开始时间、结束时间和数据库记录的分钟时长，支持刷新。缺少结束时间不直接判断为直播中，只有与最新有效直播状态的开始时间一致时才突出显示。记录来自直播检测历史，不表示录制或上传完成。
