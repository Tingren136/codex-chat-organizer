# 运行说明
入口：本Skill的 scripts/organize.py；实现全部位于 scripts/runtime。
doctor 只读显示版本、目录、连接是否存在、锁定条数和历史分析批次，不输出环境值。
持久数据独立于代码。配置按环境变量、CODEX_HOME/chat-organizer.local.json、默认路径依次决定。
配置字段：data_dir、output_dir、resources_dir（可选的已安装官方 resources 目录）。必须绝对路径。
环境变量：CHAT_ORGANIZER_DATA_DIR、CHAT_ORGANIZER_OUTPUT_DIR、CHAT_ORGANIZER_RESOURCES；CHAT_ORGANIZER_SETTINGS 可指定配置文件。
默认 data_dir=CODEX_HOME/chat-organizer-data；output_dir=data_dir/reports。
保留 work/organizer-cache.sqlite、快照、review-sessions及执行回执；不提交Git，不在升级时清空。不复制其他用户缓存到本机。
官方工具从本机安装目录定位；不下载或修改插件。依赖桌面执行器注入的连接环境，不从其他进程窃取或保存凭据。

