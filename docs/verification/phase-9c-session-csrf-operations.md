# Phase 9C：代理入口Session/CSRF验收与运维流程整理

2026-10-11，Asia/Shanghai。基线`4673b9f`。Supporting / Pair。[学习指南](../learning/phase-9c-session-csrf-operations-study-guide.md) · [运维参考手册](../deployment/local-operations-runbook.md) · [前片9B](phase-9b-local-deployment-run.md)。

## 目的、范围与当前状态

拥有者选中“验证登录与CSRF，再整理更新、停机和备份流程”要求开始。最小证据是实际Nginx入口的身份保持、CSRF成功/拒绝与退出，不扩商品/交易或四中间件部署。

**拥有者明确批准仅新库执行并清理后，15项真实HTTP验收全部通过，临时账号与会话已清理。** 更新、停机、备份/恢复均仅整理参考命令，没有执行。工程检查不等于拥有者独立理解。

## 已执行与静态核对

- 开始时main/远端同为`4673b9f`；用户6E指南修改及未知`project-overview-content-candidate.md`未修改/纳入。
- 读取现有accounts views/serializer/model、Session/CSRF测试、前端auth/login、settings、独立Compose与Nginx。实际不存在accounts/services.py；登录直接调用Django auth，未补造文件或改变业务实现。
- 已有db/web/nginx全部running/healthy；未重建、启停、迁移或修改部署配置。
- 新脚本[`verify_local_session_csrf.py`](../../scripts/verify_local_session_csrf.py)的宿主与嵌入容器Python均AST语法通过，不执行嵌入写入代码。
- `python -X utf8 scripts/verify_local_session_csrf.py --preflight --report docs/verification/phase-9c-session-csrf-preflight.json`退出0。实际MySQL库`project_one_deployment_local`，用户/Session/商品/订单均0；来源/环境/身份guard通过，[原始JSON](phase-9c-session-csrf-preflight.json)明确`database_writes=false`。

语法和预检查不是登录验收；没有把历史9B浏览器空页或既有API单测列为本轮Session实测。

## 已批准的数据动作与实际Oracle

仅在上述新库创建1个随机普通账号（非staff/非superuser）；密码只在内存和容器stdin传递，不进入argv、报告或Git。登录写该账号last_login和本次Session，退出删除会话；最后精确清理本次账号及残留Session。无商品、地址、订单、支付写入，没有开发库或缓存访问。

清理依内存保存的具体Session Key，解码核对_auth_user_id匹配本次账号，账号按ID/随机用户名核对；有未知关联数据即拒绝删除，禁止全表Session删除、flush/clearsessions或清卷。执行后应恢复用户/Session/商品/订单0，不承诺自增值或所有数据库字节回到执行前。

使用独立CookieJar通过`http://127.0.0.1:8080`，有效POST显式Origin含8080。登录缺Header→CSRF403且Session0；正确登录→200同一账号、Session1、last_login更新、CSRF轮换、Cookie属性正确；后续GET/公开导航后me200；缺/旧Header和错误Origin退出各403，之后me200和Session1；正确退出204、me403、Session0。旧Header搭配当前Cookie，避免误测。

拥有者回答“允许，仅在独立新库执行并清理”，随后实际执行：

```powershell
python -X utf8 scripts/verify_local_session_csrf.py --execute-approved --report docs/verification/phase-9c-session-csrf-http.json
```

脚本拒绝覆盖已存在证据；失败只记类型和检查状态，不保存真实密码/Cookie/Token。HTTP与DB观察逐步写报告，任何业务错误不通过修改CSRF配置绕过。

命令退出0，[HTTP原始JSON](phase-9c-session-csrf-http.json)为`passed`，15项状态均与预期一致：

| 动作 | 实际HTTP与数据库状态 |
| --- | --- |
| 匿名me、获取csrf、缺Header登录 | 403/204/403，未新增Session；CSRF拒绝返回明确JSON错误 |
| 同源正确登录、me、公开导航后me | 200，始终同一普通账号；Session1、last_login已设置、CSRF轮换 |
| 三种错误退出，各紧接me | 缺Header/旧Header/错误Origin均403，随后me200；本次Session各仍1 |
| 当前Header正确退出、me | 204/403，原Session行和客户端Session Cookie失效 |
| 最终精确清理 | 账号删除，残留本次Session0；用户/Session/商品/订单均0 |

Cookie属性实测Session HttpOnly=true、两者SameSite=Lax，本机Secure=false；CSRF HttpOnly=false。HTTP客户端读取响应属性，不冒称浏览器策略执行。密码仅内存/容器stdin，用户名和账号ID为本轮随机合成资料；报告没有Cookie、Token、Session Key或密码。

脚本显式关闭环境代理、拒绝重定向，防止凭据离开固定本机入口；创建响应未知、角色变更、未知关联数据和清理拒绝等保护由代码审查确认，未注入故障实测。退出已删除Session，最后额外Session删除数为0。账号自增值不会倒退，不把行计数恢复称为数据库完全回滚。

## 运维文档及证据边界

手册覆盖独立Compose目标检查、保卷停机/恢复、先build后停写备份/迁移/static/recreate、Git外逻辑备份、另一个批准空卷的恢复验证。mysqldump容器内result-file再cp避开PowerShell SQL编码；密码通过容器环境，不置argv。备份hash不证明可恢复，single-transaction不保护并行DDL，镜像/迁移回退不等于数据恢复。

恢复用的独立Compose/env尚不存在，手册明确模板不能现在直接执行；没有开发库覆盖/清卷命令。所有更新、停机、备份/恢复、binlog/PITR、生产HTTPS与零停机能力均未实测。

[静态记录](phase-9c-session-csrf-static.json)：宿主/嵌入Python AST通过，9个PowerShell块解析通过，容器`sh -n`验证备份/恢复Shell片段语法，实际`mysqldump --help`核对7个选项存在。没有执行备份/恢复Shell正文或创建SQL文件；语法/选项通过不证明整条运维命令已通过。

真实登录流程只用HTTP客户端，不声称浏览器UI登录、浏览器SameSite/HttpOnly执行、匿名Session固定攻击完整试验或重启后保持。四开关、交易逻辑、Mock支付范围与生产guard未改；不重跑旧并发/MQ/Redis/ES或业务pytest。

## 下一步与静态收尾

上述具体写入/清理范围已获准，最小HTTP验收和精确清理已完成；没有业务实现/部署配置/依赖修改，没有开发库或缓存访问。登录证据够用即停止；运维是否实际演练另行说明影响，后续回到拥有者安排的整体梳理与复习。保留其它聊天的知识库整理与用户6E修改，只提交本片内容。

双交付互链、摘录与Oracle核对通过；静态JSON记录153处本地链接可解析、部署秘密精确扫描0匹配、15项HTTP计数一致及本片diff检查退出0。独立只读复核未发现P1/P2；共享导航只提交本片9C增量，其他聊天内容保留，不把“材料已整理”写成“流程已演练”。
