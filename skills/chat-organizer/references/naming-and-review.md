# 命名规则与两次确认

默认规则位于独立数据目录的 naming-policy.json，升级不覆盖。示例：
```json
{"version":1,"format":"对象｜具体用途","separator":"｜","max_length":32,"preserve_clear_titles":true,"instructions":"对象用稳定的项目或主题名称；用途写具体任务或成果，不用开场白，不随批次换格式。"}
```
separator支持“｜”、两边带空格的“ | ”、“ · ”、“ — ”；max_length为8至80。format是固定的语义模板；separator决定实际连接符。instructions可补充固定的领域用词。只有得到用户的具体要求后才更改规则，并递增version。

scan保存规则快照、原状态、正文指纹及建议。扫描只读客户端；模型不合规则的标题变为review，等待人工修订，不重发分析。新一轮仍使用持久规则。

标题批准文件由标题确认草案复制而来：approved=true且user_confirmation有真实确认；decisions为thread/action/title。rename必须符合规则；keep保留原题（可不符合新格式）。title_digest绑定该批原状态、建议及规则，过期文件拒绝执行。

apply-titles只执行本次批准项，重新检查标题、项目、目录、置顶、更新时间、正文指纹。每次写入前存意图，重复执行返回已存回执，失败不自动重发。全部标题确认完成后才能批准项目；归类继续使用独立的review_digest和批准文件。

历史已完成会话继续可读，原锁和缓存保留；历史中断会话不自动升级重跑。不能通过清空数据目录重新分析。
