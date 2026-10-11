# 同一个热点与多个冷商品怎样保护数据库？

2026-10-11，源码快照 `29b7944860080ccddedb2c0f0c060c0a27658376`。[Redis总览](../redis-current-overview-study-guide.md) · [本轮核对](../../verification/payment-redis-interview-knowledge-base-restructure.md)。Phase 4D/4F/4I保存旧策略证据；当前保护以Phase 4J共享名额为准。本轮核对未运行并发实验。

## 先把两种“很多请求”拆开

十个顾客同时看同一只Mouse M1，详情刚过期：十份请求想得到相同数据，可以合并成一份在途加载。管理员修改一个商品造成全目录换代，六个顾客各看不同商品：六份结果不同，无法合成一份，但可以控制同时重建几份。

这就是SingleFlight与共享名额各自解决的问题。SingleFlight（同Key在途加载合并）减少重复工作；BoundedSemaphore（有限通行证）限制受保护的加载并发。当前还有TTL Jitter（过期时间抖动），将每次详情TTL随机设置为25–35秒，分散自然到期；它不会让全目录换代时的新Key错开出现。

| 触发情况 | 首先面对的应用问题 | 当前手段 |
| --- | --- | --- |
| 同一个热商品变冷 | 多个请求重复查询同一份SKU与库存 | 本进程、完整同Key的SingleFlight |
| 很多不同商品一起变冷 | 即使无重复，许多详情仍集中重建 | 真实重建共用两个进程内名额，满员503 |
| Redis读取故障 | 原本命中的浏览请求也涌向数据库 | 同一组名额约束降级回源，读取熔断减少故障等待 |
| 自然过期相近 | 一批Key可能在相近时间失效 | TTL抖动；不处理逻辑换代造成的共同miss |

“缓存击穿/雪崩”只能作为问题简称。面试应该给出具体触发条件、被重复或集中执行的工作，以及保护的范围，不能只背三种术语和三种组件。

## 同一个商品：等待共享结果的完整过程

十个请求都先捕获G1，各自查询一次Product/Category确认上架，再初查同一完整详情Key。十个miss并不必然意味着十次数据库详情加载。

第一个注册Key→Future（未来结果：供其他线程等待这次加载完成）成为owner；其他请求取得同一Future。owner在锁外复查Redis，防止上一轮刚刚完成回填，自己还根据早先miss重复查库。仍miss才申请共享名额，查询SKU/库存，转成公开dict，尽力回填，然后通知等待者。

等待者拿的是只读公开数据，不共享ORM对象、Serializer、Request或Response。每个人已经各自确认可见性，合并的是后一份详情加载。完整Key包括环境前缀、格式版本、generation和商品ID；G2不能加入G1的Future，避免新请求共享旧代结果。

核心[登记代码](../../../backend/apps/catalog/cache.py#L64)只把注册表操作放进短Lock：

```python
def run(self, key, load, wait_timeout=DETAIL_WAIT_SECONDS):
    with self._lock:
        flight = self._flights.get(key)
        owner = flight is None
        if owner:
            flight = Future()
            self._flights[key] = flight
```

后面的Redis、SQL和Future等待都在锁外。如果把它们放进这把锁，正在加载鼠标会连带阻塞键盘等不同Key注册，锁会从保护小状态变成串行整个详情入口。

完成后finally删除本次登记，不保存长期结果。缓存本身由Redis负责；如果Future永久保留，就出现第二套内存缓存与新的失效责任。

## 200ms为什么不能保证始终只加载一次

等待者只等共享结果200ms。owner如果仍被数据库或其他工作拖住，等待者可以独立复查Redis并尝试加载，不能取消owner或从注册表删掉它。否则第三个请求会再次认为没有owner，制造更多重复工作。

owner抛异常时，给等待者发送失败标记，自己的请求继续抛原始异常；等待者独立复查或加载，不跨线程重抛同一个异常对象。失败、超时都意味着共享结果没拿到，不证明Redis网络故障，不能因此打开读取熔断。

[Phase 4F历史慢owner实验](../../verification/phase-4f-slow-faults-and-recovery.md)人工在SKU查询前暂停owner，四个等待者真的等200ms超时，随后五个请求都进入加载，最终10 SQL。它暴露当时健康miss不受故障名额约束。当前Phase 4J已经让这些实际独立加载也申请共享名额，因此不能把旧峰值5当作现状；200ms依然只限制Future等待，不是整个HTTP时限，也不会取消原数据库查询。

## 多个不同商品：先合并，再限制真实加载

共享名额放在确定需要加载之后。健康详情hit、owner二次GET命中、成功共享Future都不用名额，否则十个同Key等待者会占满预算，把真正有用的加载反而挡住。

当前[限并发代码](../../../backend/apps/catalog/cache.py#L43)非阻塞取得名额，满员503，finally归还：

```python
@contextmanager
def slot(self):
    if not self._slots.acquire(blocking=False):
        raise CatalogFallbackBusy()
    try:
        yield
    finally:
        self._slots.release()
```

健康miss调用 `fallback_scope(for_load=True)`，使真实SKU加载与回填共享这两个名额；Redis故障时原外层已经取得名额的请求不会在内层重复申请。正常结束、404或加载异常都要释放，避免一次故障耗尽永久预算。

六个不同商品例子：先把六份详情预热，正常save使目录切G2。旧六Key仍有正TTL，但新代六Key为空。两个加载者被有序暂停在SKU SQL前，另四个到来发现名额满，在前两个放行之前就结束503。后续顺序再请求这四个，分别完成加载与回填。

**峰值降低不代表必要数据消失。** 六份不同结果最终仍各需要一次SKU查询；保护的是同时做重建的峰值，把部分成功推迟到用户重试。503是接受的可用性代价，不能称为同等成功流量下查询永久减少。

## 名额保护范围必须说准确

| 具体路径 | 名额放在哪里 | 满员503前的业务SQL |
| --- | --- | ---: |
| 健康Redis，详情miss确需重建 | SKU加载及Redis回填 | 已有1次商品/分类检查 |
| Redis代号/负标记早期读取失败或跳过 | 商品SQL前的降级scope | 0 |
| 详情GET晚期失败 | 商品检查之后、SKU加载之前 | 1 |
| 健康hit或共享成功结果 | 不取得重建名额 | 正常商品检查仍有1 |
| 显式禁用缓存或事务内旁路 | 当前不新增名额保护 | 原查询路径 |

这不是全站数据库并发上限，健康商品检查SQL、健康随机缺失slug查询等都不被这份预算包住。两个名额也不是每秒两个请求，不是Redis连接池上限或MySQL连接数。四个应用进程各有两个名额，受保护scope可能合计八个；暂慢Redis SET也会占名额，因为scope包含回填。

## 两组证据分别支持什么

| 证据 | 原路径 | 新路径 | 能证明的结论 |
| --- | --- | --- | --- |
| [Phase 4D同Key，三轮各10请求](../../verification/phase-4d-detail-singleflight.md) | 详情加载9/10/7，总SQL19/20/17 | 详情加载1/1/1，总SQL11/11/11 | 本机自然调度的冷波详情加载26→3，总SQL56→33；30次guard保留 |
| [Phase 4J六个不同Key，有序保持重叠](../../verification/phase-4j-refill-admission.md) | 每轮6个200、SKU SQL6、峰值6 | 每轮2个200/4个503、SKU SQL2、峰值2 | 三轮均守住两个加载名额；随后四份必需加载仍执行 |

4D是请求合并当时的真实历史比较；4J以测试专用历史策略fixture保留旧故障限定，并与当前策略对照。APIClient没有经过真实HTTP服务或浏览器，Event暂停用于验证并发边界，不能冒充MySQL实际慢查询。自然运行六个很快的请求可以都200，有序暂停才固定形成2成功/4拒绝；这些比例不是生产容量。

[Phase 4I](../../verification/phase-4i-multikey-avalanche.md)还确认TTL未到期也会因换代一起变冷，说明TTL抖动不能替代共享名额。[Phase 4E](../../verification/phase-4e-fallback-admission.md)则给出故障回源满员零SQL拒绝的早期边界。

## 如果重新做，先预算，再决定跨进程手段

我会在接入SingleFlight时同步列出“不同Key、超时后的独立加载、Redis故障”三条路径，早点明确它们是否允许503，避免把同Key合并误当成数据库保护。这是设计遗漏的改进，不是再添加一个组件名称。

若实际负载证明多个进程重复重建同一热点代价大，可评估Redis租约锁加二次检查；但需要锁过期、所有权、释放、持有者崩溃和Redis故障策略，不能沿用本进程Future的假设。若问题主要是数据库总预算，则先根据进程数和其他SQL来源确定上游/实例预算；两个Python通行证不能承担全局责任。

当前K=2、等待200ms都是实验参数。重做时应先确定可接受的成功率与等待预算，再测同等业务产出下的资源和时延；排队能减少短期503，却占用等待线程并增加尾延迟，立即拒绝则需要页面明确重试。当前证据足以讲明机制，未量出生产最佳数值。
