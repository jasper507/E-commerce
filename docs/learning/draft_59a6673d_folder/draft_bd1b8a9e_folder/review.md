# Page 04 内容复查

- 主模式：Knowledge / Explanation；先业务生命周期，再 MQ 的具体收益和恢复边界。
- 学习产出：成功/未付关闭同事务；FAILED/UNKNOWN 区别；数据库事实与消息事实区别；原单归属。
- 源码核对：promotions/lifecycle_services.py 13-52；payments/services.py 330-381、385-549、583-609；dispatch.py 13-30；tasks.py 21-67；recovery.py 44-131。
- 事实：default sync；async 本地可选；秒杀建单未入队；MQ 支付后台已验证，不虚构 QPS 收益。
- 参数：累计 3 次包含首次、退避 5/10 秒、默认租约 60 秒；未宣称自动恢复时限。
- 证据：6B 2026-10-07 真 MySQL生命周期；1205 注入明确；5C3/C4 2026-10-05 真 Broker/Worker 历史证据。
- 业务替代：事务内资格 vs 提交后补写；同步+扫描 vs MQ；收益与代价均写明。
- 未生成命令，读者通过原支付知识库查操作；本页不重复整个支付材料。
- 根代理制作 MQ 图并替换唯一白板占位；根统一补导航。
- XML：唯一 title；h1 全 seq=auto；表格/pre合法。语义复查通过，未发布。
- CLI Profile Check: assessment.status=passed；word_count=2051。范围较广，保留资格、MQ、恢复和归属四条业务关联，不重复操作命令。
