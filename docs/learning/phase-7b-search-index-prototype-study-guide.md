# ES搜索索引原型：从MySQL重建，而不是另造商品事实

2026-10-10。[学习导航](README.md) · [验证记录](../verification/phase-7b-search-index-prototype.md)。原型已在真实隔离MySQL与ES中验证；公开商品API仍使用7A的MySQL搜索。

## 本片前后变化

- **开始前：** 名称或描述只能包含匹配完整关键词，没有独立搜索副本或中文分析证据。
- **本片改变：** 从隔离MySQL导出公开商品，构建新的ES候选索引；用分析后的词项查询名称或描述，保留分类交集，观察相关性与重建。
- **完成后与边界：** 查询、重建、部分写入失败和服务离线已验证；没有API接入、实时同步或上线切换。价格与库存未进入ES，也没有生产性能结论。

## 1. 本片形成什么闭环

本片完成的是“明确搜索字段 → 从MySQL生成副本 → 查询候选商品 → 源资料改变后重新构建”的工程闭环。它帮助你解释为什么需要搜索引擎、第二份数据为什么会旧、以及怎样判断重建成功；顾客业务入口暂时不改。

搜索文档只有product_id、slug、category_slug、name、description。只导出商品和分类都有效的记录，缺货或无SKU商品仍能被发现。价格、库存、订单与支付不进入索引；后续接API时还要从MySQL重新检查可见性并生成当前商品摘要。

## 2. 重点一：分词改变查询语义，也会带来漏匹配

Analyzer（文本分析器）把名称、描述与查询文字转换成可检索的词项。这里使用内置CJK并保留英文停用词，避免安装第三方插件；它按相邻双字切片，不是按中文词典理解语义。依据[官方分析器说明](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/analysis-lang-analyzer.html#cjk-analyzer)，具体词项与反例必须以本片真实记录为准。

关键查询只有这段：

```python
{"multi_match": {
    "query": keyword, "fields": ["name^3", "description"],
    "type": "best_fields", "operator": "and", "zero_terms_query": "none",
}}
```

它仍在名称或描述中找，但要求全部分析词项出现在至少一个字段里；分散到两个字段不会自动拼在一起。`name^3`提高名称的评分权重，不能保证任意语料里名称命中永远排前；同分时按product_id排序。分类使用精确过滤，不参与相关性打分。[官方multi_match语义](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/query-dsl-multi-match-query.html)

需要跨字段拼词时，combined_fields是有意义的替代方案，但会改变“名称或描述单字段匹配”的语义；本片先把一套规则讲清，没有同时实现第二套查询。[官方对照](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/query-dsl-combined-fields-query.html)

本片对照词项、词序、名称/描述、分类与少量排序例子，同时保存CJK的漏匹配。它们说明需求和边界，不能当搜索质量全面验收或ES比MySQL更快的证据。

真实例子：`无线鼠标`被CJK切为`无线／线鼠／鼠标`，standard则切成四个单字。查询`鼠标 无线`在这组商品中命中6条，MySQL完整包含匹配为0条；但查询单字`标`时，ES为0条、MySQL为6条。连写查询还会要求`线鼠`，因此漏掉名称`无线 鼠标`。这是语义变化和代价，不能概括成“中文搜索全面变好”。

## 3. 重点二：重新构建不能先毁掉旧索引

先核对配置与实际连接的测试库身份，再用单条商品SELECT物化公开文档，最后调用ES；拒绝在外层事务里构建，避免拿数据库事务等待搜索服务。模型没有更新时间或版本号，本片也不假装已经有可靠增量同步。

每次创建随机新索引，旧成功索引保留。Bulk（批量写入）里的部分文档可能失败，即使HTTP请求成功也不能宣布整个索引完成。代码先检查逐项结果，再refresh使新文档可查询，并核对实际文档数；全部成功才加入可查询集合。

```python
refreshed = self.client.indices.refresh(index=index)
counted = self.client.count(index=index)
if refreshed["_shards"]["failed"] or counted["_shards"]["failed"]:
    raise RuntimeError("Search index validation returned failed shards.")
actual = counted["count"]
if written != len(documents) or actual != len(documents):
    raise RuntimeError("Incomplete search index build.")
self._ready.add(index)
```

这个集合只是原型内的完成门槛，不是生产读入口切换。使用新候选而不先删旧索引，可以证明失败不损坏旧结果；代价是重建期间保留多份索引、额外空间与清理工作。创建索引后响应丢失可能留下尚未登记的孤儿，close只清理确认创建的候选；本片结束时销毁整个隔离ES服务。没有alias原子切换、并发重建协调或崩溃后自动清理。

## 4. 为什么采用这套最小方案

MySQL继续保存事实，ES处理文本检索；这比把价格库存复制进去更容易控制陈旧数据影响。ES不可达时原型明确失败，不回写MySQL，也不偷偷切换公开接口。

替代选择是继续MySQL包含匹配，依赖少但搜索语义有限；MySQL FULLTEXT也值得按相同需求评估，本片没有证明它不适合。内置CJK省去插件维护，但漏召回必须接受或在后续需求中另选分析器；不能靠添加技术名称代替效果证据。

全量物化和全量新建适合小原型，代价是内存、写入和暂时双份空间。源读取、索引写入不是跨系统事务；源在导出后变化，候选立刻可能陈旧。文档数量一致也不证明所有字段正确，本片还需逐条独立预期检查。

## 5. 实际证据与下一步

人工数据含12件商品，10件当时可见。名称、描述、分类、无SKU、同分ID排序及ASCII例子符合独立预期；名称权重只证明固定文本对的评分6.543高于5.808，不证明普遍排名。

改名、下架、隐藏分类并新增商品后，新索引包含9份文档；旧索引仍返回原5个关键词结果，新索引为3个。真实Bulk注入一条错误类型文档：请求HTTP200，但一项400，候选只有1份文档、未获准查询，旧完整索引保持10份。服务销毁后真实连接超时，原型明确失败，未使用数据库fixture。

修正后在线5项通过、1项离线默认跳过；独立离线1项通过；目录回归24项／6子场景通过、7项显式实验跳过。5个自建索引精确清理后均不存在，独立ES服务已销毁。完整响应与原始输出见[验证记录](../verification/phase-7b-search-index-prototype.md)。

首轮测试的隔离写法错误曾误清开发库4表；已修为替身连接，并在拥有者确认后从完整日志恢复139条记录、逐字段复核。见[事故记录](../verification/phase-7b-test-isolation-incident.md)，不能把首轮通过解释为隔离安全。指南与测试通过仍不代表个人已掌握。

停止条件是这组证据足够支持“副本怎样生成、怎样查询、为何会旧、失败不能伪报完成”。下一片才接最小搜索读入口：由ES返回候选ID，再从MySQL检查可见性与摘要，并明确陈旧候选、分页数量与ES故障政策；不顺便加入增量同步或MQ搜索任务。
