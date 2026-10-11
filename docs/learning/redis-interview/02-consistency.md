# 更新后怎样防止旧请求回填旧数据？

2026-10-11，源码快照 `29b7944860080ccddedb2c0f0c060c0a27658376`。[Redis总览](../redis-current-overview-study-guide.md) · [本轮核对](../../verification/payment-redis-interview-knowledge-base-restructure.md)。本页用真实失败交错解释分代Key；历史实验与当前能力分开，没有重跑故障。

## “更新数据库后删缓存”还缺什么

管理员把鼠标名称或SKU价格更新后，希望后来的浏览请求看到新值。Cache-Aside通常是读miss时回填、写提交后失效，但“提交后删除”不能取消早已读到旧值的请求。

下面价格129→169用于说明交错；Phase 4A/4B实际旧回填反例也使用商品改名等字段。重点是执行顺序，不是某个字段。

| 顺序 | 旧浏览请求A | 管理员请求B | 后来的请求C |
| --- | --- | --- | --- |
| 1 | 缓存miss，从MySQL读129，准备回填 | | |
| 2 | 暂停在SET前 | 写MySQL为169并提交 | |
| 3 | 仍暂停 | 删除详情Key | |
| 4 | 把之前读到的129写入同一个Key | | |
| 5 | | | 命中129，MySQL其实为169 |

**Race Condition（竞态：执行交错决定结果）发生在“删除后仍有旧SET”。** on_commit（提交回调：事务成功提交才执行）能避免数据库回滚却误失效，不能关闭这个旧回填窗口。TTL每次SET重新起算；旧请求很晚才写，就不能以数据库改价时间承诺“最多旧35秒”。

## 当前方案让旧请求始终留在旧命名空间

Generation（代号）实际是32字符的随机UUID十六进制串，下面简称G1/G2。详情Key为 `prefix:catalog:detail:v2:G1:product_id`，负标记也包含同代号；`v2`是格式版本，G1是目录内容代号，两者不是同一个概念。

1. A在任何详情SQL之前捕获G1，再查询MySQL得到旧内容。
2. B修改MySQL并成功提交；on_commit在Redis写新代号G2。
3. C在成功换代后开始，捕获G2，只读G2的详情。没有G2副本时加载当前MySQL并回填G2。
4. A晚到仍用自己捕获的G1，只能把旧内容放进G1。它可以返回当时已读取的旧内容，但不会污染G2。

Logical Invalidation（逻辑失效：副本还在，但正常读不再选它）让旧代物理存在也无妨。旧详情按原25–35秒TTL回收；目录代号本身没有TTL。图见[更新与恢复时序图](https://ocnbkcx1ug58.feishu.cn/wiki/O4iUwSvl1i4Je7keOL0ci1iAnLf#doxcnu7Y0NYEQ3i3iTSRSRmwKyg)，这里的步骤与图等价。

承诺要说完整：**数据库成功提交，并且Redis成功换代之后才开始的新请求，不选择旧代副本。** 提交前已经开始的A可以旧响应；MySQL提交与回调之间、回调失败和进程崩溃都不在这个保证内。

## 代号为什么必须在第一条SQL前读取

第一条Product/Category SQL会拿到名称、描述等字段，不只是检查上架。如果A先读出旧名称，再在B换代后取G2，之后把这个旧Product与SKU组装成JSON，就会把旧名称放进G2。只在SKU查询前取代号仍不够。

同样，回填时不能为了“取最新版本”重新查generation。那会把旧数据重新贴上G2标签。每个请求从开始到结束固定读写捕获的代号；具体源码在[缓存对象构造](../../../backend/apps/catalog/cache.py#L150)，视图在Product查询前创建它。

## 提交后换代的核心代码

保存Product/Category/SKU共用的[提交回调](../../../backend/apps/catalog/cache_signals.py#L12)：

```python
@receiver(post_save, sender=Product)
@receiver(post_save, sender=Category)
@receiver(post_save, sender=SKU)
def catalog_saved(sender, instance, using, raw, **kwargs):
    if raw or not settings.CATALOG_CACHE_ENABLED:
        return
    transaction.on_commit(invalidate_catalog_details, using=using)
```

事务成功才换代，回滚不执行回调。没有外层事务时，正常save在数据库提交后执行。当前选择全目录代号，分类改名、SKU改变所属商品都不需要列举每一份详情Key；代价是无关商品也一起变冷。`QuerySet.update`、`bulk_update`、外部SQL不发送普通save信号，不在这一维护契约内。

换代动作的[实际代码](../../../backend/apps/catalog/cache.py#L259)：

```python
def invalidate_catalog_details():
    if not settings.CATALOG_CACHE_ENABLED:
        return
    try:
        # 全目录逻辑失效；旧读只能回填旧代，旧副本由原 TTL 回收。
        get_redis_client().set(catalog_generation_key(), uuid4().hex)
    except RedisError:
        # 数据库已提交，失效失败不能把成功的 Admin 写入变成错误响应。
        logger.warning("catalog_cache_invalidation_failed")
```

代码不会因为Redis失效失败把已经提交的管理员修改说成失败；日志保留故障。但这也明确暴露了缺口：目前没有持久保存失败的换代待办，没有后台自动补偿。

## 代号丢失为什么用随机值与SET NX

若generation Key丢失，详情旧Key可能仍存在。重置为`1`会重新选择历史代1副本；随机新代避免主动复用旧命名空间。两个请求同时发现缺失时，流程是GET→SET NX随机提议→再次GET实际赢家，不能各自直接使用自己的提议。

NX表示“仅Key不存在时写入”。实际winner只允许一个，第二次GET使两个请求用相同代号。代号读取无效或Redis失败时本请求绕过副本、受限读MySQL；不能凭任意字符串选一个命名空间。这不等于已验证Redis复制延迟、旧快照回滚或整个生产拓扑恢复。

## 真实故障为什么会出现129→169→129→169

[Phase 4F第三个实验](../../verification/phase-4f-slow-faults-and-recovery.md)使用真实连接拒绝，健康Redis保留旧Key，确认不是“超时但SET其实已执行”。

| 顺序 | 数据与通信状态 | 实际详情价 | 业务SQL |
| --- | --- | ---: | ---: |
| 1 | MySQL129，G1详情已回填 | 129.00 | 首次2 |
| 2 | SKU.save提交169；换代连不上Redis；读取也受限回源 | 169.00 | 2 |
| 3 | 恢复原Redis，代号仍G1，旧详情当时还剩32秒TTL | 129.00 | 1 |
| 4 | 再次正常save成功切G2，新请求加载MySQL | 169.00 | 2 |
| 5 | G2热详情 | 169.00 | 1 |

数据库始终已经是169，没有回滚。恢复只是重新能访问G1，不会自动知道有一次失效失败。最后再次save是实验中的主动动作，不能说成系统自己修好了；读取熔断探测成功也不会换代。

## 替代方案要对准同一个失败窗口

| 候选 | 对旧回填或失效能做什么 | 为什么本项目没直接选择它 |
| --- | --- | --- |
| 删除+TTL | 简单，旧副本最终可到期 | 已真实复现删除后旧SET；不是严格提交后新鲜保证 |
| 延迟双删 | 试图在旧回填后再删一次 | 需要可靠调度，固定延迟难覆盖任意慢请求，故障仍需处理 |
| Redis WATCH/原子条件回填 | 版本不变才写，解决比较与SET交错 | 额外事务/失败处理；普通GET比较后SET不具有该保证 |
| 当前全局分代Key | 旧SET与新读用不同Key，机制简单 | 全目录逻辑失效、多GET、旧副本短暂占内存；换代失败不修复 |
| 商品/分类粒度版本 | 减少无关详情变冷 | 分类影响、多SKU归属、写入口传播与失败处理更复杂 |
| 持久版本或持久失效待办 | 为崩溃/失效失败留下恢复依据 | 扩展数据模型与写入协议，仍要定义应用/重试/监控方式 |

“先拿分布式锁”不是唯一出路。若只锁读回填而写侧不参与同一协议，也不能阻止上述交错；让所有写读参与又新增过期、所有权与故障责任。当前分代把已观察到的旧回填隔离问题用较小设计解决。

## 证据与重做取舍

[Phase 4A](../../verification/phase-4-product-detail-cache.md)保留删除后的真实旧回填反例；[Phase 4B](../../verification/phase-4b-cache-generations.md)在同样交错下允许旧SET仍发生、断言新请求读新代，并覆盖首条SQL窗口、并发初始化、代号丢失重建、正常save和回滚。另一商品也变冷的成本随后由[Phase 4I](../../verification/phase-4i-multikey-avalanche.md)实际复现。它们能支撑机制和边界，没有强一致或生产性能结论。

如果重新做，我会先决定故障后的展示新鲜度要求。若业务可接受短期旧展示、下单仍实时校验，当前分代+TTL可以作为简单基线；若改价必须在重启/故障后最终自动修复，优先评估**随数据库改动持久记录待失效意图**，再由受控任务执行换代。代价是积压、重复执行、持续失败告警和清理，不能说只增加一张表就获得保证；当前未实现。

我会把这个可靠性问题与失效粒度问题分开。只有目录写入导致大量无关重建的证据足够时才评估细粒度版本；没有必要同时改所有写路径、加跨进程锁和同步整份JSON。面试可以准确说：我解决了成功换代后的旧回填隔离，并真实展示了失效失败后恢复旧读，知道下一步应解决哪一个窗口。
