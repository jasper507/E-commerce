# 认证、CSRF与数据权限知识库：文档核验与发布记录

2026-10-11。Supporting / Delivery，文档归档；适用源码`a5d4b04995552a340a471d31e68973e89f67e54c`。[当前认证总览](../learning/auth-current-overview-study-guide.md)与四个问题页反向链接本记录。

本轮用户选择“认证、CSRF与数据权限”要求开始，沿已有电商项目Wiki新增一个模块总览和四页问题解释、两张时序图，并最小更新项目阅读导航。目的是能在面试中独立区分身份、浏览器防伪造与业务授权；不新增应用行为、账号、会话或数据库实验。

## 当前事实与已有证据

Session身份由后端恢复；项目仅DRF Session认证，匿名本人请求实际403。登录/注册/退出显式CSRF保护；普通登录后的业务写请求由DRF认证检查CSRF，失败正文路径不同。列表和总数先限定本人；详情与部分锁内写查询同时限定本人和目标。管理员发货还需模型权限；Mock webhook另有本地范围、大小和HMAC/内容校验，不拿浏览器CSRF当网关认证。

历史实测主要引用[9C记录](phase-9c-session-csrf-operations.md)和[实际15项HTTP报告](phase-9c-session-csrf-http.json)，以及问题页明确链接的历史业务验证。9C本机Nginx、CookieJar、新独立库的账号/Session验收与精确清理已经完成；本轮不执行该脚本，不新建/删除账号或Session，不将历史通过当本轮通过。

## 本轮执行记录

| 检查 | 本轮实际结果与范围 |
|---|---|
| 源码静态核对 | 5篇指南，44个本地正文链接存在；8段Python/TypeScript摘录匹配固定a5d4b049源码，相关固定代码行号有效 |
| 独立交叉审查 | 已修正取消未确认付款门槛、SHIPPED发货重放范围，以及图中数据库读取/应用密码校验分工；不把编写者自检当独立业务实测 |
| 草稿与输入评估 | 5份草稿CLI初始化；创建前、Wiki互链生成后两轮评估均passed；根导航局部XML评估passed |
| 实际创建与填写 | 一个模块总览、四个子页共5个唯一节点；五次正文写入均success、无warnings；没有覆写旧模块 |
| 正文回读 | 5页全文语义、标题层级、62个链接及8段代码与计划一致；各修订号2，54个正文标题；两页各有1张新板 |
| 两张原生图 | 写前实际确认空板，各用固定写入键写1次；实际导出34/30节点，与生成源的文字、节点类型、24/20连接箭头的端点和方向一致；两张2560×2560实际预览已查看 |
| 目录与旧资料 | 项目直属节点6→7，原6个模块身份、标题、对象保持相同；新认证总览下恰好4页。根正文只替换1个阅读导航段落，旧导航链接、原交易画板与其他资源标识保留 |
| 当前Git范围 | 新增认证总览/四页、图、发布与验证材料；学习README更新69→74，项目总览仅补导航。未纳入已有6E指南修改 |
| 业务执行 | 没有运行pytest、登录HTTP脚本、浏览器交易或任何DB/缓存实验；没有账号、Session、商品、订单、支付写入 |

检查材料：[源码与链接](../learning/auth-knowledge-base-2026-10-11/local-checks.json)、[汇总核验](../learning/auth-knowledge-base-2026-10-11/final-checks.json)、[发布材料说明](../learning/auth-knowledge-base-2026-10-11/README.md)、[CSRF实际图核验](../learning/assets/auth-interview-2026-10-11/csrf-sequence/published-verification.json)、[归属实际图核验](../learning/assets/auth-interview-2026-10-11/ownership-sequence/published-verification.json)。逐次CLI响应、fresh read与原目录/正文基线保留在发布目录，不含凭据或个人open_id。

## 飞书交付

[认证、CSRF与数据权限总览](https://ocnbkcx1ug58.feishu.cn/wiki/TS8SwlKxKi8gJ5kIjlScjyyFn3d)位于原电商项目根节点下。四页为[01 Session](https://ocnbkcx1ug58.feishu.cn/wiki/ClJNwFzuDi4Fpskvrioc3WRpnVb)、[02 CSRF](https://ocnbkcx1ug58.feishu.cn/wiki/CGqPwW2tyirrVCkXOTZcr4fknho)、[03 本人资源](https://ocnbkcx1ug58.feishu.cn/wiki/PuHtwhyymi0GiLkdAMacUiRYnKd)、[04 角色与网关边界](https://ocnbkcx1ug58.feishu.cn/wiki/NRfSw08LXiNKW5ksGyKcldlynGc)。

每页从具体业务场景推导规则，保留设计理由、实际流程、少量真实核心代码、替代与代价、历史证据和重做候选；没有新增末尾测验、参考答案或掌握记录。原支付、Redis、秒杀、搜索与MySQL正文/画板没有修改。

两图[本地资产](../learning/assets/auth-interview-2026-10-11/)含可编辑Mermaid、PNG、生成节点和真实远端节点/预览。实际飞书图为原生节点，source导出均返回“no code blocks found in whiteboard”；本地源码可编辑，不能宣称远端保留了Mermaid代码块。

## 发布辅助失败与修正

首次用Windows PowerShell 5运行初始化时，JSON参数引号丢失，被CLI输入校验拒绝，未创建草稿或远端节点。改由已配置的PowerShell 7.6.5执行原结构后五份草稿初始化成功；[失败输入记录](../learning/auth-knowledge-base-2026-10-11/init-input-failure.json)保留。没有通过重新建远端页面来处理输入错误。

首次画板只读导出将输出路径置于发布目录的父目录，被CLI本地路径allowlist拒绝，尚未写图。改在各图资产目录用本地相对路径操作；随后空板导出成功但仅返回“whiteboard is empty”而不生成JSON文件，辅助脚本因试图读取文件退出。改为明确接受该空状态响应，重新读取仍为空后，才写入对应固定键的节点。不存在因这些失败重写旧板、重复新板或重复页面的情况；没有安装依赖或改变认证配置。

最终敏感值扫描首次把正文中的通用术语“Bearer Token”误判为凭据。核对匹配类别后确认没有个人ID、OAuth或密钥字段；将Bearer扫描限定为后面跟实际凭据形式的长字符串，保留其他扫描项，再运行完整检查。

上述失败不计作通过。最终回读与核验使用成功的实际写入、原始节点和预览，不以空板或本地PNG替代远端交付。

## 边界与停止条件

本轮只读业务源码/测试/历史材料并生成文档和图。不改业务代码、配置、部署、依赖、数据库或缓存，保留既有6E指南修改。没有运行应用测试、真实HTTP登录、跨站攻击或负载实验。

既有HTTP验收不证明浏览器UI、SameSite/HttpOnly实际策略、公网HTTPS、XSS防御、全部设备退出或所有安全威胁。核心源码、已有证据、候选重做方案区分清楚即可收束，不自动扩大安全审计或认证架构。
