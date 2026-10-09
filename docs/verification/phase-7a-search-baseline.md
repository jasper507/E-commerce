# Phase 7A：商品关键词搜索与 MySQL 查询基线

日期：2026-10-09。Supporting / Pair；配套[学习指南](../learning/phase-7a-product-search-baseline-study-guide.md)。本片位于订单索引、Lua准入之后，建立已确认ES计划的可比较基线；并非重复落地订单索引。

能力目标：解释搜索业务规则、过滤与分页顺序，以及为什么要捕获完整COUNT/列表SQL再评估搜索引擎。停止条件为最小搜索闭环、可见性/摘要/分页合同及一个规模的三个实际查询计划，不扩大索引候选或负载矩阵。

## 当前规则与改动

拥有者明确选择**商品名称＋描述**。公开GET `/api/catalog/products/?q=无线鼠标` 增加可选`q`，去首尾空白、最多80字符，空缺/空白仍为原商品浏览。完整关键词包含匹配，名称OR描述；分类、商品上架及分类有效性与它取AND。没有中文分词、同义词、纠错或相关性排名。

保留固定20条、`name,id`排序、原响应字段，缺货/无SKU仍可展示；最低价只算有效SKU，有货只是布尔提示。ORM处理参数及LIKE转义，`%`/`_`/反斜线作为文字。具体字符比较沿用实际MySQL collation，不自行模拟全部Unicode比较规则。

前端增加搜索表单与`q`传递；关键词保存到URL，提交搜索/切换分类重置第一页，翻页/分类切换保留关键词，继续取消旧请求。空结果取消分类按钮改成“查看全部分类”，它会保留关键词。前端构建通过、路由源码已核对；**没有本轮真实或模拟浏览器验收**。

修改为catalog Serializer/View、两份搜索测试、前端catalog API/ProductListView及本片材料/进度记录。没有新增依赖、数据库索引/迁移、ES服务、部署或实际`.env`修改；订单/库存/支付、Lua和详情缓存实现未改。保留原Phase6E指南用户修改。

## 实际执行

Windows / Python3.11.9 / Django5.2.17 / DRF3.18.1，真实Docker MySQL8.4.10；没有启动Redis或ES。普通目标与回归使用pytest隔离MySQL；基线显式要求DJANGO_ENV=test、test_project_one且默认库不同，连接再次核对MySQL、autocommit及隔离库名。报告绝对路径存在则拒绝覆盖。

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| 新功能＋显式查询实验 | **9 passed，12.84秒** | [目标raw](phase-7a-search-baseline-target.raw.txt) |
| 新功能＋既有公开目录/模型/Admin回归，实验默认关闭 | **24 passed / 6 subtests passed / 1 skipped，10.99秒** | [回归raw](phase-7a-search-baseline-regression.raw.txt) |
| 最终前端type-check＋构建 | 通过 | [构建raw](phase-7a-search-baseline-frontend.raw.txt) |
| Django check / makemigrations --check --dry-run | 0问题 / No changes detected | 系统与迁移检查，不是运行浏览器或应用迁移 |
| 源码、摘录、链接、scoped diff及报告 | 静态核对 | 不算性能/业务实测 |

功能覆盖：名称/描述独立命中、分类交集、隐藏商品及分类不泄漏、空白退回原浏览、无匹配仅COUNT、首尾trim、ASCII大小写例子、字面通配符/SQL注入文字、81字符及NUL400且0商品SQL、80字符有效、同名25件按ID分成20/5且分页链接保留q/category。完整关键词“鼠标 无线”不命中包含“无线鼠标”的商品：这是本片合同的语义限制。

可复现命令（pytest会创建/写入/销毁隔离测试库，不与其他pytest进程共享运行）：

```powershell
$env:DJANGO_ENV='test'
$env:PROJECT_ONE_SEARCH_QUERY_BASELINE='1'
$env:PROJECT_ONE_SEARCH_BASELINE_REPORT='C:\project-one-ecommerce\docs\verification\phase-7a-search-baseline-reproduced.json'
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests/test_product_search.py backend/apps/catalog/tests/test_search_query_baseline.py -q -s
# 后续回归先关闭显式实验；上面报告使用新文件名，拒绝覆盖已有证据。
Remove-Item Env:PROJECT_ONE_SEARCH_QUERY_BASELINE
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests/test_public_api.py backend/apps/catalog/tests/test_models.py backend/apps/catalog/tests/test_admin.py backend/apps/catalog/tests/test_product_search.py backend/apps/catalog/tests/test_search_query_baseline.py -q -s
.\.venv\Scripts\python.exe backend/manage.py check
.\.venv\Scripts\python.exe backend/manage.py makemigrations --check --dry-run
npm.cmd --prefix frontend run build
```

首次真实实验报告为`phase-7a-search-baseline.json`，命令环境指向此新文件，当时不存在。复现必须另选新路径，不修改旧报告。

## 完整查询观察

合成5000件可见商品＋1件下架商品＋1件隐藏分类商品；各有效/隐藏SKU一个，共10004 SKU/库存。名称为统一前缀＋四位数字，描述每10件一件包含关键词；明确索引集合/切片作为独立Oracle，不重复ORM过滤来计算答案。有效SKU129元，隐藏SKU1元且有货；后者不能污染最低价和有货提示。

对真实API捕获全部SQL及参数，保存四表SHOW INDEX/SHOW FULL COLUMNS，ANALYZE TABLE更新隔离统计，再对捕获的每条SELECT执行EXPLAIN和EXPLAIN ANALYZE。没有强制索引或临时候选。[完整JSON](phase-7a-search-baseline.json)保留每条SQL、参数、响应与计划；name/description实际为`utf8mb4_0900_ai_ci`。

| 请求 | 匹配总数 / 返回数 | 实际SQL | 商品候选索引步骤 / 后续处理 |
| --- | --- | --- | --- |
| `q=无线鼠标` | 500 / 20 | COUNT＋列表：2 | 分类FK查找约2500 rows×2 loops，过滤得到500；列表聚合500件后排序取20 |
| 无匹配关键词 | 0 / 0 | COUNT：1 | 仍检查两有效分类下约5000候选商品；没有第二条列表查询 |
| `q=无线鼠标&category=search-a` | 250 / 20 | COUNT＋列表：2 | 分类唯一slug定位；商品FK索引步骤2501 rows×1 loop，过滤250后聚合/排序 |

EXPLAIN ANALYZE中的rows是每loop平均值且打印会舍入；不能把2500×2称为精确访问5000条。两有效分类下真实fixture有5000可见＋1下架候选，隐藏分类在分类过滤中排除。

COUNT是聚合查询派生子查询上的统计，不是独立的简单Product.count：仍带SKU联表、分组，以及in_stock相关表达式。分类限定的COUNT实际计划显示投影Exists子查询250 loops；列表的LIMIT20也在聚合、排序之后。分页限制响应大小，并不保证只检查20件商品。不能凭某份计划未列出子查询就断言它没有执行。

此处用到的是分类外键等现有索引，关键词仍为`LIKE '%关键词%'`过滤。MySQL文档说明以通配符开头的LIKE不满足普通B-tree前缀范围条件；这不等于该查询任何索引都不使用，也不证明加某个索引必败。[官方范围优化说明](https://dev.mysql.com/doc/refman/8.4/en/range-optimization.html)。本片未增加或比较名称/FULLTEXT索引。

## 证据边界与收束

每条计划只取一次本地合成数据观察，附带的数据库耗时不是HTTP总时延或负载基准；没有慢查询阈值、旧/新性能对照、吞吐或生产容量结论，也没有ES对照。实际说明的是搜索语义有限、候选过滤及原商品摘要聚合有成本；不能宣传“已经优化慢SQL”或“ES必然更快”。

前后测试schema0、可见测试连接0，默认开发schema仍30表；没有开发migrate或业务写入，计数不是逐行开发数据比对。构建会写入忽略的frontend/dist和构建缓存；没有提交这些产物。基线没有开启Redis/ES/后台Worker，也未重跑订单索引、Lua、支付MQ矩阵。

本片达到停止条件。下一片建议隔离ES商品搜索原型：先定义MySQL商品到可重建搜索文档的字段与排序，验证中文分析和少量相关性例子；明确上架可见性、索引延迟、商品事实回读与重建边界。同步可靠性和故障回退分片处理，不能把当前计划写成已实现；个人独立理解、真实浏览器和开发运行仍待另行验收。
