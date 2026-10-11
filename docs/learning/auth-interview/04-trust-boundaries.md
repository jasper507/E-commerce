# 顾客、管理员、支付网关：为什么不能共用一种授权方式？

2026-10-11整理。本文帮助你解释Trust Boundary（信任边界：来自不同主体的信息，必须分别验证后才能改变业务事实）。适用源码快照为`a5d4b04995552a340a471d31e68973e89f67e54c`。本轮只核对代码与既有证据，不接第三方支付、不启用服务、不运行交易测试。

[认证当前总览](../auth-current-overview-study-guide.md) · [本人资源防越权](03-ownership.md) · [支付当前总览](../payment-current-overview-study-guide.md) · [学习导航](../README.md) · [本轮文档验证](../../verification/auth-interview-knowledge-base-publication.md)

## 本专题前后变化

**开始前：** 浏览器Session/CSRF、Admin发货和Mock签名通知已经分别存在。只把它们概括成“接口要登录”，会误以为顾客可以决定订单已付款、`is_staff`就能发货，或者Webhook跳过CSRF等于开放任意写入。

**本次整理：** 把每一步对应的主体、凭据、允许动作和业务门槛放在同一张地图中，说明三条入口最终怎样汇合到MySQL订单事实。

**当前边界：** 顾客与管理员仍是同一Django用户模型中的不同权限主体；支付网关是后端Mock，不处理真钱。第三方支付Sandbox在当前项目范围内不采用。没有完成真正供应商验签、公网Webhook接入或生产支付安全验收。

## 一张89元订单，需要三种不同的“可信”

顾客甲有权购买自己的商品、查询自己的订单；他发起付款，并不等于他有权把订单标成`PAID`。否则甲可以发送`status=SUCCEEDED, amount=0.01`，让系统相信已经付了89元。

订单变成已付款以后，需要有发货权限的管理员操作，普通顾客不能自己写`SHIPPED`。顾客确认收货时又回到本人资源边界：他只能把本人已发货、有Shipment记录的订单确认完成。

这里不是让三个主体都“有权修改全部Order”。而是对每个动作确定**谁提供身份、谁提供可信业务事实、哪条状态变化允许成立**。

| 主体/入口 | 如何确定身份或来源 | 可以请求的业务动作 | 仍必须满足的条件 |
|---|---|---|---|
| 顾客浏览器：购物车、地址、订单与Attempt API | 服务端Session；写请求CSRF；本人资源查询 | 下单、发起本人付款、查原结果、取消、确认收货 | 服务端金额、归属、库存、幂等、订单/Attempt状态 |
| 管理员：Django Admin的Order action | Admin身份/框架权限；业务服务再次要求`is_staff`与`ordering.change_order` | 模拟发货 | Order锁内确认为PAID；Shipment与SHIPPED共同提交 |
| Mock事件：后端返回或本地Webhook | HMAC签名；Webhook额外限定本地环境、回环来源和体积 | 提交模拟供应商结果供处理 | Provider、Attempt引用、订单号、金额、币种、终态规则与去重 |

`SessionUserSerializer`返回`is_staff`给界面显示，不等于浏览器能修改角色。注册Serializer只允许`id/username/password`，id只读；提交`is_staff`、`is_superuser`不会创建管理员。真正的权限分配不能由普通注册请求自己决定。

## 一次完整业务流，各主体分别在哪一段起作用

1. 甲通过Session/CSRF发起本人下单。服务端计算89元、保存订单与地址/商品快照、预占库存，订单为`PENDING_PAYMENT`。客户端总价和订单状态不作为权威输入。
2. 甲为自己的订单创建PaymentAttempt（支付尝试：记录这一次付款请求）。服务在短事务中锁本人User与本人Order，检查待付款状态、原Key及已有尝试，将订单中的89元/CNY复制到Attempt后提交。
3. 后端在事务外调用MockGateway。没有持着Order/Inventory锁等网关返回。Mock生成带Provider请求引用、订单公开号、金额、币种、结果和事件号的payload，并由后端签名；正常同步调用直接进入事件接收服务，不一定经过Webhook HTTP地址。
4. 网关通知或查单结果进入同一事件机制。收到原文后先独立持久化Inbox（收件箱：留下待处理通知），再处理。事件处理器验签、解析引用、锁Order与Attempt、比较交易字段，并检查状态。不是仅看payload里的`SUCCEEDED`。
5. 可信成功使Attempt成功、订单PAID、库存预占核销与秒杀资格状态在一个业务事务中提交。重复通知不能重复核销；迟到成功遇到已关闭订单走原有退款模拟规则。启用异步时，202只说明通知已保存待处理，不表示已付款或Broker成功投递。
6. 有权限管理员在`/admin/ordering/order/`的action选择“模拟发货”。Admin逐单调用`ship_order`，服务先检查角色权限，再锁Order并检查PAID。一单的Shipment与SHIPPED状态一起提交；仍为SHIPPED且已有Shipment时重复发货返回原结果，COMPLETED后再次发货则409，不能概括为任意以后重放都成功。
7. 甲通过顾客收货API提交确认。服务按本人公开号锁Order，检查SHIPPED和Shipment事实，才变为COMPLETED；乙知道订单号也不能代甲确认。

付款、发货、收货这条链上的身份来源不同，但最终都要守住同一份MySQL事实。Frontend按钮、Session、签名各有用途，不能互相替代。

## 为什么Webhook不用顾客的CSRF Token？

CSRF针对浏览器会自动携带登录Cookie的风险。一个支付供应商在自己的服务器发送通知，并不是顾客浏览器，也没有顾客的Session Cookie或当前CSRF Token。要求它登录为顾客既不符合来源，也会混淆“顾客希望付款”和“供应商报告付款结果”。

本项目`MockPaymentWebhookView`因此明确使用`authentication_classes=()`和`AllowAny`，不走顾客SessionAuthentication。**这表示采用另一条来源验证链，不能理解为允许任意人声明订单付款。** 接口先检查`IS_LOCAL_ENVIRONMENT`与`REMOTE_ADDR`是否为`127.0.0.1/::1`，超过16KiB的请求体拒绝；之后调用签名与事件处理服务。

HMAC（共享密钥消息认证：用只有可信双方知道的秘密计算消息校验值）用于识别未持有密钥的人篡改或伪造事件。当前Mock把payload序列化为固定字段顺序、固定JSON格式后计算SHA-256 HMAC；签名密钥由Django的`SECRET_KEY`与项目Mock用途字符串派生，不是在前端保存。`compare_digest`比较签名，但不能弥补密钥泄漏或错误业务规则。

这个协议完全是项目自定义Mock协议，不是任何第三方支付的官方验签格式。生产供应商可能要求原始body、证书或指定字段，不能把当前canonical JSON方案直接迁移过去。

## 即使签名正确，为什么还要比较订单和金额？

签名正确说明消息通过当前签名机制，不说明消息一定对应这张交易。事件里的请求引用先定位原PaymentAttempt，订单也从这个Attempt找到，再检查payload中的订单号、金额、币种和Provider。

例如原订单89元/CNY，通知签名有效但金额0.01，处理器仍拒绝`payment_mismatch`，不能把订单改为已付。测试中这种有效签名坏金额由受控后端生成，是模拟错误通知；普通前端不知道密钥，不能随意生成有效签名。

还要防止重放：合法签名事件可能被重复发送。Inbox事件唯一性、原事件内容冲突检查与锁内状态门槛使同一笔付款效果只产生一次。当前不是通过时间戳或nonce全面禁止消息重放，而是将重复消息的业务效果约束为幂等。

伪造者也不能先用错误签名占住未来合法的`event_id`。事件接收会给无效签名生成内容摘要对应的内部事件号，保留审计原文；后来合法通知仍能使用真正事件号。完整验签和交易校验在处理器内再次执行。来源或体积前置拒绝则不会进入Inbox，这两类失败不能混为“每个拒绝请求都会留下事件记录”。

## 两段核心代码：权限与签名都要由后端判断

第一段是模拟发货的实际门槛，位于`@transaction.atomic`修饰的`ship_order`中。先确认`staff+订单修改权限`，再检查真实订单状态；代码摘录之后创建Shipment并更新SHIPPED，共同提交。[固定源码：ordering/fulfillment_services.py](https://github.com/jasper507/E-commerce/blob/a5d4b04995552a340a471d31e68973e89f67e54c/backend/apps/ordering/fulfillment_services.py#L29)

```python
    if not actor.is_staff or not actor.has_perm("ordering.change_order"):
        raise FulfillmentRuleError(
            "shipping_forbidden", "只有具备订单修改权限的管理员才能模拟发货。", 403
        )

    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if order is None:
        raise FulfillmentRuleError("order_not_found", "订单不存在。", 404)
    if order.status == Order.Status.SHIPPED and Shipment.objects.filter(order=order).exists():
        return order, False
    if order.status != Order.Status.PAID:
        raise FulfillmentRuleError(
            "order_not_paid", "只有已支付的订单才能模拟发货。", 409
        )
```

第二段是Mock签名验证。长度与ASCII限制防止比较函数因异常输入抛错；它之后仍有订单、币种、金额与状态校验。验证不依赖顾客登录，所以没有顾客Session的合法本地通知也可以进入处理。[固定源码：payments/gateway.py](https://github.com/jasper507/E-commerce/blob/a5d4b04995552a340a471d31e68973e89f67e54c/backend/apps/payments/gateway.py#L87)

```python
    def verify_event(self, payload, signature):
        """用常量时间比较校验签名；拒绝非 ASCII，避免比较函数抛异常。"""

        return (
            isinstance(signature, str)
            and len(signature) == 64
            and signature.isascii()
            and hmac.compare_digest(
                sign_payload(payload),
                signature,
            )
        )
```

金额/订单的实际比较见[锁内处理代码](https://github.com/jasper507/E-commerce/blob/a5d4b04995552a340a471d31e68973e89f67e54c/backend/apps/payments/services.py#L308)，本地入口限制见[Webhook View](https://github.com/jasper507/E-commerce/blob/a5d4b04995552a340a471d31e68973e89f67e54c/backend/apps/payments/views.py#L124)。

## 具体失败与结果，怎样讲得准确

| 尝试 | 当前实际规则与结果 |
|---|---|
| 普通注册提交`is_staff=true/is_superuser=true` | 不属于写入字段，创建的仍是普通用户 |
| 只有staff标记、没有change_order权限的人调用发货服务 | 403，不改已付款订单或Shipment |
| 权限完整的管理员选择未付款订单 | 409 `order_not_paid`，管理权限不能绕过付款状态 |
| 顾客在页面伪造“已经支付” | 顾客API不把该字段当成确认事实；付款确认来自事件机制 |
| 无Session但有正确签名的本地Mock通知 | 可以处理；仍经过字段匹配与状态机，不代表任意公网回调开放 |
| 非回环/非本地环境通知 | HTTP404，事件不入箱 |
| 超过16KiB的通知 | HTTP413，事件不入箱 |
| 进入接收服务但坏签名或错金额/币种 | 原通知保留并拒绝处理，不改变确认状态或库存 |
| 已处理的合法通知再次发送 | 复用事件/终态事实，不重复付款与库存效果 |

## 为什么这样选，有什么代价和替代方案？

发货只放Django Admin，适合当前单商家、小规模模拟履约：复用框架身份与权限，业务service仍是唯一合法状态转换入口。若做独立运营后台，必须新增受保护的管理API、角色分配和审计，不是把普通顾客按钮显示出来就完成管理权限。

Mock通知采用本地访问限制、签名、字段匹配、Inbox和幂等，能真实学习来源认证与交易正确性。替代方案是由顾客提交订单状态；实现容易，却把资金侧事实交给不可信客户端，不能采用。真正第三方协议可以提供独立可信来源，但带来密钥/证书管理、协议兼容、环境与恢复工作，当前没有业务或面试证据需要把范围扩大到真钱集成。

`REMOTE_ADDR`限制也有代价：经过反向代理时它通常反映最后一跳，不能单独代表原始调用者；不能因一个代理地址是回环，就宣称外部请求也安全。现在的限制只作为Mock本地门槛，来源真实性仍依赖签名。当前没有用可伪造的`X-Forwarded-For`替代签名，也没有完成公网代理下Mock回调防护验收。

签名能保护消息真实性与完整性，却不提供加密、订单状态正确性或业务副作用幂等。它们分别需要传输保护、字段/状态检查和持久化事务机制。这样分层增加了检查步骤和审计数据，但每一步都对应一个不同失败场景。

## 证据来自哪里，不能扩大到哪里？

以下是既有测试与历史执行，2026-10-11本轮只静态核对：

- 注册测试明确提交管理员相关字段并断言普通用户没有staff/superuser权限，见[固定测试](https://github.com/jasper507/E-commerce/blob/a5d4b04995552a340a471d31e68973e89f67e54c/backend/apps/accounts/tests/test_registration_api.py#L82)。测试代码存在与本轮已执行是两回事。
- 支付API测试验证没有顾客Session的正确签名通知、非回环拒绝、非本地禁用、16KiB拒绝、坏签名/错误金额/币种无付款效果，以及无效事件号不能抢占合法事件，见[固定测试](https://github.com/jasper507/E-commerce/blob/a5d4b04995552a340a471d31e68973e89f67e54c/backend/apps/payments/tests/test_payment_api.py#L326)。
- 发货与收货测试验证只有staff仍403、授予change_order后一次发货、他人收货404、本人重复收货不重复Shipment，并核对Admin action调用服务，见[固定测试](https://github.com/jasper507/E-commerce/blob/a5d4b04995552a340a471d31e68973e89f67e54c/backend/apps/payments/tests/test_payment_api.py#L749)。
- [MVP-4最初记录](../../verification/mvp-4-mock-payment.md)只完成SQLite非并发烟测，不能拿它证明MySQL行锁。后来[2026-10-08秒杀HTTP回归](../../verification/phase-6d-flash-sale-http.md)在隔离MySQL运行promotions/ordering/payments等组，共207项、565个子场景；[同日订单分页回归](../../verification/phase-3-order-list-pagination.md)再次运行支付API、订单生命周期等组，共63项、8个子场景。历史的署名日期、环境和限制保留。
- [2026-10-11 Phase9C](../../verification/phase-9c-session-csrf-operations.md)真实代理HTTP的15项认证检查通过；它没有验证顾客交易、Admin实际发货页面或公网Webhook。不能把它当成这一整条业务流的真实部署验收。

当前没有供应商真实通知、公网HTTPS、签名密钥轮换、消息期限、第三方证书或防滥用容量证据。Mock的正确性学习价值已经成立，不能把“本地签名测试通过”写成生产支付安全。

## 如果重新做，我会改哪里？

我会保留三种主体的职责与服务端状态机。顾客请求意图、管理员负责履约、网关报告支付结果；不能让某个前端字段跨越这三个边界。

如果仍做同一个学习项目，我会优先让Mock签名密钥与Django Session用途的密钥分开配置，并定义轮换时旧事件怎样继续验签。当前复用`SECRET_KEY`便于小范围Mock实现；分开后可以缩小泄漏影响和独立轮换，但增加配置、历史Inbox兼容和测试成本，需要明确采用条件，不能只改一个环境变量就宣称完成。

如果以后确实需要真实供应商，则按其官方协议重新设计验签适配、独立密钥/证书、消息时效与可信代理边界；继续保留原Attempt引用、金额/币种匹配、先入箱和幂等效果。是否增加时间戳/nonce要服从供应商协议，不能把Mock缺少它们直接等同于当前重复付款漏洞。

只有运营流程变复杂时，我才把发货权限细分为独立动作权限，并加入必要审计。当前change_order配service状态检查已经保护了模拟闭环；新权限会增加授权配置和回归成本。本专题达到能独立解释主体、凭据、规则与一条失败证据后收束，不自动建设运营系统或接入第三方支付。
