# Project One — Project Charter v1.4

## 0. 文档状态

- 项目名称：Project One — Django E-commerce
- 当前阶段：Phase 6C-5 单活动 SKU 条件 UPDATE 已正式落地；真实超时整单回滚、原 Key 重试及相关回归通过，HTTP/前端待接入
- 文档版本：v1.4
- 路线图进度更新：2026-10-08
- Charter 状态：Final（项目范围与首轮设计基线已确认）
- 技术定位：Backend-heavy Django E-commerce System
- 主要用途：中国 Backend / Software Engineering Internship 面试主力项目
- 当前状态：已有 Mock Payment 业务闭环、持久重试预算、真实 MQ 基线、有界后台重试及人工发布补漏。Phase 5C-4 增加默认60秒的持久处理租约与显式 `--recover-stale`：逐条短事务锁内核对原领取版本，跳过正在持锁的业务事务，修复后由原恢复/补投入口继续处理；不重置次数。真实隔离 Worker 的领取后、业务提交前、支付/退款提交后但 ACK 前中断已验证，见 `docs/verification/phase-5c4-payment-worker-recovery.md`。Phase 5D增加两种Order锁顺序×三种未确认Attempt状态的六场景，真实观察记录锁等待，验证成功后只一次核销，见 `docs/verification/phase-5d-payment-expiration-concurrency.md`；业务实现未改。默认同步，项目拥有者确认本期保留人工兜底，没有周期自动补漏、心跳或全自动接管。Redis当前进度见 `CONTEXT.md` 第32节。前序成果已于2026-10-05提交并推送main（`2dacc9a`），C4/5D及Phase 6A增量已分组保存；Phase 6A真实隔离MySQL目标10项与相关回归151项通过；Phase 6B新增资格生命周期目标11项、promotions/ordering/payments回归171项通过；Phase 6C-1补当前NOWAIT完整秒杀双顾客竞争；6C-2同完整事务候选正确性通过；6C-3完成8worker/24顾客、两库存/预算/策略各3轮共24批次的时延/重试/连接成本，全部有效且默认未换策略，见docs/verification/phase-6c3-flash-sale-inventory-load.md；Git整合状态以提交历史为准。生成新代码前先commit/push的持续授权见PROJECT_INSTRUCTIONS.md；未执行开发库恢复、迁移或浏览器验收。

最新状态（2026-10-08）：单活动 SKU 条件 UPDATE 已在 Phase 6C-5 正式落地，见 [ADR 0001](docs/adr/0001-flash-sale-inventory-conditional-update.md) 和 [验证记录](docs/verification/phase-6c5-flash-sale-inventory-failures.md)。拥有者选择沿用现有会话上限，本机观察 50 秒；库存 UPDATE 的 1205/1213 返回繁忙并退出整单事务，未知错误上抛，无自动重试。目标9项/198子场景、相关回归183项/403子场景通过；普通多 SKU、Redis Lua 范围及 HTTP 合同未变，HTTP 顾客等待预算仍待确定。上段及阶段条目中“默认未换策略”描述各片当时状态。

本文件是项目范围、目标和完成标准的当前可信来源。发生重大变更时，应先更新本文件或新增 ADR，再开始实现。

本 Charter 的 `Final` 表示当前项目范围和设计方向已经确认，可以分切片进入实现；它不表示所有实现都已经完成或通过测试。v1.1 将执行策略收窄为 Interview Core MVP + 三条深度主线，并加入 CRUD Fast Track；v1.2 按项目拥有者决定将 RabbitMQ + Celery 支付异步链路升级为必做，第三方 Sandbox 本期 No-Go。标为“实验候选”的技术方案仍必须经过真实基线、失败复现和比较后，才能在 ADR 中成为最终实现结论。后续重大变化按照第 19 节记录。

---

## 1. Project Vision（项目愿景）

Project One 是一个以单商家电商为业务载体的后端系统工程实验场。

它不追求成为真实运营的淘宝或京东，也不追求功能数量最多。它追求：

1. 有一条完整、可运行的电商业务闭环。
2. 在库存并发、缓存正确性、支付幂等三个方向形成可复现的技术深度。
3. 使用真实代码、自动测试、故障实验和性能数据证明设计有效。
4. 让项目拥有者能够脱离 AI 解释、修改和调试关键系统。
5. 最终成为面向中国后端实习面试的主力项目。

### 核心原则

> 商业真实性可以简化，技术真实性不能降低。

允许：

- 使用合成商品与测试用户；
- 使用模拟物流；
- 使用后端 Mock 支付完成核心版；第三方 Sandbox/Test Mode 本期 No-Go，只有项目拥有者以后重新决定才进入；
- 使用合成并发流量；
- 用单机或少量容器模拟分布式场景。

不允许：

- 虚构用户数量、生产流量或性能结果；
- 没有运行实验却声称问题已解决；
- 只添加技术依赖，不解释它解决的问题；
- 无法解释 AI 生成的核心业务代码；
- 用全部 Mock 掩盖数据库、Redis、MQ 的真实行为。

---

## 2. Project Priorities（优先级）

优先级从高到低：

1. 面试可展示的后端技术深度。
2. 正确性、可验证性和故障处理。
3. 对核心代码的个人理解和独立修改能力。
4. 完整的电商业务闭环。
5. 可部署、可复现和可演示。
6. 前端功能完整。
7. 视觉精细度与真实商业运营细节。

出现冲突时，优先保护前五项。

最终价值优先级与实现顺序不同。实现时先完成最小可运行闭环，形成 MySQL + Mock Payment 基线；再稳定复现问题并展开三条旗舰主线。RabbitMQ + Celery 在 Phase 5 为支付事件异步处理与故障恢复必做，先保留同步/恢复扫描基线，再接入队列并验证失败路径；其他条件技术根据真实问题、证据需求和工时审计结果引入。不得先堆叠 Redis、MQ 或 Elasticsearch，再回头补业务闭环。

### Interview Core 学习分层

项目不再要求所有模块逐行、同深度学习：

- `Depth`：Order、库存事务与并发、Redis 缓存正确性、支付状态与幂等、架构取舍和证据解释。必须深入理解、参与规则与测试 Oracle，并能独立修改和调试。
- `Supporting`：Accounts、Catalog、Cart、Address、权限与前后端请求链。需要理解输入、输出、所有权、关键约束和在闭环中的位置，可使用 Pair Mode。
- `Boilerplate`：普通 CRUD、Admin、重复 Serializer/View/Form/Table、fixture、HTML/CSS 与构建样板。行为确认后走 CRUD Fast Track，由 AI 直接交付并验证，用户完成关键 Diff Review。

CRUD Fast Track 不等于无测试或黑盒：至少保留权限/数据归属、关键数据库约束、一个正常例子和一个重要失败例子；金额、库存、状态机、幂等与并发规则不得归类为普通 CRUD。

---

## 3. Business Scope（业务范围）

### 3.1 第一版业务模型

本节宏观范围已在 Day 0 Step 1 由项目拥有者确认。

- 商业模式：Single-merchant B2C（单商家面向消费者）
- 商品类型：普通、可重复计数的数码配件类实物商品；不处理整机序列号、设备激活、维修和复杂保修
- 仓库模型：单仓库；核心版不建立只有一条记录的显式 `Warehouse` 表，使用 `SKU 1:1 Inventory`
- 地区：中国大陆场景
- 币种与金额：仅 CNY；Django/MySQL 使用 `DecimalField(max_digits=12, decimal_places=2)`，Python 使用 `Decimal`，API 使用两位小数字符串
- 物流：模拟发货与物流状态，不连接真实物流公司
- 支付：Mock Payment Gateway 与 RabbitMQ + Celery 支付事件异步链路为核心必做；第三方 Sandbox 本期 No-Go；不接 Production，不处理真钱
- 前端：一个 Vue 3 + TypeScript/Vite 工程中的顾客端路由与 Layout
- 管理端：Interview Core 使用 Django Admin 承担普通商品、SKU、库存和订单运营 CRUD；只在能够直接展示库存、缓存或支付主线时增加极简 Vue 管理/实验页面
- 开发早期及 Interview Core：允许持续使用 Django Admin 验证和维护后端数据

顾客端 Vue 只承担最小业务闭环和必要演示；完整自定义 Vue Admin 不再是 Interview Core 的完成条件。任何管理权限都必须由 Django 后端验证，不能依赖前端隐藏路由或按钮。

### 3.2 用户角色

#### Guest（游客）

- 浏览分类、商品和 SKU；
- 搜索商品；
- 不创建服务端购物车、收货地址、订单或支付记录；
- 第一版不实现游客购物车或游客结账。

#### Customer（顾客）

- 注册、登录和维护个人资料；
- 管理包含收货人姓名、收货手机号和地址信息的收货地址；
- 浏览商品并选择 SKU；
- 管理购物车；
- 创建和查询自己的订单；
- 发起 Mock 测试支付，查看后端确认的结果；本期不提供第三方 Sandbox 支付；
- 查看订单状态；
- Coupon、Review 与 Favorite 属于闭环完成后的可选业务扩展，不属于 Interview Core。

#### Admin（管理员，包含 Operator 运营职责）

- 管理分类、Product、SKU 和库存；
- 上架、下架和调整商品；
- 查看与处理订单；
- 模拟发货；
- 若对应可选阶段实际进入，再管理优惠券和秒杀活动；
- 查看支付事件、已引入的异步任务结果和基础运行指标。

单商家模型中，Admin 代表平台卖家侧并承担 Operator 的日常运营职责。第一版不单独设置 Seller、仓库员、客服或财务等顶层角色。Customer 使用普通用户身份；Admin 使用 Django 的 `is_staff`、Groups 和 Permissions 表达管理权限，不再维护一套重复的角色字段；`is_superuser` 只用于系统维护，不作为日常业务角色。

### 3.3 登录与收货手机号边界

- 第一版 Customer 与 Admin 都使用唯一 `Username + Password` 登录；
- 项目骨架开始即使用 `Custom User(AbstractUser)`，避免迁移产生后再更换用户模型；
- Username 去除首尾空格后统一转为小写，长度为 3–30，只允许小写字母、数字和下划线，并必须以小写字母开头；数据库中保持唯一；
- Username 只承担登录标识；以后如需昵称，使用独立的 `display_name`，不改变登录身份；
- 密码必须交给 Django 的密码哈希机制处理，任何代码、日志或数据库字段都不得保存明文密码；
- 第一版 Web 登录使用 Session Cookie + CSRF；部署目标为 Vue 与 Django 经 Nginx 同源访问，不使用 localStorage 长期保存 JWT；
- 收货手机号只是 `Address` 和订单地址快照中的联系字段，不等同于登录身份；
- Vue 前端与 Django 后端都进行基础格式校验，后端校验是最终数据边界；
- 开发、测试和演示只使用模拟数据，不发送短信验证码；
- 第一版不验证号码归属、号码是否真实存在或用户是否实际控制该号码；
- 第一版不进行短信验证码或邮箱验证。

### 3.4 核心业务闭环

最小可运行闭环：

```text
Guest 浏览商品
→ 注册/登录成为 Customer
→ 选择 Product 下的具体 SKU
→ 加入 Cart
→ 选择 Address
→ 提交下单请求
→ 服务端验证 Idempotency-Key，并重新计算金额
→ 在同一 MySQL 事务中保存 Order、OrderItem/Address 快照和 Inventory Reservation
→ 原子地将 available_stock 转为 reserved_stock
→ 在同一事务末尾移除相应 CartItem；任一步失败则整体回滚并保留 Cart
→ 提交数据库事务
→ 创建 Payment Attempt
→ Mock Payment
→ 验证并处理 Payment Event
→ 更新 PaymentAttempt 与 Order 状态，并核销预占库存
→ Admin 模拟发货
→ Customer 确认收货
→ Order 完成
```

最小主流程稳定后加入的简单取消分支：

```text
PENDING_PAYMENT 主动取消或超时
→ CANCELED 或 EXPIRED
→ 已预占库存最多释放一次，不退款

PAID 且尚未发货时主动取消
→ 原子地恢复库存并创建一次 Refund Simulation
→ CANCELED

SHIPPED / COMPLETED
→ 核心版拒绝取消，避免进入复杂售后
```

对 `CANCELED` 或 `EXPIRED` 的重复请求必须幂等返回，不能再次释放库存或退款。若订单终态后收到迟到的支付成功事件，保留终态、记录异常支付并创建幂等的 Refund Simulation，不能重新打开订单。Coupon、Review、Favorite 和秒杀等能力在最小闭环稳定后增量加入，不是首次闭环的完成门槛。RabbitMQ + Celery 支付异步链路在 Phase 5 必做，不阻塞首次同步 MVP 闭环；第三方 Sandbox 本期不进入。

---

## 4. Non-goals（明确不做）

第一版及核心深度版不做：

- 多商家入驻；
- 店铺体系、佣金、分账与商家结算；
- 多仓库和跨仓调拨；
- 多币种；
- 游客购物车和游客结账；
- 真实物流公司接口；
- 真实或复杂售后：实物退货、换货、商品验收、售后工单、纠纷处理和复杂部分退款；
- 真实退款；但保留简单订单取消、库存释放和 Refund Simulation，用于验证状态与幂等正确性；
- 短信验证码、邮箱验证、手机号归属/控制权验证和真实用户手机号测试数据；
- 整机序列号、设备激活、维修和复杂保修；
- 推荐系统；
- 社交 Feed；
- 原生移动 App；
- 完整自定义 Vue 管理后台；Interview Core 使用 Django Admin，只有深度主线需要时才增加极简实验入口；
- Coupon、Favorite、Review 和复杂搜索 UI 不属于 Interview Core 的完成门槛；
- 微服务拆分；
- Kubernetes；
- Service Mesh；
- Kafka 核心链路；
- 自研真实支付平台；Mock Payment Gateway 仅作为可控的测试替身；
- 第三方支付 Production 接入和真实资金；第三方 Sandbox 本期 No-Go，未来只有项目拥有者重新决定才考虑，不属于当前完成标准；
- 动态运费、税务引擎、多层 Category、复杂可配置优惠引擎和通用 EAV 商品属性系统；
- 商业产品级 UI 动画与视觉精修。

Kafka 仅作为以后出现真实 Event Streaming 需求时的可选研究扩展，不属于当前完成标准。

---

## 5. Core Domains（核心领域）

以下是业务领域。第一批 Django App 已确认为 `accounts`、`catalog`、`inventory`、`ordering`、`payments`；Cart 与模拟物流先归入 `ordering`。`promotions` 与 `engagement` 在最小闭环后按需建立；Search 仍受 Gate 约束，Async Processing 中的支付事件链路在 Phase 5 必做。

以下领域也不等于都要阻塞最小可运行闭环。Promotions、Engagement、Search 和 Async Processing 按路线图逐步加入；Search 和支付范围外的异步任务仍为条件性扩展。支付异步链路先以现有 MySQL + Mock 同步流程和 Inbox 恢复为基线，不自动新增独立 Django App 或拆分微服务。

### Accounts

- `Custom User(AbstractUser)`；Customer 为普通用户，Admin 使用 `is_staff + Groups/Permissions`
- `Username + Password` 与 `Session Cookie + CSRF`
- Address：收货人、省、市、区、详细地址和收货手机号；一个 Customer 可保存多个地址，但最多一个默认地址
- AddressSnapshot：下单时复制为不可变历史事实，不继续依赖可修改的 Address

### Catalog

- 单层 Category；一个 Product 属于一个 Category
- Product（共享名称、介绍、图片等商品概念/SPU）
- SKU（真正可购买、定价和持有库存的具体规格）
- 常用且需要查询的规格使用普通字段；仅展示型差异可以使用 JSON；不建设通用 EAV 属性系统
- Product Image
- Price
- Availability

### Inventory

- 每个 SKU 一条 Inventory，不建立 Warehouse 表；
- `available_stock` 与 `reserved_stock`；
- Inventory Reservation 与 Inventory Movement；
- 创建订单时预占、支付成功后核销、取消或超时后幂等释放；
- 库存并发正确性；
- 库存变更流水和审计信息。

### Cart

- 每个 Customer 一个活动 Cart
- CartItem；同一 Cart 中 `(cart, sku)` 唯一
- 数量范围 1–99
- 商品失效与改价处理
- 在 Order/预占事务末尾移除相应 CartItem；事务失败会整体回滚并保留 Cart

### Ordering

- Order
- OrderItem
- 金额使用 Decimal；`商品小计 - 优惠 + 0 运费 = 应付金额`，应付金额不得小于 0
- 商品和地址快照
- `PENDING_PAYMENT → PAID → SHIPPED → COMPLETED`，以及终态 `CANCELED`、`EXPIRED`
- 创建订单使用 Idempotency-Key 和请求摘要识别重复请求
- 受状态机约束的简单取消、15 分钟未支付超时关闭和迟到支付处理
- 取消触发库存释放或退款模拟时的幂等处理

### Promotions

- Coupon：最小闭环后只实现订单级“满额减固定金额”
- Coupon Claim / Redemption
- 每单最多一张、不可叠加、具有效期与总额度；每位用户对同一 Coupon 最多领取/使用一次
- 秒杀活动

### Payments

- PaymentAttempt；一个 Order 可以有多次历史尝试，但最多一个活跃尝试
- `CREATED → PENDING / UNKNOWN → SUCCEEDED / FAILED / EXPIRED / CANCELED`
- Payment Event / Webhook Inbox；`(provider, event_id)` 唯一并保留原始事件
- Provider Adapter：统一创建支付、查单、验签、事件解析和发起模拟退款边界
- Idempotency（幂等）
- Mock Payment Gateway（必做、后端可控；测试场景选择器只在开发/演示环境开放）
- RabbitMQ + Celery（必做，限支付事件异步处理、有界重试、Inbox 补漏恢复与故障证据）
- Refund Simulation（独立生命周期，服务于简单取消和迟到支付，不处理真钱）
- Reconciliation 思路

### Engagement

- Review：Order 完成后，每个 OrderItem 最多一条，可以修改原评价
- Favorite

### Search

条件性扩展领域：只有 MySQL 搜索基线暴露真实限制，且最近一次工时审计允许时才进入实现。

- MySQL 基础搜索
- Elasticsearch 索引与查询
- 索引同步、重建和对账

### Async Processing

支付主线必做支撑：Phase 5 使用 RabbitMQ + Celery 处理已持久化的支付事件。范围外的邮件、物流任务或通用任务平台不因本次决定自动加入；Transactional Outbox 仍须由真实投递一致性问题决定。

- Celery Task
- RabbitMQ
- 有界 Retry / Backoff；明确拒绝的事件不反复重试，暂时性失败重试耗尽后保留可恢复记录
- Idempotent Consumer
- Inbox 恢复扫描：补处理已入箱但未完成的 `RECEIVED/ERROR`，覆盖投递前崩溃或投递失败
- Transactional Outbox 候选方案

---

## 6. First Business Requirements（第一版业务要求）

### Accounts / Address

1. Guest 只能浏览和搜索商品，登录成为 Customer 后才能创建服务端购物车、收货地址、订单和支付记录。
2. 收货手机号作为字符串保存在 Address 中，并在下单时进入地址快照。
3. Vue 前端与 Django 后端都进行基础格式校验，后端校验结果为最终依据。
4. 开发、测试和演示只使用模拟手机号数据，不发送短信，也不验证号码归属或实际控制权。
5. Customer 与 Admin 使用唯一 Username + Password 登录，收货手机号不作为登录标识。
6. 密码必须使用 Django 密码哈希机制保存，任何地方都不得存储或记录明文密码。
7. 第一版不实现短信验证码或邮箱验证。
8. 项目从第一次迁移开始使用 `Custom User(AbstractUser)`；Customer 是普通用户，Admin 使用 `is_staff + Groups/Permissions`。
9. Username 先 trim、再转小写，必须匹配 `^[a-z][a-z0-9_]{2,29}$` 并保持数据库唯一；昵称以后使用独立字段。
10. 浏览器认证使用 Session Cookie + CSRF；部署时 Vue 与 Django 同源，不在 localStorage 中长期保存 JWT。
11. Address 结构化保存收货人、省、市、区、详细地址和手机号；一个 Customer 可以有多个地址，但最多一个默认地址。
12. 输入手机号时可去除空格、连字符和 `+86`，最终规范化为匹配 `^1[3-9]\d{9}$` 的 11 位字符串；格式合法不代表号码真实。
13. Django 开启时区支持，持久时间统一使用 UTC，顾客和管理界面按 `Asia/Shanghai` 展示。

### Catalog

1. Admin 可以创建 Category、Product 和 SKU。
2. Product 可以包含多个具有独立价格和库存的 SKU。
3. 下架商品不能产生新订单，但历史订单仍可查看。
4. 顾客只能购买处于可售状态且库存足够的 SKU。
5. Category 第一版只做单层结构，一个 Product 属于一个 Category。
6. Product 保存共享商品信息；SKU 是实际选择、定价、库存和购买单位，一个 Product 可以有多个 SKU。
7. SKU code 全局唯一；重要可查询规格使用普通字段，仅展示型差异可以使用 JSON，不建设通用 EAV 属性系统。
8. 没有可售 SKU 的 Product 不能被购买；Product/SKU 下架不改变历史订单快照。

### Cart

1. 登录用户拥有自己的购物车。
2. 相同 SKU 重复加入时更新数量，而不是创建无意义重复项。
3. 购物车不是价格和库存的最终事实来源。
4. 提交订单时必须重新读取并校验价格与库存；启用优惠能力后，再校验对应优惠规则。
5. 每个 Customer 只有一个活动 Cart；同一 Cart 中同一 SKU 只能有一个 CartItem。
6. CartItem 数量范围为 1–99；数量或 SKU 不合法时由后端拒绝，前端校验只负责及时提示。
7. 在创建 Order、OrderItem 和库存预占的同一事务末尾移除本次 CartItem；只有整个事务成功才会生效，下单失败回滚并保留 Cart 内容。

### Order

1. OrderItem 保存下单时的商品名、SKU 属性、单价、数量和折扣快照。
2. Order 保存下单时的收货地址快照。
3. 用户修改商品或地址后，历史订单不得发生变化。
4. 订单金额不能由前端直接决定。
5. 订单只能沿合法状态转换。
6. 重复业务请求必须有明确的幂等策略。
7. 取消订单必须经过合法状态转换，重复取消不得重复产生业务副作用。
8. 金额使用 `DecimalField(max_digits=12, decimal_places=2)` 与 Python `Decimal`，API 使用十进制字符串；财务计算禁止二进制浮点。
9. 统一按人民币分位和 `ROUND_HALF_UP` 舍入；第一版运费为 0、价格视为含税，不建设税费引擎。
10. 金额公式为 `商品小计 - 优惠 + 运费 = 应付金额`，应付金额不得小于 0；Provider Adapter 再按供应商协议转换为“分”或金额字符串。
11. 创建订单必须携带 Idempotency-Key；同一 Customer、同一端点、相同 Key 与相同请求返回原订单，相同 Key 配合不同请求返回 `409 Conflict`。
12. Order 使用不可猜测的公开订单号；内部主键使用项目统一的大整数主键。
13. Order 状态为 `PENDING_PAYMENT → PAID → SHIPPED → COMPLETED`，另有 `CANCELED` 和 `EXPIRED`；终态不得回退。
14. 支付失败属于 PaymentAttempt，不新增 `PAYMENT_FAILED` Order 状态；Order 在可支付期限内仍可再次支付。
15. 未支付 Order 默认 15 分钟后进入到期处理；当前一次性扫描基线保留，活跃支付未确认时暂缓关闭。Celery/RabbitMQ 的支付事件必做决定不自动改变过期判定或强制引入延时消息，主动查单、关闭竞态与触发机制按独立切片讨论并验证。

Order v1.0 合法转换：

| 当前状态 | 允许进入 | 条件 |
|---|---|---|
| `PENDING_PAYMENT` | `PAID` | 已验证 PaymentAttempt 成功 |
| `PENDING_PAYMENT` | `CANCELED` | Customer/Admin 合法主动取消 |
| `PENDING_PAYMENT` | `EXPIRED` | 15 分钟可支付期限结束且未确认成功 |
| `PAID` | `SHIPPED` | Admin 完成模拟发货 |
| `PAID` | `CANCELED` | 尚未发货，库存恢复与 Refund Simulation 原子完成 |
| `SHIPPED` | `COMPLETED` | Customer 确认收货 |
| `COMPLETED/CANCELED/EXPIRED` | 无 | 终态；重复请求幂等返回，迟到事件不得复活 Order |

### Inventory

1. 库存永远不能小于 0。
2. 成功购买数量不能超过可售库存。
3. 同一个业务请求不能重复扣减库存。
4. 失败或合法取消后的库存释放规则必须明确、可测试，同一业务动作最多释放一次。
5. MySQL 是库存事实的最终持久来源。
6. 每个 SKU 对应一条 Inventory，不建立显式 Warehouse 表；至少保存 `available_stock` 与 `reserved_stock`，并维护 Reservation 和 Movement 审计记录。
7. 创建 Order 时在同一 MySQL 事务中原子预占库存；任何外部支付调用只能发生在事务提交后。
8. 支付成功时核销预占；`PENDING_PAYMENT` 取消或 `EXPIRED` 时释放预占；`PAID` 且未发货取消时恢复库存并创建 Refund Simulation。
9. 库存预占、核销和释放都必须使用唯一业务标识做到最多一次，并保持 `available_stock >= 0`、`reserved_stock >= 0`。
10. 普通 read-modify-write 只作为超卖复现实验基线；单活动 SKU、数量 1 的条件 UPDATE 已经 Phase 6C-1/2/3 对照及拥有者确认，由 ADR 0001 定案，6C-5 正式替换、真实1205整单回滚/原Key重试及相关回归通过。1213与未知错误仅注入检查，不是真实死锁/断连证据。普通购物车多 SKU 不属于本次决定。
11. 秒杀定义为每位 Customer 对每个 FlashSaleItem 最多一个成功订单、购买数量固定为 1；数据库唯一约束是首要正确性防线。

### Payment

1. 不相信前端“支付成功”页面作为最终支付事实。
2. Payment Provider Event 必须能够唯一识别。
3. 重复回调不得重复产生业务副作用。
4. 回调必须校验订单、金额、币种和合法状态转换。
5. 支付接口使用测试环境，不处理真实资金。
6. 需要退款时只执行 Refund Simulation 并保存状态记录，不调用真实退款接口。
7. 重复取消或退款请求不得重复产生退款副作用。
8. 核心版以 PaymentAttempt 表达支付状态，不额外维护容易与其失配的 Payment 状态表；Refund Simulation 使用独立记录。
9. PaymentAttempt 状态为 `CREATED`、`PENDING`、`UNKNOWN`、`SUCCEEDED`、`FAILED`、`EXPIRED`、`CANCELED`；`SUCCEEDED` 不因迟到的失败或处理中事件回退。
10. 同一 Order 最多有一个活跃 PaymentAttempt；创建尝试必须携带 Idempotency-Key，相同 Key 与相同请求返回原记录，相同 Key 与不同请求返回 `409 Conflict`。
11. `UNKNOWN` 表示结果未知，不等于失败；必须复用原 Attempt 查单或对账，不能直接创建新尝试。
12. 只有前一次明确 `FAILED`、`EXPIRED` 或 `CANCELED`，且 Order 仍是 `PENDING_PAYMENT` 时，才允许用新 Key 创建新尝试；演示版默认最多 3 次主动尝试，并保持为可配置规则。
13. 任一 Attempt 成功后禁止继续创建；终态 Order 收到迟到成功事件时不重新打开 Order，而是记录异常并幂等创建 Refund Simulation。
14. Webhook Inbox 以 `(provider, event_id)` 唯一，保存原始事件；先可靠入箱，再在明确事务边界内进行校验和状态转换。
15. Provider Adapter 统一创建支付、查单、验签、事件解析和发起模拟退款；业务层不得直接依赖某家第三方字段。
16. Mock Gateway 是必做后端测试替身，必须制造成功、失败、处理中、超时/未知、重复、乱序、错误金额/币种/签名等场景；前端测试选择器只在开发或演示环境开放。
17. 第三方 Sandbox 本期 No-Go，不进行账号可用性检查、SDK 接入或外部回调配置；未来只有项目拥有者重新决定才评估。核心版以 Mock 支付和真实 MySQL/RabbitMQ 故障验证完成。
18. 创建 PaymentAttempt 的幂等记录必须先在短事务中提交，再调用 Gateway；不得在持有订单或库存数据库锁时等待网络响应。
19. RabbitMQ + Celery 支付事件异步处理为核心必做：Inbox 提交后再投递事件记录编号，Worker 读取持久事件并复用校验/状态机/库存事务；入箱或入队成功不代表订单已支付。
20. 消息可能重复投递；消费者必须幂等。明确设计消息确认、有界重试和失败留存，不能依赖队列实现业务恰好一次。
21. 数据库提交与消息投递之间的崩溃窗口必须复现并恢复；`on_commit` 仅保证提交后调用，不保证消息必达。先以 Inbox 扫描补漏，Outbox 保留为证据支持后的条件方案。

PaymentAttempt v1.0 合法转换：

| 当前状态 | 允许进入 | 说明 |
|---|---|---|
| `CREATED` | `PENDING/UNKNOWN/SUCCEEDED/FAILED/EXPIRED/CANCELED` | 支持同步结果、超时未知或 Provider 直接回调 |
| `PENDING` | `UNKNOWN/SUCCEEDED/FAILED/EXPIRED/CANCELED` | 网络或查单仍可能进入未知状态 |
| `UNKNOWN` | `PENDING/SUCCEEDED/FAILED/EXPIRED/CANCELED` | 必须由复用原 Attempt 的查单、对账或可信事件收敛 |
| `SUCCEEDED/FAILED/EXPIRED/CANCELED` | 无正常转换 | 终态；矛盾的迟到事件进入 Inbox 并标记异常，不覆盖已经确认的事实 |

### Review

1. 只有满足购买/订单条件的用户才可评价。
2. 用户不能评价他人订单中的商品。
3. 只有 Order 为 `COMPLETED` 后才可评价；每个 OrderItem 最多一条 Review，可以修改原评价但不能重复创建。

### Coupon

1. Coupon 在最小业务闭环稳定后实现，不阻塞首次闭环。
2. 第一版只支持订单级“满额减固定金额”，每个 Order 最多一张且不可叠加。
3. Coupon 具有效期、总额度，并限制每位 Customer 对同一 Coupon 最多成功领取和使用一次；核销必须与 Order 创建保持一致且可幂等验证。

---

## 7. Critical Invariants（关键不变量）

项目开发过程中必须持续保护：

1. `available_stock >= 0` 且 `reserved_stock >= 0`。
2. 成功订单数量不超过可售库存。
3. 金额使用 Decimal，API 使用十进制字符串，禁止使用二进制浮点作为财务金额。
4. Order 总金额由服务端根据快照和规则计算。
5. 历史 OrderItem 与地址快照不随当前 Product/Address 改变。
6. 同一 Payment Event 最多产生一次业务副作用。
7. 非法订单状态转换必须被拒绝。
8. Customer 只能访问自己的地址、购物车、订单和支付记录。
9. Redis 不是订单、支付和核心库存的唯一事实来源。
10. 异步任务允许重复投递时，消费者必须安全处理重复消息。
11. 同一取消或退款业务动作不得重复释放库存或重复产生退款副作用。
12. 同一 Customer、端点和 Idempotency-Key 不能创建两个不同的 Order 或 PaymentAttempt。
13. 每个 Order 最多有一个活跃 PaymentAttempt，任何一个 Attempt 成功后不得再次发起支付。
14. 同一 Cart 中 `(cart, sku)` 唯一，每个 Customer 最多有一个活动 Cart 和一个默认 Address。
15. `available_stock >= 0` 且 `reserved_stock >= 0`；每次预占、核销和释放都必须拥有可审计的唯一业务标识。
16. `CANCELED`、`EXPIRED`、`COMPLETED` Order 不得被迟到事件重新打开。
17. Order、快照、库存预占和对应 CartItem 清理必须在同一 MySQL 事务中成功或回滚；Gateway 网络调用只能发生在相关短事务提交后。
18. Username 规范化后唯一，密码不得以明文保存；Customer 与 Admin 权限由后端执行。
19. 每位 Customer 对同一 FlashSaleItem 最多一笔成功购买，数量固定为 1。
20. Order、PaymentAttempt、Payment Event、RefundSimulation 和 InventoryMovement 等业务事实不得作为普通数据硬删除。

这些不变量是测试和架构设计的第一依据。

---

## 8. First Architecture（第一版系统架构）

项目采用 Modular Monolith（模块化单体），不采用微服务。

```text
                         Browser
                            │
                 Vue 3 + TypeScript/Vite
             ┌──────────────┴──────────────┐
             ▼                             ▼
 Customer Routes + Layout        Admin Routes + Layout
             └─ Shared Auth/API/Types ─────┘
                            │
                   Session Cookie + CSRF
                         HTTP/JSON
                            │
                Nginx（进入部署阶段后）
                            │
                      Django + DRF
                    Modular Monolith
                            │
             ┌──────────────┴──────────────┐
             ▼                             ▼
           MySQL                         Redis
  Core Source of Truth        Required Flagship Addition
                                       Cache/Atomic Ops

    Required Payment Processing（Phase 5 待接入）
             └── RabbitMQ → Celery Worker → 同一 MySQL 支付处理事务

    Conditional Supporting Services（达到引入门槛后）
             └── Elasticsearch — Derived Search Index

                Provider Adapter
                       │
                       ▼
             Mock Gateway（核心必做）
                       │
                    Webhook
                       ▼
             Django Payment API → MySQL Inbox → RabbitMQ

    External Sandbox 本期 No-Go，不属于当前运行链路
```

### 架构约束

- Django/DRF 是唯一业务后端部署单元。
- 若未来增加极简 Vue 管理或实验入口，它与顾客端共享一个工程；Interview Core 的普通运营管理使用 Django Admin；部署目标为浏览器、Vue 与 Django API 同源。
- MySQL 保存订单、支付和核心库存的最终持久事实。
- Redis 在 MySQL baseline 后按必做旗舰主线引入，但不是核心事实来源；相关辅助状态必须能够重建、降级或对账。
- Elasticsearch 是可重建、可延期的条件性派生索引。
- RabbitMQ + Celery 是 Phase 5 支付事件处理必做支撑；Worker 复用同一代码库与 MySQL，不拆微服务，不替代订单事务的核心正确性。支付事件、原支付尝试和业务处理结果以 MySQL 持久记录为准。
- Mock Gateway 通过 Provider Adapter 接入；第三方 Sandbox 本期 No-Go，Production Payment 明确不接入。
- 不因“看起来高级”提前拆微服务。

---

## 9. ER Diagram v1.0（确认后的概念关系）

```text
CustomUser 1 ─── N Address（最多一个 default）
CustomUser 1 ─── 1 ActiveCart
Cart 1 ─── N CartItem N ─── 1 SKU
UNIQUE (cart_id, sku_id)

Category 1 ─── N Product
Product 1 ─── N SKU
SKU 1 ─── 1 Inventory
Inventory 1 ─── N InventoryMovement
Inventory 1 ─── N InventoryReservation

CustomUser 1 ─── N Order
Order 1 ─── N OrderItem
OrderItem N ─── 1 SKU
Order 1 ─── 1 AddressSnapshot
Order 1 ─── N InventoryReservation
OrderItem contains Product/SKU/PriceSnapshot

Coupon 1 ─── N CouponClaim
CustomUser 1 ─── N CouponClaim
CouponClaim 0..1 ─── 1 Order

Order 1 ─── N PaymentAttempt
PaymentEvent N ─── 0..1 PaymentAttempt（先独立入箱，验证并解析后可关联）
UNIQUE (provider, PaymentEvent.provider_event_id)
Order 1 ─── N RefundSimulation

CustomUser 1 ─── N Review
Review N ─── 1 OrderItem
Review N ─── 1 SKU/Product
UNIQUE (review.order_item_id)

CustomUser N ─── N Product through Favorite

FlashSale 1 ─── N FlashSaleItem
FlashSaleItem N ─── 1 SKU
UNIQUE (customer_id, flash_sale_item_id) for successful flash-sale order
```

该 ERD 固定聚合关系和关键唯一性方向，不代表已经创建数据库。具体字段长度、外键删除策略和查询索引在每个纵向切片开始前确定；业务事实默认不硬删除，索引必须由真实查询、测试或 `EXPLAIN` 支持。

---

## 10. Technology Strategy（技术策略）

### Level 1 — 项目基础必做

- Python
- Django
- Django REST Framework
- Django ORM
- MySQL
- Git / GitHub
- pytest / pytest-django
- 基础日志
- 最小 CI

### Level 2 — 三条旗舰主线必做

| 技术/问题 | 开始前必须有的基线 | 必须留下的证据 |
|---|---|---|
| 库存并发与秒杀 | MySQL 普通库存流程与 naive 并发版本 | 超卖复现、方案比较、正确性与性能数据 |
| Redis Cache | 商品读取的 MySQL baseline | 查询量、命中率、延迟与一致性/故障实验 |
| 防穿透/击穿/雪崩 | 能够复现对应失败模式 | 并发回源、TTL、空值/保护策略实验 |
| Redis Lua | Redis 中存在需要原子完成的多步操作 | 并发正确性测试与 MySQL 对账 |
| 支付状态与幂等 | Mock Payment 与 Order/PaymentAttempt 状态机基线 | 重复/乱序回调、校验、故障恢复与 ADR |
| RabbitMQ + Celery 支付异步链路 | 同步 Inbox 处理与一次性恢复扫描基线 | 真实投递、消息确认、有界重试、重复消费、Worker/Broker 故障与补漏恢复 |

Mock Payment Gateway、PaymentAttempt、Webhook Inbox、RabbitMQ + Celery 支付链路及幂等/恢复证据属于支付旗舰主线必做范围。第三方 Sandbox 本期 No-Go，不属于本 Level，也不影响核心版完成。

### Level 3 — 计划内交付支撑

| 技术/能力 | 引入时机 | 必须留下的证据 |
|---|---|---|
| Docker Compose | 服务依赖增加或需要可复现环境 | 一条流程启动完整环境 |
| Nginx/Gunicorn/Linux | 进入部署阶段 | 可访问服务、日志与健康检查 |
| CI | 从最小 CI 逐步扩展 | 实际执行记录、失败与修复证据 |
| 日志与可观测性 | 业务闭环形成后逐步增强 | 能定位关键请求、任务和故障 |
| Performance Test | 每个优化前先建立 | p50/p95/p99、吞吐、错误率、资源变化 |

### Level 4 — 条件性、可延期

| 技术 | 引入前必须出现的问题 | 若引入必须留下的证据 |
|---|---|---|
| Distributed Lock | 默认不使用；数据库约束、条件更新、行锁或 Lua 经证据证明不足 | 锁超时、错误释放和故障边界分析 |
| Transactional Outbox | 已经存在数据库事实与消息可靠投递之间的一致性问题 | 崩溃窗口、重复投递、幂等消费和恢复证据 |
| Elasticsearch | MySQL 搜索在功能或测量上不足 | 相关性、中文分词、延迟、同步和重建结果 |

RabbitMQ + Celery 已移入 Level 2 必做，限支付链路，不再以条件技术 Gate 决定是否采用。其他候选仍不必全部落地：Distributed Lock 默认不用；Transactional Outbox 只解决已复现的可靠投递一致性问题；Elasticsearch 只在 MySQL 搜索基线不足时引入。Alipay/Stripe Sandbox 本期 No-Go，未来只有项目拥有者重新决定才评估，不自动开展账号可用性检查。任何技术一旦实际引入，都必须完成真实集成、故障或一致性验证，不能只添加依赖。

---

## 11. Three Flagship Engineering Tracks（三条旗舰深度主线）

三条旗舰主线是已确认的必做范围，共享 720 小时建议预算；各主线的实际小时数根据 MVP 进度、失败复现难度和证据风险动态调整。开始深入优化前，必须先有对应的最小业务闭环以及 MySQL 或 Mock baseline。

### Track A — Inventory Concurrency & Flash Sale Correctness

学习和实现顺序：

```text
普通 read-modify-write
→ 并发复现超卖
→ MySQL Transaction
→ 原子条件更新 / select_for_update 比较
→ 预占、核销、释放与死锁/锁等待重试
→ Redis Lua 预检查/预扣
→ 一人一单
→ 故障补偿与对账
→ Distributed Lock Gate（默认不采用）
```

单活动 SKU 条件 UPDATE 方向已由拥有者在 Phase 6C-4 接受，依据是既有 naive 基线、完整事务正确性与有限负载证据，见 ADR 0001；6C-5 正式服务替换与真实超时回滚/重试通过，沿用现有会话等待值、无自动重试。这不证明生产容量或普遍无死锁，也不改变普通购物车多 SKU。Redis 可以承担缓存或秒杀流量准入，但 MySQL 始终保存最终订单与核心库存事实。

核心证据：

- 初始库存与并发请求参数；
- naive 版本失败结果；
- 每种方案的成功数、库存终值、重复订单数；
- 吞吐量、p95/p99、错误率；
- Redis 与 MySQL 最终一致性检查；
- ADR 与 Trade-off。

### Track B — Redis Cache Consistency & Resilience

学习和实现顺序：

```text
MySQL catalog baseline
→ Cache-Aside
→ 事务提交后的主动失效
→ TTL 与随机抖动
→ 数据更新后的陈旧窗口
→ Cache Penetration
→ Hot-key / Cache Breakdown
→ Cache Avalanche
→ Redis unavailable fallback to MySQL
→ 并发与性能实验
```

第一批缓存对象是可以从 MySQL 重建的 Category、Product 和其他公开目录数据。价格或可售状态即使进入缓存，也必须在下单时由后端重新读取权威数据。具体 Key 和 TTL 由基线与实验确定，不在没有测量时伪造“最佳值”。

核心证据：

- 缓存前后 DB 查询量和响应延迟；
- 命中率；
- 热 Key 冷启动时的数据库回源数量；
- Redis 宕机时系统行为；
- 更新后读取一致性与可接受陈旧窗口；
- 方案限制和 Trade-off。

### Track C — Payment State Machine & Idempotency

学习和实现顺序：

```text
Order / PaymentAttempt 状态机
→ Mock Payment Gateway
→ Idempotency-Key 与多次 Attempt
→ Webhook Inbox 与 (provider, event_id) 唯一性
→ 金额 / 币种 / 模拟签名校验
→ 重复和乱序事件
→ 已入箱 RECEIVED/ERROR 的一次性恢复基线
→ RabbitMQ + Celery 支付事件异步处理（必做）
→ 消息确认、有界重试、重复消费与投递/Worker/Broker 故障恢复
→ UNKNOWN、查单、重试和崩溃窗口
→ 取消竞态与 Refund Simulation
→ Reconciliation
→ 支付与消息可靠性 Evidence Pack
```

Mock Gateway 是运行在后端、由项目控制的支付供应商测试替身，不是单纯的前端成功按钮。正常顾客界面只能发起支付；成功、失败、处理中、超时、重复、乱序或错误数据等场景只能通过测试、受保护的开发工具或演示环境注入。

RabbitMQ + Celery 的最小链路为：可靠入箱并提交 → 发布事件记录编号 → Worker 读取原事件并调用统一处理器 → MySQL 原子更新支付、订单、库存及事件结果。已有同步基线与扫描恢复保留为对照及补漏路径。消息重投、发布失败及提交后崩溃都要验证，不能声称安装队列即可避免丢消息；Outbox 仍是条件方案。第三方 Sandbox 本期不进入。

核心证据：

- 相同请求或事件重放 10/100 次只产生一次副作用；
- 乱序事件不会非法回退状态或重新打开终态 Order；
- 错误金额、币种或签名被拒绝；
- `UNKNOWN` 不被误判为失败，并能通过查单或对账收敛；
- 数据库事务失败不会留下半完成状态；
- 真实 RabbitMQ 消息重复投递、Worker 处理前/事务提交后中断，均不重复核销或模拟退款；
- Broker 不可用、Inbox 提交后尚未投递就中断，持久事件仍可补漏恢复；暂时性失败有界重试，明确拒绝不反复重试；
- 顾客不再次点击查询、网关不重发时，已入箱事件仍有明确恢复入口；未知支付另由原 Attempt 查单收敛；
- 状态机图、回归测试、故障注入与 ADR。

---

## 12. Testing Strategy（测试策略）

测试从项目骨架开始，不作为后期附加模块。

### Fast Tests

- 价格、折扣、状态转换等纯业务规则单元测试；
- Serializer 校验；
- Username trim/lower/regex/unique、密码哈希与收货手机号规范化；不测试短信或号码归属，因为这两项明确不实现；
- Decimal 精度、API 金额字符串、0 运费、固定满减和总额不为负；
- Product/SKU、单层 Category、CartItem 1–99 与可售规则；
- 每条 Order/PaymentAttempt 合法和非法状态转换；
- Customer/Admin Permission 规则；
- Mock 与可选外部 Provider Adapter 的合约测试。

### Database/API Integration Tests

- 使用 pytest-django 与隔离 MySQL 测试数据库；
- Model 约束、唯一约束和索引相关行为；
- 最多一个默认 Address、不可变地址快照、一个活动 Cart 与 `(cart, sku)` 唯一；
- DRF Session/CSRF、认证、权限和响应；
- Order/PaymentAttempt Idempotency-Key 同参复用与异参 `409`；
- Order/快照/Reservation/Cart 清理的同一事务，以及失败后的完整回滚；
- Inventory available/reserved 非负、Movement 审计与重复核销/释放；
- 每个 Order 最多一个活跃 PaymentAttempt、成功后禁止新建、UNKNOWN 查单与最多三次配置；
- Event Inbox 联合唯一、重复/乱序/迟到事件和最多一次 Refund Simulation；
- 不使用 SQLite 证明 MySQL 的并发和锁语义。

### Infrastructure Integration Tests

- 隔离真实 Redis：Cache、Lua、锁语义；
- RabbitMQ + Celery 支付链路必须使用隔离真实服务测试投递、消息确认、重试和重复消费；Worker 与 Broker 的故障恢复不能仅用 Mock 替代；
- 若引入 Elasticsearch，使用隔离真实服务测试写入、查询、重建与同步；
- Mock Payment：真实执行 Webhook/Event Inbox 路径与支付异步处理事务；Sandbox 本期 No-Go，不增加第三方集成测试。

### Concurrency / Performance / Failure Tests

- 并发库存与秒杀；
- naive 超卖与原子条件更新/`select_for_update()` 的相同条件对比；
- 同一 Customer + FlashSaleItem 最多一笔成功购买且数量为 1；
- 重复请求和重复回调；
- 重复取消、重复库存释放和重复退款模拟；
- Redis 故障；Worker、Broker 和搜索服务仅在对应技术实际引入后进行故障测试；
- 负载基线和优化前后对比；
- 与普通快速 CI 测试分组运行。

测试、调试和验证工时嵌入每个开发阶段与预算类别，不作为项目最后才补做的独立阶段。

### Test Ownership Rule

AI 不得在用户尚未确认正确行为时，同时定义测试 Oracle 和完整实现。用户必须先说明预期结果以及第一个失败测试为什么应该失败。

---

## 13. Evidence Standard（完成证据标准）

每条重要技术主线都要形成 Evidence Pack：

1. Business Problem：解决什么业务问题；
2. Invariants：哪些事实绝不能被破坏；
3. Baseline：最简单实现；
4. Failure Reproduction：如何稳定复现缺陷；
5. Alternatives：至少两个候选方案；
6. Decision：最终选择及原因；
7. Implementation：关键实现；
8. Verification：测试、日志、数据库状态或性能数据；
9. Failure Injection：依赖失败或重复调用时的行为；
10. Trade-off：获得了什么，付出了什么；
11. Interview Summary：两分钟和十分钟版本的讲解。

每次并发、缓存或支付实验至少记录：代码/Git 版本、Python/Django/MySQL/Redis 等依赖与服务版本、固定数据集与 seed、并发模型和数量、预热、持续时间、运行次数、硬件/OS/容器资源、p50/p95/p99、吞吐、错误率以及实验前后的不变量检查。保存所有有效运行和原始输出，不能只挑最好的一次；性能目标在 baseline 后设定，不得预先捏造漂亮数字。

Evidence Pack 必须明确区分 `Confirmed Rule`、`Experiment Candidate` 和引用证据后的 `Validated Decision`。

未经实际运行，不得把“应该有效”写成“已经验证”。

---

## 14. Development Roadmap（开发路线）

进度标记：`[x]` 表示已经取得对应实现与验证证据；`[ ]` 表示尚未完成。当前正在推进的纵向切片会额外标注“← 当前”。

### Phase 0 — Day 0 & Foundation（已完成）

- [x] Project Charter v1.2 — Current Design Baseline（2026-10-01 支付 MQ 范围更新）
- [x] Business Requirements
- [x] Domain Context v1.0
- [x] Core User Journey
- [x] ERD v1.0 概念关系
- [x] 已确认的 Order/PaymentAttempt 状态机
- [x] 已确认的 Modular Monolith App 边界
- [x] Repository
- [x] Django/MySQL Skeleton
- [x] pytest、基础日志与最小 CI

### Phase 1 — Catalog Vertical Slice（已进入 main）

- [x] Custom User、Session/CSRF 与权限基础
- [x] **Catalog Public Browse Baseline（商品目录公开浏览基线）**
  - [x] Category/Product/SKU
  - [x] 基础 Inventory
  - [x] Guest 顾客公开浏览 API
  - [x] 本切片的 Model/API/Permission 测试
- [x] 顾客浏览 Vue 页面（证据见 `docs/verification/guest-catalog-browse.md`）
- [x] Django Admin 作为 Interview Core 的商品/SKU/Inventory 管理入口
- [ ] 完整 Vue Admin：延期，不阻塞 Interview Core；只有深度主线出现明确演示需求时才局部实现

### Phase 2A — Interview Core MVP（进行中）

#### MVP-1 — Authenticated Cart（已进入 main）

- [x] 复用 Session/CSRF 的极简登录状态，不扩展新的前端状态框架
- [x] 单活动 Cart、`(cart, sku)` 唯一和数量 1–99
- [x] 查看、加入、修改数量和删除 CartItem；用户只能操作自己的 Cart
- [x] 价格由后端读取；Cart 不承诺下单价格或库存，加入 Cart 不预占库存
- [x] 后端约束、权限/API 测试和极简 Vue 联调

MVP-1 中普通 GET/POST/PATCH/DELETE、Serializer/View/URL 与页面样板走 CRUD Fast Track；用户重点理解身份/所有权、同 SKU 合并、数量边界、服务端价格边界和 Cart 非库存承诺。

#### MVP-2 — Minimum Address（已进入 main）

- [x] 结构化 Address 与收货手机号规范化
- [x] 首地址自动默认、每用户最多一个默认地址、切换默认与删除默认后的确定性补位
- [x] 只访问本人地址的 Session/CSRF API 与最薄 Vue 创建/编辑/删除/设默认流程
- [x] MySQL 约束、权限、字段校验、事务与双线程并发切换默认测试
- [x] AddressSnapshot 已在 MVP-3 创建订单事务中生成；MVP-2 本身不做省市联动、地图或产品级地址体验

#### MVP-3 — Order + Inventory Reservation（已提交）

- [x] Order/OrderItem 与创建订单 Idempotency-Key
- [x] 从选定 Address 复制不可变 AddressSnapshot，历史订单不依赖当前可修改地址
- [x] Decimal 金额合同、0 运费和服务端重新计算
- [x] Inventory Reservation/Movement 与事务内预占
- [x] 在同一事务末尾清理相应 CartItem；失败完整回滚
- [x] Model/API/权限/事务/快照/幂等与同 Key 双线程并发自动测试
- [x] 最薄 Vue `/checkout` 创建订单入口与学习/验证文档

#### MVP-4 — Mock Payment + Completion

- [x] Order/PaymentAttempt 最小状态机的代码与 SQLite 功能烟测
- [x] 事务提交后的最小同步 Provider Adapter、Mock Payment、Event Inbox baseline 和库存核销代码
- [x] Django Admin 模拟发货；Customer 确认完成的代码与 SQLite 功能烟测
- [ ] MySQL 隔离测试数据库中的约束、并发与全项目回归，以及浏览器端到端验收

本切片未执行开发数据库迁移，不处理真实资金。15 分钟超时关闭、取消、库存释放、迟到成功退款模拟仍属于 Phase 2B；本阶段不能宣称这些分支已完成。

进入高级基础设施前，最小端到端闭环必须以 MySQL + Mock Payment 实际运行并通过目标测试。早期商品和管理数据可以使用 Django Admin 辅助操作。

### Phase 2B — Correctness and Business Enhancements After the Loop

- [ ] 已确认的取消/过期、迟到支付、库存释放与 Refund Simulation
- [ ] 固定满减且不可叠加的 Coupon
- [ ] Favorite
- [ ] Completed OrderItem 的单条 Review

这些能力全部移入闭环后的可选 Backlog，不阻塞 Interview Core。若时间不足，Coupon、Favorite 与 Review 可以保持 No-Go；取消/过期、迟到支付、库存释放和 Refund Simulation 只在支付深度主线需要对应故障证据时进入。

### Phase 3 — Database Correctness & Optimization

- [ ] Transaction
- [ ] 唯一约束
- [x] naive 库存并发失败复现（Phase3最小错误基线已于2026-09-26观察2单/2预占，库存0/1；见phase-3-inventory-race-baseline.md，不等于完整秒杀或性能验证）
- [x] 原子条件更新与 `select_for_update()` 对比及 Evidence-backed ADR（Phase3/6C-2正确性与6C-3有限负载证据已有，6C-4/ADR0001接受；6C-5正式替换、真实1205与相关回归通过）
- [ ] N+1、索引、EXPLAIN
- [ ] SQL 数量和延迟基线

### Phase 4 — Redis Cache Track

- [ ] Category/Product Cache-Aside
- [ ] `transaction.on_commit` 后失效与 TTL 随机抖动
- [ ] 穿透、击穿、雪崩
- [ ] Redis unavailable fallback to MySQL
- [ ] 性能证据

### Phase 5 — Payment Track Deepening

- [ ] Provider Adapter 合约与故障边界深化
- [ ] Mock Gateway 故障场景
- [ ] Webhook Inbox 的重复、乱序、验证失败和处理失败路径
- [ ] 幂等、乱序、重试
- [x] 已入箱 `RECEIVED/ERROR` 的人工一次性恢复扫描与独立事务基线（Phase 5A 历史基线）
- [x] 事件失败分类、持久累计预算、到期门槛和耗尽状态（Phase 5B；人工触发，无自动调度/处理中接管）
- [x] RabbitMQ + Celery 支付事件异步处理基础链路（Phase 5C-1，真实正常/重复/伪造事件验证，默认同步）
- [x] Worker 按持久预算安排短 ETA 后台重试（Phase 5C-2，真实受控锁超时验证；消息数量不等于业务次数）
- [x] 原 Inbox ID 人工单轮补投与 Broker 停机后的首次/重试漏投恢复（Phase 5C-3；无周期调度）
- [x] 持久处理租约、人工过期领取修复与真实 Worker 中断恢复（Phase 5C-4；不自动调度）
- [x] 已选配置的消息确认、有界重试、重复消费、Worker/Broker 故障与人工发布补漏证据（C1–C4；限制见各期记录）
- [x] 原活跃Attempt下支付成功与订单到期两种Order获锁顺序、真实记录锁等待与重复效果验证（Phase 5D；六场景）
- [ ] 原活跃 Attempt 查单恢复；与 Inbox 重处理区分，避免重新付款
- [ ] Refund/Reconciliation 思路
- [ ] 支付与 MQ Evidence Pack；保留同步基线、失败复现、方案取舍与限制

第三方 Sandbox 本期 No-Go；支付异步处理先基于已有状态机与事务分切片接入，不一次重写整个 payments 模块。

### Phase 6 — Flash Sale & Concurrency Track

- [x] 单活动商品下单/限购服务基线（Phase 6A 真实MySQL目标10项与订单/支付回归151项通过；历史证据保留）
- [x] 资格消费/真正未付关闭释放与O1旧事件隔离（Phase 6B目标11项、相关回归171项通过；未新增秒杀下单HTTP/前端，见阶段指南与验证记录）
- [x] 完整秒杀NOWAIT双顾客竞争基线（Phase6C-1目标3项、promotions回归23项；历史证据保留）
- [x] 同完整事务NOWAIT/条件UPDATE候选正确性与受控行为对照（6C-2目标6项、模块29项；历史证据保留）
- [x] 同条件有限批次负载、重试/时延/连接成本（6C-3共24有效批次、576旅程/767调用，目标1pass及模块29pass1skip；非持续生产容量，默认未切换）
- [x] 单活动 SKU 条件 UPDATE 选型决定（6C-4，ADR 0001 已接受；仅文档，源码仍 NOWAIT）
- [x] naive 超卖基线复用（Phase 3 历史普通库存证据；不是新的完整秒杀压测，不重做同一实验）
- [x] 正式条件 UPDATE 最小落地、失败合同与相关回归（6C-5目标9项/198子场景、相关183项/403子场景；1213/2006仅注入，不自动重试）
- [ ] MySQL 最终事实与方案总验收（正式服务验证已完成；HTTP/端到端与项目拥有者独立讲述待验收）
- [ ] Redis Lua 流量准入/原子前置候选
- [x] 每个 Customer + FlashSaleItem 正常成功限购、数量 1（Phase 6A/B 资格及旧订单隔离证据；服务层，HTTP 待接入）
- [ ] Distributed Lock Gate（默认 No-Go）
- [ ] 压测和补偿
- [ ] 最小秒杀 HTTP 查询/下单入口
- [ ] 极简顾客入口、端到端与面试讲述验收

Redis Lua 在 Level 2 必做表与本阶段候选清单中措辞不同，须由拥有者另行确认本期范围。本次 `ok` 只接受条件 UPDATE，不删除 Lua 要求，也不自动实施。原“压测和补偿”总验收复用有限负载及资格生命周期证据，只有具体新失败或入口验证需要才增加实验。

### Phase 7 — Conditional: Further Async Reliability

- [ ] Transactional Outbox 候选
- [ ] 支付范围外确有业务需求的异步任务
- [ ] 新需求对应的投递一致性、恢复与故障证据

RabbitMQ + Celery 的支付事件处理、重试、消费者幂等与 Worker/Broker 故障证据已移入 Phase 5 必做，本阶段不重复实施。只有额外异步可靠性问题与最近一次工时审计共同支持时才扩展；数据库提交与消息发布一致性窗口经复现且现有补漏方案不足时，进一步评估 Transactional Outbox。

### Phase 8 — Conditional: Search

- [ ] MySQL Search Baseline
- [ ] Elasticsearch
- [ ] 中文分词和过滤
- [ ] 索引同步、重建和对账

只有 MySQL 搜索基线暴露真实限制且最近一次工时审计允许时才进入 Elasticsearch 实现，否则延期。

### Phase 9 — Deployment, CI & Performance

- [ ] Docker Compose
- [ ] Gunicorn/Nginx/Linux
- [ ] GitHub Actions
- [ ] 健康检查、日志和配置
- [ ] 三种负载的性能报告

### Phase 10 — Portfolio & Interview Package

- [ ] README
- [ ] 架构图与 ERD
- [ ] ADR
- [ ] Evidence Packs
- [ ] 演示脚本
- [ ] 简历描述
- [ ] 面试问题与无 AI 讲解演练

开发以纵向业务切片推进；不得先批量创建所有 Model，再批量创建所有 API。测试与验证随每个 Phase 同步完成；每累计 100 个专注小时执行一次范围与证据审计 Gate。

---

## 15. Definition of Done（项目完成标准）

### Business Done

- 顾客端业务主链端到端可运行；
- Django Admin 可完成普通商品、SKU、库存和订单运营；只有深度主线需要时才提供极简 Vue 管理/实验入口；
- 权限与历史快照行为正确；
- Session/CSRF、Decimal 金额、订单幂等、状态机与库存预占/核销/释放合同均有端到端和自动化证据；
- 模拟支付和物流状态可完整演示。
- 未支付取消/过期与库存最多释放一次在支付深度主线中验证；Paid 取消、迟到支付和 Refund Simulation 根据 Track C 证据需要进入，不阻塞首次 MVP 闭环。
- 第三方 Sandbox 本期 No-Go，Production Payment 不接入；Mock 支付闭环和支付证据包必须完成。

### Engineering Done

- 库存并发、缓存正确性、支付幂等三条旗舰主线均有 Evidence Pack；
- 三条主线均包含失败复现、至少两种方案比较和证据支持的结论；实验最终可以合理选择“不采用”某个候选；
- RabbitMQ + Celery 支付链路必须完成真实集成、有界重试、重复消费、Worker/Broker 故障及持久事件补漏恢复证据；仅安装依赖或使用即时执行模式不算完成；
- Distributed Lock、Transactional Outbox 和 Elasticsearch 仍为条件性，不属于 Interview Core 完成阻塞项；若实际引入，必须提供真实集成以及可靠性、故障或一致性证据；第三方 Sandbox 本期不进入；
- Docker Compose 可以复现完整环境；
- 自动化测试和 CI 通过；
- 浏览、下单、秒杀至少三种负载有基线和优化对比；
- 关键失败场景经过真实测试。

### Learning Done

项目拥有者能够在不让 AI 代答的情况下：

- 画出请求、订单、支付和库存数据流；
- 解释关键不变量和状态机；
- 解释为什么订单与库存预占在数据库事务内，而 Gateway 网络调用必须在提交后；
- 解释 Decimal/API 字符串、Session/CSRF 和后端权限边界；
- 比较 MySQL 锁、条件更新、Redis Lua 与分布式锁；
- 解释缓存一致性和故障降级；
- 解释支付与消息幂等；
- 解释 RabbitMQ、Celery Worker 与 MySQL Inbox 各自职责，提交后投递的崩溃窗口、消息确认与有界重试规则；
- 解释 UNKNOWN、重复/乱序事件、终态后的迟到支付和 Refund Simulation；
- 独立修改一个业务约束；
- 独立定位一个变体 bug；
- 解释 Vue → HTTP → Django 的关键数据流，并能读懂、运行、修改和调试 AI 交付的前端样板；
- 用测试、日志和性能数据证明结论。

---

## 16. Time Budget（工时预算）

1200 个专注小时保留为长期上限规划，不是 Interview Core 必须耗尽的工时。v1.1 的新增工时优先按以下比例投入，并在满足可演示、可复现、本人能解释的 Definition of Done 后停止扩张：

| 方向 | 比例 | 初始预算 |
|---|---:|---:|
| Interview Core MVP、普通 CRUD 与薄 Vue | 20% | 240 小时 |
| 三条旗舰主线及实验 | 60% | 720 小时 |
| Docker、CI、日志、测试环境与可复现支撑 | 10% | 120 小时 |
| Evidence Pack、演示和面试准备 | 10% | 120 小时 |
| **合计** | **100%** | **1200 小时** |

这是可调整的初始规划，不是要求每类必须精确耗尽的固定配额。测试、调试和验证嵌入各类别，不另设最后补做的测试预算。工时不包含无效等待和走神时间；AI 可以减少前端和其他样板代码时间，但不能替代学习、运行、调试、验证和复盘。

每累计 100 个专注小时执行一次范围与证据审计，至少记录：

- 实际投入与预算差异；
- 最小业务闭环的运行状态；
- 三条旗舰主线的 Evidence Pack 进度；
- 缺陷、返工和学习风险；
- 下一 100 小时的调整；
- RabbitMQ + Celery 支付必做范围的进度与复杂度是否受控；Distributed Lock、Transactional Outbox、Elasticsearch 是否继续延期或满足引入门槛；第三方 Sandbox 本期 No-Go 不自动重启评估。

前 100 小时完成后，根据真实速度重新估算。发生调整时，优先保护最小业务闭环、三条旗舰主线和证据真实性，并将重要变化写入本 Charter 或对应记录。

---

## 17. AI Collaboration Model（AI 协作模式）

### Learning Mode — 默认

用于 `Depth` 工作和新的核心逻辑。用户先定义问题、例子、不变量、预期和初稿；AI 追问、提示与 Review。

订单、库存、支付等核心业务规则，三条旗舰主线，架构取舍，测试预期以及实验结果解释属于项目拥有者的核心学习责任。AI 可以提供帮助阶梯，但不能替项目拥有者决定并背诵这些结论。

### Pair Mode

用于已经理解但尚不熟练的任务。用户掌握接口、关键分支和测试；AI 可生成局部样板并协助 Review。

部署配置、前后端 API 集成、认证请求流和错误处理可以使用 Pair Mode；项目拥有者仍需实际运行、观察日志并解释关键链路。

### Delivery Mode

用于低风险、机械性或重复工作。接口契约与预期行为确认后，AI 可以直接实现 Vue 页面脚手架、HTML、CSS、常规表单与表格、重复组件、TypeScript 类型、API 调用样板和普通 CRUD，但必须展示改动并运行相应验证。

### CRUD Fast Track

普通 CRUD 不再使用完整学习检查。AI 可在已确认行为后一次性交付最小实现，用户只需：

- 沿一条请求解释 URL、认证/权限、View/Serializer、ORM、MySQL 与响应；
- 确认所有权和关键数据库约束；
- 查看聚焦 Diff，运行目标测试；
- 能独立修改一个字段、校验或筛选条件。

Cart 中的 HTTP CRUD 和页面样板可以快速交付，但同 SKU 合并、数量边界、用户隔离和服务端价格边界属于 `Supporting` 规则，不能跳过。Order、库存、Redis 与 Payment 一律不得降级为 CRUD Fast Track。

### Frontend Delivery Boundary（前端交付边界）

AI 主要交付前端样板不代表前端成为黑盒。项目拥有者必须：

- 读懂关键组件的数据输入、输出和请求流；
- 使用浏览器 Network 等工具观察 HTTP 请求与响应；
- 能够区分并初步排查常见的 400、401、403 和 500 错误；
- 实际运行构建与目标流程；
- 能够独立修改和调试至少一个相关功能。

前端校验只用于及时反馈和改善体验；需要保护业务数据的规则必须由 Django 后端独立校验。

### Help Ladder

```text
1. 引导问题
2. 概念和资料位置
3. 流程、测试方向或伪代码
4. 最小局部代码
5. 明确请求或多次尝试后才给完整参考实现
```

核心业务逻辑不得默认从 Level 5 开始。

### Git Development Workflow — Main First

Project One 当前由项目拥有者个人开发。现阶段 Git 流程优先服务于学习效率、核心业务交付和后端技术深度，不要求为每个普通功能模拟完整企业协作流程。

- 普通、低风险、范围清晰的日常功能默认直接在 `main` 开发。
- 一个清晰小阶段完成后，先检查当前分支、`git status`、diff 和测试结果，再执行范围明确的 `git add`、有意义的 `git commit` 与 `git push origin main`。
- commit 必须聚焦且可解释；不得混入大量无关修改。直接在 `main` 开发不降低测试、Review、自检、CI、版本控制和证据标准。
- 大规模重构、高风险数据库修改、库存并发/秒杀、支付状态机/Webhook、不确定是否采用的实验方案，以及需要隔离或方便回滚的大型修改，才建议使用独立 branch；worktree 只在确实需要并行隔离时使用。
- AI 不得为普通小功能自动创建大量 `codex/*` 分支、worktree 或 PR。创建例外分支前必须说明风险和隔离收益。
- 例外分支完成后，先在分支验证，再与最新 `main` 安全整合；确认 `main` 包含预期提交或等价内容后，才能清理旧 branch/worktree。
- Pull Request 是高风险变更、需要集中 Review 或用户明确要求时的工具，不是所有功能的默认完成门槛。

---

## 18. Project Knowledge Structure（项目知识结构）

计划使用：

```text
PROJECT_CHARTER.md       项目总纲、范围与完成标准
PROJECT_INSTRUCTIONS.md  可复制到 ChatGPT Project 的全局提示词
AGENTS.md                Codex 本地项目持久指令
CONTEXT.md               稳定领域术语
ARCHITECTURE.md          当前已实现架构
docs/adr/                重要架构决定
docs/specs/              复杂功能规格
docs/experiments/        实验设计和原始结果
tests/                   可执行行为证据
LEARNING_LOG.md          个人理解、错误与复盘
```

聊天不是最终可信来源。稳定决定必须进入受 Git 管理的项目文件。

---

## 19. Change Control（变更控制）

下列变化必须先讨论并记录：

- 改变本版 Charter v1.2 的宏观业务边界或已确认设计基线；
- 从单商家改为多商家；
- 增加多仓库或多币种；
- 拆分微服务；
- 改变 Custom User、Username 规范、Session/CSRF 或 Admin 权限表达；
- 改变 Decimal 金额合同、0 运费或含税价边界；
- 将单 Vue 工程拆为多个独立应用；
- 改变扣库存时机；
- 改变订单或支付状态机；
- 改变 Order/PaymentAttempt Idempotency-Key 语义或业务事实不硬删除原则；
- 更换核心事实来源；
- 将 Kafka/Kubernetes 加入完成标准；
- 删除三条旗舰主线中的任何一条；
- 取消 RabbitMQ + Celery 支付必做要求、扩大其业务用途，或将 Elasticsearch 从条件性技术升级为核心必做项；
- 将 Distributed Lock、Transactional Outbox 或第三方 Sandbox 升级为核心必做项；
- 改变 1200 小时四类 v1.1 分配时，应在最近一次范围与证据审计中记录原因、影响和取舍。

普通字段、页面样式和局部实现不需要 ADR。

### v1.2 支付与 MQ 范围调整记录（2026-10-01）

- 决策来源：项目拥有者明确要求将 MQ 写入支付模块范围与总项目文档，并升级为必做；同时明确本期不准备接入支付沙盒。
- 业务问题：现有支付事件处理依赖同步请求、用户查单或网关重投；已入箱事件处理失败、进程中断或发布失败后，需要明确的后台执行与恢复路径。
- 不变量：订单金额取原快照、UNKNOWN 不重新付款、成功与库存核销同事务、消费者重复执行不产生第二次副作用、持久事件可恢复；MQ 不替代 MySQL。
- 最小设计：先补 Inbox 一次性恢复基线，再使用 RabbitMQ + Celery 投递事件编号并复用现有处理器；每事件独立事务、提交后投递、有界重试和失败留存，扫描补漏继续存在。
- 替代与取舍：只使用扫描的依赖较少，但恢复由扫描触发；MQ 提供及时后台执行，代价是新增 Broker/Worker、消息确认、重复投递和发布窗口。必做决定不免除基线、方案对比与真实故障证据。
- 必需证据：隔离真实 RabbitMQ/Worker 的投递、重试、重复消费、中断及恢复；核对订单、Attempt、Event、库存与退款模拟，不能用同步 Mock 或即时任务模式声称完成集成。
- 范围限制：本期保留 Mock Gateway + CNY + 简单取消/退款模拟；不接第三方 Sandbox/Production，不新增复杂售后、通用异步平台或微服务；Outbox、搜索、分布式锁仍条件性。
- 状态：已确认范围，尚未实施 MQ；本次只编辑文档，未新增依赖、修改运行配置、迁移数据库或运行业务测试，既有未提交改动保留，未 commit/push。

### v1.1 范围调整记录（2026-09-18）

- 保留最窄真实交易闭环与三条旗舰深度主线；完整指纵向状态链完整，不再等同于电商功能数量齐全。
- 完整 Vue Admin、Coupon、Favorite、Review 和复杂搜索 UI 移出 Interview Core；普通运营 CRUD 使用 Django Admin。
- 学习方式改为 `Depth / Supporting / Boilerplate` 三层，并为普通 CRUD 建立 Fast Track；快速交付不取消权限、约束、关键失败测试和 Diff Review。
- Phase 2A 重排为 MVP-1 Cart、MVP-2 Address、MVP-3 Order/Inventory Reservation、MVP-4 Mock Payment/Completion。
- 1200 小时保留为长期上限而非必须耗尽的工时；新增投入优先保护 Interview Core、三条深度主线和 Evidence Pack。

---

## 20. Immediate Next Step（下一步）

Redis Phase 4J暂时收束，支付/MQ已有同步Mock、真实异步链路、有界重试和人工恢复证据。Phase 6A/B与6C-1/2/3历史证据保留，6C-4保存ADR，6C-5正式条件UPDATE及失败回滚/重试通过。当前状态见CONTEXT第46–52节。

1. 选型 ADR 已完成；继续练习独立解释影响行数 0/1、完整事务回滚、限购/幂等各自职责与证据边界，不把一次正确预测当成全部学习验收。
2. 先用6C-5真实1205证据解释“语句失败与整单回滚”，然后接最小活动查询/下单HTTP。HTTP接入前确定顾客等待预算与错误响应；本片沿用的50秒只是本机会话值，不是请求截止时间。不自动追加负载矩阵。
3. 支付MQ继续保留本期人工兜底，不横向新增异步功能或周期接管；既有MQ/Worker证据不能当成本轮已重跑。
4. 秒杀下单HTTP/前端与开发数据库迁移属于后续独立切片；现有开发数据操作另行确认，不进入第三方Sandbox或外部账号。
