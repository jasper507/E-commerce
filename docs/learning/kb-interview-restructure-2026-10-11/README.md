# 2026-10-11 面试知识库重构发布记录

业务源码固定在 `29b7944860080ccddedb2c0f0c060c0a27658376`；本目录保存已授权文档重构的候选、结构和实际读回，不包含业务实现或新的服务实验。

- `before/`：十个旧页面的完整XML及原知识库层级。
- `candidates/`：最终正文分段和用于本地检查的完整profile。profile中的画板说明只供检查，未写入飞书；实际13个原生画板块原样保留。
- `publication-plan.json`、`draft-workspaces.json`、`redis-created.json`：十个原节点、五个新Redis节点及创作决策。
- `*-latest.json`、`*-verified.json`：十五页最终完整读回及正文/层级/链接/代码/画板核对结果。
- `publication-manifest.json`、`publication-journal.jsonl`：完成状态与完整CLI执行结果。原始逐条journal仅本地保留，合并后以JSONL归档。
- `local-static-checks.json`：23段Python摘录匹配固定源码快照、相对链接与源码锚点检查。`local-link-checks.json`另记录飞书正文的链接转换。

正文源文件在上级项目、支付、Redis总览以及`payment-interview/`、`redis-interview/`。历史业务记录保留各自日期，本轮没有重跑MySQL/Redis/MQ实验。

工具的默认模式只解析候选。`run-publication.ps1`中的create/fill/existing为实际写入操作，仅在明确授权后使用；每次结构修改都先取最新版本、核对资源、写入、再读回。已完成页的重写须显式给`-RewriteVerified`且版本匹配，防止覆盖并发修改。五个CLI创建的临时草稿工作区已有候选与决策归档；清理被自动审批拒绝，暂留本地忽略目录。以后若完成清理，parse可使用已保存候选与决策。

本轮首次范围替换被CLI拒绝，原因是序列化列表包装没有块ID；确认revision未变化后改为显式块ID列表。末轮去掉自动标题编号，避免与手写中文序号重复。两项均有原始结果和最终读回，不把拒绝操作计为成功。

完整范围、核对结果和边界见[验证记录](../../verification/payment-redis-interview-knowledge-base-restructure.md)。
