# Phase 6E 学习指南：秒杀页面、购买意图恢复与浏览器验收

日期：2026-10-08。对照实现提交 `67d8c82` 与[6E 验收记录](../verification/phase-6e-flash-sale-browser.md)。
本次补写指南只读核对当前源码与已有证据，没有重新运行应用、浏览器或数据库测试。

这一片最需要掌握的结论是：**浏览器没收到响应，不能据此断言订单没有创建；确认时必须保持原用户、Key、活动项和地址。**
浏览器保留购买意图，后端先查已有订单，两者共同完成恢复。

它支撑的工程能力是沿 Vue → HTTP → Django → MySQL 排查一次交易，而不是只看到报错就重新发单。
可回答的面试追问是：如何处理响应丢失、为什么禁用按钮不能代替后端幂等、如何证明没有重复预占库存。
最小证据已经保存：实际创建、刷新后同 Key 重放、同 Key 异地址拒绝、Mock 支付后的业务事实。
停止于能解释这些证据与代价，不为学习本指南追加 Lua、MQ、压测或另一套页面。

## 1. 怎样读，以及哪些文件负责什么

先读第2–4节，用一张89元订单理解问题和请求；再读第5–8节，对照关键代码；最后做第10–11节的练习。
前置知识需要补查时，读[6D HTTP 指南](phase-6d-flash-sale-http-study-guide.md)和[6C-5 事务回滚指南](phase-6c5-flash-sale-inventory-failures-study-guide.md)，不用重读全部旧聊天。

| 文件/函数 | 作用 | 本片重点 |
| --- | --- | --- |
| [FlashSalesView.vue](../../frontend/src/views/FlashSalesView.vue)：loadSession、buy、submitIntent、startNew | 页面加载、购买、确认和主动开启新购买 | 参数冻结、状态切换、错误反馈 |
| [flashSaleIntent.ts](../../frontend/src/flashSaleIntent.ts)：read/save/clearFlashSaleIntent | 保存和恢复浏览器中的购买意图 | 先存后发、按用户分区、刷新恢复 |
| [flashSales.ts](../../frontend/src/api/flashSales.ts)：fetchFlashSaleItems、createFlashSaleOrder | 调用两个秒杀接口并解释响应 | Session/CSRF、Key、成功响应检查 |
| [flashSale.ts](../../frontend/src/types/flashSale.ts)：FlashSaleItem、FlashSaleIntent | 描述前端对象有哪些字段 | 列表数据与购买意图不同 |
| [auth.ts](../../frontend/src/api/auth.ts)：fetchCurrentUser、ensureCsrfCookie | 获取登录身份、准备写请求的安全校验 | 身份核对与CSRF各自职责 |
| [views.py](../../backend/apps/promotions/views.py)：FlashSaleOrderCreateView | 校验输入，调用服务并返回201/200/业务错误 | 前端参数如何进入后端 |
| [http_services.py](../../backend/apps/promotions/http_services.py) | 暂设5秒行锁等待、调用事务、恢复连接原值 | 复用6D边界；5秒不是整个请求期限 |
| [services.py](../../backend/apps/promotions/services.py)：create_flash_sale_order | 查旧意图或原子创建新订单 | 为什么重放不再次预占 |
| [OrderDetailView.vue](../../frontend/src/views/OrderDetailView.vue) | 展示本人订单、发起Mock支付 | 沿用既有业务闭环 |

页面和API接线属于 Supporting（辅助实现）；意图、幂等、事务和证据解释属于 Depth（必须独立理解的核心规则）。
不用逐行背诵HTML/CSS，但应能指出哪个函数发请求、哪些条件允许重试、哪里真正保护库存。

## 2. 为什么网络报错后不能直接重新买

以真实验收中的合成数据为例：用户1，活动项711，地址1，普通SKU价格129.00，活动价89.00，库存初始可售1、预占0。
为了方便讲述，把本次生成的UUID Key简称为K1；真实格式是`flash-sale-<UUID>`，不能把教学缩写K1直接填进浏览器恢复存储。

第一次购买可能经历：

```text
浏览器准备购买 → 后端创建89元订单 → MySQL提交 → 发回201 → 浏览器未收到可用响应
```

前端只知道“结果待确认”；MySQL已经有订单，库存已经变成可售0、预占1。
如果刷新就丢弃K1，自动生成K2，后端会把K2视为另一购买意图。
当前项目还有限购规则，K2可能被拒绝；这依然没有替你确认K1对应的订单。不能靠限购把错误的恢复流程变正确。

**Idempotency（幂等）**：同一次业务意图重复提交，返回已有结果，不重复产生订单或库存副作用。
这里通过同用户、同下单范围、同Key识别原意图，再核对活动项/地址是否相同。
**Intent（购买意图）**：这一位顾客希望用这个地址购买这个活动项的一次具体决定。

正确恢复是：保留K1、item711、address1，在相同登录用户下确认。
结果有两种：原单已经存在就200重放；如果原单没有创建，而且当前规则仍允许，则相同意图可能在重试时首次创建并返回201。
所以“同Key重试”不保证总是200，但不会把已存在的同参订单变成第二张订单。

## 3. 为什么保存四个字段，而不是只保存Key

实际存储对象如下，Key缩写仅用于示例：

```json
{"userId":1,"key":"K1","itemId":711,"addressId":1}
```

| 字段 | 恢复时的作用 | 不能替代什么 |
| --- | --- | --- |
| userId | 区分哪一个登录用户的意图，发送前核对身份 | 不能代替后端Session认证 |
| key | 标记这一次购买 | 不能把不同参数强行变成同一请求 |
| itemId | 固定原活动项 | 不包含客户端决定的成交价 |
| addressId | 固定原请求中的地址ID | 不是整份个人地址快照 |

只保留Key而丢失地址时，你可能用K1+地址2重试：后端先找到原订单，再发现参数不一致，返回409/idempotency_conflict。
你之前回答“地址不一样冲突，409”对应这个情况。正确的页面应先冻结地址1，用原四字段确认原购买，而不是让顾客改参数后碰到冲突。

**sessionStorage（会话存储）**：浏览器按站点、标签页保存的一小份数据，同一标签页刷新时仍能读取。
本片按用户使用名称 `project-one-flash-sale-intent:1`。换账号时读取新账号自己的名称，不把原用户的意图作为新用户的购买。
关闭标签页、换设备或手动删除存储以后，没有这份恢复承诺；也没有实现跨标签页购买协调。

存储只放这四个字段，不放密码、Cookie、Token、手机号或地址全文。
**Session（登录会话）**负责让Django识别当前用户；意图中的userId只是前端恢复检查，不是权限凭据。

## 4. 一次请求的完整流程

### 4.1 首次加载页面

```text
App导航 → /flash-sales路由 → FlashSalesView挂载
  ├─ GET /api/flash-sales/items/ → 当前可见活动与in_stock提示
  └─ GET /api/auth/me/ → 当前用户
       → 读取该用户的sessionStorage
       → 有旧意图则显示待确认
       → GET /api/addresses/ → 本人地址选择
```

`onMounted`表示组件出现后开始加载；`Promise.all`让活动加载和用户/地址加载并行。
它们是读取请求，不代表已经创建订单。
Vue的`ref`让页面随数据变化更新；脚本中`.value`取出或修改其当前值。`computed`根据这些值计算能否购买等条件。

没有旧意图时，新购买要求已登录、选中当前有货的活动项、选中本人地址，且没有存储错误。
有旧意图时，“确认原购买”直接用保存的ID，即使活动列表为空、显示无货或地址列表加载失败，也不拿这些列表条件阻止重试。
这不绕过权限：每次真正提交仍要通过Session、CSRF和后端服务。

### 4.2 点击购买一件

```text
buy()
→ 生成K1，固定用户1/item711/address1
→ sessionStorage成功保存
→ submitIntent()，进入submitting
→ GET /api/auth/me/，再次核对当前用户
→ GET /api/auth/csrf/，准备CSRF值
→ POST /api/flash-sales/orders/，携带Session Cookie与K1
→ Django URL / Session认证 / CSRF / 输入校验
→ HTTP适配：在事务外保存原等待值、暂设5秒
→ create_flash_sale_order完整事务
→ 先锁用户、查同用户/FLASH_SALE范围/K1
   ├─ 已存在：核对item/address → 读原订单，不再次预占
   └─ 不存在：检查地址/活动/资格 → 建订单与快照 → 条件UPDATE预占 → 写流水 → 提交
→ 事务结束，HTTP适配恢复连接原等待值
→ 新创建201 / 同意图重放200
→ 浏览器解析并检查所需订单字段 → confirmed → 顾客点击链接进入现有订单详情
```

**CSRF（跨站请求伪造防护）**：给使用Cookie登录的写请求增加核对，防止其他网站借用你的登录状态操作。
它与“地址是否属于本人”是不同检查，二者都由后端负责。
**Transaction（事务）**：把订单、快照、资格、预占和流水作为一起成功或一起回滚的一组操作。

页面发送的主要内容是：

```http
POST /api/flash-sales/orders/
Content-Type: application/json
Idempotency-Key: K1
X-CSRFToken: <浏览器取得的CSRF值>

{"flash_sale_item_id":711,"address_id":1}
```

本段是教学示例，没有真实Cookie或Token。页面不发送userId、价格、数量或总额。
客户端不能决定成交价和数量；后端忽略额外字段，按活动价和数量1创建交易。

## 5. 对照关键源码，理解执行顺序

以下为当前源码摘录，不是可以独立运行的完整模块。

### 5.1 冻结原意图，再保存

来自页面`buy()`：

```ts
  const original = Object.freeze({
    userId: user.value.id, key: `flash-sale-${crypto.randomUUID()}`,
    itemId: selectedItemId.value, addressId: selectedAddressId.value,
  })
```

- `crypto.randomUUID()`生成本次意图使用的标识；刷新恢复不能重新调用它替换旧Key。
- `selectedItemId/selectedAddressId`是此刻的选择，复制进原意图后，确认请求继续用这份固定值。
- `Object.freeze`禁止代码直接修改这个对象的字段。本对象只有四个简单值，无须保存完整商品或地址对象。
- 类型中的`readonly`用于TypeScript编译检查，freeze用于运行时；都不能代替后端校验，也不能防止顾客刻意修改自己的浏览器存储。

保存发生在提交前：

```ts
  try {
    saveFlashSaleIntent(original)
  } catch {
    storageError.value = '浏览器无法保存购买信息，因此尚未发送下单请求。请启用会话存储后刷新。'
    return
  }
  intent.value = original
  await submitIntent()
```

`try/catch`捕获存储失败；`return`立即结束，后面的请求没有执行。
只有保存成功，页面才持有原意图并调用提交。
`await`表示等待异步操作完成再处理结果；它不会把前端与后端变成一个数据库事务。

来自存储函数：

```ts
  sessionStorage.setItem(storageName(intent.userId), JSON.stringify(intent))
```

`JSON.stringify`把对象转成可保存的文字；刷新后`JSON.parse`再恢复为对象。
读取函数校验用户ID、正整数item/address和Key格式，重新构造并冻结四字段对象。
损坏或不可读取的存储会阻止新购买，不能静默当成“从来没有旧意图”。

### 5.2 发送固定参数，核对身份

来自`submitIntent()`：

```ts
    const currentUser = await fetchCurrentUser()
    if (currentUser.id !== original.userId) {
      throw new Error('登录账号已变化，请刷新页面后处理当前账号的购买。原购买信息已保留。')
    }
    const result = await createFlashSaleOrder(original)
```

如果页面加载后账号已经变化，不把旧账号的意图继续发给新账号。
这项检查支持正常客户端恢复；没有声称实现所有跨标签页身份切换的原子保证。最终身份/所有权仍由Django确定。

来自API：

```ts
  const token = await ensureCsrfCookie()
  const response = await fetch('/api/flash-sales/orders/', {
    method: 'POST', credentials: 'same-origin',
    headers: {
      Accept: 'application/json', 'Content-Type': 'application/json',
      'X-CSRFToken': token, 'Idempotency-Key': intent.key,
    },
    body: JSON.stringify({ flash_sale_item_id: intent.itemId, address_id: intent.addressId }),
  })
```

`credentials: 'same-origin'`让同源请求携带Session Cookie；浏览器经现有Vite代理访问Django。
Header（请求头）携带Key和CSRF，Body（请求体）只放两个ID。
`fetch`收到HTTP错误不一定抛异常，因此API还检查`response.ok`；页面捕获已分类业务错误与未知异常。
响应的`as Order`类型声明不能代替实际检查：当前`isOrder`核对公开订单号、允许的状态、币种、金额、单件商品等本页需要的字段。
它不是完整的通用响应schema校验器。状态200/201或解析出来的JSON不足以单独确认订单。

### 5.3 后端为什么先查原订单

来自`create_flash_sale_order()`：

```python
    existing = Order.objects.filter(
        user=locked_user,
        creation_scope=Order.CreationScope.FLASH_SALE,
        idempotency_key=idempotency_key,
    ).first()
    # 重放读取已经提交的事实，不受活动结束、改价或当前名额占用影响。
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise OrderRuleError("idempotency_conflict", "该幂等键已用于不同请求。", 409)
        return _order_with_details(existing.pk), False
```

**Fingerprint（请求指纹）**：把活动项与地址规范化后形成摘要，用来判断同Key的参数是否仍相同。
查找限定用户、下单范围、Key；数据库还有相应唯一约束，不能仅靠前端按钮防重复。
`FLASH_SALE`的实际数据库值是`flash_sale_checkout`，不要把枚举名称当成SQL里的存储字符串。

`return ... , False`表示“取回已有订单，这次没有新创建”。函数在这里结束，后面的活动检查、建单和库存UPDATE不会执行。
View据此返回200；新单的`created=True`才返回201。
若把库存/活动检查放在查旧单前，已经抢到最后一件的顾客会被自己的无货状态挡住，无法安全确认原单。

单用户行锁让同用户的下单请求按顺序核对已提交结果；完整事务确保失败时订单与库存不会留下半套事实。
保存Key解决刷新后的输入恢复，后端幂等/事务解决业务副作用，职责不能互相替代。

## 6. 页面状态与订单状态要分开

| 页面state | 含义 | 可以做什么 |
| --- | --- | --- |
| choosing | 尚未发送这次购买 | 选择商品与本人地址 |
| submitting | 当前正在核对身份或提交 | 等待；提交入口不重复发送 |
| pending | 原意图已保存，但结果还没确认 | 使用原参数确认；不能开始新购买 |
| rejected | 收到被明确分类的业务拒绝 | 看原因，主动开始新购买后再选 |
| confirmed | 收到有效200/201订单结果 | 查看订单/Mock支付，或主动开始新购买 |

`pending`属于浏览器状态；`PENDING_PAYMENT`属于服务端订单状态，表示订单存在但尚未付款。
响应丢失时两者可以同时存在：浏览器pending，MySQL里的订单PENDING_PAYMENT。
支付后重新用原Key确认，服务端可返回PAID；重放不要求整份JSON与第一次完全相同。

刷新时只恢复四字段，统一先显示pending，再通过原请求确认当前订单；不会把旧页面的一句“成功”当作新查到的服务端状态。
收到成功后也保留意图，直到顾客主动点击“开始新购买”。`startNew()`先清理已解决意图，再回到choosing，不自动发送另一单。
它只允许confirmed或rejected。清理失败继续保留原状态，不假装已安全开始新购买。

## 7. 怎样解释失败，避免错误地换Key

| 情况 | 页面处理 | 为什么 |
| --- | --- | --- |
| 网络错误、5xx、JSON解析/成功字段检查失败 | pending，原四字段不变 | 后端是否已提交不能仅由客户端错误判断 |
| 403 | pending，提示登录状态或安全校验问题 | 匿名与CSRF失败均可能403；原单是否已存在也没有因此被否定 |
| 409 stock_busy / checkout_busy | pending，提示繁忙，手动用原参数确认 | 当前事务已退出；无自动重试或新Key |
| 409 idempotency_conflict | 保留意图，要求核对原订单 | Key已有其他参数对应的事实，不能自动修改参数修补 |
| 404/409且code是明确业务拒绝 | rejected，展示原因 | 只把已识别合同分类；不把所有404或409当成同一种失败 |
| 意图存储失败 | 不发送下单POST | 否则刷新后无法知道原Key和参数 |

明确拒绝的例子是`address_not_found`、`flash_sale_unavailable`、`flash_sale_limit_reached`、`insufficient_stock`。
这些结论来自具体服务合同；未知错误继续保留待确认。
当前代码在POST的403分支只给出两种可能原因，未自动诊断究竟是哪一种；不要把这种提示说成已经实现精准CSRF诊断。

最常见的三个错误：刷新就生成新Key；换地址时自动改原意图；用当前GET无货禁用旧单确认。
现有普通Checkout只在内存保存Key，地址watch会换Key；本片因此没有照搬，也没有扩大范围修改普通结账。

## 8. 用具体值看正常、重放与Mock支付

| 时刻 | 浏览器 | MySQL订单/库存 | 副作用累计 |
| --- | --- | --- | --- |
| 尚未提交 | choosing | 无本次订单；可售1/预占0 | RESERVE0，COMMIT0 |
| 实际201后响应丢失 | pending，保存K1/711/1 | 一单PENDING_PAYMENT；可售0/预占1；资格HELD | RESERVE1，COMMIT0 |
| 刷新后原Key200 | confirmed，同一public_id | 前述事实不变 | RESERVE仍1，COMMIT0 |
| K1却改地址2 | 冻结UI不允许；测试用fetch刻意发错参数 | 409，订单与地址A快照/库存不变 | 没有新增副作用 |
| 从订单详情Mock支付 | 既有详情显示已支付 | PAID；可售0/预占0；资格CONSUMED，预占COMMITTED | RESERVE1，COMMIT1 |
| 支付后原Key200 | confirmed，显示当前PAID | 仍同单、同终值 | RESERVE/COMMIT都仍只有1 |

库存预占是把一件从可售移到预占；支付核销把原预占正式消耗，所以支付时不是再次减少可售库存。
Mock Gateway（模拟支付网关）只模拟供应商结果，本项目没有真实资金。
本轮验收用同步支付，已有MQ能力的证据来自此前阶段；没有在此重新运行RabbitMQ/Worker。

## 9. 已有证据支持什么，暂时不要声称什么

以下均为2026-10-08此前6E验收保存的结果，本次补指南只核对来源：

- 真实HTTP首次201后，测试取得响应再故意abort客户端；刷新恢复原四字段，实际无货仍能200取回同一订单。
- 数据库首次、重放、异地址409三个快照相同；支付后只有一次COMMIT。这比只看“成功”提示更能证明副作用次数。
- 存储失败注入没有下单POST；503、畸形200、有效Session下403注入保持原意图。
- 桌面1280×720与手机390×844通过，正常刷新后控制台0errors/0warnings；定向目标12passed/162子场景，相关回归51passed/19子场景。

来源：[验收说明](../verification/phase-6e-flash-sale-browser.md)、[请求与数据库JSON](../verification/phase-6e-flash-sale-browser-data.json)、
[正式目标输出](../verification/phase-6e-flash-sale-browser-target-output.txt)、[正式回归输出](../verification/phase-6e-flash-sale-browser-regression-output.txt)。

边界必须一起讲：响应丢失是收到真实201之后的客户端注入；没有证明切断真实网络，也没有验证数据库提交结果不确定的所有情形。
503/403/畸形响应是客户端替换响应；不是依赖实际停机。异地址409是真浏览器HTTP检查，正常UI始终冻结原参数。
这些测试耗时不能作为QPS、延迟优化或生产容量数据。没有加入Redis Lua、分布式锁或新的恢复基础设施。

## 10. 小练习：先预测，再对照源码

不用写真实订单，不必重跑实验。先用纸或临时草稿回答，再打开相应函数核对。

### 练习A：画出执行顺序

将下面五件事排顺序：生成原意图、发送POST、写sessionStorage、核对/me身份、设置submitting。
再解释存储失败时哪几件事根本不会执行。
核对`buy()`和`submitIntent()`，不要凭函数名称猜先后。

### 练习B：同一个Key的三种情况

先预测：K1/item711/address1重放；K1/item711/address2；K2/item711/address1且原单仍占资格。
分别指出服务在哪个分支结束、可能是什么HTTP/code、是否再次UPDATE库存。
核对`create_flash_sale_order()`；区分幂等冲突和限购拒绝。

### 练习C：手写最小恢复逻辑

在临时草稿写伪代码，处理：

```text
当前用户是1，存储有 {userId:1,key:K1,itemId:711,addressId:1}
活动GET返回[]，地址GET暂时失败
顾客点击确认原购买
```

要求：不产生K2、不读取新的地址选择、不因列表空而拒绝原意图；身份变化时不POST；错误后保留原四字段。
写完再对照`loadSession()`、`submitIntent()`；本练习不要求复制整套Vue页面。

### 练习D：口述证据边界

“服务器201后route.abort，最后一单/一次预占”分别证明了哪两层行为？
为什么不能改写成“已验证真实断网和任意提交不确定恢复”？
对照验收记录中的实测与客户端注入两节。

如果只是查代码，可在项目中打开上述文件。`npm.cmd --prefix frontend run build`会写被忽略的构建产物，但不写数据库；通过表示类型检查与打包成功，不能代替浏览器验收。
浏览器复现会创建/迁移/写入/销毁可丢弃测试库，操作顺序统一见验收记录，不把测试fixture加载进开发库。
本次学习整理不启动这些操作。

## 11. 面试复述与停止条件

先闭卷按这条线回答：**问题 → 必须保持的输入/副作用 → 前后端流程 → 为什么这样选 → 一个替代方案 → 证据 → 剩余代价。**

可对照的60–90秒回答示例（用于校正，不代表你已经掌握）：

> 秒杀下单存在服务器已成功提交、浏览器没收到响应的情况，所以我不会把网络报错当成没建单。发送前，页面按当前用户保存并冻结Key、活动项和地址；刷新后用同一组参数确认，不被无货提示挡住。后端锁用户，先按用户、下单范围和Key找订单，再核对参数指纹；已有同参订单直接返回200，后面的建单和库存预占不会再次执行。新单才在完整MySQL事务中创建订单、资格和库存事实。同Key改地址返回409，未知网络或5xx保留待确认。我们在真实201后注入客户端响应丢失，刷新重放得到同一单，数据库只有一次预占；Mock支付后只有一次核销。代价是当前只恢复同标签页的会话存储，注入也不代表真实断网或所有提交不确定情形都已验证。

一个有意义的替代方案是只用内存Key，代码更少，但刷新不能恢复；另一条路是从订单列表人工核对，当前已有该入口，但它不等于按原Key准确确认购买。
目前选择保存四字段加原请求重放，复用已存在的后端能力，没有引入新查询接口或存储系统。

能独立完成以下三点就先收束：解释K1同单/一次预占与异参409；串起正常、未知结果和Mock支付请求；说清存储与故障注入限制。
用户已经正确预测同Key异地址409；整条链路的闭卷复述、最小草稿恢复逻辑仍待核对，不能由AI的参考答案代替。
