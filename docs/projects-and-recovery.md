# 项目创建与恢复
正常已有项目：用户批准后 apply，原生移动并读回。新项目：批准文件 destination 写 new:项目名；roots.json 用项目名映射到已有目录的绝对路径列表，如 {"示例项目":["D:\\ExampleProject"]}。先核对真实目录与用户意图，不随意创建业务目录。
运行：
```text
organize.py prepare-projects --session-id ID --approval APPROVAL --roots ROOTS
```
程序通过官方 App Server project/list、project/create、project/read 复用或创建，并先持久化意图。超时后先核查，不再次创建。路径相同且名称相同复用；同名异路径拒绝自动合并。
若所有目标均已进入原生项目目录，返回ready_to_move，运行 resume --plan 返回的plan路径。

## 桌面登记缺失
本机已验证桌面 26.1013.4894.0、CLI 0.163.0-alpha.10.2 的后端创建与桌面登记分离。通用恢复代码有版本门禁；其他版本需要重新核对，不取消门禁硬跑。
用户明确授权修复后，在 prepare-projects 增加 --repair-confirmation 用户真实原话。程序输出 plan.json 和 patch-plan.json。前者用于归类，后者用于最小配置修复。
arm-repair --plan PATCH_PLAN 通过隐藏WMI启动独立助手。让用户退出前，必须核对返回码、进程存在、父进程为WmiPrvSE、同用户同会话，以及worker-status.json连续心跳；启动回执本身不证明助手存活。如失败先解决，不让用户反复退出。
助手最多等待30分钟，不强杀应用，不常驻。连续离线后备份最新配置，只补项目登记和有证据的项目外标记；不直接设置目标归属，不写生产SQLite，然后请求重开。
退出前说明准确一次性步骤。重开后先读 repair-status --plan PATCH_PLAN，只有 applied_config_verified/already_applied 才继续 resume --plan PLAN。
修复失败先查状态和备份，禁止盲目重复。取消标记为同目录cancel文件；已取消后若用户明确重新启动，先核实旧助手已停止，再移除这一标记。已完成计划不能再次arm。

## 恢复边界
offline_registration.py rollback --plan PATCH_PLAN 仅对登记补丁做最小逆向恢复，也必须离线并核对守卫；项目已含成员时拒绝删除登记。
这不是聊天标题与归属的通用一键回退。写入记录保留旧值，但人工恢复必须另获具体授权、逐条核查。不得把模拟测试说成真实回退验收。

