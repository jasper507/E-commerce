# Phase 7B：可重建ES商品搜索索引原型

日期：2026-10-10。应用基线`b0d58a5`。Supporting实现＋Depth设计/证据解释；[学习指南](../learning/phase-7b-search-index-prototype-study-guide.md)。状态：原型、真实隔离验证与事故恢复完成；公开搜索入口仍为MySQL。

## 范围与最小正确预期

- 独立测试Compose：ES8.19.23，127.0.0.1:9201，单节点、无副本、2GB容器上限/512MB堆；官方Python客户端8.19.3。关闭安全只用于本地隔离服务，不是部署配置。镜像digest：`sha256:4d0724bb8d78d7a2330693623e26ca4d621079d062ecc8a30b3e1fc180ec14c1`。
- 原型仅允许DJANGO_ENV=test、配置及实际MySQL test_project_one、自动提交且无外层事务；ES地址、cluster_name与8.19版本族核对。每次随机创建新候选，清理仅确认创建的精确名字。
- 商品/分类均有效才导出product_id、slug、category_slug、name、description；不因缺货/无SKU排除。严格mapping，价格/库存不进索引。
- 从MySQL物化来源重新构建；检查Bulk逐项结果、refresh/count失败分片与实际数量，成功才可查询。不做ES_reindex、alias切换或公开API契约变更。
- CJK分析；名称或描述至少一个字段包含全部分析词项，名称权重3；分类精确filter，相关性降序/ID升序，最多20条。原型空词按ID，不能当成现有API的name/id排序已改变。

## 实际执行与结果

所有pytest数据库进程串行，DJANGO_ENV=test、CATALOG_CACHE_ENABLED=false、PAYMENT_EVENTS_ASYNC=false。在线必须显式设置`PROJECT_ONE_TEST_ELASTICSEARCH_URL=http://127.0.0.1:9201`，报告必须是未存在的绝对路径；离线另设`PROJECT_ONE_TEST_ES_OFFLINE=1`。命令为pytest新原型文件在线运行，服务销毁后单独运行offline测试；回归为public_api/product_search/models/admin及两个显式实验文件，URL/实验开关全部移除。

| 检查 | 实际结果与证据 |
| --- | --- |
| 首轮在线 | 5 passed／1 skipped，11.45秒；[raw](phase-7b-search-index-prototype-online.txt)、[JSON](phase-7b-search-index-prototype-online.json)。随后发现隔离测试错误，不能据此声称开发库安全 |
| 修正后在线 | 5 passed／1 skipped，10.69秒；[raw](phase-7b-search-index-prototype-online-final.txt)、[JSON](phase-7b-search-index-prototype-online-final.json) |
| 原目录与默认关闭 | 24 passed／6 subtests／7 skipped，9.70秒；[raw](phase-7b-search-index-prototype-regression.txt)。不启动ES，7项跳过为旧查询实验及6项ES测试 |
| 首轮离线 | 1 failed，2.53秒；[raw](phase-7b-search-index-prototype-offline.txt)、[JSON](phase-7b-search-index-prototype-offline.json)。Oracle只接受ConnectionError，实际是ConnectionTimeout |
| 修正后离线 | 1 passed，2.18秒；[raw](phase-7b-search-index-prototype-offline-final.txt)、[JSON](phase-7b-search-index-prototype-offline-final.json)。接受两种不可达结果，不改原型故障传播；真实本地连接超时约2.002秒，无数据库fixture |

在线数据为12件人工商品／10件可见，没有SKU；全部结果按预先明确的slug集合核验，不从实现重新推导期望。

- `_analyze`：CJK对`无线鼠标`为无线、线鼠、鼠标；对`无线 鼠标`为无线、鼠标；standard为无、线、鼠、标；M1归一为m1。
- ES完整词／分开词／反向词为5／6／6；分类限定4、隐藏分类0、同字段AND1、相同文本同分2且ID排序、m1为1、单字标0、空词10。旧MySQL API对无线鼠标／无线 鼠标／鼠标 无线／标分别5／1／0／6。名称和描述等长互换的固定对评分6.5429316／5.8075876，只支持该对观察。
- 改名、商品下架、分类隐藏和新增商品后，候选10→9文档；关键词旧5／新3，改名后新词命中1。旧索引保留陈旧结果，证明未实现实时同步。
- 错误文档由测试替换导出结果注入，真实ES Bulk返回HTTP200但一项400 document_parsing_exception；合法项仍写入，失败候选count1、未进入ready；旧完整索引count10且hits不变。不是MySQL出现了错误字段，也不是模拟HTTP响应。
- 最终在线创建5个索引，逐个精确删除并经独立client核对全部404；没有通配符删除。独立Compose down --volumes完成，原MySQL继续运行，Redis/MQ未启动。

## 隔离事故与恢复

首轮guard临时修改真实connection.NAME后打开连接，pytest teardown误清开发库。binlog确认删除1个用户、1个Session、104条权限、26条内容类型。修正为只patch模块connection替身，正常来源另核对SELECT DATABASE；修正后测试及恢复演练期间开发快照不变。拥有者明确确认后，一事务恢复139条记录，提交后另开连接逐字段相同、Session能解析原用户。详见[事故记录](phase-7b-test-isolation-incident.md)；敏感行、原始binlog和恢复候选保存在Git外，未提交。

不能声称本轮“开发数据完全未触碰”。没有开发迁移、库存/订单/支付业务写入或实际.env修改；其他开发业务表没有该次删除行事件。4表自增计数未倒退，不把逐字段恢复说成所有元数据完全回到原时点。

## 静态检查与证据边界

官方客户端安装和pip check通过；Compose config、两模块编译、Django check为0、makemigrations --check --dry-run无变化，见[静态输出](phase-7b-search-index-prototype-static.txt)。122个本地链接存在，源码两处摘录与JSON核对通过；测试schema及可见测试连接均0，开发30表、用户/Session各1、原Session身份与认证hash相符，见[收尾JSON](phase-7b-search-index-prototype-closeout.json)。日志仅规范行尾空白，未删改失败或结果。没有重跑浏览器、Redis/MQ、负载或5,000商品性能矩阵。本片不证明通用中文搜索质量、ES速度优势或生产容量；refresh/count失败分片分支仅静态核对，没有多节点故障实验。

未解决：陈旧候选、MySQL回读入口、分页数量与补齐政策、自动同步/可靠变更版本、alias切换、节点副本/故障切换、并发重建协调与崩溃孤儿清理。创建成功但响应丢失可能留下未登记候选，close只清理确认创建的索引，本片独立服务已销毁。2秒是单请求等待边界，不是整个重建期限。

下一片只规划ES候选ID→MySQL当前可见性与摘要的最小读入口，先确定陈旧项/分页/故障合同，不自动横向扩展MQ搜索任务。
