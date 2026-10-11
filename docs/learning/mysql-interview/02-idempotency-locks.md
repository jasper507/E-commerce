# 为什么幂等唯一约束和行锁都要有？

[MySQL与订单查询总览](../mysql-current-overview-study-guide.md) · [本轮整理与发布验证](../../verification/mysql-interview-knowledge-base-publication.md) · [快照与事务](01-data-transaction.md) · [分页与归属](03-pagination.md)

本文按2026-10-11源码快照`886600018badf0d157a6cd6d7164a308c4bd1c2b`核对，复用历史隔离MySQL证据。本轮没有重新运行服务或测试。面试重点是分清三件事：重复请求应返回什么、不同顾客怎样竞争库存、最后哪些规则由数据库兜底。不能把它们都简称为“加锁防重复”。

## 一次断网重试和两个人抢货，是两个问题

顾客点下单后，服务器成功提交，返回包却丢了。浏览器重试时，应返回原订单，而不是再占两件库存。Idempotency（幂等）在这里指同一次购买意图重复执行，最终仍是一份交易事实及一份预占副作用。

另外，甲和乙各用自己的Key同时买最后一件鼠标。两次请求都是有效的新意图，不能靠Key把它们合并；数据库必须决定谁能占到这件货。这是共享库存并发的问题。

Unique Constraint（唯一约束）禁止同一业务身份落成两行；Row Lock（行锁）让正在竞争的事务按规则使用同一行。前者决定“数据库不接受哪些重复记录”，后者控制“读、检查、更新之间谁能插进来”。当前方案两者都用，但承担不同职责。

## 当前订单的幂等身份是什么

订单唯一键是`(user, creation_scope, idempotency_key)`。同一个字符串Key，两个顾客可以分别使用；同一顾客的普通购物车和秒杀入口也分开识别。`creation_scope`把购买入口加入身份，避免普通下单Key误命中秒杀订单。

普通购物车请求摘要由规范化的`address_id`计算。Fingerprint（请求摘要）用于判断同一个Key是否又被拿去提交不同参数；SHA-256是比较工具，不是验签、权限或防伪机制。秒杀摘要另含活动商品ID，不能把两种请求当同一个参数结构。

| 同一顾客、同一入口下的请求 | 当前行为 |
|---|---|
| 新Key，购物车及库存合法 | 创建待支付订单，201 |
| 已成功的原Key，原`address_id` | 返回原订单，200，不再次预占 |
| 已成功的原Key，换`address_id` | `409 idempotency_conflict` |
| 原请求事务已回滚，没有成功订单 | 原Key仍可再尝试，重新校验当前购物车和库存 |

**普通购物车摘要没有包含购物车版本、商品清单或数量。** 第一次已经成功后，即使用户重新往车里加了商品，同Key、同地址仍返回旧订单。想发起第二次购买必须使用新的购买意图和新Key。第一次失败并回滚后，由于没有保存成功订单，原Key重试会按重试时的购物车处理；当前不能宣称它会强制重现第一次失败前的全部商品组合。

当前Key随订单保存，没有单独的短TTL去重缓存。原单后来取消、过期或支付成功，重放仍先读取已有订单；这不是“换状态后可拿旧Key创建新单”。

## 同Key并发为什么不会两次都创建

关键在于**先锁用户，再检查已有订单**。不能先在事务外查“Key不存在”，然后觉得可以安全创建，因为两个请求都可能得到“不存在”。当前同顾客请求A取得User锁；请求B等待同一行。A提交后B再进入检查，读取A已经保存的订单，返回原单。

第一处核心代码：[普通下单幂等分支](../../../backend/apps/ordering/services.py#L133)。下面是函数内部133—148行的真实连续摘录，省略外层`@transaction.atomic`函数定义及后续业务写入。

```python
    locked_user = User.objects.select_for_update().get(pk=user.pk)
    fingerprint = _request_fingerprint(address_id)
    existing = Order.objects.filter(
        user=locked_user,
        creation_scope=Order.CreationScope.CART,
        idempotency_key=idempotency_key,
    ).first()
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise OrderRuleError(
                "idempotency_conflict",
                "This Idempotency-Key was already used with a different request.",
                409,
            )
        return _order_with_details(existing.pk), False
```

幂等检查早于地址、购物车和库存重校验，这很关键：第一笔订单成功已清空购物车，原Key重放不能因此返回“购物车为空”。即使原地址后来删除，也应该返回当时已经保存的订单和地址快照，而不是再验证今天的购物条件。

数据库[订单唯一约束](../../../backend/apps/ordering/models.py#L119)是最后一道防线：即使另一写入路径未遵守服务锁序，同用户、同入口、同Key也不能落成两张订单。但唯一约束冲突本身不会自动变成“比较摘要并友好返回原单”，那仍是应用服务的职责；当前正常请求通过User锁把判断串行化。

User锁比“每个Key一把锁”粗。同一用户不同Key的结账也会被串行化，且地址操作同样可能参与竞争。这里选择实现小、业务易解释的基线，代价是限制同顾客并发；不同顾客各锁各自User，随后仍会在共享库存竞争。

## 普通购物车下单按什么顺序申请锁

| 顺序 | 请求使用的锁 | 保护的动作 |
|---|---|---|
| 1 | 当前User | 串行化同顾客新下单，先识别原Key；与已有地址写入规则协调 |
| 2 | 本人Address | 读取用于复制的收货资料，限制地址归属 |
| 3 | 本人Cart | 取得当前购买集合的购物车边界 |
| 4 | CartItem，按`sku_id, id`排序 | 锁本车商品项；联查目录用于本次报价，不锁整个联查目录 |
| 5 | Inventory，按`sku_id`排序，NOWAIT | 避免库存读—改交错；被其他事务占用时快速拒绝这一库存步骤 |

Lock Ordering（锁顺序）让参与竞争的路径尽量按同样顺序申请资源，减少“A握住SKU1等SKU2、B握住SKU2等SKU1”的循环等待。排序和一致协议能降低风险，不能证明全项目所有SQL永远无死锁；其他写入路径、查询计划及新增规则仍须一起评审。

第二处核心代码：[购物车锁范围与库存锁](../../../backend/apps/ordering/services.py#L170)。下面是函数内部170—185行的连续摘录；前面的User、Address和Cart已锁定，后面只把MySQL错误3572转成`409 stock_busy`，其余数据库错误继续上抛。

```python
    cart_items = list(
        CartItem.objects.select_for_update(of=("self",))
        .select_related("sku__product__category")
        .filter(cart=cart)
        .order_by("sku_id", "id")
    )
    if not cart_items:
        raise OrderRuleError("cart_empty", "Cart is empty.", 400)

    try:
        inventories = {
            inventory.sku_id: inventory
            for inventory in Inventory.objects.select_for_update(nowait=True)
            .filter(sku_id__in=[item.sku_id for item in cart_items])
            .order_by("sku_id")
        }
```

`select_related`联查并不等于只锁最外层表。项目曾真实复现：没有限定范围的`FOR UPDATE`把共享SKU/Product/Category也锁住，第二位顾客先在这一步等待，根本还没走到库存NOWAIT。后来改成`of=("self",)`，实际SQL限定为`FOR UPDATE OF ordering_cartitem`，才恢复预期的库存繁忙反馈。

NOWAIT（不等待这个锁）只针对这条库存锁查询。User、Address、Cart以及其他数据库操作仍可能等待，因此不能说“整笔下单都是非阻塞”。缩小锁范围还有报价代价：目录变更与在途下单没有严格互斥，订单使用本次服务端读取的资料，详见上一页。

## 甲持锁时，乙收到繁忙还是售罄

初始库存1件，甲先取得Inventory锁，把库存改为`available=0、reserved=1`但尚未提交。乙申请同一库存NOWAIT锁，得到`409 stock_busy`。**繁忙表示此刻无法完成可靠判断，不代表已经确定没有货。**

随后有两种结局：甲提交，乙用原Key重试后读取库存0，得到`insufficient_stock`；甲回滚，库存回到`1、0`，乙重试可以成功。乙先前失败没有留下订单，也没有清空购物车。这个交错比一张“加锁以后不超卖”的简图更能解释锁、事务和重试的关系。

如果甲成功但响应丢失，甲重试自己的原Key时直接返回已提交订单，不重复走库存预占。幂等识别解决自己的重复请求，库存锁解决甲乙之间的竞争，两者同时存在才覆盖这两个场景。

## 为什么不把普通订单全部改成条件UPDATE

Conditional UPDATE（条件更新）把“库存足够才扣减”写进一条SQL，并用影响行数判断成功。例如单活动SKU购买1件时，当前秒杀服务使用`WHERE available_stock >= 1`配合`F`表达式，同时减可售、加预留；只有更新1行才继续。它依然需要数据库写锁，竞争时可能等待，不能叫“无锁扣库存”。

本项目普通购物车多SKU继续使用NOWAIT，秒杀单活动SKU已经采用条件UPDATE。后者仅改变一个库存步骤，订单、购买资格、快照、预占和流水仍属于同一事务；它没有代替幂等，也没有代替整单回滚。多SKU若逐项条件更新，后一项失败必须回滚前面的成功，还要统一申请顺序与等待策略，不能把“单SKU已验证”直接推广为整个购物车方案更优。

秒杀创建仍先锁User、检查原Key、锁本人Address；活动和目录读取后创建新Order及快照，先取得`FlashSalePurchase`名额，再执行库存UPDATE，最后写预占和流水。名额放在库存前与关闭路径的资源顺序协调。这里没有普通购物车的Cart/CartItem步骤，不能把普通订单锁序直接复制过去；新建记录与查询已有行锁也要分清。

早期两线程对照显示：首单提交时条件UPDATE等到锁释放后判断售罄；首单回滚时等待者能继续成功，NOWAIT则已返回繁忙。早期人为持锁约0.2秒，因此216—225毫秒的等待不是生产性能指标，也不能单凭这个实验宣布赢家。后来的完整秒杀实验与正式替换属于[秒杀专题](../phase-6c5-flash-sale-inventory-failures-study-guide.md)，不在本页重复扩展。

## 数据库约束究竟能保证什么

| 约束 | 能拒绝的错误 | 不能单独保证的事 |
|---|---|---|
| 订单`user + scope + key`唯一 | 同一创建身份写出两张订单 | 返回原响应、识别不同参数、按规则选出当前价格 |
| `available/reserved >= 0` | 负库存字段 | 无超卖、无丢失更新、库存与全部预占总数相符 |
| 订单/行金额公式及非负检查 | 一行内部金额公式不成立 | 订单小计等于所有明细之和，价格来自合法服务端报价 |
| Reservation与OrderItem一对一 | 一条明细出现两条预占记录 | 预占状态转换正确、过期时间到了自动释放 |
| `reservation + movement_type`唯一 | 同一预占同类型重复记流水 | 每笔数字变化恰好对应正确业务状态与流水增减量 |

“库存不为负”尤其容易说过头：两个请求都先读到1、都按旧值写回0，最终没有负数，却可能产生两份订单和两份预占。项目的错误基线真实出现过订单2、预占2、最终库存`0/1`，非负约束并没有救回丢失更新。数据库约束、应用不变量和竞争控制必须互相配合。

## 替代设计有什么取舍

| 方案 | 可以简化什么 | 需要承担的代价 |
|---|---|---|
| 只禁用前端按钮 | 减少普通误点 | 无法防网络重试、并发请求或其他客户端 |
| 仅依赖订单唯一约束，冲突后读取原单 | 减少粗粒度User串行化 | 要正确处理事务冲突、摘要比较与所有副作用；不能在破损事务里直接续写 |
| 独立创建幂等记录并按Key锁 | 可以表达处理中/已完成，并减少同顾客不同Key竞争 | 多一张表、失败恢复与生命周期规则；必须定义Cart变化怎样影响同意图 |
| 库存用阻塞行锁或条件UPDATE | 等待首单结果后可能直接成功 | 等待占用连接，需评估超时与整个事务回滚；没有自动消除热点 |
| 再加Redis分布式锁 | 可协调某些跨数据库路径 | 引入过期、释放、网络不确定性；当前单MySQL事务已有可用机制，不能替代数据库事实保护 |

当前最小充分证据已经覆盖同Key双线程、最后一件货、真实锁等待和写入后回滚。本轮先形成能独立讲述的回答，不重复负载矩阵，也不自动扩成分布式锁或新幂等系统。

## 已有证据和当前边界

| 历史记录 | 可以据此讲什么 |
|---|---|
| [MVP-3，2026-09-18](../../verification/mvp-3-order-inventory.md) | 隔离MySQL目标14项、9子场景通过，包含同Key双线程一单一预占、异参409；旧唯一键后来加入scope，旧日期不改写 |
| [错误库存基线，2026-09-26](../../verification/phase-3-inventory-race-baseline.md) | 两线程旧快照写回能产生两单；正式流程这组最多一单。一次受控正确性例子，不是生产压测 |
| [库存步骤对照，2026-09-26](../../verification/phase-3-inventory-strategy-comparison.md) | 4项通过，区分NOWAIT立即繁忙与条件UPDATE等待提交/回滚；没有完整下单性能结论 |
| [完整下单竞争与锁范围修复](../../verification/phase-3-checkout-contention.md) | 修复前2失败1通过，修复后3通过；实际观察到CartItem JOIN提前阻塞及范围修复，10顾客抢1件只有一单，失败购物车保留 |
| [单SKU正式条件UPDATE](../../verification/phase-6c5-flash-sale-inventory-failures.md) | 秒杀路径的真实锁超时、完整回滚和原Key重试另有证据；1205/1213映射只限库存UPDATE，不能推广到所有SQL |

当前[同Key并发测试](../../../backend/apps/ordering/tests/test_order_concurrency.py#L61)仍检查一个公开订单号、一个`created=True`、一份订单/预占/流水。本轮只核对代码与记录，没有声称本轮这些测试又通过。Payment处理从既有Order等事实开始，采用自己的锁协议；这里的“User起手”是创建普通订单流程，不能当作全项目所有操作通用的锁序。

## 如果重新做这个项目，我会改哪里

我会先把购买意图的边界定义得更明确：原Key是否绑定一个购物车版本和商品数量，失败后购物车变化还能否沿用它？当前成功重放规则清楚，但摘要只含地址，不能保证失败重试还购买同一组商品。如果业务要求更严格，我会让结账意图携带服务端可验证的Cart版本或独立意图身份，再比较是否值得建立幂等记录表。

我会更早检查`select_for_update`与JOIN的实际SQL，保留“首单尚未提交，第二人必须已返回繁忙”的受控测试。它检查真实等待位置，比只对着ORM代码说“库存用了NOWAIT”可靠。

我仍会保留数据库唯一约束和事务回滚。只有观察到User粗锁确实妨碍所需业务后，才考虑按Key缩小串行化范围；只有证据显示多SKU等待策略需要改变后，才比较新的库存方案。重做的改进是把意图、锁范围和失败协议更早明确，不是把所有路径统一换成一个技术名词。
