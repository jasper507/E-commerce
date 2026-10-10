# 部署后的登录与CSRF：身份保持、写请求保护与运维边界

2026-10-11。[学习导航](README.md) · [验证记录](../verification/phase-9c-session-csrf-operations.md) · [更新/停机/备份操作手册](../deployment/local-operations-runbook.md) · [前片9B](phase-9b-local-deployment-run-study-guide.md)。

## 本片前后变化

- **之前：** 镜像、新库迁移、公开商品读取和浏览器空页已通过，但没有验证代理入口下的登录写请求。
- **本片改变：** 为现有登录/退出接口增加可重复运行的独立HTTP验收脚本；运维手册整理当前部署的更新、停机和备份恢复步骤。
- **完成后与边界：** 拥有者批准后，真实入口15项HTTP验收全部通过，临时账号和会话已清理，用户/Session/商品/订单均回到0。更新、停机、备份与恢复仍是操作参考，没有实际演练。

## 1. 这片解决什么问题

网页200并不能证明登录功能在部署后正常。代理可能丢失Host端口，Cookie可能没有被带回，登录前后的CSRF值也可能混用，表现为403或登录后又成为游客。

本片目标是同一入口的“拿到CSRF Cookie → 登录 → 后续请求识别本人 → 错误写请求拒绝但不破坏会话 → 正确退出”。它支撑你解释部署请求链、登录身份和请求安全，不需要创建商品或再演示完整交易。

这是Supporting/Pair验收，沿用既有认证业务。HTTP请求真实经过Nginx、Gunicorn/Django与独立MySQL；客户端使用CookieJar（临时保存并带回本次Cookie的独立存储器），不会共用开发浏览器登录。它不是浏览器UI操作，也不证明个人已掌握。

## 2. 重点一：Session与CSRF各自负责什么

Session（登录会话）回答“这个请求是谁发的”：登录成功后Django保存会话，客户端拿到`sessionid` Cookie；后续GET带回该Cookie，后端才能识别用户。密码用于登录校验，不能作为每次读取的身份凭证。

CSRF（跨站请求伪造防护）回答“这次修改请求是否满足来源与令牌规则”：本项目先拿`csrftoken` Cookie，再在POST中带`X-CSRFToken`。有Session不等于写请求可以绕过CSRF；未登录的登录入口也需要保护。

现有[登录入口](/C:/project-one-ecommerce/backend/apps/accounts/views.py:46)保留显式保护，即使它允许未登录访问：

```python
@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = (AllowAny,)
```

`AllowAny`只说明不用先登录才能调用登录接口，不代表忽略CSRF。密码校验成功后调用`django_login`，才建立Session，并轮换CSRF值；获取CSRF Cookie本身不应创建登录Session。

Session Cookie为HttpOnly（页面JavaScript不能直接读取）、SameSite=Lax；CSRF Cookie允许前端读取。当前本机HTTP的Secure=false属于练习环境，不能据此写“生产HTTPS配置已通过”。真实浏览器对这些属性的执行与本轮HTTP属性检查也要分开。

## 3. 重点二：同源、端口和登录后的新Token

Origin（来源）由协议、主机、端口组成。`http://127.0.0.1:8080`与`http://localhost:8080`不是同一来源；本片所有有效POST固定前者，拒绝试验才显式使用后者。

浏览器看到8080，Nginx通过`proxy_set_header Host $http_host`保留这个端口，Django才能正确核对同源请求。不能通过随意加可信来源或关闭CSRF来掩盖配置错误。

登录前取得的Token记作A，登录后Cookie变成B。正确退出应为**当前Cookie B＋Header B**；旧值拒绝试验是**当前Cookie B＋Header A**。如果Cookie也倒回A，就没有验证“当前会话误用旧Header”这个条件。

前端[登录请求](/C:/project-one-ecommerce/frontend/src/api/auth.ts:58)已按现有同源方式携带Cookie与Header，本片无需改成长期Token。JWT（签名令牌）是有意义的替代方案，适用于其他客户端/服务边界；它仍有存储、失效和泄露后的取舍，不能仅为部署登录换认证方案。

## 4. 最小验收和写入边界

验收脚本先实际查库名、数据库用户、地址、环境与进程身份；来源不是`project_one_deployment_local`或业务数据已非空就拒绝执行。

本轮获得具体写入/清理确认后，只创建一个随机普通账号；成功登录更新该账号的`last_login`并新增本次Session；正确退出删除该Session。最后按本次账号ID/用户名与内存保存的Session Key核对所有权，清理账号。本轮退出已经删除Session，清理时残留为0；最终四项计数均0。出现未知关联数据会拒绝扩大删除，保留证据；这些拒绝清理分支只做了代码复核，没有故障注入。

| 验收动作 | 本轮实测结果 | 解决的误解 |
| --- | --- | --- |
| 未登录GET me、获取CSRF | 403、204；Session仍0 | 有CSRF Cookie不代表已登录 |
| 登录缺Header | CSRF 403，Session仍0 | 登录接口公开不代表无需保护 |
| 当前Token＋同源登录 | 200，同一账号、Session1，CSRF轮换 | Session和Token要分别检查 |
| 后续GET与公开页面导航后GET me | 200，同一账号 | 后续靠Cookie识别，导航不是重新登录 |
| 缺Header、旧Header、错误Origin退出 | 各403，之后me仍200、Session仍1 | 拒绝请求不能误清已有登录 |
| 当前Token正确退出、再次GET me | 204、403，Session0 | 退出应失效会话，不只改页面状态 |

这组HTTP证据不包含浏览器登录表单、浏览器重启后的保持、会话固定攻击完整试验、用户规模或性能提升；不要把已有单元测试与本轮实测混为一谈。

## 5. 更新、停机和备份最容易混淆的事

[运维手册](../deployment/local-operations-runbook.md)给出逐步命令、成功判据和失败处理，所有命令明确指定独立环境文件及Compose项目。

更新是先构建新镜像，再停业务写入、备份、迁移、收集静态文件，最后重建web/nginx并验证。镜像回退不会恢复数据库数据；共享静态卷也可能已变，因此失败后不能盲目启动旧代码。本方案接受短暂停机，没有零停机承诺。

停机默认保留命名卷，重启需要核对数据还在；“使用卷”只是设计，“实际持久性通过”需要重启前后证据。备份导出成功与文件哈希一致也不代表能恢复，必须在另一个空卷实际导入并核对。

备份可能包含账号和业务数据，所以留在Git外。SQL先由容器写文件再复制，避免PowerShell重定向修改编码；密码不放命令参数。恢复先确认另一个空库/空卷，不能直接覆盖当前库。当前没有binlog保留与重放证据，不能声称支持PITR（按时间点恢复）。

## 6. 证据到哪里停止，下一步怎样安排

当前已完成预检查、真实HTTP登录/CSRF验收与参考流程；证据见[15项HTTP记录](../verification/phase-9c-session-csrf-http.json)。运维命令通过语法/选项核对，但更新、停机、备份和恢复都尚未演练。缓存、Lua、MQ、ES仍默认关闭，生产HTTPS范围也未改变。

本片真实HTTP已足够说明会话、CSRF轮换、代理同源和退出边界，停止加试验。运维手册用于先学部署流程；是否再做一次隔离备份恢复演练，按独立说明能力与学习成本决定，不自动扩公网或集群。

随后按拥有者原安排进入项目整体梳理与复习，串起浏览→下单/库存→Mock支付/MQ→缓存→搜索。能独立解释问题、方案、替代选择、真实证据与局限才算学习收束，工程检查通过不代表已经掌握。
