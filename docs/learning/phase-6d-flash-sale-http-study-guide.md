# Phase 6D：最小秒杀 HTTP 接口

日期：2026-10-08。HTTP 适配为 Supporting / Pair；等待政策与事务失败边界为 Depth / Learning。
面试目标是独立解释登录、CSRF、参数、幂等与事务怎样组成一次请求，并指出客户端展示与数据库事实的区别。
最小证据是接口权限、输入、响应、重放/失败事实及相关回归；不追加负载矩阵或新前端。

## 本片前后变化

- **开始前：** 秒杀服务已用条件 UPDATE 保护库存，并支持支付和关闭；顾客还没有公开活动查询或登录下单的 HTTP 入口。
- **本片改变：** 接上活动 GET 与 Session/CSRF 下单 POST；基础接口完成后，6D-2 在事务外临时设 5 秒行锁等待，再恢复原连接值，并区分库存繁忙和其他下单繁忙。
- **完成后与边界：** API 与等待恢复已验证，成功提交不因恢复失败被否定；5 秒不是总请求期限。该片 APIClient 证据仍不能代替真实浏览器验收。

**后续衔接：** [6E 已接通极简页面与隔离浏览器验收](phase-6e-flash-sale-browser-study-guide.md)。临时 5 秒的执行证据见 [6D-2](../verification/phase-6d2-flash-sale-http-wait.md)。[学习导航](README.md)

## 1. 当前范围与已确认等待政策

沿用 [Phase 6C-5](phase-6c5-flash-sale-inventory-failures-study-guide.md) 的正式条件 UPDATE，核心下单函数不改。
本片先连接公开活动查询和登录下单，不操作开发数据或创建演示活动。
项目拥有者已选择“临时5秒并恢复原会话值”。HTTP适配先读取当前连接的等待值，设置为5秒，
再调用原下单事务；事务提交或回滚后，恢复同一物理连接原先的值。原值可以是50、17或其他值，不能硬编码恢复50。
数据库行锁等待值只限制相关等待，多个语句可依次等待，不能称为整个HTTP请求截止时间。

若采用临时会话值，必须在完整业务事务外保存/设置/恢复，连接复用前确认状态。
已提交成功后恢复失败不能改写为“订单失败”；需要丢弃无法确认状态的连接并保留原业务结果。
恢复失败时记录不含异常原文的告警，关闭并丢弃连接；新连接不继承旧连接的原值。
读取/设置失败时不调用下单事务；未分类数据库错误继续上抛，没有自动重试。
最小替代是继续沿用原值，少两次SET和一次读取，但不能给本入口单独缩短行锁等待。
本次5秒是学习项目的明确取舍，没有生产用户等待目标或容量证明。
实现见 [HTTP适配](../../backend/apps/promotions/http_services.py)，新证据见 [等待与恢复验证](../verification/phase-6d2-flash-sale-http-wait.md)。
参考 [MySQL 行锁等待变量](https://dev.mysql.com/doc/refman/8.4/en/innodb-parameters.html#sysvar_innodb_lock_wait_timeout)
和 [Django 持久连接](https://docs.djangoproject.com/en/5.2/ref/databases/#persistent-connections)。

## 2. 两个入口

| 请求 | 用途 | 关键边界 |
| --- | --- | --- |
| GET `/api/flash-sales/items/` | 匿名查看当前开放、商品可见的活动项 | 活动/Item/SKU/Product/Category都启用，starts<=now<ends；没有修改数据 |
| POST `/api/flash-sales/orders/` | 登录顾客购买一件活动商品 | Session + CSRF；服务端用户、价格、数量；完整业务事务 |

公开查询返回列表，不分页；当前范围是小规模学习项目，未验证大规模活动列表容量。
响应包含活动项id、活动名/时间、商品名/slug、SKU code/color/attributes、活动price、CNY、quantity=1及in_stock。
售罄或缺库存仍可展示为in_stock=false；不提供库存数字、资格、顾客或订单。
in_stock只是读取时提示；显示有货后被别人抢走，POST返回库存不足是正确行为。

## 3. 从请求到事务

例子：活动项7、本人地址12，活动价89.00、普通SKU价129.00。

```http
POST /api/flash-sales/orders/
Content-Type: application/json
Idempotency-Key: flash-sale-intent-001
X-CSRFToken: <当前浏览器的CSRF值>

{"flash_sale_item_id": 7, "address_id": 12}
```

浏览器还须携带登录Session Cookie。Session表示服务端识别出的当前用户。
CSRF是对Cookie写请求的额外核对，防止别的网站借用浏览器登录状态发起操作；它不能代替地址所有权检查。

1. 总URL交给promotions URL，DRF根据Session取request.user并验证写请求CSRF。
2. Serializer只校验两个正整数ID，View校验Header中1–128个可打印字符的Key（两端空白先trim）。
3. View传入request.user、两个ID、Key；不传客户端价格、数量或用户ID。
4. HTTP适配在事务外保存当前会话值、设置5秒。原服务进入atomic，锁用户，先查同用户/FLASH_SALE scope/Key，存在则核对请求指纹并重放。
5. 新意图核对本人地址、活动/商品、购买资格，再写订单、快照、Item和Purchase。
6. 条件UPDATE预占一件，写Reservation与Movement；整个atomic共同提交或回滚。
7. 事务已经结束，适配恢复原会话值；无法恢复则丢弃连接，保留已有业务结果。
8. 新建返回201，重放返回200；复用现有OrderSerializer与本人订单查询/支付/取消入口。

Serializer表示把输入转换为校验后的字段，并把输出对象整理成响应。
接口沿用现有普通下单行为：未声明字段被忽略，不是全部拒绝。
即使客户端额外传quantity=99、price=0.01、total=0.01或别人的user_id，成交仍为当前登录用户、一件、活动价89.00。
秒杀不读取或清空购物车；普通购物车下单入口继续保持原行为。

## 4. 三个关键例子

- 新Key首次成功：201/PENDING_PAYMENT，六类业务事实各一、库存可售5→4/预占0→1，购物车不变。
- 同Key同item/address再次请求：200/同一public_id，不重复预占。活动结束、改价或原地址后来删除不阻止重放已有订单事实；订单当前状态可能变化，不能承诺响应逐字相同。
- 同Key改地址或item：409/idempotency_conflict；新Key再次抢同活动项：409/flash_sale_limit_reached。幂等和限购保护不同边界。

普通购物车与秒杀的creation_scope不同，同Key可分别表达两个不同业务意图。
不同顾客也可以独立使用同Key。Key不代替登录或权限，知道他人public_id也不能读其订单。

## 5. 响应与失败

| 场景 | 当前薄接口合同 |
| --- | --- |
| 匿名下单 / 登录后缺CSRF | 403；业务服务未执行 |
| 输入字段格式不合法 | 400；DRF字段错误 |
| 缺失 / 非法Key | 400/idempotency_key_missing 或 idempotency_key_invalid |
| 别人地址或不存在地址 | 404/address_not_found，避免泄露其他用户地址存在性 |
| 活动项不存在 | 404/flash_sale_item_not_found |
| 未开放、商品不可购买、限购、库存不足 | 409/相应业务code |
| 库存UPDATE的1205/1213 | 409/stock_busy，先退出整单事务 |
| 原下单事务其他SQL的1205/1213 | 409/checkout_busy，先退出整单事务；用户/地址锁竞争不冒充库存不足 |
| 读取/设置会话失败 | 不下单；丢弃连接并上抛数据库错误 |
| 提交后恢复会话失败 | 保留201/订单结果，告警并丢弃连接；原Key重放仍是200/同一订单 |
| 未映射数据库异常 | 保持异常可见；不伪装成库存不足 |

业务拒绝使用code/detail供客户端处理；参数错误和认证/CSRF错误沿用DRF现有结构，不宣称全部响应格式相同。
当前View仅处理OrderRuleError；HTTP适配在原事务退出后，把其他下单SQL的1205/1213转换为checkout_busy。
不会自动换Key或自动重试。响应丢失时应保留同一意图的Key、item和address重新查询/提交，避免制造新意图。

## 6. 验证与复述

基础接口历史证据见 [首片记录](../verification/phase-6d-flash-sale-http.md)，当前等待及完整相关回归见 [6D-2记录](../verification/phase-6d2-flash-sale-http-wait.md)。
DRF APIClient覆盖URL、认证、CSRF、Serializer、Service、ORM和响应，不是浏览器或真实HTTP网络验收。
用TransactionTestCase验证真实事务，避免APITestCase外层事务掩盖提交/回滚边界。

闭卷复述：客户端能决定哪些字段、价格为什么不能由它决定、同Key为什么先于活动可用性校验、展示有货为什么仍可能抢购失败。
变式：数据库已成功提交，但浏览器没收到201；活动此时已结束。重试应保持什么输入，服务应怎样处理？

## 7. 用具体状态理解5秒与恢复

假设原连接等待值17秒，可售库存5、预占0。成功请求经历17→5→17；订单六类事实各一，库存4/1。
同Key重放经历17→5→17，但没有第二次库存UPDATE；新Key违反限购，也恢复17。

若另一连接锁住库存，当前订单已经写了Order、Item、快照与Purchase各一，此时UPDATE真实抛1205。
必须先让异常离开atomic，六类业务事实回滚为0，库存仍5/0；然后恢复等待值，返回stock_busy。
若堵在更早的用户锁，尚无这四类写入，最终返回checkout_busy；放行后原Key都能重新创建并重放。

若订单已提交，随后恢复SQL失败，数据库订单仍存在。将清理错误改成“下单失败”会误导顾客。
适配保留成功结果、告警、丢弃连接；用户收到成功，或响应丢失后用原Key取回同一单。
这不等于证明网络断连或提交结果不确定已经得到恢复，相关测试明确使用故障注入。

停止条件是这些请求/事务/连接边界和相关回归通过。下一片才是极简顾客入口与端到端验收，不自动追加压测。
面试复述：为什么5秒不是总请求期限？为什么恢复发生在事务外？为什么库存繁忙与下单繁忙分开？为什么清理失败不能否定已提交订单？

## 8. 后续阅读：Phase 6E

上述“下一片”描述6D结束时的状态。6E极简页面与隔离浏览器证据已完成，学习入口是[6E购买意图与浏览器指南](phase-6e-flash-sale-browser-study-guide.md)。
接着理解保存完整意图、刷新后原参数确认、403/未知结果分类和Mock支付的连接；独立讲述尚待验收。
