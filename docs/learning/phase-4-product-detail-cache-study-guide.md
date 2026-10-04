# Track B 第一段：商品详情 Cache-Aside

> 这是 Phase 4A 的基线学习材料。当前代码已进入 Phase 4B，请接着阅读
> [缓存分代学习指南](phase-4b-cache-generation-study-guide.md)，区分物理删除
> 与分代 Key 的逻辑失效；下面保留旧方案用于理解失败与取舍。

本段属于 Depth / Learning Mode。业务闭环已经能从浏览走到收货，现在用
商品详情建立“缓存一致性与故障降级”这条面试故事的基线。

## 先理解三个例子

1. 第一次打开无线鼠标详情：MySQL 确认商品及分类上架，Redis 没有副本，
   MySQL 再读 SKU/库存，Serializer 输出公开 JSON，回填 Redis。
2. 再次打开：MySQL 仍确认上架状态，Redis 命中，直接返回公开副本。
   缓存保存的 `price="129.00"` 和 `in_stock=true` 是浏览提示。
3. 管理员已下架商品：下一次请求的 MySQL 检查返回 404，不能因为 Redis
   还有旧详情就继续展示。这里承诺的是下架提交后才发起的新请求；已经在途的
   请求与原 MySQL 方案一样，仍可能完成此前观察到的数据读取。

## 请求怎样经过项目

```text
Vue 商品详情页 → GET /api/catalog/products/{slug}/
→ URL → PublicProductDetailView（公开只读，不读取顾客身份）
→ MySQL 查询 Product + Category，检查上架状态
→ Redis GET project-one:development:catalog:detail:v1:{product_pk}
   ├─ 命中：Response 返回公开 JSON
   └─ 未命中/异常：MySQL 查询活跃 SKU + Inventory
       → PublicProductDetailSerializer → 尽力 SET JSON + TTL → Response
```

缓存默认关闭，保持可重复比较的 MySQL 基线；只在本地开发/测试启用。
列表和分类接口、Session、地址、订单、支付均不使用这个缓存。
缓存中的数字库存、身份和订单数据没有暴露给公开接口。

Cache-Aside（旁路缓存）表示应用主动查缓存，缺少副本时从数据库重建。
Key（缓存键）是 Redis 中定位副本的名字，包含环境前缀、用途、格式版本及
Product 主键。主键不随 slug 改名而变化；格式改变时可以升级 `v1`。

TTL（自动过期时间）初始为 30 秒，Jitter（随机抖动）为正负 5 秒，
每次回填实际为 25–35 秒。抖动让大量同时回填的 Key 不必同时过期，
但这一段没有证明它足以防止缓存雪崩。30 秒也是实验起点，不是测得的最优值。
命中不续期；不存在商品不做空值缓存。

## 修改怎样使副本失效

读 `cache_signals.py`：Django Signal（模型保存时通知其他代码的钩子）
覆盖当前 Admin 调用的普通 `save()`。Product 修改删除自身 Key；SKU 修改
删除所属商品的 Key，改归属时同时删除旧、新商品；Category 修改删除当前
关联商品的 Key。公开目录还没有其他业务写 API。

这些删除放在 `transaction.on_commit()`：数据库成功提交后执行；回滚时
不删除。若先删除、再提交，其他请求可能在提交前读到旧数据库值并回填。
这解释了为什么“提交后删”优于“提交前删”，但它仍不是强一致方案。

Inventory 写入没有接入目录缓存失效。预占、释放或补货后，有货提示可以
暂时陈旧，直到 Key 到期或目录修改触发失效。这减少了第一段对交易路径的
耦合。下单继续读取并锁定 MySQL 库存，重新验证上架状态、SKU 价格和数量；
缓存有货不等于一定能买，缓存价格也不是订单最终报价。

## 故障与必须承认的限制

- Redis 读失败：回源 MySQL，本次请求不再尝试回填。连接和命令超时各设
  100 ms，客户端不重试；它们不是整个 HTTP 请求耗时的上界。
- Redis 写失败：仍返回本次 MySQL 读取结果；删除失败：仍保留已提交的
  数据库修改。日志只记操作类别，不输出连接 URL、密码或原始 payload。
- 事务内的详情读取绕过缓存读和写，防止未提交数据进入共享副本。
- `QuerySet.update()`、`bulk_update()` 和外部 SQL 不触发保存信号。
  商品/分类下架仍有实时检查；其他浏览字段依靠过期收敛。
- 即使提交后删除成功，旧请求也可能随后回填旧值：
  `A 读旧值 → B 改名并提交/删除 → A SET 旧值`。
  TTL 从 SET 开始计算，不能说“从 B 提交起最多陈旧 35 秒”。
- Redis 中断时删除可能丢失；恢复后未过期旧值可能再次命中。
- 不缓存不存在商品，也没有热点保护、熔断或并发请求合并。
  Redis 故障时每个请求都可能回源；本段未证明 MySQL 能承受集中回源。

## 阅读与练习

依次读 `views.py` 的 `retrieve()`、`cache.py`、`cache_signals.py`，最后对照
`tests/test_detail_cache.py` 和验证记录。原 Serializer 和下单 Service 都保留。

完成这一段后，请自己解释：

1. 为什么缓存命中仍要查一次 MySQL？为什么未命中不是三次查询？
2. 为什么改数据库后删除缓存，比把新详情直接写进 Redis 更容易维护？
3. `on_commit` 解决了哪个窗口，又留下哪个窗口？
4. 页面显示 129 元且有货，下单前变成 149 元且库存为零，应以谁为准？
5. 为什么两次查询变成一次，不能直接说接口快了一倍？

练习：关闭参考，重写 `get → miss → MySQL → SET` 的伪代码；再在测试中
把 TTL 改成 1 秒，解释为什么命中不能延长过期时间。

下一段先用本段保留的旧值回填实验比较最小修正方案，再决定是否需要
版本校验。Lua、分布式锁和消息队列都应由具体问题推动。

机制参考：[Django on_commit 官方说明](https://docs.djangoproject.com/en/5.2/topics/db/transactions/#performing-actions-after-commit)
解释了提交、回滚与回调的关系；[Redis 客户端超时与重试说明](https://redis.io/docs/latest/develop/clients/redis-py/produsage/)
可用于对照本段显式的超时和零重试配置。测试观察以仓库验证记录为准。
