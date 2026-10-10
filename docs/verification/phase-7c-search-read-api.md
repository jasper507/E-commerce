# Phase 7C：ES候选与MySQL当前商品摘要

日期：2026-10-10。应用基线`04df68f`。Supporting/Pair实现＋Depth分页与故障解释；[学习指南](../learning/phase-7c-search-read-api-study-guide.md)。状态：独立隔离读入口验证完成，默认关闭；旧列表/前端未切换。

## 用户确认的合同与最小正确预期

- 独立公开只读`GET /api/catalog/search/`；旧`products/`、详情与前端保持原规则。新开关默认关闭，本片只接隔离test来源，不建立开发索引。
- q必填非空，去首尾空白、最长80；category可选、最长80；page默认1，整数1–500。非法参数400，写方法405；关闭入口503，参数校验先于客户端及商品SQL。
- ES按相关性降序/product_id升序每页最多20个候选。返回`candidate_count`、整数或null的`next_candidate_page`/`previous_candidate_page`、`results`；候选数不冒充当前可见商品数。
- MySQL重新检查商品/分类有效性以及当前分类归属，读取有效SKU最低价和有货布尔提示，按候选ID顺序返回原公开摘要。不再套关键词LIKE、不凑满页。短页或空页仍可有下一候选页；无命中第一页200，超过候选尾404；超过10,000候选400要求缩小范围。
- ES不可达、索引缺失、来源/完成标记不符、不完整搜索结果返回503；不回退MySQL搜索，也不伪装成零结果。旧浏览保持可用。
- 新读入口没有索引写入或清理的代码路径；配置只能精确随机候选名，校验本地9201/测试集群/8.19族、实际test_project_one、自动提交且不在事务内。构建器只有逐项写入、refresh与count通过后才写`_meta`完成标记。

## 隔离与证据边界

所有MySQL pytest由root串行；fixture前后实际`SELECT DATABASE()`。拒绝配置测试只替换模块连接引用，禁止改真实connection.NAME。本轮前后以只读整表摘要比较开发30表，只公开计数与相等结论；摘要位于Git外，没有真实行值、密码或认证hash。

精确配置与`_meta`证明约定来源和重建门槛，不是签名或内容完整性校验。只记录库名不能识别同名测试库下一生命周期，测试每片必须新建自己的索引并在销毁测试库前精确清理。旧索引不得用于重新创建的测试库。

无自动同步、alias/PIT、索引不可变强制、跨请求一致快照、全量可见count、补页或开发/部署启用。改名、改分类和新增会产生陈旧命中或遗漏；回读解决返回事实与可见性，不能修复召回。两秒为每次ES请求超时，info/mapping/search串行，不能声称整个HTTP请求两秒完成。当前每请求新建并关闭client，尚无连接复用、搜索限流或生产容量证据。

## 本轮实际检查

环境为MySQL8.4.10、ES8.19.23、Python客户端8.19.3；沿用7B独立Compose，未新增依赖。所有pytest串行，DJANGO_ENV=test、CATALOG_CACHE_ENABLED=false、PAYMENT_EVENTS_ASYNC=false。在线显式PROJECT_ONE_TEST_ELASTICSEARCH_URL=http://127.0.0.1:9201；报告PROJECT_ONE_SEARCH_READ_REPORT为未存在的绝对路径，离线另加PROJECT_ONE_TEST_ES_OFFLINE=1并先销毁隔离服务。

| 检查 | 实际结果与证据 |
| --- | --- |
| 首轮新API＋共享原型回归 | 13 passed／2 skipped，14.84秒；[raw](phase-7c-search-read-online.txt)、[API JSON](phase-7c-search-read-online.json)、[原型JSON](phase-7c-search-prototype-regression.json)。含新API8项和旧原型5项，两个离线场景默认跳过 |
| 修正记录后的新API在线 | 8 passed／1 skipped，12.37秒；[raw](phase-7c-search-read-online-final.txt)、[JSON](phase-7c-search-read-online-final.json)。其中5项真实ES/MySQL、3项无数据库单元/注入；不是8次真实依赖故障 |
| 首轮真实离线 | 1 passed／8 deselected，12.52秒；[raw](phase-7c-search-read-offline.txt)、[JSON](phase-7c-search-read-offline.json) |
| 修正记录后的真实离线 | 1 passed／8 deselected，12.22秒；[raw](phase-7c-search-read-offline-final.txt)、[JSON](phase-7c-search-read-offline-final.json)。真实ConnectionTimeout约2.017秒，搜索503、1条来源SQL、0摘要SQL；旧浏览200 |
| 原目录/搜索/模型/Admin | 24 passed／6 subtests，10.47秒；[raw](phase-7c-search-read-catalog-regression.txt)。未重跑旧5000商品计划或其他性能矩阵 |
| 静态 | 编译、check为0、makemigrations无变化、pip check与独立Compose config通过；[raw](phase-7c-search-read-static.txt) |

执行命令主体：在线首先pytest test_search_read_api.py与test_search_index_prototype.py，修正记录后只重跑新文件；停服务后pytest新文件`-k real_offline`；目录回归为test_public_api.py、test_product_search.py、test_models.py、test_admin.py。参数均`-q`，实验证据另加`-s`。

首轮断言确认正常/离线为2/1条SQL，但JSON在后续请求清空Django查询日志后才读取CaptureQueriesContext，错误记录为0。已立即物化`list(queries)`并补跑新API在线与离线；上述最终JSON才是SQL数量依据。原始首轮未覆盖或修改，不把报告错误当业务失败或把首轮0当作数据库成本。读取汇总的Windows控制台编码错误仅以`-X utf8`重读修正，没有因此重跑服务实验。

### 观察值与独立预期

- 9件人工商品中7件构建时可见。受控名称/描述对返回name-hit、description-hit，即使后者ID更早仍保持ES评分顺序；旧MySQL反序关键词命中0、新API命中2。摘要保持原5字段，隐藏SKU的1元价格和99库存不参与，有效SKU最低价129、任一有效SKU有货为true；未暴露ID、score或精确库存。正常请求实际2条SQL（来源检查＋当前摘要）。
- 建索引后在测试MySQL下架商品、隐藏分类、迁分类、改名/价/库存并新增商品：旧词仍有5候选，但当前返回3；指定旧分类4候选、当前只返回2。改名商品展示新款音箱/89元/有货；新词、新分类与新增商品仍漏召回。完整候选与响应见最终在线JSON，不称实现实时同步。
- 21个同文案候选初页20，隐藏第1件后19；隐藏前20件后初页空但next=2，第二页仍返回candidate-20。候选总数始终21，不补拉、不扫描全部候选。无命中第一页200；合法窗口内越尾404。
- 自建真实索引的未完成/错来源/错误标记类型均503；恢复正确标记正常。另一个不存在的精确名字503且未被创建。只有测试修改自己的元数据；API读路径没有写ES。
- 默认关、11组非法参数和POST分别503/400/405，reader构造0次；这些无数据库fixture。超10,000候选使用注入reader证明400分支；超时响应、失败分片、近似总数、空ID数组、布尔ID由替身client/模块连接注入，均503，不能称真实多节点或网络故障。

### 隔离与收尾

[运行前计数](phase-7c-search-read-preflight.txt)、[首轮在线后](phase-7c-search-read-after-online.txt)与[最终收尾](phase-7c-search-read-closeout.json)确认：开发30表整表行摘要相同，用户/Session各1、权限104、内容类型26、迁移记录35；测试schema及可见测试连接均0。比较只读、不包含行值；没有本轮开发库写入、迁移或实际.env修改。三个在线报告共15个确认创建的索引，逐个精确删除、独立client核对无剩余；最后独立Compose down --volumes完成，只有原MySQL容器运行，Redis/MQ未启动。

修改范围为catalog候选读入口/共用摘要查询/查询参数与路由、测试-only默认关闭设置、构建完成元数据和双文档导航。库存/订单/支付/Lua逻辑、模型/迁移、详情缓存、前端与部署Compose未改；原6E指南用户改动保留且不纳入提交。没有浏览器、开发服务启用、生产容量、速度提升或个人独立掌握证据。

下一片建议只验证手动重建后安全切换读入口与失败保留旧索引，不直接加入自动同步、MQ任务或生产部署。

文档核对108个本地链接、1处摘录与源码一致（省略注释）、AST与最终JSON；[检查结果](phase-7c-search-read-document-checks.json)。检查脚本先修正省略注释的摘录比较及fixture数/实际索引数的口径后通过，未因此重跑应用。raw只规范行尾空白与换行，未删改场景和结果；Git diff检查通过。两份文档仍不表示个人已掌握。
