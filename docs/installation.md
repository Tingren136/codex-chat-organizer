# 安装与自检

推荐把 README 的安装指令发给 Codex。安装只用 Python 标准库和 PowerShell，不需要 Git，不读取聊天、不调用模型。

下载 Release ZIP，解压后运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -SourcePath .`。可追加 `-CodexHome "D:\MyCodex"`，必须与桌面实际主目录一致。

也可运行 `py -3.14 -X utf8 tools/install.py --source .`。仅下载 install.ps1 时，脚本按 -Version 获取版本，默认 v0.4.1；失败不回退 main。脚本仅含 ASCII，适用于 PowerShell 5.1/7。

升级流程：暂存新版、自检、旧代码移到主目录 skill-backups、安装新版；替换失败恢复旧目录。备份不放在 skills 内，避免重复识别。配置和数据保持原位；不会创建与自定义配置冲突的数据目录。

## 区分安装和运行

- `doctor --installation`：平台、Python、必要文件检查，成功退出0。可在普通终端执行。
- `doctor`：额外检查 Codex CLI、官方资源和桌面连接环境；缺项退出2。应在 Codex 桌面对话中执行。

installation_ok 是安装检查结果，execution_environment_ready 是运行前置条件是否齐备，checks_missing 列出缺项。doctor 不调用模型，不验证账号权限或真实接口通信；实际整理仍需检查接口回执。

缺 CLI：安装官方 Codex CLI 并登录。缺桌面环境：回到桌面对话运行，不复制连接凭据。缺官方资源：检查桌面安装，必要时配置 resources_dir。后端项目存在而桌面未登记时，按项目恢复文档继续，不重复创建。
