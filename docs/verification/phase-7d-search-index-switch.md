# Phase 7D：手动重建与安全切换搜索索引

日期：2026-10-10。应用基线`14b59f3`。Supporting/Pair实现＋Depth发布结果和在途读取解释；[学习指南](../learning/phase-7d-search-index-switch-study-guide.md)。状态：手动切换及真实隔离验证完成，仍默认关闭、仅测试来源。

## 本片合同与最小正确预期

- 用户授权手动重建成功后切换、构建失败保留旧入口。Alias（固定读取名称）只指向一个完整候选索引；每次重建创建新索引，Bulk逐项、refresh/count、完成来源标记通过才允许发布。
- 单操作者、同一构建会话和同一隔离测试库生命周期；本片不增加多进程锁、数据代次或自动同步。expected_index必须是本会话确认完成的索引或首次发布的None；实际别名目标不符则写前拒绝。
- 一次aliases更新包含精确remove旧目标（must_exist=True）和add新目标，无通配符或remove_index；构建/发布失败不调用builder.close，不自动删旧或新索引。
- 正常/异常更新后均回读。state=switched表示回读确认新目标，unchanged表示确认仍为预期旧目标，unknown表示无法确认或出现其它目标。acknowledged、errors与action_results另行记录；超时/ack=False不自动等于旧入口保留，不盲重发或反向切换。
- 读API可配置精确测试别名或保留7C精确索引，两者只填一个。先核对集群，再解析一次唯一候选并查该精确索引，校验完成来源标记；别名过滤/路由/多目标拒绝，避免精确查询绕过别名规则。
- 旧成功索引在切换后保留，所以已解析旧目标的请求可继续读取；MySQL摘要仍按请求读取时的当前事实生成。不是跨页PIT、一致数据库/ES快照或生产零停机保证。
- 仍默认关闭、只允许实际test_project_one、自动提交且无外层事务、本地9201/指定集群/8.19族；不建立开发索引，不修改原前端、旧浏览、交易逻辑、模型迁移或部署。

## 最小证据与隔离

目标仅为成功切换、真实Bulk失败旧入口不动、已解析旧目标的在途读、真实更新后响应丢失注入的回读/未知结果，以及不完整候选/异常指针拒绝。证明这些边界后收片，不扩展负载、同步或多节点故障矩阵。

root串行运行MySQL pytest；fixture写入前后实际SELECT DATABASE，线程独立连接并关闭，不改变真实NAME。随机候选/别名同测试库生命周期，仅精确清理自己确认创建的索引并独立核验别名不存在。运行前后只读比较开发30表整表摘要，摘要保存在Git外，仅公开计数与相同结论。

## 本轮执行

沿用MySQL8.4.10、ES8.19.23、官方客户端8.19.3与7B独立Compose，无新依赖。root串行运行，DJANGO_ENV=test、CATALOG_CACHE_ENABLED=false、PAYMENT_EVENTS_ASYNC=false；在线显式PROJECT_ONE_TEST_ELASTICSEARCH_URL=http://127.0.0.1:9201，报告PROJECT_ONE_SEARCH_SWITCH_REPORT为未存在的绝对路径。读接口和原型回归各有独立报告变量；离线加PROJECT_ONE_TEST_ES_OFFLINE=1并先销毁服务。

| 检查 | 实际结果及原始证据 |
| --- | --- |
| 新切换＋7C/7B回归 | 18 passed／2 skipped，19.82秒；[raw](phase-7d-search-index-switch-online.txt)、[新5项JSON](phase-7d-search-index-switch-online.json)、[读接口8项JSON](phase-7d-search-read-regression.json)、[原型5项JSON](phase-7d-search-prototype-regression.json)。两个离线测试默认跳过 |
| 独立真实离线读接口回归 | 1 passed／8 deselected，10.54秒；[raw](phase-7d-search-read-offline-regression.txt)、[JSON](phase-7d-search-read-offline-regression.json)。ConnectionTimeout约2.007秒，503、1条来源SQL/0摘要SQL，旧浏览200 |
| 原目录/搜索/模型/Admin | 24 passed／6 subtests，8.67秒；[raw](phase-7d-search-index-switch-catalog-regression.txt) |
| 静态 | 编译、check为0、无迁移漂移、pip check与Compose config通过；[raw](phase-7d-search-index-switch-static.txt) |

命令主体：pytest test_search_index_switch.py与test_search_read_api.py/test_search_index_prototype.py `-q -s`；服务down后pytest读接口文件`-k real_offline -q -s`；原目录回归为test_public_api.py/test_product_search.py/test_models.py/test_admin.py `-q`。未重跑Redis、MQ、浏览器、性能矩阵或旧数据库计划。

## 本轮观察与预先明确的Oracle

1. **成功切换：** 旧来源2件；旧款键盘改为新款音箱并新增同名商品，新候选3件。切换前新词0，之后2件，人工预期changing-product、added-product按同分ID返回。独立client核对别名唯一指新索引；旧索引仍有2文档且保留旧词候选。7C精确索引方式兼容，摘要名称仍取MySQL新款音箱。
2. **真实Bulk失败：** 测试替换导出内容，合法文档与错误long字段发给真实ES；HTTP200但一项400。候选仅1文档、完成标记False、未入ready。alias更新0次，显式发布也拒绝；独立别名仍旧目标，旧词API200。没有自动删旧索引或把部分成功伪报完成。
3. **在途读：** 真实API请求解析旧目标后以Event暂停，主线程切新，再释放读线程。次序resolved_old → switched_new → searched_old_exact → old_response_complete；旧请求200、1个旧候选但摘要显示MySQL新名称。后续新词2候选、旧词0。线程前后核对测试库并关闭连接，退出前禁止清理。这是一个有序交错，不是全部并发交错或负载证明。
4. **结果不确定：** 两轮均完成真实alias POST后由包装器抛ConnectionTimeout。第一轮回读新目标，state=switched；第二轮再注入回读异常，state=unknown/current_index=None。独立control均确认实际新目标，两轮更新各1次，旧/新候选全保留。异常为测试注入，不是实际丢包、主节点故障或多节点ack超时。
5. **拒绝规则：** 完成候选元数据改为False后禁止发布；已切新目标后以旧expected发布，写前冲突拒绝。只修改自己的别名为多目标、filter或search_routing，三次API503；恢复单目标空配置后200。没有绕过别名规则返回商品。

acknowledged=False、HTTP200逐action失败/坏响应及state=unchanged返回分支仅静态核对，本轮没有真实主节点/多节点故障实验。构建失败旧入口保留与响应丢失回读已有真实证据，停止于这些业务解释，不为覆盖每个分支扩大实验。

## 安全、代价与收尾

[运行前](phase-7d-search-index-switch-preflight.txt)、[在线后](phase-7d-search-index-switch-after-online.txt)与[最终收尾](phase-7d-search-index-switch-closeout.json)确认开发30表整表行摘要一致；用户/Session各1、权限104、内容类型26、迁移35，测试schema/可见连接0。摘要Git外，不提交真实行值、凭据或认证hash。21个确认创建的候选（本片11、回归10）逐个精确删除、独立核对0剩余；5个本片别名均404。独立Compose down --volumes完成，仅原MySQL运行。

修改alias发布/解析、读适配器、共用实际来源/完成标记检查、默认空测试别名设置和双文档导航。无开发数据/索引写入、开发迁移或实际.env修改；库存订单支付Lua、详情缓存、原前端/浏览契约、模型迁移和部署Compose未改。原6E指南用户改动不纳入提交。

别名路径多一次GET；串行info/alias/mapping/search各有2秒请求超时，不是整个HTTP2秒。全量来源物化、新旧索引的内存/空间和人工清理成本仍在。旧索引保留支撑在途读，但没有自动安全回收。发布器只认可同一builder会话的完成集合，新进程不能直接沿用旧ready继续发布；本片无独立管理命令、跨进程控制台或开发启用。

按[8.19官方alias语义](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/indices-aliases.html)，remove.must_exist=True拒绝预期旧关系不存在时的整组变更；更新未完全ack仍可能生效，结果以实际回读分类。它不是多操作者CAS、跨存储事务或跨页快照；同测试库生命周期/单操作者约束仍在，源变化不会自动更新索引。

下一步先整理个人理解并评估项目演示、环境启用和收尾缺口，不自动增加同步、分布式发布锁、恢复机器人或MQ搜索任务。

文档核对本地链接、两处摘录AST与实际源码节点一致、报告和清理总数；[检查结果](phase-7d-search-index-switch-document-checks.json)。raw仅规范换行和行尾空白，没有删改场景/结果；Git diff检查通过。独立只读终审未发现P1/P2或证据不一致，不把静态审查当成额外实测。
