# 配置与隐私
在当前 CODEX_HOME 中建立私有文件 chat-organizer.local.json，例如：
```json
{
  "data_dir": "D:\\ChatOrganizerData",
  "output_dir": "D:\\ChatOrganizerReports"
}
```
示例目录由用户选择，不是项目固定要求。支持环境变量覆盖，详见 Skill 的 references/runtime.md。
升级时只替换Skill代码，保持data_dir不变。否则程序无法识别旧的处理记录，可能再次分析。
本地快照含聊天正文，报告含摘要和项目建议，SQLite含处理标记和回执。它们都不应进入公开仓库。应用本身的数据也不应打包。
该工具通过当前登录的Codex CLI调用模型，受该账户的权限、用量与数据处理规则约束。不要把“没有单独配置API Key”理解成“没有模型费用”。


命名规则另存 data_dir/naming-policy.json，支持固定分隔符、最大长度、保留清晰旧标题和补充说明。每个会话保存规则快照；升级代码保留该文件和全部旧缓存。见 [命名规则](../skills/chat-organizer/references/naming-and-review.md)。
