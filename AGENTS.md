# 安装与维护入口

用户发来链接要求安装时，由助手完成安装，不把复制目录等步骤交回用户。

1. 检查 Windows 和 Python 3.14；先试 py -3.14，再探测 python/python3 的实际版本。缺失时给出 https://www.python.org/downloads/windows/，不擅自安装系统软件。其他 Python 版本不在当前支持范围。
2. 获取最新正式 Release，当前稳定版 v0.4.1。有 Git 可 clone 后切到 tag；没有 Git 用 Release ZIP。用户指定版本时必须遵守，下载失败不偷偷回退 main。
3. 确认 CODEX_HOME，未设置则用用户主目录 .codex。在解压目录执行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -SourcePath .`，可用 -CodexHome 指定与桌面一致的主目录。
4. 安装器先暂存、自检，再替换代码，旧版备份到 CODEX_HOME/skill-backups。保留数据、处理记录和命名规则，不创建与自定义配置冲突的默认配置。
5. `doctor --installation` 只检查安装结构、Python 和平台。再在 Codex 桌面对话运行 `py -3.14 <Skill目录>/scripts/organize.py doctor`。完整自检缺 CLI、官方资源或会话连接环境时返回非零；按 checks_missing 处理，不输出连接变量值。doctor 不验证登录权限，必要时另查 CLI 登录状态。
6. 汇报版本、安装/备份/数据路径、自检结果。缺运行条件时说“已安装，待补运行条件”，不能说已经可以整理。下一句建议：“找最近20条需要改名的聊天，先给我看标题方案。”

安装不授权读写聊天。实际整理读取 SKILL.md，分别确认标题和项目。建项目由工具执行，不让用户手动建；需退出修复桌面登记时先准备程序，再说明必要操作。

维护：设置 PYTHONPATH 为 skills/chat-organizer/scripts/runtime，运行 `python -m unittest discover -s tests -v` 及 `python tools/release_check.py`。不上传真实聊天、截图、日志和私有配置；发布需用户授权。
