# Project One — Domain Context v1.40

## 0. 文档状态

- 当前阶段：10A最终版本/证据验收完成；交易与三主线工程证据收束，Lua准入与ES隔离扩展、9B/9C基础部署/认证已有证据；本人理解与简历待完成
- 文档版本：v1.40
- 更新日期：2026-10-12
- 范围来源：`PROJECT_CHARTER.md` v1.17、已确认支付MQ范围（第33节）、Lua后续采用（第63节）、搜索7A–7D与部署9A–9C，以及第71节本期收尾决定；第55节暂缓保留为历史
- 当前内容：记录项目拥有者已经确认的稳定术语、业务规则、技术采用边界和实验候选

本文件用于统一 Project One 中的业务与工程语言，不替代 `PROJECT_CHARTER.md`。如果两份文件发生冲突，以 `PROJECT_CHARTER.md` 和后续正式 ADR 为准。

第 13 节起的各阶段记录保留当时的范围、实现与证据；其中较早的 MQ 条件性采用和 Sandbox 可选计划，已由第 33 节的新范围决定更新，不代表当前采用边界。

第34节的未分类ERROR人工恢复为Phase 5A历史基线；当前处理门槛按第35节执行。第36/37节加入真实MQ与有界后台重试，第38节新增人工发布补漏，第39节补人工过期领取修复与真实Worker中断证据；没有周期自动补漏或心跳续租。

第40节补支付成功与到期关闭的真实Order记录锁等待证据，并记录未来学习资料使用项目分层飞书Wiki的明确偏好；本期未写入飞书。

本文件使用以下状态：

| 状态 | 含义 |
|---|---|
| Confirmed | 已由项目拥有者确认，可作为后续设计基线 |
| Required Later | 属于项目必做范围，但要等业务基线形成后再实现 |
| Experiment Candidate | 已确认优先比较的技术候选，但必须用真实实验决定是否成为最终实现 |
| Conditional | 只有出现真实问题且工时审计允许时才引入 |
| Pending | 已识别，但具体规则尚未确认 |
| Out of Scope | 第一版及核心深度版明确不做 |

---

## 1. 项目与业务范围

| English Term | 通俗中文 | Project One 中的具体含义与边界 |
|---|---|---|
| Project One | 项目一号 | 面向中国 Backend / Software Engineering Internship 的后端重型 Django 电商项目。目标是形成完整闭环和可验证的技术深度，而不是堆积页面或技术名称。`Confirmed` |
| Backend-heavy | 后端重型 | 主要学习与面试价值来自后端业务规则、数据正确性、故障处理和可复现实验；前端负责形成完整、可操作的业务入口。`Confirmed` |
| Single-merchant B2C | 单商家面向消费者 | 系统只有一个经营主体，直接向普通消费者销售商品。不包含 Seller 入驻、店铺、佣金、分账或商家结算。单商家不等于只有一个管理员或一种商品。`Confirmed` |
| Digital Accessories | 数码配件 | 普通、可重复计数的实体配件，例如鼠标、键盘、充电器、数据线和手机支架。不处理整机序列号、设备激活、维修和复杂保修。`Confirmed` |
| Single Warehouse | 单仓库 | 所有 SKU 在业务上只有一份库存，不做多仓选择、跨仓调拨或按仓拆单。核心版不建立显式 `Warehouse` 表，每个 SKU 对应一条 Inventory。`Confirmed` |
| Mainland China Scenario | 中国大陆业务场景 | 使用中文数据、国内收货地址、CNY 和中国时区等基础规则；不代表系统已经满足真实经营、税务或监管要求。`Confirmed` |
| CNY | 人民币币种 | 商品、优惠、订单、支付和退款模拟统一使用人民币。Django/MySQL 使用 `DecimalField(max_digits=12, decimal_places=2)`，Python 使用 Decimal，API 使用金额字符串，统一按人民币分位和 `ROUND_HALF_UP` 舍入；Provider Adapter 在边界按协议转换。`Confirmed` |
| Minimum Business Loop | 最小业务闭环 | 浏览商品 → Session 登录 → 购物车 → 地址 → 幂等创建 Order/快照并预占库存 → Mock 支付 → 核销库存 → 模拟发货 → 确认收货。它必须先实际运行，再展开高级基础设施和旗舰实验。`Confirmed` |
| Non-goal | 明确不做的范围 | 主动排除、用于保护项目重点的功能。它不是“以后忘了做”，而是当前版本有意不承担的工作。 |

---

## 2. 用户、身份与权限

| English Term | 通俗中文 | Project One 中的具体含义与边界 |
|---|---|---|
| Guest | 游客 | 未登录访问者，只能浏览、搜索和查看商品信息；不能创建服务端购物车、地址、订单或支付记录。`Confirmed` |
| Customer | 顾客 | Custom User 中的普通已认证用户，可以管理自己的购物车、地址、订单和测试支付记录，只能访问自己的个人业务数据。`Confirmed` |
| Admin | 管理员 | `is_staff=True` 并由 Django Groups/Permissions 授予卖家侧能力的用户，负责商品、SKU、库存、订单、模拟物流及已启用运营能力。`Confirmed` |
| Operator | 运营职责 | 不是第四种顶层角色或重复的 role 字段，而是 Admin 通过 Django 权限承担的日常运营职责。`Confirmed` |
| Authentication | 身份认证 | 系统判断“你是谁”的过程。第一版 Customer 与 Admin 都使用唯一 Username + Password 登录。`Confirmed` |
| Custom User | 自定义用户模型 | 项目第一次迁移前使用 `Custom User(AbstractUser)`；以后不在已有业务迁移上更换用户模型。`Confirmed` |
| Username | 用户名 | 第一版唯一登录标识。trim 后转小写，长度 3–30，匹配 `^[a-z][a-z0-9_]{2,29}$` 并保持数据库唯一；收货手机号不用于登录，显示昵称以后使用独立字段。`Confirmed` |
| Password / Password Hash | 密码与密码哈希 | 用户输入密码证明账号使用权；Django 只保存不可直接还原的密码哈希，任何代码、日志或数据库字段都不得保存明文密码。`Confirmed` |
| Session Cookie + CSRF | 会话 Cookie 与跨站请求伪造保护 | Vue Web 登录后由 Django Session 维持身份，修改数据的请求必须带 CSRF 防护；部署目标为同源访问，不在 localStorage 中长期保存 JWT。`Confirmed` |
| Authorization / Permission | 授权与权限 | 系统在知道用户身份后，判断“你能做什么、能访问哪些数据”。隐藏前端按钮不能替代 Django 后端权限检查。`Confirmed` |
| Address | 收货地址 | 结构化保存收货人、省、市、区、详细地址和收货手机号。一个 Customer 可保存多个地址但最多一个默认地址；下单时复制为不可变地址快照。`Confirmed` |
| Recipient Phone | 收货手机号 | Address 和订单地址快照中的联系字段，不作为登录标识。输入可去除空格、连字符和 `+86`，最终保存为匹配 `^1[3-9]\d{9}$` 的 11 位字符串；后端校验为准，只用模拟数据，不发短信、不验真。`Confirmed` |
| Time Zone | 时间规则 | Django 开启时区支持，持久时间统一为 UTC，Vue 顾客端和管理端按 `Asia/Shanghai` 展示。`Confirmed` |

---

## 3. 商品目录

| English Term | 通俗中文 | Project One 中的具体含义与边界 |
|---|---|---|
| Catalog | 商品目录 | 顾客能够浏览和搜索的 Category、Product、SKU、图片、价格及可售状态集合。`Confirmed` |
| Category | 商品分类 | 第一版只做单层分类；一个 Product 属于一个 Category，例如“键盘”“鼠标”“充电配件”。`Confirmed` |
| Product / SPU | 商品概念 | 保存共享名称、介绍和图片等信息，例如“无线鼠标 M1”；它不是定价、库存和最终购买单位。`Confirmed` |
| SKU | 可购买规格 | 顾客实际选择并购买的规格，例如“无线鼠标 M1 / 黑色”。SKU code 全局唯一，每个 SKU 可以拥有独立价格、可售状态和库存。`Confirmed` |
| SKU Attributes | SKU 规格属性 | 常用且需要查询的规格使用普通字段，仅展示型差异可以使用 JSON；不建设通用 EAV 属性系统。`Confirmed` |
| Availability | 可售状态 | 商品或 SKU 当前是否允许产生新订单。下架商品不能产生新订单，但历史订单仍可查看。`Confirmed` |
| Price | 价格 | SKU 的当前销售金额。使用两位小数 Decimal；购物车显示价格不是最终事实，下单时必须由后端重新读取和计算。`Confirmed` |
| Favorite | 商品收藏 | Customer 可以收藏 Product，作为最小闭环后的增量能力；它不改变商品可售性、价格、库存或订单事实。`Confirmed` |

### Product 与 SKU 的具体例子

```text
Product：无线鼠标 M1

SKU A：无线鼠标 M1 / 黑色 / ¥129.00 / 库存 30
SKU B：无线鼠标 M1 / 白色 / ¥139.00 / 库存 20
```

顾客购买的是 SKU，而不是抽象的 Product。
一个 Product 没有任何可售 SKU 时不能被购买；Product/SKU 下架不改变历史 OrderItem 快照。

---

## 4. 库存

| English Term | 通俗中文 | Project One 中的具体含义与边界 |
|---|---|---|
| Inventory | 库存 | 每个 SKU 一条记录，至少包含 `available_stock` 和 `reserved_stock`；没有 Warehouse 表。MySQL 保存最终持久库存事实。`Confirmed` |
| Stock Invariant | 库存不变量 | 无论发生多少并发、重复或失败请求，available 与 reserved 都不能小于 0，成功购买数量不能超过可售库存。`Confirmed` |
| Inventory Reservation | 库存预占 | 创建 Order 时在同一 MySQL 事务中原子地把数量从 available 转为 reserved；默认保留 15 分钟。每次 Reservation 拥有唯一业务身份。`Confirmed` |
| Inventory Deduction / Commit | 库存核销 | PaymentAttempt 成功后从 reserved 中核销已售数量并写入不可硬删除的 InventoryMovement；相同 Reservation 最多核销一次。`Confirmed` |
| Inventory Release | 库存释放 | 待支付取消或过期时将 reserved 返还 available；已支付未发货取消时恢复库存。相同业务动作最多释放一次。`Confirmed` |
| Inventory Movement | 库存流水 | 记录预占、核销、释放、恢复和人工调整等库存变化及唯一业务来源，用于审计和对账，不得作为普通记录硬删除。`Confirmed` |
| Overselling | 超卖 | 成功订单数量超过实际可售库存。例如只有 10 件，却有 12 个订单购买成功。这是库存旗舰主线需要先稳定复现、再解决的问题。`Required Later` |
| Concurrency | 并发 | 多个请求在相近时间同时读取或修改同一份数据。并发测试必须使用真实请求或任务，而不能只靠推测。 |
| Flash Sale | 秒杀 | 在短时间内让大量请求竞争有限 SKU 库存的销售场景。每位 Customer 对每个 FlashSaleItem 最多一笔成功购买且数量为 1；最终事实由 MySQL 保证。`Required Later` |
| Atomic Conditional Update | 原子条件更新 | 让 MySQL 在 `available_stock >= quantity` 时一次完成库存变化。它是当前优先实验候选，必须与行锁方案实测比较后才能称为最终选择。`Experiment Candidate` |
| select_for_update | 数据库行锁候选 | 在事务中锁定目标库存行再检查和修改。它必须与原子条件更新比较正确性、锁等待、死锁、吞吐和延迟。`Experiment Candidate` |

---

## 5. 购物车与订单

| English Term | 通俗中文 | Project One 中的具体含义与边界 |
|---|---|---|
| Cart | 购物车 | 每个 Customer 同时只有一个活动 Cart。它方便用户准备订单，但不是价格、优惠或库存的最终事实来源。`Confirmed` |
| CartItem | 购物车项 | Cart 中的一种 SKU 和期望购买数量，范围 1–99；同一 Cart 中 `(cart, sku)` 唯一，重复加入时更新数量。`Confirmed` |
| Cart Checkout | 购物车结账 | 后端重新读取价格与库存；在 Order、快照和库存预占的同一事务末尾移除本次 CartItem，任一步失败则整体回滚并保留用户选择。`Confirmed` |
| Order | 订单 | 服务端根据顾客选择、当前规则和快照创建的交易记录。使用内部大整数主键和不可猜测的公开订单号，总金额不能由前端决定。`Confirmed` |
| OrderItem | 订单项 | 保存 SKU、数量以及下单时 Product/SKU 名称、规格、单价和折扣快照；历史内容不随当前商品变化。`Confirmed` |
| Snapshot | 快照 | 把下单当时的重要信息复制到订单中，例如商品名、SKU 属性、单价和收货地址。以后修改 Product 或 Address，不得改变历史订单。`Confirmed` |
| Money Formula | 金额公式 | `subtotal = Σ(unit_price_snapshot × quantity)`；`discount` 为一张有效 Coupon 的固定满减或 0；`shipping_fee = 0.00`；`total = max(subtotal - discount, 0.00) + shipping_fee`。全程使用 `DecimalField(max_digits=12, decimal_places=2)` / Python Decimal、CNY 和 `ROUND_HALF_UP`。`Confirmed` |
| Order Idempotency | 创建订单幂等 | 创建接口必须带 Idempotency-Key。同一 Customer、端点和 Key 配合相同规范化请求时返回原 Order；配合不同请求时返回 `409 Conflict`。`Confirmed` |
| Order State Machine | 订单状态机 | 合法转换为 `PENDING_PAYMENT → PAID/CANCELED/EXPIRED`、`PAID → SHIPPED/CANCELED`（取消仅限未发货）、`SHIPPED → COMPLETED`；`COMPLETED/CANCELED/EXPIRED` 都是不得回退或复活的终态。PaymentAttempt 失败不新增 Order 的付款失败状态。`Confirmed` |
| Order Expiration | 未支付过期 | `PENDING_PAYMENT` 默认 15 分钟后由到期扫描尝试关闭并幂等释放预占库存；支付结果未确认时保留预占等待查单。时间字段不会自动释放库存，支付主线必做 MQ 不改变此业务规则。`Confirmed` |
| Cancellation | 简单取消 | `PENDING_PAYMENT` 可取消并释放预占、不退款；`PAID` 仅在未发货时可取消，并恢复库存和创建一次 Refund Simulation；`SHIPPED/COMPLETED` 拒绝取消。`Confirmed` |
| Invariant | 不变量 | 无论请求顺序、并发或依赖故障怎样变化，都必须一直成立的业务事实，例如 `stock >= 0`。 |
| Idempotency | 幂等 | 同一个业务请求或事件重复执行，最终不会产生重复业务副作用。例如重复取消不能重复释放库存。 |

---

## 6. 支付与物流

| English Term | 通俗中文 | Project One 中的具体含义与边界 |
|---|---|---|
| Payment Attempt | 支付尝试 | 一个 Order 可以保留多次历史尝试，但同时最多一个 `CREATED/PENDING/UNKNOWN` 活跃 Attempt；演示版默认最多 3 次主动尝试并由配置控制。`Confirmed` |
| Payment State Machine | 支付状态机 | `CREATED → PENDING/UNKNOWN/SUCCEEDED/FAILED/EXPIRED/CANCELED`，`PENDING → UNKNOWN/SUCCEEDED/FAILED/EXPIRED/CANCELED`，`UNKNOWN → PENDING/SUCCEEDED/FAILED/EXPIRED/CANCELED`；`SUCCEEDED/FAILED/EXPIRED/CANCELED` 无正常转换。矛盾的迟到事件保留在 Inbox 并标记异常，不覆盖已确认事实；任何 Attempt 成功后禁止新建。`Confirmed` |
| UNKNOWN | 支付结果未知 | 网络超时只表示本地不知道 Provider 是否已经成功。必须复用原 Attempt 查单或对账，不能把 UNKNOWN 当成失败后直接重付。`Confirmed` |
| Payment Attempt Idempotency | 支付尝试幂等 | 创建 Attempt 必须带 Idempotency-Key；同 Key 同请求返回原 Attempt，同 Key 不同请求返回 `409 Conflict`。只有前次明确失败、过期或取消后才可用新 Key 创建。`Confirmed` |
| Payment Network Boundary | 支付网络边界 | PaymentAttempt 的幂等记录先在短事务中提交，再调用 Gateway；不能持有订单或库存数据库锁等待网络。`Confirmed` |
| Payment Event | 支付事件 | Provider 通知系统发生了某件事的数据。以 `(provider, event_id)` 唯一识别，并保存接收时间、原始 payload、验证/处理状态和错误信息。`Confirmed` |
| Webhook | 服务端回调 | 支付 Provider 主动向 Django Payment API 发送的 HTTP 请求。前端“支付成功”页面不能替代 Webhook 或服务端支付事实。`Confirmed` |
| Webhook Inbox / Event Inbox | 回调收件箱 | 先可靠保存原始事件，再在明确事务边界内校验并处理；重复、乱序、验证失败和处理失败都不能丢失原文。保留同步Mock baseline；Phase 5C-1至4已验证真实MQ、有界重试、人工发布补漏与人工过期领取恢复，没有周期自动补漏。`Confirmed` |
| Mock Payment Gateway | 模拟支付网关 | 项目后端自己控制的测试替身，必须制造成功、失败、处理中、超时/UNKNOWN、重复、乱序以及错误金额/币种/签名。场景选择器只在测试或受保护演示环境开放。`Confirmed` |
| Sandbox Provider | 第三方支付测试环境 | 本期 No-Go，不接支付宝、Stripe 等第三方 Sandbox，也不以它作为支付主线完成要求；以后只有项目拥有者重新明确决定才评估。`Out of Scope` |
| Provider Adapter | 支付供应商适配层 | 统一创建支付、查单、验签、事件解析和发起模拟退款；Mock 必须实现同一合同，Provider 字段不得渗入 Order 领域。`Required Later` |
| Refund Simulation | 模拟退款 | 独立于成功 PaymentAttempt 保存退款模拟生命周期，不转移真钱。用于已支付未发货取消和终态后的迟到支付；相同业务动作最多创建一次。`Confirmed` |
| Reconciliation | 对账 | 比较本地订单/支付记录和 Provider 事件，发现漏单、重复或状态不一致。第一版只学习和验证必要思路，不建设商业级对账平台。 |
| Simulated Logistics | 模拟物流 | 实现真实的内部发货权限、状态和记录，但不连接真实物流公司。Admin 可以模拟发货，Customer 可以查看状态和确认收货。`Confirmed` |

---

## 7. 数据、缓存与架构

| English Term | 通俗中文 | Project One 中的具体含义与边界 |
|---|---|---|
| Source of Truth | 最终事实来源 | 多个系统数据冲突时，最终以哪个系统为准。MySQL 是订单、支付和核心库存的最终持久事实来源。`Confirmed` |
| MySQL | 关系型数据库 | 保存核心业务事实，并提供事务、约束、索引和行锁等能力。库存和订单关键语义不能用 SQLite 测试结果代替。`Confirmed` |
| Redis | 内存数据系统 | Phase 4A 已实现可选的本地商品详情缓存并通过真实集成测试；热点保护、原子准入仍待后续实验。不能成为订单、支付和核心库存的唯一事实来源。 |
| Cache | 缓存 | 保存可重新获得的数据副本，以减少数据库压力和响应时间。缓存更快不代表缓存一定正确。 |
| Cache-Aside | 旁路缓存模式 | 商品详情公开 JSON 未命中读 MySQL 并回填；Phase 4B 正常模型 save 在 `transaction.on_commit` 切换全目录随机代号，Key 含代号。每次读取仍检查 MySQL 上架状态；列表/分类不缓存。 |
| Cache Consistency | 缓存一致性 | Phase 4A 复现旧回填；Phase 4B 已验证成功换代后新请求不选旧代，代号在首条详情 SQL 前捕获。换代失败、提交后崩溃等仍不保证，非强一致。见第 22 节。 |
| Cache Experiment Parameters | 缓存实验参数 | 具体 Key 结构、基础 TTL 与抖动范围、空值缓存及热点保护参数必须由 baseline 和实验决定。`Experiment Candidate` |
| Resilience | 故障韧性 | Redis 或其他辅助系统故障时，核心系统按明确规则降级、恢复或对账，不能丢失核心事实；下单仍重新读取 MySQL 权威价格与库存。`Confirmed` |
| Redis Lua | Redis Lua 脚本 | 拥有者2026-10-08明确当前先不加入、后续评估；不阻塞MySQL秒杀/HTTP收尾。以后若采用，须验证并发、失败窗口和MySQL对账。`Deferred by owner / Evaluate later` |
| Modular Monolith | 模块化单体 | 整个 Django/DRF 后端是一个部署单元，但内部按 Accounts、Catalog、Inventory、Ordering、Payments 等业务职责划分。第一版不拆微服务。`Confirmed` |
| Django App | Django 应用模块 | 第一批固定为 `accounts`、`catalog`、`inventory`、`ordering`、`payments`；闭环后按需增加 `promotions`、`engagement`。支付异步处理使用必做 RabbitMQ + Celery，不要求拆微服务或建立通用异步平台；Search 继续受 Gate 控制。`Confirmed` |
| Vue Application Boundary | Vue 工程边界 | 使用一个 Vue 3 + TypeScript/Vite 工程，内部提供 Customer 与 Admin 两套路由和 Layout，并共享认证、API Client、类型和基础组件。`Confirmed` |
| Primary Key / Public ID | 内部主键与公开编号 | Model 内部统一使用大整数主键；Order 对外使用不可猜测且稳定的公开订单号，不能把连续自增 PK 当业务编号暴露。`Confirmed` |
| No Hard Delete for Facts | 业务事实不硬删除 | Order、PaymentAttempt、Payment Event、RefundSimulation、InventoryMovement 等审计事实通过状态或归档保留，不作为普通记录物理删除。`Confirmed` |
| Vertical Slice | 纵向切片 | 一次完成一个能穿过界面/API、业务逻辑和数据库的可验证小流程，而不是先批量写完所有 Model，再批量补 API。`Confirmed` |

---

## 8. 工程证据与学习方式

| English Term | 通俗中文 | Project One 中的具体含义与边界 |
|---|---|---|
| Baseline | 基线 | 引入优化或高级技术前，先保留最简单、能够运行和测量的实现，作为比较起点。 |
| Failure Reproduction | 失败复现 | 用固定条件稳定重现超卖、重复回调、缓存回源等问题，而不是只说“理论上可能发生”。 |
| Evidence Pack | 工程证据包 | 记录业务问题、不变量、基线、失败复现、候选方案、决定、实现、测试、故障注入、数据、取舍和面试讲解。三条旗舰主线都必须拥有。`Confirmed` |
| Confirmed Rule | 已确认规则 | 项目拥有者已经选择的业务行为或架构边界，例如创建订单时预占库存；实现不得自行改变。 |
| Experiment Candidate | 实验候选 | 已确定优先比较的技术方向，例如 MySQL 原子条件更新；在真实数据出来前不能称为最终胜出方案。 |
| Validated Decision | 已验证决定 | 引用可复现实验、测试或故障证据后形成的技术结论，必须写入 ADR 或 Evidence Pack。 |
| Trade-off | 取舍 | 一个方案获得了什么，同时付出了什么，例如更强一致性可能带来更高延迟或更低吞吐。 |
| ADR | 架构决定记录 | 保存重要架构选择、候选方案、理由和后果的短文档。普通字段或页面样式不需要 ADR。 |
| Test Oracle | 测试判定标准 | 测试用什么预期判断行为正确。用户必须先确认核心行为，AI 不能同时擅自决定完整实现和正确答案。 |
| Learning Mode | 学习模式 | 用于核心业务和新技术。用户拥有业务规则、不变量、测试预期、架构取舍和实验结论，AI 按帮助阶梯提供支持。`Confirmed` |
| Pair Mode | 结对模式 | 用于已理解但不熟练的工作，例如部署配置、API 集成和错误处理；用户与 AI 共同完成并验证。 |
| Delivery Mode | 交付模式 | 用于低风险、机械和重复工作。AI 可以主要完成 HTML、CSS、常规 Vue 页面和 CRUD 样板，但用户必须读懂、运行、调试并能够修改。`Confirmed` |
| Focused Hour | 专注小时 | 实际投入学习、设计、编码、测试、调试、实验或复盘的时间；无效等待不计算。1200 小时仅作为长期投入上限，不要求耗尽；按 MVP 阶段、范围变化和 Evidence Pack 风险进行审计。`Confirmed` |
| Experiment Protocol | 实验记录协议 | 保存代码/Git 与依赖/服务版本、固定数据集和 seed、并发模型与数量、预热、持续时间、运行次数、硬件/OS/容器资源、p50/p95/p99、吞吐、错误率、实验前后不变量检查、所有有效运行和原始输出；不能只保留最好的一次。`Confirmed` |

---

## 9. 必做异步能力与条件性技术

RabbitMQ + Celery已由项目拥有者列为支付主线必做，Phase 5C-1正常/重复消费、C2有界后台重试、C3人工发布补漏与Broker停机恢复、C4人工过期领取修复与Worker中断已用真实隔离服务观察验证。用途限定支付Inbox，没有周期自动补漏或心跳续租。保留同步Mock baseline，不建立通用异步平台。

Elasticsearch、Distributed Lock 和 Transactional Outbox 仍属条件性技术，只有出现真实问题、比较过简单方案并通过范围与证据审计后才引入；No-Go（暂不采用）也是有效工程结论。MQ 必做不自动使 Outbox 必做；数据库提交后未成功投递的事件必须有可验证的恢复路径，具体方案仍待比较。一旦引入相关技术，必须进行真实集成与故障验证。

| English Term | 通俗中文 | 当前采用边界 |
|---|---|---|
| Celery | Python 异步任务框架 | 支付主线必做，真实Linux Worker消费Inbox并按数据库计划安排短ETA重试；发布/过期领取人工恢复与选定中断窗口已验证，业务副作用由MySQL保护。`Verified in isolated scenarios` |
| RabbitMQ | 消息代理 | 支付主线必做，已验证正常/重复/非法事件、短ETA重试、Broker停机与Worker退出后的原未确认消息重投；消息成功不等于支付成功，没有生产运行证据。`Verified in isolated scenarios` |
| Elasticsearch | 搜索引擎 | MySQL 搜索基线在中文分词、相关性或测量结果上出现明确不足。`Conditional` |
| Distributed Lock | 分布式锁 | 默认不用；只有数据库约束、条件更新、行锁或 Redis Lua 等简单方案经证据证明不足时才评估。`Conditional` |
| Transactional Outbox | 事务发件箱 | 默认不用；只有已采用可靠消息，且数据库提交与消息发布的一致性窗口成为真实问题时才评估。`Conditional` |

Docker Compose、Linux、Nginx、Gunicorn、CI、日志、健康检查和性能基线属于计划内必要工程能力，不属于本节的条件性技术。

---

## 10. 已确认决定索引

### 10.1 原 12 组显式决定

1. 金额：MySQL/Django 使用 `DecimalField(max_digits=12, decimal_places=2)`，Python 使用 Decimal，API 使用字符串，按人民币分位和 `ROUND_HALF_UP` 舍入；CNY、0 运费、含税价且无税务引擎。
2. 仓库：单仓、无 Warehouse 表，SKU 与 Inventory 一对一。
3. 状态机：Order 与 PaymentAttempt 使用第 5、6 节已经列明的状态、合法转换和终态；不建立重复维护状态的独立 Payment 表，Refund Simulation 使用独立记录保存生命周期。
4. 库存时机：创建 Order 的 MySQL 事务内预占，事务外调用 Gateway，支付成功核销，取消或超时幂等释放。
5. 支付尝试：每单最多一个活跃 Attempt，默认最多 3 次主动尝试且可配置；Idempotency-Key 区分重放与新尝试；UNKNOWN 先查单；成功后禁止新建。幂等记录先在短事务中提交，再调用 Gateway，不能持有订单或库存数据库锁等待网络。
6. 支付集成：Mock Gateway与RabbitMQ + Celery支付异步处理必做；Phase 5C-1至4已验证基础链路、有界重试、人工发布补漏与人工过期领取/Worker中断恢复，没有周期自动补漏。第三方Sandbox本期No-Go，以后须由项目拥有者重新决定；Production Payment不做。
7. Vue：一个 Vue 3 + TypeScript/Vite 工程，两套路由树与 Layout，共享认证、API Client 和类型。
8. Django/Schema：首批 App 为 accounts、catalog、inventory、ordering、payments；使用 Custom User、大整数内部主键、公开 Order 编号、业务事实不硬删除，字段和索引随纵向切片审查。
9. 手机号：输入先去除空格、连字符和 `+86`，再规范化为匹配 `^1[3-9]\d{9}$` 的 11 位字符串；后端为最终校验边界，不发短信、不验真。
10. Username：Customer/Admin 都使用 Username + Password 登录；Username trim、转小写、3–30 位、匹配 `^[a-z][a-z0-9_]{2,29}$`、数据库唯一，显示昵称独立，手机号不用于登录。
11. 次要业务：单层 Category 且一个 Product 属于一个 Category；闭环后实现固定满减且不可叠加、具有效期和总额度、每位用户对同一 Coupon 最多成功领取和使用一次的 Coupon；实现 Favorite；Order 完成后每个 OrderItem 最多一条可修改 Review。
12. 取消：待支付取消/过期释放预占不退款；已支付未发货取消恢复库存并产生一次退款模拟；发货后拒绝；终态与迟到支付都按幂等规则处理。

### 10.2 同时确认的 17 项跨领域方向

1. 从第一次迁移使用 Custom User；Customer 是普通用户，Admin 使用 `is_staff + Groups/Permissions`。
2. Web-only 第一版采用 Session Cookie + CSRF，并以同源部署为目标。
3. Product 与 SKU 分离，SKU 是购买、定价和库存单位。
4. SKU 规格采用“重要可查询字段 + 展示型 JSON”，不使用通用 EAV。
5. Address 使用结构化国内地址，允许多地址但最多一个默认地址。
6. Order 保存独立、不可变的商品、价格和地址快照。
7. 每个 Customer 一个活动 Cart，同 SKU 唯一、数量 1–99；在 Order/预占事务末尾清理对应项，失败时整体回滚并保留 Cart。
8. 金额公式由服务端使用 Decimal 计算，折扣后不得小于 0。
9. Order 创建使用 Idempotency-Key，同 Key 同请求复用，异请求返回 409。
10. Inventory 使用 available/reserved、Reservation 和不可硬删除的 Movement。
11. MySQL 原子条件更新是优先实验候选，必须与 `select_for_update()` 比较后才能验证定案。
12. 秒杀按 Customer + FlashSaleItem 限制最多一笔成功购买，数量固定为 1。
13. Redis 可做缓存和流量准入，但 MySQL 始终保存最终订单和核心库存事实。
14. 目录缓存采用 Cache-Aside、事务提交后失效、TTL 抖动和 Redis 故障回退 MySQL；具体参数由实验决定。
15. Provider Adapter 统一创建支付、查单、验签、事件解析和发起模拟退款，Mock 实现同一合同。
16. Event Inbox 使用 `(provider, event_id)` 唯一并保留原始事件；已有同步 Mock baseline，后续必做 RabbitMQ + Celery 异步处理、有界重试与恢复。支付、订单和库存副作用仍由 MySQL 事务、状态机与幂等约束共同保护。
17. 三条旗舰实验统一记录代码/Git 与依赖/服务版本、数据集和 seed、并发模型与数量、预热、持续时间、运行次数、硬件/OS/容器资源、p50/p95/p99、吞吐、错误率、实验前后不变量、所有有效运行和原始输出。

以上都是已确认的规则或方向，不再作为 `Pending`。其中技术优胜方案和具体实验参数仍必须经过真实证据，不能因为 Charter 已 Final 就宣称已经验证。

---

## 11. 明确排除的范围摘要

- 多商家、Seller 入驻、店铺、佣金、分账和商家结算；
- 多仓库、跨仓调拨和多币种；
- 游客购物车与游客结账；
- 真实短信、真实物流和真实资金；
- 复杂退货、换货、商品验收、售后工单和复杂部分退款；
- 推荐系统、社交 Feed 和原生移动 App；
- 微服务、Kubernetes、Service Mesh 和 Kafka 核心链路；
- 第三方 Production Payment、真实资金与真实退款；
- 本期第三方支付 Sandbox；以后须由项目拥有者重新明确决定，不能自动恢复为可选任务；
- 动态运费、税务引擎、多层 Category、复杂优惠规则和通用 EAV 商品属性系统；
- 商业产品级 UI 动画与视觉精修。

---

## 12. 按纵向切片确定的实现细节

Charter v1.2 及第 33 节支付范围更新是当前设计基线，但下列机械性或证据驱动细节不需要在 Day 0 假装全部冻结：

- 每个 Model 的非业务字段名、精确长度、外键删除动作和查询驱动索引；
- API URL、分页、过滤和错误详情等局部契约；Order 与 PaymentAttempt 的幂等规则不得因此改变；
- Redis Key、TTL 秒数、抖动范围、热点阈值和最终 Lua 路径；
- MySQL 隔离级别、条件更新与行锁在目标环境中的实测结果；
- 性能目标值；必须先得到 baseline，再根据证据设定；
- 搜索、分布式锁和 Outbox 的条件技术 Gate；支付 MQ 的具体重试、恢复与故障实验设计；若项目拥有者以后重新决定，再记录 Sandbox 的范围变化。

这些细节在对应 Vertical Slice、ADR 或实验计划中确认。它们可以细化实现，但不得绕过第 10 节已确认的不变量和边界。

---

## 13. Accounts Session Authentication + CSRF Vertical Slice

### 13.1 API 契约

| Method | Path | 成功结果 | 认证与 CSRF |
|---|---|---|---|
| `GET` | `/api/auth/csrf/` | `204 No Content` 并设置 `csrftoken` Cookie | Guest 可访问 |
| `POST` | `/api/auth/register/` | `201 Created`，创建普通用户但不自动登录 | Guest 可访问；必须通过 CSRF 校验 |
| `POST` | `/api/auth/login/` | `200 OK`，返回 `id/username/is_staff` 并创建 Session | Guest 可访问；必须通过 CSRF 校验 |
| `GET` | `/api/auth/me/` | `200 OK`，返回当前 Session 用户 | 必须通过 Session Authentication；安全 GET 不要求 CSRF Header |
| `POST` | `/api/auth/logout/` | `204 No Content` 并清除 Session；重复请求保持 `204` | Guest/Customer/Admin 均可调用；必须通过 CSRF 校验 |

### 13.2 已确认安全语义

- 注册、登录和退出都是会改变服务端状态的 Web 请求，必须显式执行 CSRF 校验；`AllowAny` 只表示不要求已有登录身份。
- Vue 读取非 HttpOnly 的 `csrftoken` Cookie，并通过 `X-CSRFToken` Header 回传；`sessionid` Cookie 保持 HttpOnly，前端 JavaScript 不读取身份凭据。
- 登录成功后 Django 轮换 Session ID 和 CSRF Token；退出必须使用登录后当前有效的 CSRF Token。
- Username 登录输入沿用 trim + lowercase 规范化规则；错误密码、不存在用户和 inactive 用户返回相同的通用错误，不透露账号状态。
- `/api/auth/me/` 对未登录请求保持 DRF SessionAuthentication 的原生 `403 Forbidden`。
- Customer 与 Admin 共用登录接口；响应中的 `is_staff` 可供界面展示，但不能替代后端权限检查。

### 13.3 2026-09-15 验证证据

- 使用 Django 5.2.17、DRF 3.18.1、pytest 9.1.1、pytest-django 4.14.0 和项目 MySQL 8.4.10 隔离测试数据库。
- 新增 Session/CSRF 目标测试：`12 passed`。
- 完整项目回归：`31 passed in 12.25s`。
- `manage.py check`：`System check identified no issues (0 silenced)`。
- `manage.py makemigrations --check --dry-run`：`No changes detected`。

---

## 14. Catalog Public Browse Baseline

### 14.1 第一版数据模型

- `Category` 保存唯一名称、唯一公开 `slug` 和上下架状态。
- `Product` 属于一个 Category，保存唯一公开 `slug`、共享名称、介绍和上下架状态；Category 被 Product 引用时使用 `PROTECT`，避免误删目录关系。
- `SKU` 属于一个 Product，保存全局唯一公开 `code`、常用查询字段 `color`、仅展示的 JSON 对象 `attributes`、两位小数价格和上下架状态；数据库约束保证价格不小于 0。
- `Inventory` 与 SKU 是一对一关系，保存 `available_stock` 与 `reserved_stock`；数据库约束保证二者不小于 0，删除被引用 SKU 时使用 `PROTECT`。
- 一对一约束只保证“一个 SKU 最多一条 Inventory”，不保证“一定存在一条”。本基线不使用 signal 或数据库 trigger 自动补记录；缺失时公开接口安全返回 `in_stock=false`。
- 写入入口暂时只使用 Django Admin。普通删除在目录 Admin 中关闭；Inventory 作为 SKU 的一对一内联维护，`reserved_stock` 暂时只读。新增 SKU 必须同时具备 `catalog.add_sku` 与 `inventory.add_inventory` 权限，避免权限不完整的员工从正常入口创建无 Inventory 的 SKU。

### 14.2 公开 API 契约

| Method | Path | 公开结果与规则 |
|---|---|---|
| `GET` | `/api/catalog/categories/` | 返回有效分类的 `slug/name` 列表，不分页 |
| `GET` | `/api/catalog/products/` | 返回有效商品分页列表；固定每页 20 条，可用 `?category=<category-slug>` 过滤 |
| `GET` | `/api/catalog/products/<product-slug>/` | 返回商品详情及有效 SKU；不存在、商品下架或所属分类下架时统一返回 `404` |

商品列表只公开 `slug/name/category/lowest_price/in_stock`。详情只公开 `slug/name/description/category/in_stock/skus`，每个 SKU 只公开 `code/color/attributes/price/in_stock`。内部数据库 ID、`available_stock` 和 `reserved_stock` 均不对外暴露；金额继续使用 JSON 字符串。

### 14.3 可见性与库存提示语义

- Guest、Customer 和 Admin 读取公开接口时遵循同一套上下架规则；Admin 身份不会绕过公开可见性。
- 只有有效 Category 下的有效 Product 才能出现在列表或详情；详情只包含有效 SKU。
- 有效但库存为 0 的 SKU 仍会显示，并返回 `in_stock=false`。
- 有效 Product 即使没有有效 SKU，仍可以用于浏览：`lowest_price=null`、`in_stock=false`。
- `lowest_price` 只在有效 SKU 中计算，不受下架 SKU 价格影响。
- `in_stock` 只是目录浏览提示，不是下单承诺。购物车与下单阶段必须重新读取 MySQL 中的权威价格、上下架状态和库存，并处理并发变化。
- 公开接口不读取 Session 身份，只支持安全的 `GET`；因此 Guest 或已登录用户对这些 URL 发写请求时都稳定返回 `405 Method Not Allowed`，不会先被 Session CSRF 检查改写为 `403`。这不削弱认证写接口的 CSRF 防护，因为目录公开视图本身没有写方法。

### 14.4 2026-09-16 本地验证证据

- 使用项目 MySQL 8.4.10 隔离测试数据库执行 Model、API、可见性、权限和约束测试。
- Catalog/Inventory 目标测试：`19 passed, 8 subtests passed in 3.77s`。
- 完整项目回归：`50 passed, 8 subtests passed in 14.33s`。
- 商品列表固定分页大小及查询次数测试通过；代表性数据下列表和详情都不超过 2 条数据库查询，避免随 SKU 数量产生 N+1 查询。
- Admin 权限组合测试通过：只有 SKU 新增权限时返回 `403`；同时具备 SKU 与 Inventory 新增权限时，SKU 和其一对一 Inventory 可以在同一次 Admin 提交中创建。
- `manage.py check`：`System check identified no issues (0 silenced)`。
- `manage.py makemigrations --check --dry-run`：`No changes detected`。
- 本节记录的是本地验证结果；远端 CI 应在对应 commit push 后单独观察，不能因为直接使用 `main` 就跳过，也不得在未观察时宣称已经通过。PR 不再是触发或核对 CI 的默认前提。

---

## 15. Git Development Workflow — Main First

### 15.1 默认规则

- Project One 当前是个人学习型后端项目，默认直接在最新 `main` 上完成普通、低风险、范围清晰的纵向切片。
- 不再要求每个功能创建 feature branch、Codex worktree 或 Pull Request；AI 也不得把这些步骤当成普通任务的自动前置条件。
- 一个清晰小阶段完成后，依次检查当前分支与 `git status`、运行相关测试、复核 diff、只暂存本阶段文件、创建聚焦且有意义的 commit，然后 `git push origin main` 并观察 CI。
- main-first 只减少不必要的流程，不降低测试、代码 Review、自检、CI、版本控制、失败复现和证据标准。

### 15.2 使用独立分支的例外

以下修改默认建议独立 branch；只有确实需要并行隔离时才额外创建 worktree：

- 大规模重构；
- 高风险数据库修改；
- 库存并发、秒杀等复杂并发功能；
- 支付状态机、Webhook 等高风险模块；
- 不确定是否采用的实验性方案；
- 其他需要隔离或方便回滚的大型修改。

例外分支必须说明隔离理由，完成后先验证，再与最新 `main` 安全整合。只有确认 `main` 包含预期提交或等价内容后，才能考虑删除 branch/worktree。PR 用于高风险变更、需要集中 Review 或用户明确要求的场景，不是所有功能的默认要求。

### 15.3 2026-09-16 迁移记录

- `codex/catalog-public-browse` 的 Catalog/Inventory 提交为 `acd49aa`。
- 远端 `main` 先通过 PR #6 的提交 `def9d16` 包含相同内容，随后以 `99adb1a` 合并 `codex/catalog-public-browse`；`def9d16` 与 `acd49aa` 的文件内容比较无差异，因此历史虽然保留两个来源提交，但没有重复实现。
- 清理旧 worktree 前必须先确认其工作区干净、本地 `main` 与 `origin/main` 一致，并从主项目目录使用 `git worktree remove`，不得直接删除文件夹。
- 在最新 `main` 上重新验证：Catalog/Inventory 为 `19 passed, 8 subtests passed`，完整回归为 `50 passed, 8 subtests passed`；Django 配置检查通过且没有迁移漂移。

---

## 16. Guest Catalog Browse UI（2026-09-17）

- 新增一个 `frontend/` Vue 3 + TypeScript/Vite 工程，使用 Vue Router 与原生 `fetch`；没有 Axios 或 Vitest。
- 当前只实现商品发现闭环：进入列表 → 分类过滤 → Django 后端分页 → 商品详情/SKU → 返回原分类/页码继续浏览。购物车、下单、支付不是本阶段范围。
- 列表 URL 保存 `category` 与 `page`；切换分类回到第一页。详情返回保留查询参数。`next/previous` 只用作是否可翻页的标记，前端构造同源相对 API URL。
- 分类、列表、详情分别维护 loading/error；列表成功空结果使用 empty，详情公开不可见使用 404，网络或 HTTP 错误可重试。越界分页可返回第一页；未知前端路由也有 404。
- 请求切换时先取消旧请求；每次请求用自己的局部 AbortController，旧请求不能覆盖新结果或提前关闭新请求的 loading。
- 金额继续使用 `string`/`string | null`；库存数量不公开，`in_stock` 只是浏览快照，不是预占或购买承诺。
- 开发期新增 Vite `/api` proxy 指向 `127.0.0.1:8000`。没有修改 Django 认证、CORS、数据库模型、迁移或部署配置；生产同源代理与 SPA fallback 尚未实现。
- 本轮实际验证：前端类型检查与构建通过；真实 Chromium 请求覆盖列表、分类、分页、详情、empty、缺货、404、离线重试和快速分类切换；Catalog/Inventory `19 passed in 4.81s`，完整回归 `50 passed in 15.75s`；Django check 与迁移漂移检查通过。
- 浏览器验证使用临时 `test_project_one` + 合成 fixture，没有迁移或写入开发库。停止 testserver 后使用 pytest 重建并销毁测试库，已查询确认测试库不存在。
- 对应内容已经进入 `main`；本节记录的是当时的本地验证证据，具体操作、限制与工程能力映射见 `docs/verification/guest-catalog-browse.md`。

---

## 17. Interview Core 学习策略与 MVP 路线（2026-09-18）

### 17.1 分层学习

- `Depth`：Order、库存事务/并发、Redis 缓存正确性、Payment 状态/幂等。用户必须掌握不变量、测试 Oracle、失败复现、方案比较和证据解释。
- `Supporting`：Accounts、Catalog、Cart、Address、权限和 API 契约。用户掌握请求链、所有权、关键约束和业务边界，可由 AI 协助完成局部实现。
- `Boilerplate`：普通 CRUD、Admin、重复 Serializer/View/Form/Table、fixture、HTML/CSS 和构建样板。行为确认后使用 CRUD Fast Track，由 AI 交付并验证；用户 Review 聚焦 Diff，不逐行背诵。

CRUD Fast Track 不能跳过权限、数据归属、数据库约束、一个正常例子和一个重要失败例子，也不能把金额、库存、状态机、幂等或并发规则当作普通 CRUD。

### 17.2 Interview Core 范围

- 保留最窄真实闭环：浏览 → 登录 → Cart → Address → Order/库存预占 → Mock Payment → 模拟发货 → 完成。
- 完整 Vue Admin 不再阻塞 Interview Core；普通运营使用 Django Admin，只有三条深度主线出现明确演示需要时才增加极简实验入口。
- Coupon、Favorite、Review、复杂搜索 UI、MQ、Elasticsearch、Transactional Outbox、分布式锁和第三方 Sandbox 均不阻塞 Interview Core，并继续受各自 Gate 控制。
- 闭环完成后不横向补普通功能，直接进入 MySQL 库存并发、Redis 缓存正确性和支付状态/幂等三条主线。

### 17.3 当前切片：MVP-1 Authenticated Cart

- 普通 CRUD 部分：查看 Cart、加入 CartItem、修改数量、删除 CartItem，以及对应 Serializer/View/URL 和极简 Vue 页面，走 CRUD Fast Track。
- 必须理解的 Supporting 规则：每位 Customer 一个活动 Cart；`(cart, sku)` 唯一；数量范围 1–99；用户只能操作自己的 Cart；同 SKU 重复加入语义明确；价格只由后端读取；Cart 不预占库存，也不承诺最终价格或库存。
- 本切片不包含 Address、Order、Reservation、Redis、MQ 或 Payment。
- 已确认并实现的 Oracle：空购物车 GET 不写数据库，第一次加入时创建 Cart；重复 SKU 合并数量；合并后超过 99 拒绝且保持原值；当前失效或库存不足的 SKU 不能新增/更新；已经存在的条目保留、标记不可用并允许删除；加入时按当前库存提供快速反馈，但不锁价、不预占库存，下单仍必须重新校验。
- 自动验证已通过：购物车目标测试 14 passed、4 subtests passed；全量回归 64 passed、12 subtests passed；Vue 生产构建通过。开发数据库没有迁移，浏览器人工验收尚未执行。

---

## 18. MVP-2 Minimum Address（2026-09-18）

### 18.1 业务规则与取舍

- 已登录 Customer 可创建、列出、编辑、删除和设为默认自己的结构化地址；Guest 无权访问，知道别人的地址 ID 也统一得到 `404`。
- 字段为收件人、收货手机号、省、市、区/县和详细地址。手机号去除空格、连字符和可选 `+86` 后，必须匹配 `^1[3-9][0-9]{9}$`；格式合法不代表号码真实。
- 零地址时没有默认地址；首个地址无论客户端是否勾选都会自动成为默认；只要存在地址，Service 路径始终保留恰好一个默认地址。
- 设置新默认地址时，旧默认地址与新默认地址在同一 `transaction.atomic()` 中切换。直接取消当前默认而不提供替代地址会被拒绝，避免产生含糊的“有地址但无默认”状态。
- 删除默认地址后，按 `created_at, id` 选择最早创建的剩余地址接任；删除最后一个地址后回到零地址、零默认。该规则稳定、可预测并可测试。
- 不做省市联动、地图、地址补全、复杂 UX，也不在本阶段创建 AddressSnapshot。订单地址快照属于 MVP-3 创建订单事务的一部分。

### 18.2 应用层与数据库保证

- 应用层 Service 先对当前 User 行执行 `select_for_update()`，把同一用户的地址写操作串行化；它负责首地址自动默认、默认切换、删除补位和“至少一个默认”的跨行规则。
- MySQL 使用 `default_marker`：默认地址存 `1`，普通地址存 `NULL`。`UNIQUE(user, default_marker)` 利用 MySQL 允许多个 `NULL` 的语义，保证每个用户最多一行 `1`；CheckConstraint 只允许 `1/NULL`。
- 数据库可以独立保证“最多一个默认”，但不能用普通行约束保证“只要存在地址就至少一个默认”；后者由 Service 与测试保证。绕过 Service 直接写 ORM/SQL 仍可能制造全部为 `NULL` 的地址集合，因此核心写入口必须集中在 Service。
- 两个请求同时设默认时都会先竞争同一 User 行锁，随后按顺序执行；最终后完成切换的请求对应地址成为默认。数据库唯一约束继续作为绕过 Service 或未来代码错误的最后防线。

### 18.3 API 契约

| Method | Path | 作用 |
|---|---|---|
| `GET` | `/api/addresses/` | 读取当前用户地址，默认地址排在最前 |
| `POST` | `/api/addresses/` | 创建地址；首地址自动默认，可用 `is_default=true` 切换默认 |
| `PATCH` | `/api/addresses/{id}/` | 局部修改当前用户地址；可用 `is_default=true` 设为默认 |
| `DELETE` | `/api/addresses/{id}/` | 删除当前用户地址；删除默认后按规则补位 |
| `POST` | `/api/addresses/{id}/default/` | 显式、可重复地把当前用户地址设为默认 |

所有接口要求 Session 身份，所有写请求要求 CSRF。权限通过每次查询同时带 `user=request.user` 实现，不依赖前端隐藏按钮。

### 18.4 2026-09-18 本地验证证据

- Python 3.11.9、Django 5.2.17、DRF 3.18.1、pytest 9.1.1、MySQL 8.4.10。
- Address Model/API/并发目标测试：`16 passed, 6 subtests passed in 16.66s`。
- Accounts 相关回归：`44 passed, 6 subtests passed in 32.00s`。
- 完整项目回归：`80 passed, 18 subtests passed in 46.58s`。
- `manage.py check` 为 0 issues；`makemigrations --check --dry-run` 为 No changes detected。
- Vue `type-check` 与生产 `build` 通过，Vite 转换 43 modules；没有新增前端依赖。
- pytest 使用并销毁隔离 `test_project_one`；未运行 `migrate`，未迁移或写入开发数据库。浏览器人工验收尚未执行。

---

## 19. MVP-3 Order + Inventory Reservation（2026-09-18）

### 19.1 本阶段交易事实

- 创建订单只接受当前用户选中的 `address_id`，最终价格、金额、可售状态和库存全部由 Django 在事务中重新读取；客户端金额不是事实来源。
- Order 使用内部 BigInt 主键和不可猜测 UUID `public_id`，初始状态固定为 `PENDING_PAYMENT`；当前只有 CNY、0 优惠与 0 运费。
- OrderItem 复制 Product 名称、SKU code/color/attributes、当前单价、数量和行金额；AddressSnapshot 复制下单时完整结构化地址。后续修改或删除源数据不改变历史订单。
- 每个 OrderItem 创建唯一 InventoryReservation，初始为 `RESERVED`，保存 15 分钟预留期限；同时创建 RESERVE InventoryMovement 记录 available/reserved 变化量。
- 当前切片只负责预留，不负责 PaymentAttempt、库存核销、取消/过期触发或释放。

### 19.2 事务、锁与幂等

- `create_order_from_cart()` 使用一个 MySQL `transaction.atomic()` 完成：幂等判断、地址/Cart 读取、库存验证、Order/快照/Reservation/Movement 写入、available→reserved 和 CartItem 清理。
- 同一用户先锁 User 行，随后锁自己的 Address、Cart/CartItem；Inventory 按 `sku_id` 固定顺序 `select_for_update()`，降低不同请求反序拿锁的死锁风险。
- 任一商品失效或库存不足时，整组写入回滚，Order/Reservation/Movement 不存在，Inventory 与 Cart 保持原值。
- POST `/api/orders/` 必须携带 1–128 个可打印字符的 `Idempotency-Key`。数据库唯一约束为 `(user, idempotency_key)`；服务保存规范化 `{address_id}` 的 SHA-256 摘要。
- 同用户同 Key 同摘要返回原 Order，即使第一次成功已经清空 Cart；同 Key 异摘要返回 `409 Conflict`。并发同 Key 请求先通过 User 行锁串行化，唯一约束继续作为最后防线。
- 当前 `select_for_update()` 是正确性 baseline，不是性能最终结论。原子条件更新仍是 Phase 3 的优先比较候选，必须通过真实热点库存实验决定。

### 19.3 API 与前端边界

| Method | Path | 语义 |
|---|---|---|
| `GET` | `/api/orders/` | 只列出当前用户自己的订单 |
| `POST` | `/api/orders/` | 幂等创建待支付订单并原子预留库存 |
| `GET` | `/api/orders/{public_id}/` | 只读取当前用户自己的订单详情 |

- 所有接口要求 Session，POST 额外要求 CSRF 与 Idempotency-Key；他人 Address 和 Order 都不会因知道 ID 而越权访问。
- Vue `/checkout` 只读取 Cart/Address、选择地址、生成并在不确定重试中复用 Key、提交请求和展示订单结果；它不计算可信金额、不扣库存、不保证权限或幂等。

### 19.4 2026-09-18 本地验证证据

- Order Model/API/双线程并发目标测试：`14 passed, 9 subtests passed in 13.40s`。
- 完整项目回归：`94 passed, 27 subtests passed in 44.52s`。
- `manage.py check` 为 0 issues；`makemigrations --check --dry-run` 为 No changes detected。
- Vue `type-check` 与生产 `build` 通过，Vite 转换 46 modules；没有新增前端依赖。
- `git diff --check` 通过，只有 Windows 工作树 LF/CRLF 提醒。
- 未运行 `manage.py migrate`，未迁移或写入开发数据库；未执行浏览器人工验收；尚未 push，远端 CI 未观察。

---

## 20. MVP-4 Mock Payment + Completion（2026-09-23）

### 20.1 本阶段最小闭环

- MVP-3 的 `PENDING_PAYMENT` Order 与 `RESERVED` InventoryReservation 现在可以经后端 Mock Gateway 确认支付，进入 `PAID`，并在同一数据库事务中把预占库存核销为 `COMMITTED`，写一次 COMMIT InventoryMovement；`available_stock` 不再减少第二次。
- 顾客可在订单详情发起模拟支付、查原 Attempt 的结果；具备 `ordering.change_order` 权限的 Django Admin 可模拟发货，订单本人可确认收货，形成 `PENDING_PAYMENT → PAID → SHIPPED → COMPLETED`。
- Mock 支付不涉及真钱；顾客页面没有可任意选择“支付成功”的输入，成功事实由经签名校验的后端 Event 路径确认。

### 20.2 支付正确性边界

- PaymentAttempt 创建使用短事务与 `(user, idempotency_key)` 唯一约束；同 Key 同 Order 返回原 Attempt，异 Order 冲突。一个 Order 最多一个活跃 Attempt，成功后不能再建，失败后可用新 Key，默认最多 3 次。
- Attempt 事务提交后才调用 Gateway；超时成为 `UNKNOWN`，不能当作失败再建 Attempt，必须查原 Attempt。MockGatewayPayment 持久保存模拟 Provider 的结果，供查单恢复。
- PaymentEvent 先保存原始 payload，再独立事务验签、校验 Order/金额/币种/状态并处理；`(provider, event_id)` 唯一防事件重放，无效签名用内容摘要形成的内部 synthetic ID 留存与去重，避免抢占将来的合法事件号。Mock Webhook HTTP 入口只在本机开发/测试环境开放，限制 16 KiB 请求体。
- Order、Attempt、Reservation、Inventory 与 COMMIT Movement 在支付成功处理事务内一起提交；处理失败回滚业务变更，Inbox 标记 `ERROR` 可重试。
- 订单详情页/列表页只承担展示和请求，不决定可信金额、支付成功、库存和权限。

### 20.3 已验证与仍待验证

- 本机 `manage.py check` 为 0 issues；迁移生成后 `makemigrations --check --dry-run` 为 No changes detected，但因 MySQL 不可连接而有迁移历史检查警告。
- 在单个进程内覆盖为临时 SQLite `:memory:` 的支付 API 功能烟测：27 passed、1 deselected、6 subtests passed；未写入开发数据库。
- 相同临时 SQLite 设置下的全项目非并发回归：123 passed、3 deselected、35 subtests passed；它覆盖一般功能回归，但不等于真实 MySQL 锁或并发验证。
- Vue `type-check`、生产 `build` 和 `git diff --check` 已通过。真实 MySQL 双线程并发、MySQL 环境下的全项目回归与浏览器端到端验收尚未执行；Docker 引擎和本机 MySQL 端口均不可用。
- 未执行开发数据库迁移、commit 或 push。15 分钟超时关闭、取消/释放、迟到成功退款模拟仍属 Phase 2B，不能将此切片理解为生产级支付。

---

## 21. Track B / Phase 4A 商品详情缓存（2026-09-26）

### 21.1 当前切片与范围

- 业务闭环及 Phase 2B 取消/退款模拟、Phase 3 下单竞争已有代码和独立验证记录。
  其最新结果见 `docs/verification/phase-2b-*.md` 与 `phase-3-*.md`，上面的
  历史 MVP-4 环境限制段落保留为当时记录。
- 本段以公开商品详情为入口展开 Redis 一致性与故障降级主线，属于 Depth /
  Learning Mode。只缓存公开 JSON；列表、分类、身份和交易不使用 Redis 缓存。
- MySQL 先确认商品及分类上架状态，然后 GET Redis；未命中读取 SKU/库存，
  由原 Serializer 输出并尽力回填。基线/未命中 2 SQL，命中 1 SQL。
- Key 使用环境前缀、`catalog:detail:v1` 和 Product 主键；TTL 初始 30 秒，
  抖动正负 5 秒，只是本地实验参数。功能默认关闭，限开发/测试环境。

### 21.2 一致性与故障边界

- 当前 Admin/普通 model.save 在数据库提交后失效 Product、Category 关联商品
  和 SKU 旧/新归属商品。回滚不失效；事务内详情绕过缓存读和写。
- 商品/分类下架后发起的新请求仍由 MySQL 返回 404；库存提示、价格和 SKU
  浏览状态可陈旧，下单继续读取权威 MySQL 值。Inventory 修改不触发缓存失效。
- Redis GET 异常回源且本请求跳过回填，SET/DEL 异常不破坏 MySQL 成功结果。
  客户端连接/命令超时各 100 ms，无重试；尚无集中回源保护。
- 批量 update 不触发保存信号；提交后删除也可能被在途旧读回填。Redis
  故障期失效失败，恢复后旧副本仍可能命中。这两个窗口已实际复现，尚未修复。
  TTL 从回填时计算，不能说“修改提交后严格最多陈旧 35 秒”。

### 21.3 实际验证与下一步

- 修改前查询基线：1 passed，观察 2 SQL；修改后原目录回归：16 passed、
  6 subtests passed。
- 真实隔离 MySQL + Redis 缓存专项：首次 20 passed in 12.52s；补充数据库
  隔离断言及连接池清理后复测 20 passed in 13.14s。不是 SQLite 或假 Redis。
- 相关购物车/下单/完整下单并发/取消/支付回归：70 passed、10 subtests passed。
- Django check、迁移漂移、依赖检查与 diff whitespace 检查通过。
- 未修改开发数据库、`.env`、前端、交易实现或既有未提交修改；没有新增迁移、
  commit/push。测试 Redis 独立端口 6381，逐测试 UUID 前缀清理，无 FLUSHDB。
- 证据与复现：`docs/verification/phase-4-product-detail-cache.md`；学习流程：
  `docs/learning/phase-4-product-detail-cache-study-guide.md`。
- 下一步沿用延迟旧回填实验，先比较最小修正与取舍，再推进热点回源和 Redis
  故障集中回源验证。尚未得到生产性能、容量或强一致性结论。

---

## 22. Track B / Phase 4B 缓存分代（2026-09-26）

### 22.1 已验证决定及取舍

- 先运行 Phase 4A 旧回填反例，再将判定改为“后来请求读新值”，原实现实际
  失败。修正使用全局随机 UUID generation + 包含 generation 的 v2 详情 Key。
- 请求必须在首条 Product/Category 数据库读取前捕获代号，并一直用该代号
  回填。正常 Product/Category/SKU save 在 on_commit 成功后切换全目录代号。
- 旧请求仍可以返回旧值和 SET 旧代；成功换代后开始的新请求不读取旧代。
  旧详情保留原 TTL 自动清理，代号 Key 不过期；丢失后 SET NX 随机重建并读赢家。
- 与 WATCH 条件回填、延迟双删、数据库/每商品版本比较后，本段选择不改
  schema 的最小方案。代价是任意目录保存都会使全部详情逻辑失效、多一次
  Redis GET、旧代短期占内存；没有性能优胜结论。
- 去掉分类关联商品失效查询和 SKU 原归属查询。不改变业务 API、权限、
  价格/库存权威来源或商品/分类的实时下架检查。

### 22.2 实际证据与限制

- 原反例 1 passed（确认失败现象）；新判定在旧实现 1 failed；修正后原
  缓存专项 20 passed in 11.82s，额外代号专项 8 passed in 8.74s。
- 额外证据包含首条 SQL 后暂停、代号丢失、真实并发 SET NX 同赢家、全局
  失效影响无关商品、旧详情自然过期、SKU 改价和非法代号回源。
- 相关目录/购物车/下单/支付 API 回归：65 passed、28 deselected、
  14 subtests passed in 47.66s。28 个 Redis 用例已在专项运行，回归未重复。
- 命中 1 SQL + 2 Redis GET，未命中 2 SQL；不是性能提升倍数或生产容量结论。
- MySQL 提交与回调之间的窗口、提交后崩溃、Redis 换代失败、批量 update、
  绕过 Admin 硬删除与库存提示 TTL 陈旧仍未解决；不是强一致方案。
- 没有新增依赖、模型、迁移或服务，没有修改订单/库存/支付、前端或开发库。
  本轮没有执行暂存、commit、push 或部署。
- 详细证据见 `docs/verification/phase-4b-cache-generations.md`，学习与面试追问
  见 `docs/learning/phase-4b-cache-generation-study-guide.md`。
- 下一步测量热点冷启动及 Redis 故障时的集中回源，再决定请求合并或局部
  版本。保留本段的故障边界，不宣称全链路缓存一致性已完成。

---

## 23. Track B / Phase 4C 并发回源基线（2026-09-26）

- Depth / Learning Mode，业务代码不变。真实隔离 MySQL + Redis，DRF
  APIClient 10 并发 × 3 轮 × 3 场景，共 90 请求，全部 200 且 JSON 正确。
- 热缓存每轮 10 SQL、0 详情加载；新代冷缓存三轮 16/14/16 SQL、
  6/4/6 详情加载；Redis 连接失败每轮 20 SQL、10 详情加载。
- 总 SQL 包含每请求一次 Product/Category 可见性检查；请求合并即使有效，
  也不意味着所有数据库查询归零。自然调度下重复加载数不要求固定。
- 实验 `1 passed in 7.83s`；相关最小回归 `3 passed in 8.31s`。计量排除
  建 DB 连接和开始屏障，没有经过 HTTP 服务器，不能推断生产容量。
- 当前没有唯一加载者和等待机制，未测试加载者失败/等待超时；连接失败
  降级也不证明能承受大流量或处理静默超时。
- 证据及复现：`docs/verification/phase-4c-cache-bursts.md`；90 个请求的
  原始记录：`docs/verification/phase-4c-cache-burst-data.json`。
- 下一步比较进程内请求合并与 Redis 租约锁，先定 generation 隔离、异常
  清理和等待时限；缓存整体故障的数据库保护单独评估，不预写优化结论。

---

## 24. Track B / Phase 4D 进程内请求合并（2026-09-27）

- Depth / Learning Mode。共享 Key 为完整详情 Key（prefix + generation +
  Product ID）；每请求仍在首条 SQL 前捕获代号，各自执行 MySQL 上架检查。
- SingleFlight 使用短 Lock + Future，共享进行中公开 payload。加载者二次
  查同代缓存，必要时加载/回填；等待者等待最多 200ms。失败通知后或超时
  时独立复查/回源，超时不移除或取消仍运行的加载者；结束清理登记。
- 初次缓存不可用、禁用或事务内绕过；只共享普通 dict，不共享 ORM 实例、
  Request/Response。进程内有效，不协调多个服务器进程。
- 真实隔离 MySQL + Redis 两方案对照，共 180 请求全部成功：冷缓存三轮
  详情加载从 9/10/7 到 1/1/1，SQL 从 19/20/17 到 11/11/11；热缓存仍
  每轮 10 SQL，Redis 连接失败仍每轮 20 SQL。未得到生产性能或容量结论。
- 对照 `2 passed in 9.79s`；最终商品目录整体回归 `57 passed`、
  `6 subtests passed in 20.39s`，包括共享、不同键、失败清理、0/200ms
  超时后仍加入原加载者、二次命中、换代隔离与绕过规则。Django check 和
  diff whitespace 检查通过；实际 API 受控4请求观察4 guard + 1 SKU SQL。
- 学习指南：`docs/learning/phase-4d-detail-singleflight-study-guide.md`；
  证据及两份逐请求 JSON：`docs/verification/phase-4d-detail-singleflight.md`。
- 不取消卡住的加载者；超时和失败降级仍可重复回源；Redis 整体故障、多个
  进程与原分代一致性窗口没有解决。没有改模型、接口、依赖、交易或开发库。
- 下一步先由项目拥有者复述协议，再比较 Redis 故障时的本地限并发与快速
  拒绝，先确认可用性规则后实现，不把本段宣传为数据库整体保护。

---

## 25. Track B / Phase 4E 故障回源名额（2026-09-27）

- Depth / Learning Mode。用户明确选择故障回源满员立即503，不排队。
  进程内 BoundedSemaphore 设2名额，是本地实验参数，不是生产安全容量。
- 初次代号读取异常/非法进入降级，在 Product SQL 前申请名额；payload
  GET 晚失败只限制后续SKU加载。缓存禁用和事务内主动绕过不进入故障保护。
- 同请求嵌套作用域只取得一次名额，正常/404/异常均 finally 归还；SET
  失败保留已有正常结果。503响应detail为“商品详情暂时繁忙，请稍后重试。”。
- 真实 MySQL + Redis 连接拒绝受控实验：保持2个加载者，另外8请求在其
  释放前返回503且零业务SQL；2个成功各2 SQL，总4 SQL，峰值加载者2。
  释放后新请求正常。人工保持重叠，不是性能或自然流量拒绝比例结论。
- 首次保护目标 `10 passed in 11.06s`；补充第二次GET故障及失败Future
  清理后，最终目录整体回归 `68 passed`、`6 subtests passed in 23.70s`。
  Django check、diff whitespace及原始JSON重算通过。
- 现有突发测试已按新策略记录成功/503与SQL；没有扩大名额隐藏拒绝，
  Phase4C/4D历史结果保留。未修改开发数据、依赖、模型、交易或前端。
- 学习：`docs/learning/phase-4e-fallback-admission-study-guide.md`；证据与
  10请求数据：`docs/verification/phase-4e-fallback-admission.md`。
- 仅指定降级入口、本进程有效；正常上架SQL、晚故障之前SQL、其他接口和
  进程不受全局约束；不是限速/熔断，不控制DB连接总数，无生产容量结论。
- 下一步先复述缓存证据链与可用性代价，整理演示，再根据真实超时或多进程
  问题决定是否引入熔断/跨进程协调，不自动扩大技术范围。

---

## 26. Track B / Phase 4F 慢故障与恢复边界（2026-09-27）

- Depth / Learning Mode，第一段仅增加实验和证据，业务实现不变。真实
  隔离MySQL与测试Redis，APIClient直接走后端，不是HTTP压测或生产容量实验。
- 测试Redis实际CLIENT PAUSE 2500 ALL，4轮各6请求：正常/首轮慢故障/
  继续慢故障/恢复分别为6/2/2/6成功，0/4/4/0拒绝，SQL6/4/4/6；
  应用GET12/6/6/12，超时0/6/6/0。故障轮回源名额峰值2，每个拒绝
  请求零业务SQL，但均已等待约100ms。两故障轮均在暂停窗口内完成。
- 健康Redis慢加载：Event保持原加载者，4个等待者实际等满200ms后独立
  加载，5请求全成功、5详情加载、10SQL、加载段峰值5，故障名额申请0次。
  峰值包含人为等待，不是同时执行SQL数；计时不是MySQL慢查询性能成绩。
- 独立真实连接拒绝实验：缓存129，正常SKU.save提交169但换代连接失败，
  故障期回源169，恢复后同代命中129（旧TTL还剩32秒）。再正常save成功
  换代后新读169；仅复现边界，未实现自动恢复一致性。读熔断也不自动修复。
- 首次慢加载1 passed in 7.43s；慢Redis与恢复2 passed in 11.50s；
  目录整体回归71 passed、6 subtests passed in 26.90s。
  Django check为0 issues，diff及新文档空白检查通过，原始计数独立重算一致。
- 学习：docs/learning/phase-4f-slow-faults-study-guide.md；证据、方法边界
  及三份逐请求JSON：docs/verification/phase-4f-slow-faults-and-recovery.md。
- 未新增依赖、接口、模型、配置或迁移，未修改交易、前端与开发数据，
  未commit/push。此前阶段未提交工作保留。
- 下一段先讨论本地读熔断状态与恢复探测，对照持续失败的GET/等待成本；
  健康慢加载的503规则与恢复一致性补偿分别确认，不把三类问题混为一体。

---

## 27. Track B / Phase 4G 最小读取熔断：状态机第一步（2026-09-27）

- 用户接受首轮实验候选：一次指定网络失败打开，冷却 1 秒；不是生产最佳参数。
  本段仍为 Depth / Learning Mode。用户要求控制首个项目的深度：完成最小
  读取熔断、缓存穿透及多 Key 雪崩证据后进入支付，不继续扩展治理框架。
- 新增独立 RedisReadCircuitBreaker，仅提供本进程 Closed/Open/Half-open
  状态规则，尚未实例化或接入 cache.py/views.py；HTTP 业务行为没有改变。
- 短锁仅管理状态；逻辑操作在锁外执行。半开唯一探测，网络失败重新 Open；
  非网络异常原样抛出，半开异常退出保守地重新冷却，不假称通信恢复。
  状态轮次隔离晚到旧结果；正常返回 None 也表示操作成功。
- 首个无熔断简化基线测试实际 1 failed in 0.23s：第二次仍调用超时读取。
  最小状态机实现后，纯逻辑专项 12 passed in 0.06s；可控时钟与线程 Event，
  没有数据库 fixture、Redis 网络连接或真实等待冷却。不能据此声称依赖故障
  集成、API 降级、延迟改善或目录回归已通过。
- 学习、判定和本次运行记录：docs/learning/phase-4g-minimal-read-breaker.md。
  现有 2 个回源名额与满员立即 503 已确定，下一步接入时沿用。
- 下一步仅接入代号读取逻辑操作与每次 payload GET；回填与提交后换代独立。
  再对照真实隔离慢 Redis 的尝试/等待、SQL、503、名额峰值及响应内容。
  恢复通信不能修复旧价；不增加跨进程协调、后台探测或恢复补偿。
- 没有修改依赖、配置、模型、迁移、交易、前端或开发数据，未 commit/push；
  此前阶段的未提交修改保留。

---

## 28. Track B / Phase 4G 读取接入与真实对照（2026-09-27）

- cache.py 的初次代号读取与每次 payload GET 接入进程共享 detail_reads。
  代号 GET→可选 SET NX→GET 是一组逻辑操作；跳过视为降级，沿用既有
  2 个回源名额/满员立即 503。视图、接口及提交后换代没有改写。
- 指定连接/超时异常触发，排除已知鉴权、权限、连接池耗尽、外部鉴权、
  服务器加载期的 ConnectionError 子类；普通 miss/404/SQL 错误/坏 JSON
  不触发。回填及失效写独立，读恢复不表示缓存最新。
- 真实 MySQL/测试 Redis 两方案各 4 轮×6 请求，持续故障短超时为 6 GET/
  6 超时、p50 111.665ms；熔断为 0 GET/0 超时、p50 2.147ms。
  两方案首轮各 6 GET/6 超时，名额峰值均 2，成功响应内容全正确。
  正常/探测后恢复各 6 成功、6 SQL；不要求固定 200/503 比例。
- 冷却后真实失败探测重新 Open，新的冷却期再次零读取；恢复请求转 Closed。
  Windows 本次未监听端口实际抛 TimeoutError，测试已允许指定连接/超时
  两类，不把平台异常名称当业务判定。半开唯一探测用真实 API+Event 验证。
- 纯状态专项 17 passed in 0.14s；API 边界/失败探测目标 10 passed in
  8.70s；目录整体 99 passed、6 subtests passed in 37.63s。原始数据独立
  重算一致；只使用 test_project_one 和 UUID 前缀，没有 FLUSHDB。
- 历史 Phase 4F 基线使用测试专用直通策略保留；旧恢复测试推进可控冷却
  后再确认缓存行为，测试之间隔离熔断状态，不通过重置生产状态隐藏问题。
- 证据与两份逐请求 JSON：docs/verification/phase-4g-minimal-read-breaker.md；
  学习：docs/learning/phase-4g-minimal-read-breaker.md。数字只说明本机受控
  故障行为，不是生产性能/容量，持续故障仍会在冷却后重新探测。
- 最小熔断收束，下一步缓存穿透，再多 Key 雪崩、整理缓存故事、进入支付。
  不继续增加框架、后台探测或恢复补偿；未改变依赖、配置、迁移、交易、
  前端或开发数据，未 commit/push，先前修改保留。

---

## 29. Track B / Phase 4H 文档核对与穿透基线（2026-09-27）

- 用户再次要求以面试为主，先核对文档再推进。已核对 Charter、项目协作
  规则、当前上下文及 Phase 4G 证据；保持一条业务闭环与库存、缓存、支付
  三个可独立解释的技术故事，证据足够后前进，控制首个项目的复杂度。
- Charter 与 README 部分活跃进度仍旧；它们的范围原则有效，当前实现
  与最新阶段事实以 CONTEXT 和较新验证记录为依据，不退回旧 MVP 待办。
  本轮只核对这些旧段落，没有改写其历史记录或整体路线图。
- 本段为 Depth / Learning Mode，先记录简单基线：真实隔离 MySQL/Redis，
  顺序6次同一缺失slug、6次不同缺失slug均全部404、各6次商品SQL和
  6次Redis GET；6次已有商品暖详情全部200、6 SQL、12 GET。
  Redis健康、读熔断Closed，没有SKU加载，正常请求仍有上架检查。
- 目标实验实际 1 passed in 9.54s，18请求完整记录保存。未重跑历史
  Phase 4G 的99个目录测试；没有延迟/容量结论，没有实施负缓存。
- 候选：只记录数据库确实不存在的slug，按已捕获generation隔离，短TTL
  5秒；已有隐藏商品不写负标记，200仍每次执行MySQL可见性检查。
  同一缺失slug期望首读1SQL、后读0SQL但仍404；成功save换代后新请求
  不选旧标记。绕过save的创建可能受负标记影响，TTL从实际回填起算。
  该规则与测试判定尚待用户确认；不将它作为已实现/已验证决定。
- 收益只针对重复缺失地址；不同随机slug仍可能各查一次并产生短期Key。
  健康商品会新增一个负标记GET，不能宣称防住所有穿透或全站数据库保护。
- 记录：docs/verification/phase-4h-cache-penetration.md；学习：
  docs/learning/phase-4h-cache-penetration-study-guide.md；原始JSON：
  docs/verification/phase-4h-penetration-baseline-data.json。
- 本轮仅新增实验/学习证据并更新上下文，没有修改业务实现、依赖、模型、
  配置、迁移、交易、前端、开发数据，未commit/push，既有修改保留。
  最小穿透验证完成后继续多Key雪崩、整理缓存故事，再进入支付。

---

## 30. Track B / Phase 4H 最小负缓存（2026-09-30）

- 用户已解释两次 HTTP 请求独立、重复缺失地址会重复查 MySQL，并提出同一
  地址在短时间内只查一次的预期。首个目标测试在原实现下实际失败：预期
  商品 SQL `[1, 0, 0, 0, 0, 0]`，观察为 `[1, 1, 1, 1, 1, 1]`。
- 复用当前 Redis 与请求已捕获的 generation，按 slug 保存 `missing` 标记，
  TTL 5 秒为首轮实验值，不视作最佳参数。标记 GET 在商品 SQL 前且经过
  现有读取熔断；GET 故障沿用回源名额，SET 故障不改变应有的 404。
- 详情 `get_object()` 改为按 slug 查询 Product 并连带 Category 一次获取；
  找到后仍检查两者上架状态。仅真正不存在才写标记；已下架商品、分类
  继续返回 404，不写标记。正常商品仍每请求查 MySQL 确认可见性。
- 新测试实际通过：同一缺失地址 6 次依次只有首请求查商品 SQL，全部
  返回 404；标记过期重新查；正常 Product.save 新建并切代后返回 200；
  商品/分类下架不误标记；标记读故障仍受 2 名额限制，写故障保留 404。
  Phase 4H 目标测试 `8 passed`；完整目录回归 `107 passed, 6 subtests passed`。
- 2026-09-27 的原始 JSON 保留为优化前证据；现有基线测试显式关闭负标记
  来重放旧路径。健康详情多一次 Redis GET。不同随机 slug、同一 slug
  首轮同时到达的并发请求仍可能各查 SQL；绕过 save 或失效 SET 失败时
  可能暂时返回旧 404，TTL 从标记实际写入时起算。
- 本段修改仅在目录缓存、详情入口、相关测试及学习/验证记录；没有新增
  依赖、配置、迁移、交易、前端或开发数据操作，未 commit/push。下一步
  多 Key 雪崩的最小证据，然后收束缓存故事并进入支付。

---

## 31. Track B / Phase 4I 多 Key 逻辑失效最小实验（2026-09-30）

- 本段为 Depth / Learning Mode，服务于 Redis 缓存一致性与韧性的面试故事。
  只新增测试与证据，不实现新的缓存防护。六个不同商品详情先预热；一次
  正常 Product.save 在提交后切换全局 generation。旧六个 Key 仍有正 TTL，
  新代六个 Key 均不存在，说明它们逻辑上一起变冷而非物理同时到期。
- 每轮六线程各读一个不同商品；三轮均为换代前热 `6 商品检查 SQL + 0 SKU
  SQL`、换代后冷 `6 + 6`、回填后热 `6 + 0`。九组共 54 个响应均 200 且
  内容正确。现有 SingleFlight 只合并同 Key，不合并六个不同商品加载；
  TTL 抖动只能分散自然过期，不能阻止全局换代造成集中回源。
- 真实隔离 MySQL `test_project_one`、测试 Redis 6381，UUID 前缀，未清空
  Redis。目标测试 `1 passed in 6.97s`；放宽非核心 TTL 下界后，最终目录
  回归 `108 passed, 6 subtests passed in 38.68s`。
  原始逐请求 JSON、环境、方法、时延/吞吐及局限记录在
  docs/verification/phase-4i-multikey-avalanche.md。
- 这只证明每轮冷波比热波额外增加六次 SKU SQL，不证明数据库过载、真实
  HTTP 吞吐或物理 TTL 同时过期。全局代号换得简单的旧写隔离，代价是无关
  商品也变冷；按商品精确失效须额外处理分类、SKU 跨商品依赖，当前不引入。
- 未修改生产缓存逻辑、依赖、配置、迁移、订单、支付、前端或开发数据，
  前序未提交改动保留；未 commit/push。下一步先让项目拥有者口述缓存
  故事与取舍，然后进入支付状态、Webhook 与幂等的最小基线。

---

## 32. Track B / Phase 4J 多 Key 回源并发名额（2026-10-01）

- 用户指出实验复现尚未补齐保护，随后明确推进“多 Key 回源并发上限”。
  本段 Depth / Learning Mode，复用两个进程共享名额，正常健康缓存 miss 的
  load_and_fill 使用 for_load=True；Redis 故障已获准请求沿用自身标志，
  避免内层重复申请。满员立即 503，加载/回填完成或异常都释放名额。
- 首个目标测试在旧实现下实际失败 `1 failed in 7.98s`，加载峰值 6 而预期
  为 2。真实隔离 MySQL/Redis，两个策略各三轮、每轮六个不同商品冷请求；
  Event 暂停前两个加载，旧策略峰值 6、六个 200，新策略峰值 2、两个正确
  200、四个在释放前完成的 503。商品检查 SQL 都为 6，冷波 SKU SQL 由 6
  到 2；恢复后四个商品各加载一次，全体热读 SKU SQL 为 0。
- 收益是加载并发受控，最终六个不同商品仍各需一次 SKU 查询。上架检查
  已发生在健康 miss 申请前；名额不是全站数据库保护或查询总量优化。
  显式禁用缓存/事务内旁路不新增限制，多进程各有容量，全局代号失效范围
  没有缩小；短时拒绝是方案代价，不宣称固定拒绝比例、时延或生产容量。
- 当前目标还验证同 Key 超时独立加载不能绕过满员预算、异常释放名额、
  失败 Future 不残留、热命中不耗名额与已有故障保护。历史 Phase 4D/4F/4I
  测试显式重放仅故障限流基线，保留历史 JSON，不用基线替代当前保护测试。
- 完整目录回归实际 `112 passed, 6 subtests passed in 43.10s`；随后只增强
  当前目标断言并复测 `15 passed in 11.22s`。记录与两份逐请求 JSON 位于
  docs/verification/phase-4j-refill-admission.md。
- 生产修改只在 catalog/cache.py；其余为相关测试和证据。没有新增依赖、
  配置、模型、迁移、视图文件、订单、支付、前端或开发数据操作，前序修改
  保留，未 commit/push。下一步让项目拥有者解释限制峰值与减少总量的区别、
  503 代价和进程边界，然后整理缓存故事并进入支付深度主线。

---

## 33. 支付主线范围决定（2026-10-01）

- 项目拥有者确认：Redis 本期暂时收束，继续支付状态、Webhook 和幂等主线；
  RabbitMQ + Celery 从条件性能力升级为该主线必做。本期不接任何第三方支付
  Sandbox，Mock Gateway 继续承担可控成功、失败、超时和异常事件的测试替身；
  Sandbox 以后仅在项目拥有者重新明确决定时评估。
- MQ 的业务贡献是让已经持久化的 Payment Inbox 事件能够异步处理、进行有界
  重试并在故障后恢复。它不决定付款是否成功，不成为订单、支付或库存的事实
  来源；MySQL 事务、状态机、唯一约束和业务幂等仍负责保证重复任务不重复核销、
  释放库存或创建退款模拟。
- 保留现有同步 Mock baseline，先理解业务规则与事务边界，再实现最小异步
  切片。必须使用真实隔离 RabbitMQ/Celery 验证重复投递、消费者崩溃、Broker
  不可用、重试耗尽及数据库已提交但尚未投递等恢复窗口；具体任务、重试次数、
  恢复机制和测试 Oracle 仍需逐步讨论，不能预先声称可靠消息已完成。
- Transactional Outbox、Elasticsearch 和 Distributed Lock 的条件性采用边界
  不变；引入 MQ 不自动升级这些能力，也不扩大为通用任务平台、微服务、真实
  支付、真实退款或复杂售后。
- 本轮只修改项目范围与协作文档，没有接入 MQ、增加依赖、修改服务配置、
  模型、迁移或业务代码；没有运行业务测试或操作开发数据，没有 commit/push。
  前序 Redis 代码、测试和证据的未提交修改全部保留。下一步共同确认支付
  状态、事件处理、库存副作用和异步任务之间的边界，再推进一个可验证切片。

## 34. Phase 5A：支付 Inbox 人工恢复基线（2026-10-01）

- 项目拥有者要求开始代码修改，并提供类似 Redis 阶段的操作/学习指南、
  核心代码与面试问题，之后自行学习；本轮只交付一个支付恢复切片。
- 基线先验证入箱后、处理前受控中断：Event 留 RECEIVED，订单待付、
  库存仍预占；这不是实际 Worker 或操作系统进程终止证据。
- 新增 `recover_payment_events` 一次性管理命令及恢复服务，从已提交
  RECEIVED/ERROR 固定选出最多 limit 条内部 ID，每条本轮最多处理一次。
  支持 dry-run 和指定内部 event-id；PROCESSED/REJECTED 不再选中。
- 同步接收与恢复复用 `process_stored_payment_event`，原业务处理事务不变。
  回滚后另开短事务记录 ERROR，锁内重读避免覆盖另一处理者已提交的终态；
  单条异常不影响本批其他事件，部分异常时命令报告 ID 并非零退出。
- 此次仅人工单轮：ERROR 尚未按可重试性分类，limit 不是累计重试上限，
  没有调度、退避或耗尽状态；库存不变量异常需调查，不能盲目定时循环。
  Inbox 恢复不代替 UNKNOWN 原 Attempt 查单，不重新付款。
- 隔离真实 MySQL 目标 `12 passed, 5 subtests passed in 10.75s`；支付与订单
  回归 `94 passed, 26 subtests passed in 76.21s`。真实两线程同选一事件最终
  只有一次库存核销；没有生产吞吐或 MQ 故障结论。详见
  `docs/verification/phase-5a-payment-inbox-recovery.md` 和对应学习指南。
- 没有修改模型、迁移、依赖、配置、API契约、前端或 Redis 代码；未执行
  开发库恢复，未 commit/push。下一步明确异步受理与有限自动重试规则，
  再接入必做 RabbitMQ + Celery，继续真实 Broker/Worker 及补漏恢复验证。

## 35. Phase 5B：支付事件分类与持久有界重试（2026-10-01）

- 本段属于 Track C / Depth，项目拥有者授权先实现并交付操作指南、核心
  代码及面试问答，之后学习；不等待答题才推进这个最小切片。
- 新增 PaymentEvent 的 PROCESSING、processing_attempt_count、failure_kind
  和 next_retry_at。每事件总共最多 3 次处理，包含首次；它与一个订单最多
  发起几次 PaymentAttempt 的付款规则不同，不新建订单或重新付款。
- 资格事务先锁内复查、计数并独立提交 PROCESSING；业务事务复查次数版本
  后复用原状态机，支付、订单、库存与事件终态一同提交。业务回滚不撤销
  已用次数；失败另事务留存，不能覆盖终态或新版本的处理资格。
- 只对白名单 MySQL OperationalError 1205/1213 允许重试，失败后分别等待
  5 秒、10 秒，第 3 次暂态失败为 EXHAUSTED。库存不变量、未知程序异常和
  其他数据库异常为 BLOCKED；原签名/金额等无效事件继续 REJECTED。
- 恢复扫描只选择首次 RECEIVED 或到期 TRANSIENT ERROR 且次数小于 3；
  --event-id 和重复接收不能绕过门槛，dry-run 不增加次数，选中后仍锁内复查。
  PROCESSING 不自动接管；硬中断或错误留存失败须调查，留待 MQ 切片补齐
  接管设计与真实 Worker 中断证据。当前没有调度器或真实 Worker。
- 新迁移 0003 将旧未分类 ERROR 保留为 BLOCKED，次数为 0，不猜测旧失败
  原因或伪造历史次数。仅在隔离测试库验证迁移，未应用到开发库。
- 隔离 MySQL 目标实际 26 passed、12 subtests passed；支付与订单回归
  108 passed、33 subtests passed。包含历史候选查询对照、预算/等待/耗尽、
  真实反向行锁死锁 1213、并发扫描及旧数据迁移。实测死锁不是生产自然
  负载；核销中途回滚另用受控错误注入验证。完整结果见
  docs/verification/phase-5b-payment-event-retry.md，对应学习指南保留核心代码。
- 只涉及支付模型、迁移、事件入口/恢复与目标测试、进度/证据文档；没有
  增加依赖、改配置、HTTP接口定义、网关、库存算法、前端或 Redis 代码，
  保留前序修改，未 commit/push。下一步 Phase 5C 接入必做 RabbitMQ + Celery，
  复用持久门槛，补发布遗漏、真实重复消费及 Worker/Broker 中断恢复证据。

## 36. Phase 5C-1：真实 RabbitMQ 与 Celery 基础链路（2026-10-01）

- 项目拥有者授权依赖与 Docker 镜像安装，交付仍按最小支付 Depth 切片推进。
  Windows `.venv` 安装 Celery 5.6.3，实际拉取 RabbitMQ 4.3.6 管理镜像，
  构建非 root Linux Python 3.11.16 prefork Worker；不使用 Windows 直接 prefork。
- `PAYMENT_EVENTS_ASYNC` 默认 false，保留同步 MySQL/Mock 基线；本地/测试
  异步模式先独立提交 Inbox，事务外发布内部整数 ID。Worker 调用已有 5B
  资格与支付处理器，状态机、预算与库存算法没有第二套实现。
- 异步 Webhook 的 202 只说明已入箱，不能证明验签、付款或 Broker 投递成功。
  已知发布错误保留 RECEIVED/count=0，日志只含内部 ID，不伪造业务 ERROR。
  创建 Attempt 的 201 不代表订单 PAID；发起付款与查原结果也复用事件入口。
- 首次真实 MQ 3 项因 Celery 内部非持久、非独占控制队列被 RabbitMQ4.3
  拒绝而失败，Worker 反复重连，没有正常消费。设置内部 control/event
  durable=True、保留默认过期后，真实3项通过54.40秒。加强测试脚本在建库前
  与 finally 停专用 Worker 并检查结果后，再次3项通过44.27秒。
- 真实隔离 test_project_one、project_one_test vhost、payment-events-test
  队列与 Linux Worker，验证先受理后确认、三条不同任务只一次核销、伪造
  通知不占合法事件 ID。每场景等待 ACK 增量与队列 ready/unack=0，再检查
  数据库终态；重复场景处理次数1、COMMIT1、ACK3，没有用 eager 代替 MQ。
- 隔离 MySQL 的异步/5A/5B 目标40 passed、24 subtests；支付与订单回归
  122 passed、3 skipped、45 subtests。3个skip为普通回归未开启的MQ用例，
  它们已在独立真实流程通过。pip check、compileall、Compose配置检查、
  git diff --check 实际通过，完整条件与结果见本期验证文件。
- 自动后台重试、提交后漏投补偿、PROCESSING 接管及真实 Worker/Broker
  故障恢复尚未完成。C2先沿用数据库总共3次预算与5/10秒门槛补后台重试，
  不自动引入 Outbox、Beat、结果库或通用任务平台。
- 文档：`docs/learning/phase-5c1-payment-mq-study-guide.md`，
  `docs/verification/phase-5c1-payment-mq.md` 与对应三场景原始JSON。
  修改支付异步入口/任务、依赖、Celery/Docker配置、目标测试与进度/证据文档；
  保留前序Redis与支付修改，没有改前端、金额或库存算法，未应用开发库迁移，
  未 commit/push。测试RabbitMQ保留运行，专用Worker已停止，未启动开发Worker。

## 37. Phase 5C-2：有界后台重试（2026-10-04）

- 项目拥有者要求在C1后继续实现，仍按支付Depth最小切片交付代码、指南与
  真实证据。本期不加依赖/模型/迁移，不改前端、Redis或开发库。
- C1在暂态失败后仅留下ERROR和next_retry_at，没有下一次自动执行。当前
  Worker在处理结束或异常留存之后重新读取Event，只对ERROR/TRANSIENT、
  count1或2、有next时显式发布原内部ID的短ETA任务。原数据库资格与业务
  处理器不变，总共3次包含首次，门槛5/10秒，不创建新订单或付款尝试。
- ETA为max(next_retry_at, now+1秒)：正常门槛不变，近边界早醒再次排程
  至少等1秒，避免立即循环投递。Task仍max_retries=0，不使用self.retry；
  这与显式发布下次任务不同。消息总数可以超过3，但真正业务处理不能超过3。
- 提前重复不扣预算，仍安排未来机会；终态、BLOCKED、EXHAUSTED、PROCESSING
  不安排。发布失败保留原ERROR/TRANSIENT与时间，报稳定任务错误，不改成
  支付失败，不自动补漏。成功完成排程的Task返回ERROR也不等于付款成功。
- 真实隔离实验只给测试Worker的连接设置SESSION锁等待1秒，独立连接锁
  Inventory造成真实MySQL1205；原资格函数只加观察包装，记录as_of/次数
  而不改规则。每场景过滤日志，errno观察器不重复安装，不输出SQL或凭据。
- 已验证一次真实锁超时后原事件自动count2成功、早到重复不扣预算、唯一
  库存COMMIT；持续锁故障真实3次1205后EXHAUSTED，再投递不重开；测试中
  故意破坏预占的库存不变量为BLOCKED/count1，不自动排程。实际领取as_of
  均不早于前次到期时间，不拿处理完成时间冒充资格证据。
- 目标规则/事务49 passed、33 subtests passed，最终受控MQ3项70.58秒通过，
  C1真实基线3项42.16秒通过；确认统计按每场景重新创建专用队列从零开始。完整
  环境、实验修正、基线与回归结果在 `docs/verification/phase-5c2-payment-mq-retry.md`。
  支付/订单回归131 passed、6 skipped、54 subtests；6个MQ用例已分别在
  独立C1/C2真实流程通过。pip check、compileall、Compose配置及静态检查通过。
  操作/核心代码/面试问答在对应learning指南，原始JSON保留本次三场景。
- 下一步C3补首次/重试发布遗漏与Broker不可用后的Inbox恢复；PROCESSING
  接管、业务提交与消息确认间的真实Worker中断留后续。Outbox仍条件性，
  当前不加入Beat、结果库或通用任务平台。前序修改保留，未commit/push，
  开发库未迁移，测试Broker保留运行、专用Worker结束停止。

## 38. Phase 5C-3：人工发布补漏与 Broker 停机恢复（2026-10-05）

- 延续支付Depth，补上MySQL已保存原通知但初次/重试任务发布失败后的
  独立发送入口。新增republish_payment_events与显式`--publish`；原同步
  恢复保留，CLI只允许development/test且async=true，dry-run也遵守环境门槛。
- 严格选择RECEIVED/NONE/count0或到期ERROR/TRANSIENT/count1/2，固定最旧
  批次、每条fresh资格复查后事务外发布原ID，不改业务状态/次数。首次
  BrokerFalse停止，报告failed/unattempted；published不代表已付款。
  重复扫描可多发，原MySQL领取门槛和支付/订单/库存事务继续保护唯一效果。
- 不选PROCESSING、BLOCKED、EXHAUSTED、终态或未到期事件，不重置预算。
  不完整RECEIVED/count>0或TRANSIENT/count0也排除；不带--publish的历史
  同步候选和行为未改。没有新增已发布标记、Outbox、Beat、结果库或周期调度。
- 真实专用Broker停机：Webhook仍202/RECEIVED/count0，真实补投失败原数据
  不变；恢复后Worker仍停，补投两次仍待付，Worker最终count1、ACK2、COMMIT1。
- 真实Worker会话锁等待1秒，受控Inventory锁造成1205，提交ERROR/TRANSIENT
  count1/+5秒；C3仅测试launcher暂停ETA发布至观察Broker关闭，再调用原
  publisher实际失败。停止Worker，恢复Broker，AMQP被动声明确认旧ready任务
  1条并清专用队列，排除原消息自行重投；新CLI只补发原ID，count2、ACK1、
  COMMIT1。两场景均一个Event/Attempt，库存19/0，最终ready/unack=0。
- 初次真实运行1过1失败63.58秒：恢复后同进程第一次发布仍失败，原状态
  保留；与旧Broker连接复用一致但原因仅推断，不认定恒定顺序。改用实际
  新CLI进程并核对完整test目标，第二轮2过72.54秒；后续加强AMQP计数后
  最终2过71.69秒。生产发布器未改行为、未清连接池或增隐藏重试。
- 本轮规则/5A/5B36 passed、20 subtests；C1/C2/C3真实分别3/3/2通过，
  支付/订单141 passed、8 skipped、62 subtests（97.96秒）。8项skip仅普通
  回归未配MQ，已在独立流程实测通过。pip check、compileall、AST、指南
  源码/链接、Compose及diff检查通过。源码指纹/条件/修正见本期verification。
- 本期仅改恢复服务/CLI与dispatch注释、测试/专用override/验证脚本和文档。
  业务services/tasks、模型/迁移、依赖、主Compose、前端、Redis与开发库未动；
  前序修改保留、未commit/push。测试Worker已停，测试Broker与原db健康。
- 指南`docs/learning/phase-5c3-payment-publication-recovery-study-guide.md`，
  证据`docs/verification/phase-5c3-payment-publication-recovery.md`及两场景JSON。
  人工单轮补投不保证自动恢复时限；下一步C4处理中断遗留PROCESSING与真实
  Worker故障，业务提交/消息确认间窗口尚未验证。Docker不可达/stop失败的
  清理仍不能承诺绝对顺序，本期仅观察正常清理路径。

## 39. Phase 5C-4：人工过期领取修复与真实 Worker 中断（2026-10-05）

- 项目拥有者要求先将全部前序成果推送，再继续下一步。先审查文件/敏感值，
  普通后端231 passed、80 integration deselected、76 subtests；Redis、支付、
  总文档分三个提交80caf34/281de6e/2dacc9a推送main，核对远端HEAD一致。
  以下C4属于推送之后的新增量，留在本地；历史记录中的未提交状态保留其时点含义。
- C4仍为支付Depth：T1已经提交PROCESSING但Worker消失，不能只依赖MQ
  重投。增加可空processing_started_at/processing_lease_until和迁移0004。
  租约从拿到Event锁后的实际领取时刻开始，默认60秒；不续租、不kill任务。
  T2业务/事件仍共同提交，终态/异常留存清截止，最后开始时间留作调查。
- recover_stale_payment_events固定最旧快照，每条短事务skip_locked，复查
  原status/count/截止与开始时间。租约到期不是死亡证明：活T2持Event锁时
  跳过；旧T2/T3与并发修复受原次数/状态门槛保护，不能覆盖后来版本。
  只修复Event元数据，count1/2变到期TRANSIENT，count3变EXHAUSTED；
  不增加/重置次数、不直接执行业务或发布。之后复用原同步恢复或C3补投。
- CLI增加显式--recover-stale，与--publish互斥（包括call_command关键词）。
  本地/测试环境限制与dry-run保留；修复不要求async=true，因为不发布。
  历史PROCESSING空租约不猜时间、不自动接管；0004迁移只在测试库验证。
- 专用测试launcher/Compose覆盖仅test环境和8秒租约；实际等待截止，
  SIGKILL Linux Worker。T1后、T2写入未提交、T2支付/退款提交后ACK前分别
  观察atomic=false/true/false。T2活进程即使租约到期仍被扫描跳过，kill后
  数据库连接退出回滚业务事务；修复与重投之后只一次核销。
- 四场景保留原未ACK任务，不purge；AMQP观察kill后ready1/消费者0，
  原任务UUID在重启后redelivered=true，加一条不同UUID补投/重复消息，
  等ACK增量2和ready/unack0再看MySQL。领取/T2回滚最终count2、PAID、
  库存19/0、COMMIT1；提交后中断count1不变。迟到退款最终库存20/0、
  Refund1、RELEASE1、COMMIT0，订单保持CANCELED。
- 首次真实3过1失败71.39秒：CREATED尚未解决时不能取消，退款准备未进入
  中断窗口。仅改fixture为同步有效FAILED→原订单取消→迟到SUCCEEDED，
  最终2条Inbox/1条Attempt，未放宽业务规则；整轮4过81.71秒。
  第三次过期耗尽只在规则/事务测试验证，未声称同一事件真实kill三次。
- 本期六模块目标69 passed、62 subtests（28.13秒）；支付/订单151 passed、
  12 skipped、83 subtests（96.51秒）。12skip为普通回归未配置的MQ场景，
  C1/C2/C3本期真实回归分别3过41.98秒、3过65.37秒、2过73.87秒，C4四项
  81.71秒，共12项独立通过；没有覆盖历史JSON。AST、JSON/指南链接、
  PowerShell语法与专用Compose配置检查通过，源码指纹和失败修正在证据内。
- 只修改支付领取/租约恢复、测试、专用覆盖/脚本及进度/指南/证据文件。
  Task、基础Compose、HTTP/前端、金额/库存算法、Redis与依赖未改；未迁移
  开发库。无Beat、Outbox、结果库、周期自动补漏或心跳；人工触发不能承诺
  自动恢复时限，空租约/持续持锁/耗尽仍可见供调查。
- 指南docs/learning/phase-5c4-payment-worker-recovery-study-guide.md，
  证据docs/verification/phase-5c4-payment-worker-recovery.md与四场景JSON。
  结束时测试Worker已停，测试Broker和原db健康，main仍为已推送的2dacc9a。
  下一步整理支付/MQ证据包及项目拥有者的解释，再按路线图进入商品秒杀。

## 40. Phase 5D：支付成功与订单到期的真实锁竞争（2026-10-05）

- 项目拥有者确认本期支付故障恢复保留人工兜底，授权补并发测试并说明方案。
  沿用已有Order行锁、锁内状态复查、业务原子提交与重复保护，没有重写状态机。
- 新增test_payment_expiration_concurrency.py，两种先获Order锁流程×
  CREATED/PENDING/UNKNOWN三种原Attempt，共六个受控交错。付款未确认时
  到期先拿锁也只能payment_unresolved；付款先提交时到期读到PAID无操作。
- 独立MySQL连接执行原process_stored_payment_event/expire_pending_order；
  线程本地SQL包装器在原Order锁SQL真正返回后暂停holder，不替换结果。
  容器现有管理连接只读performance_schema，关联本轮两连接、同server_uuid、
  当前test_project_one.ordering_order主键的RECORD WAITING/GRANTED。
  应用账号诊断权限未扩大，实验不开新数据库/账号或生产锁。
- 合成189.00元鼠标初始20库存买1件，下单19/1；六例最终PAID、SUCCEEDED、
  原成功Event PROCESSED/count1、预占COMMITTED、库存19/0、COMMIT1、
  RELEASE0、RESTORE0、Refund0。重复原通知/到期不变，不新建付款尝试。
- 原未确认阻止关闭/过期后迟到成功两项baseline实际2过9.00秒；六目标
  6过11.47秒并保存原始JSON；支付/订单157 passed、12 skipped、83 subtests
  （100.14秒），六例在回归仍显式开启。12skip仅未配置的MQ组，本期未重跑
  真实MQ，不由此次MySQL结果冒称MQ新验证；前期证据保留。
- 普通CI没有本机Compose db，新增实验显式开关默认关闭、六项明确skip；
  开启后严格本机3307/test_project_one/环境与观察目标检查，诊断失败硬失败。
  本轮业务源码、C4实现、前端、依赖、基础Compose、Redis与开发数据均未改。
- 学习指南docs/learning/phase-5d-payment-expiration-concurrency-study-guide.md；
  证据docs/verification/phase-5d-payment-expiration-concurrency.md与六场景JSON。
  只证明活跃Attempt这六交错，FAILED矛盾成功和已关闭迟到退款仍按原规则；
  没有自动关单、生产压测或第三方资金证据。增量仍本地，未commit/push。
- 资料归档偏好已明确变更：以后直接使用飞书Wiki有层级项目知识库，按
  项目总览、订单/库存、Redis、支付/MQ、秒杀/并发组织；复用已经完成的Redis
  普通文档材料。具体Wiki目标尚未指定，本轮不创建/移动/发布飞书内容。
  下一步先统一支付证据包与材料，再按确认目标归档，不扩大异步业务范围。

## 41. 支付学习入口、图解和文档SOP（2026-10-06）

- 项目拥有者要求参考已有Redis文档，解释支付数据库/学习知识库规划，绘制
  时序图并给出文档规范与SOP，随后创建专门的秒杀学习聊天。
- 只读获取现有飞书Redis文档revision6目录、业务边界与证据章节，参考其
  业务问题→流程→方案→源码→证据→限制→面试结构，没有覆盖旧材料或发布Wiki。
- 新入口docs/learning/payment-learning-and-interview-guide.md汇集实际表关系、
  UNKNOWN与ERROR区别、正常付款/原Attempt查单/MQ人工恢复/支付到期竞争四张
  时序图、已有阶段指南、历史证据与面试问答；另有payment-documentation-sop.md。
- 图源/PNG/SVG位于docs/learning/assets/payment-flows，包含四时序图和一ER图。
  源码复核修正了预占/流水非空关联、实际库存delta字段、旧Key直接返回与第3次
  故障不发布等绘图细节。Mermaid CLI12.0.0实际渲染，已进行可读性检查。
- 两份新文档本地链接/代码围栏检查、五组PNG/SVG和C1至5D五份原始JSON
  指纹核对通过；应用测试未重跑。157/12skip等数字只引用5D历史记录。
- 本轮只新增学习文档/图源/图片并追加本节，没有修改业务源码、模型/迁移、
  前端、依赖、部署配置或开发数据；既有C4/5D本地修改保留，未commit/push。
- MySQL继续单库按模块分表，未增加新表或索引；Wiki建议共享项目根节点，
  支付总览+七个问题子页，Redis复用旧文档，秒杀/并发独立组织且复用本支付链路。
  实际Wiki空间/父节点未指定，本期仍不远端写入。人工恢复边界不变。
- 秒杀学习按Phase6衔接，但已有Phase3超卖及NOWAIT/条件更新对照先复用；
  新聊天先只读定位基线、解释活动限购与成功订单约束，再讨论下一片核心逻辑。
- 已按用户要求创建本项目local新聊天“秒杀与库存并发：Phase 6 学习”，
  threadId=01a11051-7e61-7983-916b-91566a1bdde1；初始请求限定只读学习，
  保留当前未提交修改，不自动实验、修改、迁移或提交。未新建工作树。

## 42. 支付 Wiki 八页与六张原生画板材料（2026-10-06）

- 项目拥有者明确要求创建支付知识库，并使用lark-operations、lark-whiteboard、
  lark-wiki重画六图；随后确认新建“Project One 电商项目”Wiki空间。目标已确认，
  支付总览加七个子页，原Redis普通文档保留原位置，不搬迁、不覆盖。
- docs/learning/payment-wiki-draft保存八份Docx XML、六组新图源/PNG/OpenAPI
  节点、本地完整review.md和publication-plan.json。六图是知识地图、数据关系、
  正常付款、UNKNOWN查单、MQ人工恢复、支付与到期竞争；最终须原生可编辑。
- 白板CLI0.2.13实际渲染并逐张看图；六图静态检查0错误，五图0警告。
  思维导图原生自动布局坐标导致静态528条重叠警告，渲染图清晰；远端实际
  画板布局仍须发布后导出查看。八份草稿Profile Check通过；五段源码摘录
  逐字/指纹匹配，历史证据复核修正六场景与C4首次CREATED状态的措辞。
- 本次Supporting/Delivery归档任务，没有改业务源码、模型/迁移、前端、依赖、
  部署或开发数据，没有重跑应用测试，保留原C4/5D本地修改，未commit/push。
- Wiki空间查询实际返回缺wiki:space:retrieve；创建还需wiki:space:write_only与
  wiki:node:create。本地内容就绪，远端尚未创建；先走最小范围用户OAuth，
  授权后创建已确认层级、填六图、补导航并读回验证，不将准备完称为发布完。
- 随后项目拥有者完成授权：创建私有空间7693476782908935142，支付总览
  PCQ7wP9kMikz0eklmNYcH1J1nwh及七个子页。快捷节点创建要求额外旧scope
  wiki:space:read，已按schema改用现有wiki:node:create的原生创建接口；
  身份仍为user，没有扩展授权、转移所有者或改变成员权限。
- 八次正文写入success/warnings=[]，revision2读回与发布候选全文、标题、
  表格和导航一致；五段代码恢复br换行后连缩进也一致。子页列表七项且
  has_more=false。六个画板token核对通过，原生节点分别33/106/27/24/32/18，
  共240节点；六张实际飞书预览均已导出并逐张查看，自动布局无可见重叠。
- publication-plan.json状态published_verified，publication-record.md为实际链接
  和结果入口，remote/保存创建、写入、正文/节点读回、原生图及实际预览。
  发布候选为各草稿目录publish.xml，带真实Wiki导航；build_preview检测已创建
  资源时停止，保护已存编号。README/review已刷新发布状态，原Redis文档未改。
- 支付Wiki入口：https://ocnbkcx1ug58.feishu.cn/wiki/PCQ7wP9kMikz0eklmNYcH1J1nwh 。
  本次应用测试未重跑，前述C4/5D代码与原始证据保留，未commit/push。

## 43. 电商项目总目录与 Redis 原文归档（2026-10-06，已完成并验证）

- 项目拥有者要求创建名为“电商项目”的总目录，下放支付目录和之前的Redis
  文件。本次授权更新了此前保留Redis位置的决定，目标仍为现有私有Wiki空间。
- 已创建总目录WN1jwXDj0iPXm7kwLyHcmneenlb；现有支付总览原位改名“支付”，
  七个子页保留，旧链接/画板不变。原Redis Docx KKBXdHFQqoZRCNxJv1YcFb29nkf
  改名“Redis 学习与面试总结”，改名前后正文与资源编号核对一致，没有复制重建。
- 初次移动预检查时：支付仍在空间根，Redis仍为独立Docx。wiki +move明确
  缺wiki:node:move和wiki:space:read；原创建授权不包含这两项。后续追加OAuth，
  将原支付节点整体移入总目录，再用明确指定文档的docs_to_wiki原子操作迁入
  Redis，保留原obj_token。没有--apply、成员变更或自动通知他人。
- docs/learning/payment-wiki-draft/hierarchy保存当前计划、名称/正文读回、
  原始Redis快照及操作日志；publication-plan增加organization状态，前期完整
  发布记录保留。总目录：https://ocnbkcx1ug58.feishu.cn/wiki/WN1jwXDj0iPXm7kwLyHcmneenlb 。
  本轮没有改业务代码、数据库、依赖、部署或运行应用测试，没有commit/push。
- 随后用户完成追加OAuth，原支付节点连同七个子页整体移入总目录；指定原Redis
  Docx通过独立的wiki原子迁入操作归档，未通过knowledge_organize工作流迁入。
  Redis新增Wiki节点O4iUwSvl1i4Je7keOL0ci1iAnLf，原Docx编号不变。
- 总目录已补支付/Redis入口（revision2）；支付仅局部更新相关模块导航段落
  （revision4）。空间根仅有电商项目，总目录直接子节点为支付和Redis，支付
  七个子页的节点、文档编号、标题和父节点未变；各列表has_more=false。
- 八页支付移动前后正文除标题外逐字一致，六个画板编号保留；最终导航更新
  仅影响指定段落。Redis正文除标题外逐字一致，四个画板编号保留，revision7。
  hierarchy/verification-summary.json与record.md保存最终结果；plan.json及
  publication-plan.json的organization均为organized_verified，没有待完成操作。

## 44. Phase 6A：秒杀下单、活动限购与服务边界（2026-10-06）

- 用户在秒杀学习聊天接受“待支付占名额，未付真正关闭后可再抢，正常成功后用掉资格”，
  又明确要求写第一组最小测试与下单流程。本片为Depth/Learning的服务层候选，不开放HTTP。
- 新promotions模块含FlashSale/FlashSaleItem/每单独立FlashSalePurchase。
  联合唯一(customer,item,blocking_marker)限制HELD/CONSUMED合计最多一条；
  CHECK显式要求阻挡状态非空且为1、RELEASED为NULL。本片仅创建HELD。
- create_flash_sale_order以User锁串行创建，先scope内幂等重放，再验证活动/地址/服务端活动价，
  新Order/快照/购买占位后取得Inventory NOWAIT锁，数量固定1，库存预占与流水共同提交。
  秒杀直接购买活动SKU，不读取或清空普通购物车，不替换已有正式库存锁算法。
- Order新增creation_scope，旧订单默认cart_checkout，唯一(user,scope,key)；
  普通下单查询和创建显式限定原scope，秒杀摘要含活动item与地址，避免跨入口误重放。
  两份迁移仅生成文件，未执行；跨scope同Key数据出现后不能直接逆向恢复旧唯一键。
- 因本片未接支付消费/关闭释放资格，现有支付创建和事件、取消、到期入口明确保护秒杀scope；
  批量到期仅扫描普通scope。后续需一起接正常支付与未付合法关闭，再测重购和迟到通知。
  当前秒杀订单不可付款/取消/过期，仅供隔离服务实验，不往开发库投放这类订单。
- 8项服务目标测试覆盖同用户不同Key并发（库存5）、幂等/跨scope/归属/库存不足、写后回滚
  及包括事件处理在内的生命周期保护。2项模型测试绕过User锁，直接验证唯一/CHECK。
- 实际完成manage.py check为0 issues，目标10 tests collected，迁移漂移No changes detected；
  3307不可达、Docker引擎未运行，迁移历史读取warning保留，真实MySQL目标与回归均未运行。
  3306虽可连接但未改用，未执行开发/测试库迁移、启动/停止服务、MQ或Redis实验。
- 分支codex/flash-sale-baseline基点2dacc9a，同checkout保留原C4/5D/Wiki本地修改。
  无commit/push，无新worktree；本轮不发布飞书、不改前端/依赖/部署配置/MQ任务或重试预算。
- 指南docs/learning/phase-6a-flash-sale-checkout-study-guide.md；
  记录docs/verification/phase-6a-flash-sale-checkout.md。下一步恢复隔离MySQL环境并实测本片，
  不把静态收集成功冒称并发正确或秒杀闭环完成。


## 45. 生成新代码前的 Git 保存授权（2026-10-06）

- 项目拥有者明确要求现在commit/push，并持续授权今后每次生成新代码前先保存上一批改动。
  规则写入PROJECT_INSTRUCTIONS.md并同步AGENTS.md；无需重复询问commit/push。
- 先审核分支、范围、diff、远端、敏感信息和适用检查，聚焦提交后推送当前工作分支，
  再核对远端与本地HEAD。工作区干净且提交已推送时不制造空提交。
- 既有C4、5D、Phase 6A和学习归档按逻辑分组，当前分支codex/flash-sale-baseline。
  Phase 6A仍缺真实MySQL验证，候选分支保存不表示通过验证或已整合main。
- 本轮manage.py check为0 issues；迁移漂移No changes detected，数据库历史读取warning保留；
  341项测试仅收集，27份Python AST、152份JSON及PowerShell脚本语法检查通过。
  未启动/停止服务、运行数据库测试、迁移开发库、发布飞书或生成新业务代码。
- 授权保留原Review/测试/敏感信息检查要求，不允许混入未知进行中修改、强推或覆盖远端历史。
  每次实际保存是否成功以git log、git status及远端核对为准。


## 46. Phase 6A：真实 MySQL 验收（2026-10-07）

- 用户明确要求开始前一片的隔离MySQL验证。Depth/Learning，沿用be2b4f6源码，业务代码与测试均未修改。
- 本轮开始时Docker已运行，只有MySQL8.4.10 db容器健康，宿主127.0.0.1:3307；
  未发现pytest/支付Worker，既定test_project_one不存在，应用授权与无可见测试连接已只读核对。
  没有启动/停止服务、换3306、扩数据库授权或操作开发业务数据。
- pytest在test_project_one新建、执行迁移和测试，再清理该库；不执行开发库migrate。
  目标10 passed、79 subtests passed，15.63秒；两独立连接59/60，一笔创建与一笔限购409。
  六类事实各1、活动价89.00、数量1、购物车数量2不变，库存5/0→4/1，HELD/1。
- ordering/payments回归151 passed、83 subtests passed，100.58秒；6项5D显式实验skip，
  12项真实RabbitMQ集成未选入。本轮未重跑真实MQ/Redis或5D锁等待实验，不算重新验证旧证据。
- 后检测试库不存在、测试连接0，开发schema表数前后26；check为0 issues，迁移漂移No changes detected。
- 当前结果不等于HTTP/前端验收、资格消费/释放或旧订单事件不影响重购已实现；
  不证明User记录锁等待、无死锁或性能胜负。只验证新建隔离库迁移，不验证已有开发数据升级/逆向回退。
- docs/verification/phase-6a-flash-sale-checkout-data.json记录原始并发输出、结果、源码指纹和边界；
  同目录target-output.txt/regression-output.txt保留原始pytest输出，阶段指南与当前路线图同步。
- 按持续授权保存本片证据；分支安全整合与推送结果以Git历史、远端核对和最终汇报为准。
  下一片一并接入正常支付资格核销与真正未付关闭释放，再测O1关闭→O2重购、旧Key和迟到事件。

## 47. Phase 6B：资格消费、释放与旧订单事件隔离（2026-10-07）

- 项目拥有者要求接着推进已确认的资格生命周期。Depth/Learning；沿用已推送的8d1bfed，继续在现有codex/flash-sale-baseline隔离本次支付/库存事务增量。开始新代码前工作区干净，main/工作分支远端均等于该基点，无空提交。
- 每单一条Purchase：正常成功HELD/1→CONSUMED/1，与Order/Attempt/库存/事件同业务事务提交；真正未付关闭HELD/1→RELEASED/NULL，与库存释放和订单关闭同事务。已付取消恢复库存与退款模拟，但资格仍CONSUMED/1。
- CREATED/PENDING/UNKNOWN仍禁止取消或到期释放。可信FAILED只结束Attempt，资格直到订单真正关闭才释放。到期扫描现在包含秒杀订单，每单独立事务；未确认支付仍跳过。
- 资格helper只按原order锁购买记录，核对owner/status/marker。支付和关闭保持Order→Attempt→Purchase→Reservation→Inventory；缺失或冲突抛异常并回滚，支付Inbox另留ERROR/BLOCKED/flash_sale_purchase_invariant_error，不盲目重试或自动补资格。
- O1未付关闭后可用新Key创建O2；旧Key始终返回O1。O1迟到成功在原关闭分支只更新旧Attempt、Event与唯一退款模拟，不消费资格、不核销库存、不查同顾客当前名额。资金侧Attempt成功不代表已关闭O1成为正常活动成功购买。
- 真实MySQL8.4.10，已有Docker db健康，127.0.0.1:3307；没有启动/停止服务、改GRANT、运行开发库迁移或业务SQL。临时test_project_one由pytest创建/迁移/清理，目标11 passed、13 subtests passed，23.26秒。
- 目标实测包括正常/重复支付、付后取消保持消费、未付取消/到期/扫描、三种未确认Attempt保护、取消与到期两种O1→O2→旧事件、实写后异常回滚与受控重试、资格冲突BLOCKED。两连接571/572同时放行，本轮到期先返回payment_unresolved，最终PAID/CONSUMED1、库存4/0、COMMIT1/RELEASE0。
- promotions/ordering/payments相关回归171 passed、163 subtests passed，149.47秒；6项5D显式锁实验skip、12项真实MQ集成未选入。check为0 issues，迁移漂移No changes detected。后检测试库不存在、无可见测试连接，开发schema表数前后26。
- 1205为资格实写后的测试注入，不是实际MySQL锁超时/死锁观测；两连接实验未采集锁等待、未强制两取锁顺序，不证明全无死锁或性能胜出。本轮未重跑真实MQ/Redis/5D，无秒杀下单HTTP/前端、现有开发数据升级或逆向迁移验收。
- docs/learning/phase-6b-flash-sale-lifecycle-study-guide.md解释规则；docs/verification/phase-6b-flash-sale-lifecycle.md及同名data.json、target/regression-output.txt保留17条实际ORM快照、源码/输出指纹和完整边界。6A原7项下单及fixture方法AST保持，旧第8项临时禁用断言移除；6A历史证据不改写。
- 按持续授权保存并推送本片，验证后安全整合main，最终提交/远端状态以Git核对为准。下一步先由项目拥有者解释迟到资金成功和正常购买成功的区别，再进入naive超卖复现与MySQL方案对照；不横向扩展普通促销或异步业务。

## 48. Phase 6C-1：完整秒杀下单的双顾客库存竞争（2026-10-07）

- 用户要求开始下一片，并正确预测B遇到A库存锁占用会立即返回繁忙。Depth/Learning，本片只补当前NOWAIT完整服务基线，原子条件更新仍下一片候选。已有Phase3 naive超卖和库存SQL策略对比已记录，复用历史证据，不重复称为首次复现。
- 开始前main/现有codex/flash-sale-baseline/两远端均f713d03，工作区干净；无需空提交。复用现有分支隔离未提交/回滚实验，不生成worktree或源码副本。
- 仅新增test_flash_sale_contention.py及本片学习/证据文件，更新当前进度；既有业务代码、模型、旧测试、迁移、依赖、配置、HTTP/前端未改。2个不同Customer/地址、同活动商品、活动价89/普通129、初始库存1/0、购物车数量2/3固定。
- 3项Oracle：两个独立连接同时服务下单恰好一单成功；A完整建单仍持外层事务时B实际库存NOWAIT/3572→stock_busy并在release前退出；A提交后B原Key库存不足、A成功Key幂等重放；A回滚后六类事实0且库存1/0，B原Key可成功，不留失败订单/资格/Key。
- 首轮2 failed/1 passed/18 subtests、18.65秒，测试观察器退出时FrozenInstanceError掩盖原OrderRuleError。contextlib会修改__traceback__，而现有业务异常为冻结dataclass；无DB最小复现确认。只在观察器内部、服务atomic退出后捕获实际业务错误，真实SQL/回滚/判定不变；保留失败输出与修正前测试指纹。
- 修正后真实MySQL8.4.10目标3 passed/42 subtests、20.22秒。提交场景A3258/B3259、观察3256；回滚A3254/B3255、观察3250；同时抢A3261/B3262、观察3260。2个hold场景均采真实FOR UPDATE NOWAIT、3572及release前完成返回。
- A事务内看到六事实各1、库存0/1；B及观察连接在A释放前看到已提交的六事实0、库存1/0，这是未提交可见性，不表示A尚未写入。提交后6事实归A；回滚后0，B原Key再试后6事实归B。成功单qty1/89、HELD1/RESERVED/RESERVE(-1,+1)与两购物车不变均验证。
- 模块回归promotions全部23 passed/122 subtests、58.92秒；未重跑完整ordering/payments或真实MQ/Redis/5D。check0 issues、迁移漂移No changes detected。pytest临时库建表/迁移/夹具/清理；后检test_project_one不存在、可见连接0，开发schema表数前后26，无开发migrate/业务写入或服务启停/GRANT改动。
- 记录docs/learning/phase-6c1-flash-sale-contention-study-guide.md；verification同名.md/.json以及initial-failure/target/regression-output.txt保留所有运行、3条实际ORM证据、源码和输出指纹。本机服务层2顾客正确性实验，无HTTP网络、压测/性能指标、锁等待表观测或普遍无死锁证明。
- 按持续授权保存/推送，经验证安全整合main，实际提交/远端以Git核对为准。下一片将条件UPDATE候选放入相同完整下单事务，比较首单提交/回滚与失败重试；在负载和权衡证据形成前不替换正式策略或宣称性能胜出。

## 49. Phase 6C-2：同完整秒杀事务的库存策略对照（2026-10-07）

- 用户要求开始下一片。Depth/Learning，承接6C-1；开始前main/现有codex分支与两远端都为8465d2a，工作区干净，之前成果已推送，无空提交。继续复用现有分支隔离实验，不新建worktree或源码副本。
- 最小实现：正式create_flash_sale_order仅提取库存步骤为私有_reserve_flash_sale_inventory(sku)，默认仍NOWAIT/check/save，公开签名、返回契约与锁序不变。将helper内联的AST等于基点checkout。候选只在新test_flash_sale_inventory_comparison.py用F表达式UPDATE available>=1、available-1/reserved+1，影响0行抛库存不足离开atomic、1行后继续原Reservation/Movement；没有正式策略开关。
- 两算法共用同一份User/Address/活动/Order/快照/Item/Purchase逻辑；候选patch只替换库存函数并覆盖双方线程、join、重试/重放全生命周期。6C-1只分离无test_方法的FlashSaleContentionFixture，原14方法AST保持；collect29无继承重复。
- 测试固定2个不同Customer/地址、同活动商品、活动89/普通129、库存1/0、cart2/3；两算法各比较A完整下单后外层事务提交或回滚。B失败不留6类事实/Key/资格，成功单关系/归属、qty1/HELD1/RESERVED/RESERVE(-1,+1)、cart不变及winner Key重放均核对。
- 应用账号data_lock_waits读取能力只读检查1142，本片不扩GRANT或切换特权观察连接。只按B的connectionID+test库读PROCESSLIST，真实Query/库存UPDATE/STATEupdating，A未release且B未done。支持受控窗口内服务端UPDATE未完成、放行后0/1行；不代表采到RECORD WAITING/锁索引或B→A等待图。
- 真实MySQL8.4.10目标6 passed/138 subtests，27.16秒，首轮通过。NOWAIT commit A3522/B3523、rollback A3526/B3527：均真实3572并在放行前busy，A提交后B原Key不足，A回滚后B原Key重试成功。
- 条件commit A3512/B3513、观察3511，A提交后更新0行，B库存不足且自己的订单/Purchase回滚；条件rollback A3515/B3516、观察3514，A回滚后更新1行，B第一次请求直接建Order7，无需重发。最终6事实各1、库存0/1，均无败方残留且成功Key重放不重复预占。
- 候选同用户库存5双Key连接3519/3520仅一单，败方flash_sale_limit_reached且未发库存SQL，库存4/1。候选Movement实际完成后注入异常，先确认全部6事实/库存已改，再验证全部回滚到0事实/库存1,0；原Key重试与重放成功，最终仅一套预占和流水。
- promotions模块回归29 passed/260 subtests，74.04秒，首次通过；check0 issues、迁移漂移No changes detected。临时test_project_one创建/迁移/夹具/清理，后检不存在、可见连接0、开发schema前后26；无开发migrate/业务写入、服务启停、权限/依赖/配置/模型/迁移/HTTP前端变更。未重跑完整ordering/payments或真实MQ/Redis/5D。
- docs/learning/phase-6c2-flash-sale-inventory-comparison-study-guide.md与verification同名.md/.json、target/regression-output.txt记录6条实际ORM/SQL/行数/连接快照、源码/输出指纹和条件限制。测试耗时不是接口性能，受控等待不是普遍无死锁证明；等待期间B连接/事务及既有User/Address/Purchase锁仍保留，资源成本未测。
- 正式策略继续默认NOWAIT；本片是实验候选正确性与行为对照，不是最终采用或性能胜出。下一片设计同条件负载、忙/不足/成功与重试成本、延迟分布/连接占用/死锁证据，再据结果形成ADR。按持续授权保存并验证后整合main，提交/远端以Git核对为准。

## 50. Phase 6C-3：有限负载、重试与连接成本（2026-10-08）

- 用户选中“下一片测同条件负载、重试成本、延迟和连接占用”要求开始；Depth/Learning。用户正确确认应看首次请求到最终结果总耗时，已说明旅程含调用/退避，执行器排队单列，不声称已掌握全部。开始前main/codex分支/两远端3f3afbc且干净，复用现有分支，前序已保存，无空提交。
- 仅新增gated test_flash_sale_inventory_load.py和标准库 scripts/analyze_flash_sale_load.py，默认PROJECT_ONE_TEST_FLASH_SALE_LOAD=false跳过。正式代码、库存helper/候选本身、旧测试、模型、配置、依赖、迁移、HTTP/前端均未改。现有条件候选仅测试patch，默认仍NOWAIT。
- 固定8worker/24不同顾客，共用完整下单事务，库存24/6，预算1或仅busy时总3次、同Key/退避5/10ms，3次重复、24测量批次，顺序轮换；另2次独立无竞争预热排除统计。每批新SKU/活动/库存/用户/地址/cart，活动89/普通129/qty1/cart2或3。
- 每worker连接预开后APP PROCESSLIST确认8CID，共同t0放行；计时中无SQLwrapper/服务器采样/人工持锁。记录原始ns服务调用、旅程、执行器排队和批次E2E，连接开放区间裁剪[0,last_terminal]、service-active含Python/DB等待，峰8由配置决定。Thread wrapper顶层和OPTIONS复制后测试客户端超时5/10/10、session lock5秒，不修改默认配置；参数独立连接SELECT1已只读验证。
- 真实MySQL8.4.10、READ-COMMITTED、max_connections151、既有3307容器健康；宿主Ryzen78840HX12核24逻辑/16,299,335,680bytes，Docker24CPU/7,889,154,048bytes、容器无额外资源限制。本片无启停服务、GRANT修改、开发migrate或业务数据写入。
- 目标1 passed，23.58秒；完整24 measured+2warmup、576旅程、767调用、250订单、191额外retry，全部业务校验/连接清理有效，无1205/1213/意外错误/无效批次；383次真实3572属于正常busy。数据库六类事实、归属/价/地址/数量、HELD1/RESERVED/流水/库存守恒与失败无残留均核对，不把性能数字代替正确性。
- 充足库存单次NOWAIT创建3/3/4、候选24/24/24；3次预算NOWAIT11/10/10且60/57/58调用、候选24/24/24且每批24调用。快速拒绝较短延迟不能说顾客购买完成更快。充足库存3次预算候选总连接开放1.709–2.002s，NOWAIT1.889–1.932s，不能声称每轮候选都更少总连接秒。
- 紧张库存3次预算两算法都创建6单；NOWAIT52–55调用、旅程p95 106.42–121.91ms、连接开放1.703–1.834s；候选24调用、90.01–94.55ms、1.016–1.072s。相同产出下这组候选成本更低；所有范围是3轮实际值，不平均pct，n24的p99=max非稳定尾估计，连接开放/服务活跃非SQL CPU或锁等待秒。
- 独立分析检查24完整唯一矩阵/2预热，终值=最后attempt、worker/CID映射、同worker服务区间无重叠、原始ns分位数/半开峰值/裁剪累计区间与两种吞吐；另agent不用parser函数自行重算逐字段匹配。逐批原值与异常保持，合计250创建/192busy/134不足仅完整性统计，不把不同库存/预算混成性能基准。
- 模块回归29 passed/1 skipped/260 subtests，36.58s；skip本片负载开关关闭，check0 issues、迁移漂移No changes detected。后检临时test_project_one清理、可见连接0、开发schema前后26；未重跑全ordering/payments或真实MQ/Redis。
- docs/learning/phase-6c3-flash-sale-inventory-load-study-guide.md与verification同名.md/data.json/analysis.json/target/regression-output.txt记录真实环境、24轮raw（2warmup另列）、每批时延/连接/结果、源码与输出指纹、纯标准库复算命令。raw不重复复制到summary。
- 有限closed batch、8worker/24旅程、无网络HTTP/持续到达率/生产容量/锁等待图/普遍无死锁结论；未固定CPU或独占宿主资源。结果支持评估候选，等待占用连接/事务和快速拒绝需要重试的代价仍须项目拥有者独立解释。下一步结合数据确认故障/超时行为、完成选型ADR；本片不自动正式切换。按持续授权验证后保存/推送与整合main，提交/远端以Git核对为准。

## 51. Phase 6C-4：条件 UPDATE 选型收口（2026-10-08）

- 拥有者对“单个商品的库存预占采用条件 UPDATE”明确回复 ok 并要求开始下一片。本片 Depth/Learning，只保存策略决定、解释及进度，不继续新负载组合或修改正式代码。
- 开始前 main 与 origin/main 同为 86b1bd8，工作区干净，前序已推送，无空提交。ADR 0001 状态为 Accepted decision / Implementation pending：单活动 SKU、数量 1；当前正式 helper 仍 NOWAIT，普通多 SKU 下单不在决定内。
- 数据库按唯一 SKU 与 available>=1 条件执行 available-1/reserved+1，0 行为无可预占匹配（含库存不足或记录缺失），1 行为库存步骤成功但整单仍待事务提交。保留用户串行化、地址归属、服务端价格、scope Key、购买资格约束与 Purchase→Inventory 相对锁序。
- 拥有者正确预测首单提交后等候者更新 0 行、返回库存不足；已解释前提为未先发生等待超时或其他错误，并给出首单回滚后更新 1 行的分支。不声称已经独立掌握全部失败合同或模块验收。
- 复用 6C-2/3 真实历史证据，不重跑测试：同产出库存6/三次预算，候选24调用与NOWAIT52–55，有限本机数据支持取舍，不推广生产容量、所有负载更快或普遍无死锁。候选仍有 GET，等待仍保留事务、连接和此前相关锁。
- 1205/1213 不等同 0 行或库存不足。MySQL 锁等待超时默认可能仅回滚当前语句，完整业务必须让异常退出 atomic；正式等待预算、错误映射和自动重试政策尚待下一片确认与验证，没有增加配置或接口合同。
- docs/adr/0001-flash-sale-inventory-conditional-update.md 与 docs/learning/phase-6c4-flash-sale-strategy-decision-study-guide.md 保存决定、教学和停止条件。仅静态核对链接、历史证据指纹及变更范围；没有数据库操作、迁移、业务测试或 MQ/Redis/浏览器验收。
- Redis Lua 在总纲必做表与候选清单的措辞需另行确认，此次 ok 不能据以删除该范围或自动实现。下一片正式最小落地与失败合同，再接 HTTP、极简页面和端到端验收；不自动扩展普通促销。提交与远端状态以 Git 核对为准。

## 52. Phase 6C-5：正式条件 UPDATE 与失败回滚（2026-10-08）

- 用户要求下一步，并明确选择沿用现有会话上限，本机50秒；本片只落地库存步骤与错误处理，隔离测试竞争连接用1秒，HTTP接入前再确定顾客等待预算。Depth/Learning，面试目标是解释库存边界、语句失败/整单回滚、原Key重试与证据边界；达到必要证据后停止，不增加压测矩阵。
- 开始前main与远端79e2caa且干净，复用现有codex/flash-sale-baseline，将该分支快进并推送到同一基点、核对远端后才生成代码；没有新分支、worktree或源码副本。完成阶段按持续授权提交、推送及安全整合main，具体结果以Git核对为准。
- 正式_reserve_flash_sale_inventory按sku/available>=1执行F表达式available-1/reserved+1，0行保持库存不足，1行GET后继续原预占/流水。仅该UPDATE的1205/1213转stock_busy409并保留cause，其他数据库异常上抛，无自动重试；异常离开原atomic整单回滚。前面User/Address锁和其他SQL错误不在该转换范围。
- create_flash_sale_order与指纹函数AST不变，保留User串行化、地址归属、服务端价格、资格/Key及相对锁序。原NOWAIThelper函数体完整迁入测试，NOWAIT分支显式patch历史实现、条件分支直接正式helper；三个既有文件31个原方法AST保持（排除策略绑定方法）。模型、迁移、配置、依赖、普通下单、支付/关闭、HTTP/前端未改；旧证据不重写。
- 真实MySQL8.4.10、READ-COMMITTED，现有127.0.0.1:3307 db健康，rollback_on_timeout=false、deadlock_detect=true。预检/后检session等待50秒、临时test_project_one不存在、可见测试连接0、开发schema表数26。没有服务启停、GRANT修改、开发migrate或业务数据写入；pytest只操作临时测试库。
- 目标9 passed/198子场景、19.14秒，首次通过：3项新失败合同加6项既有完整策略对照。真实1205 Observer1685/Holder1686/Contender1687；holder只锁库存不写业务。竞争事务UPDATE前订单/Item/快照/Purchase各1，Reservation/Movement各0，实际SQL抛1205且只尝试一次、约1.015秒；PROCESSLIST看到放行前尚未完成UPDATE，不等于锁索引或阻塞图证据。
- 竞争请求在holder放行前返回stock_busy409、cause1205、退出atomic恢复autocommit；两观察视角六类事实0、库存1/0、无失败Key订单。放行后原Key创建Order6、同Key重放Order6/created=false，最终六事实各1、库存0/1、HELD1、qty1/活动89、预占/流水一次且cart不变；observer会话与OPTIONS前后保持。
- 1213/2006在UPDATE发往服务器前注入，actual_sql_attempted=false：分别验证已知错误映射/保留cause、未知错误原样透传，并核对完整回滚及原Key重试/重放。不是实际死锁、网络断连或提交不确定恢复。既有条件提交0行、回滚1行、同用户库存5双Key最多一单、流水实写后异常回滚均重新通过；历史NOWAIT仍实际3572。
- promotions/ordering/payments相关回归183 passed/403子场景、141.49秒，7 skip（负载1、显式支付到期并发6），12真实MQ未选入；check0 issues、迁移漂移No changes detected。没有重跑真实MQ/Redis/5D或负载矩阵；负载策略绑定仅静态核对。不把测试时长当性能提升，不证明生产容量或普遍无死锁。
- docs/learning/phase-6c5-flash-sale-inventory-failures-study-guide.md与verification同名.md/data.json、target/regression-output.txt保留教学、3条新失败完整快照、6项对照摘要、全部9条raw、源码/输出指纹及边界。ADR更新为Accepted/Implemented，仅正式服务层；下一片最小HTTP前确定顾客等待预算，随后极简页面与端到端/独立讲述验收。Redis Lua范围仍另行确认，分布式锁默认No-Go。

## 53. Phase 6D：HTTP 基础候选，等待政策待答复（2026-10-08）

- 用户选中“确定顾客等待预算，接入最小活动查询和下单HTTP”要求开始。面试目标是独立解释Session/CSRF、输入、服务端价格/用户、幂等和业务事务；HTTP适配Supporting/Pair，等待/事务政策Depth/Learning。最小合同与回归达到后不增加负载实验。
- 开始前main/现有codex分支及两个远端均b4456bd、工作区干净；上一片已推送，无空提交。复用codex/flash-sale-baseline隔离候选，没有新分支/worktree/源码副本。本轮只保存该分支，等待确认前不合main。
- 明确询问临时5秒HTTP调用会话行锁等待并恢复，或继续沿用现有值（本机50秒）；目前没有答复，不能用默认选项/沉默当确认。因此未实现5秒设置、恢复逻辑、前置锁短等待合同、自动重试或总HTTP截止；原ADR接入前等待门槛仍未完成。
- 新增promotions serializers/views/urls与test_flash_sale_api，总URL一行include，services只更新模块说明；除模块docstring外整个业务模块AST与基点相同。原模型、配置、依赖、迁移、测试、库存/资格/Key/锁序、普通下单、支付/关闭与前端未改。
- GET /api/flash-sales/items/匿名列表，仅sale/item/SKU/Product/Category全启用且starts<=now<ends；一次捕获now、关联预加载。仅公开购买id与活动/商品/价/时间、CNY、qty1和in_stock布尔，不公开库存数字或顾客/资格。售罄/缺Inventory仍显示false；小规模不分页，没有容量或有货即成交保证。
- POST /api/flash-sales/orders/继承SessionAuthentication与CSRF、IsAuthenticated；两个ID正整数到BigAutoField上限，Key trim/1–128可打印字符。只用request.user与服务端活动价/qty1，多余price/quantity/user_id/scope字段忽略且无作用。新建201、重放200，复用OrderSerializer与原本人订单查询；View只映射OrderRuleError，原库存1205/1213 stock_busy与其他数据库错误透传保持。
- 12项HTTP目标、162子场景通过，19.73秒首次通过；实际检查五级可见性/时间边界、公开字段、无库存/售罄回滚、方法/Session/CSRF、ID/Key、伪造价格/数量/用户无效、重放/冲突/限购、失效/改价后重放、地址/订单归属、跨scope/顾客同Key及六事实/库存/cart。
- 相关promotions/ordering/payments加原登录API回归207 passed/565子场景，152.27秒；7 skip（负载1、显式支付到期锁6）、12真实MQ未选入。check0、迁移漂移No changes detected。独立只读评审无阻止基础候选保存的问题，要求等等待答复再合main；本轮没有另行制造HTTP数据库锁超时或注入故障。
- 已有MySQL8.4.10/3307/READ-COMMITTED，session50、rollback_on_timeout=false；pytest只操作临时test_project_one。前后临时库不存在、可见连接0、开发schema表数26，后检session50；没有开发迁移/业务SQL、服务启停/GRANT改动。APIClient为进程内请求测试，force_login建立Session，不是浏览器/真实网络验收；未重跑真实MQ/Redis/5D/负载矩阵。
- docs/learning/phase-6d-flash-sale-http-study-guide.md与verification同名.md/data.json、target/regression-output.txt保存候选合同、完整请求解释、实测与源码/输出指纹；没有逐请求SQL/ORM raw新采集或历史证据改写。候选不等于Phase6D全部完成，待拥有者选等待方案后补必要处理/验证并整合main，再做极简页面与端到端验收。

## 54. Phase 6D-2：HTTP临时5秒、事务与连接恢复（2026-10-08）

- 用户选中“临时5秒并恢复原会话值”并要求开始。该答复解除第53节的等待门槛；历史记录、JSON和raw保留。面试目标是独立解释行锁等待、整单回滚、Key重试与持久连接状态，HTTP适配Supporting/Pair、失败边界Depth/Learning。必要证据通过后停止，不新增负载或基础设施。
- 开始前codex/flash-sale-baseline及远端同72e6291且干净，main/远端main同b4456bd；前批已保存，无空提交、新分支或worktree。复用原隔离分支，按持续授权验证、提交/推送和安全整合main；最终提交与远端以Git核对为准。
- 新http_services适配在原atomic外读取SESSION/暂设5，事务结束后恢复实际原值。拒绝外层atomic/autocommit=false，连接更换后不写旧值；设置失败不下单，恢复失败告警并丢弃连接，保留已提交结果、业务拒绝或原异常。原服务其他SQL1205/1213退出atomic后转checkout_busy409；原库存UPDATE仍stock_busy，未知错误上抛，没有自动重试或总请求期限。
- View仅改调用，新test_flash_sale_http_wait有9项；原业务services与前片内容完全相同，14个旧源码/测试指纹保持。模型、迁移、配置、依赖、普通下单、支付/关闭、前端未修改。第一片JSON/目标/回归raw保持原样，本片自查，不把旧独立评审当作新代码独立评审。
- 真实隔离库存Observer586/Holder587/Contender588，User Observer590/Holder591/Contender592；持锁者无业务写入，PROCESSLIST在放行前观察目标SQL未完成，两实际语句SESSION5并各抛1205，单次记录5.0秒。分别HTTP409 stock_busy/checkout_busy，在holder放行前返回，六类事实0、库存5/0、原连接恢复50；放行后原Key201、重放200/同单，最终六事实各1、库存4/1、价89/qty1、HELD、购物车不变。
- 实际自定义原值17的成功/重放/限购拒绝均17→5→17，仅成功首次执行库存UPDATE。注入库存前1205、Movement真实INSERT后1213、用户前2006验证回滚与原值13恢复；设置、恢复、close确认失败及连接替换为注入，不能声称真实死锁/断连/提交不确定恢复。
- 用户正确回答“订单已提交但恢复失败，应保留成功响应并丢弃状态不明连接”；已解释订单事实与清理动作不同。该回答只证明一个判断，仍需闭卷解释原值恢复、错误分类、重试和证据边界，不算整个秒杀模块验收。
- 目标21 passed/259子场景、40.14秒，首次通过；相关promotions/ordering/payments+Session API回归216 passed/662子场景、262.10秒，7 skip（负载1/支付到期锁6）、12真实MQ未选入。check0、迁移漂移No changes detected；没有重新跑MQ/Redis/负载/显式5D锁实验，不以测试耗时作性能结论。
- 现有MySQL8.4.10/3307/READ-COMMITTED，rollback_on_timeout=false/deadlock_detect=true。前后临时test_project_one不存在、可见测试连接0、开发schema表数26、SESSION/GLOBAL等待均50；pytest只操作临时测试库，没有开发迁移/业务写入、GRANT、服务启停或部署。
- docs/verification/phase-6d2-flash-sale-http-wait.md/data.json、target/regression-output.txt保存12条新增场景快照、实测/注入区分与源码/输出指纹；6D指南、ADR及当前进度同步。APIClient是进程内请求，浏览器/真实网络/极简顾客入口仍待独立切片；Redis Lua范围另行决定，不能把本片当成整个Phase6结束。

## 55. 新聊天交接与Redis Lua暂缓（2026-10-08）

- 拥有者要求新建聊天，读取/继承当前聊天全部信息并继续下一part；明确“先不加入Redis Lua，后续再进行评估”。源聊天为`009 -- 秒杀与库存并发：Phase 6 学习 (2)`，ID `01a117dc-7164-71e3-a6e4-1a2446f4d48c`。
- 当前Lua为拥有者确认暂缓，不再作为当前MySQL秒杀/HTTP收尾的必做门槛。Charter v1.6把它从旧Level2表调整为条件性后续评估；旧切片的待确认措辞保留为历史，不覆盖本决定。既有Redis缓存主线保留，不新增Lua代码、预扣或补偿实验。
- 6D两提交`72e6291`、`0317825`已安全合入并推送main；交接前main/远端均0317825、工作区干净。目标21/259子场景、相关回归216/662子场景沿用第54节的真实结果，本轮文档更新未重新运行业务测试。
- 下一part为最小秒杀顾客入口、真实浏览器端到端与独立讲述验收；复用Session/CSRF、本人地址、原Key、服务端价/数量与现有订单/Mock支付流程。以正常创建、原Key重放、关键失败和后续订单状态的最小证据收尾，不自动扩展新基础设施或负载矩阵。
- 继续遵守先说明具体面试/能力收益、初学者中文教学、核心Learning/辅助Pair、生成新代码前保存已审核前批并核对远端的持续授权。开发库迁移/演示数据属于另行说明与确认的操作，不从“新聊天继续”推导出数据库写入授权。
- 本轮只修改Charter/Context/详细项目规则，静态核对范围、链接和diff，保存后在新聊天继续；没有改源码、依赖、配置、开发数据库或历史测试证据。

## 56. Phase 6E：极简顾客入口与浏览器验收（2026-10-08）

- 用户回答“地址不一样冲突，409”，并要求开始next part；已解释同Key改地址对应idempotency_conflict，仍应先用原item/address/Key确认原购买。只证明该单题判断，独立完整链路复述尚未验收。
- 本片目标是用浏览器与MySQL证明响应丢失后刷新仍取回同单，库存只预占一次；Depth/Learning解释意图与失败，Supporting/Pair交付页面胶水。最小正常创建、原Key重放、关键拒绝及Mock支付证据通过后收束，不追加Lua、MQ或负载。
- 开始时main/远端均e47b76d且干净。新增FlashSalesView、flashSales API、flashSale类型和flashSaleIntent，App/nav与router共六个前端文件；实现67d8c82已提交推送main且远端核对一致。后端、模型/迁移、依赖、Vite代理、全局样式、普通Checkout、支付/关闭未改。
- 首次POST前成功sessionStorage保存并冻结{userId,key,itemId,addressId}；存储失败不POST。按用户分区、发送前/me核对身份。刷新待确认直接用原参数，不被列表无货/消失或地址列表变化挡住；未知网络/5xx/解析/403/繁忙/冲突保留，不自动换Key。明确拒绝与确认后须主动开始新购买才清理。仅同标签页刷新恢复，不声称跨标签页/关闭后/跨设备协调。
- 使用专用缓存Playwright CLI（Browser plugin absent）和真实隔离Django testserver/Vite。派生catalog fixture36对象，库存401初始1/0，活动701/项711/SKU301/价89。合成注册、登录与两本人地址均只写test_project_one。
- 实际201后客户端abort注入：一单/六类交易事实各一、一次RESERVE，库存0/1；刷新四字段不变、列表无货仍可确认。真200同public_id；刻意浏览器fetch同Key改地址真409/idempotency_conflict，数据库快照完全不变，不声称UI可改冻结参数。支付后PAID/Attempt SUCCEEDED/Event PROCESSED/Purchase CONSUMED/Reservation COMMITTED、库存0/0，RESERVE/COMMIT各一；再次原Key200返回当前PAID。
- 存储失败注入下单POST0；503、畸形200、有效Session下403响应注入保持pending/原参数，不准新购买。响应丢失为真实201后的客户端故障注入，不是真实断网/数据库提交不确定恢复。账号切换旧Cookie无法恢复（Django注销旧Session）；原合成账号重新登录后意图恢复，未访问外部账号。
- 桌面1280×720与手机390×844截图已查看，手机scrollWidth390无横向溢出，无框架覆盖层；正常刷新后控制台0errors/0warnings。匿名与故障/拒绝场景的资源失败日志为预期结果。
- 前端build通过；独立静态审查无阻塞。收尾第一次停止PID的路径断言失败后误开始target，该结果不作正式串行证据；随后停本轮进程/浏览器，确认库/连接0与端口空闲后重跑。正式目标12passed/162子场景19.95秒，生命周期/支付API/Session回归51passed/19子场景57.25秒；不把历史216项或耗时当本轮/性能证据。
- 前后测试库不存在、测试连接0、开发schema26表；后检SESSION/GLOBAL等待50，8000/5173无监听。只使用可丢弃测试库，没有开发迁移/业务写入、外部发布、MQ/Redis/负载重跑。记录与JSON/正式raw见docs/verification/phase-6e-flash-sale-browser.md及关联文件；截图在项目外。
- 下一步仅闭卷复述浏览器→HTTP→业务事务→MySQL，解释200同单/一次预占、409异参及注入边界。独立讲述通过前不标整个Phase6总验收完成，不自动追加基础设施。
- 用户追问学习指南后补齐docs/learning/phase-6e-flash-sale-browser-study-guide.md，并在6D指南补导航。以当前源码说明完整请求、四字段冻结/恢复、客户端pending与订单PENDING_PAYMENT、错误分类、关键源码、89元前后值、证据限制与小练习；仅文档静态核对，不重跑应用/浏览器/数据库，也不把参考口述当作用户已掌握。

## 57. 订单列表分页与后续Lua/ES计划（2026-10-08）

- 拥有者已决定加入Redis Lua与Elasticsearch；Lua先做原子流量准入、验证数据库保护效果，MySQL仍为订单/库存/支付事实来源。计划顺序：订单分页与真实查询优化基线 → Lua准入 → ES商品搜索 → 部署/收尾。该最新决定覆盖第55节和旧规则中的Lua暂缓/ES尚未采用措辞；这里只记录已确认计划，不声称Lua/ES已实现。Phase6E独立讲述仍待完成，不能因开启新片标为已掌握。
- 用户明确要求暂停知识点讲解，直接开始项目模块，已展示20条/页、归属/排序、空页、错误处理及20+1验收规则后要求开始。本片Supporting/Pair交付前后端分页，查询优化证据留到下一片Depth/Learning。
- 开始前main/origin main均18ffbe2且核对一致，无待保存的本聊天已审核代码；保留既有PROJECT_INSTRUCTIONS.md、Phase6E指南及知识库草稿修改，未提交或覆盖。普通小片直接main，无新分支/worktree/源码副本。
- OrderCollectionView GET使用独立OrderPagination，固定20、不接受last别名；先本人过滤、-created_at/-id排序，再切片、关联预加载和序列化，返回count/next/previous/results。POST、详情及交易服务不变。前端复用PaginatedResponse类型，翻页写URL，刷新/重试保留当前页，错误页可返回第一页；取消旧请求并检查取消状态。
- 新增6项分页目标，适配订单API和秒杀API现有数组断言。真实隔离MySQL目标28 passed/171子场景（31.83秒）；购物车API/订单生命周期/支付API/Session回归63 passed/8子场景（64.05秒）。系统检查0、迁移漂移No changes detected、前端构建通过。认证固定后的订单数据查询预算为每个非空页3条，不代表整个Session接口只有3条SQL。
- Playwright专用会话、Vite5181使用模拟响应验证45笔20/20/5、末页禁用、刷新页码恢复、一次503重试、abc页回到第一页、99页404和手机390px无横向溢出，截图已查看；JS运行时异常0。APIClient/MySQL是真实数据库验证，浏览器部分不是实时API端到端。首次页面脚本配置失败已修正，不算通过。
- pytest前后test_project_one不存在、可见测试连接0，开发schema前后30表。未做开发迁移/业务写入，未改索引/依赖/部署配置/库存/支付/Lua/ES，未重跑MQ/Redis/负载矩阵；保留原有8000/5173进程。验证记录和raw见docs/verification/phase-3-order-list-pagination.md及对应target/regression-output.txt。
- 页码分页有COUNT与深OFFSET成本，跨请求新增订单可能移动边界；本片不宣称性能改善、生产容量或稳定快照。下一片采集隔离数据的实际完整SQL、已有索引与查询计划，拆开分页和索引的收益后再选择索引；必要证据足够后收片。

## 58. 秒杀知识库：按业务问题归档（2026-10-08）

- 用户明确要求创建知识库并用lark-operations/lark-whiteboard画时序图，沿用已确认的电商项目Wiki目标。新增“秒杀与库存并发”总览及六个问题页，入口：https://ocnbkcx1ug58.feishu.cn/wiki/B49NwtiePiCqqFkz61kc4ac9n79 。既有支付/Redis内容未覆盖，项目总目录仅追加秒杀导航。
- 面试与能力目标是独立解释幂等与限购、条件UPDATE的选型/替代方案/实际收益、事务失败与锁等待、支付资格生命周期与MQ价值、浏览器响应丢失。按“业务场景→规则→为什么→取舍→流程→证据与边界”组织，保留少量真实核心代码；停止于充分归档证据，没有追加技术实验或实现。
- 七页正文与导航、表格、七段代码经远端读回对照；六张原生可编辑画板共148节点，节点类型/文字/分支和箭头结构与本地相符，本地图及飞书实际导出预览已查看。固定源码快照18ffbe2f2bf674d31f3d2dc055976aedd41af010，历史应用测试与故障注入按原记录注明，不当作本轮实测。详细候选、Mermaid/PNG、远端快照、发布记录和核对结果在docs/learning/draft_59a6673d_folder/。
- 当前默认同步支付与可选MQ支付事件流程分别标明；秒杀下单仍同步MySQL。知识库计划说明已同步v1.8的后续Lua原子准入与ES商品搜索计划，并读回第06页revision4；它们尚未实现，本次没有新增预扣、分布式锁或搜索代码。源码与历史实验结论仍以18ffbe2快照为准。Phase6工程/浏览器证据已具备，拥有者独立完整讲述仍待验收。
- 用户要求以后按此方式生成知识库，新增docs/learning/knowledge-base-authoring-sop.md并在PROJECT_INSTRUCTIONS.md关联；另按明确记忆授权新增个人归档偏好小记录。本轮仅改文档/发布辅助资料，没有业务源码、依赖、迁移或数据库操作，没有重跑应用测试。保留用户学习指南原改动及其他聊天的订单分页变更，提交只包含本轮文档。

## 59. 订单分页完整学习指南与每片双交付规则（2026-10-08）

- 拥有者明确要求以后每完成一个切片，都提供验证记录与完整学习指南；必须覆盖本part的逻辑/业务闭环、解决的问题、未解决缺口、下一步规划、核心代码讲解、设计理由与其他方案取舍。已写入AGENTS.md、PROJECT_INSTRUCTIONS.md和知识库写作规范，区分工程交付、文档静态检查与个人理解验收；仅补资料沿用该片原验证记录，不递归新建补文切片或重跑无关实验。
- 新增docs/learning/phase-3-order-list-pagination-study-guide.md，固定bbc20ca源码链接，串起本人历史订单查询→翻页/刷新/恢复→原详情/Mock付款与收货入口。说明本人过滤、排序、关联预加载、分页封套、URL/watch、请求取消、失败分类；含具体值、页码/游标/列表摘要/缓存取舍、证据表、缺口、下一片停止条件、口述和变式自检。
- 新增同名请求时序.mmd/.png，图注区分Vue本地拒绝无效页码与直接API非法页分支，明确无订单/库存/支付写入、不提供统一快照；使用现有Mermaid CLI及已安装Chrome渲染并查看PNG。修正初次CLI旧参数与默认浏览器缺失问题，没有安装依赖或改变应用配置。
- 指南与验证记录互链，README补入口；记录增加本轮补文边界。历史目标28/171子场景、回归63/8子场景及构建等只引用原始输出；本轮没有重新运行应用、数据库、浏览器或性能测试，没有推进索引/Lua/ES。
- 静态核对源码片段、Markdown链接与核心内容、历史原始输出；独立只读审查覆盖业务闭环与证据边界，修正fetchOrders返回值标题和图的前端页码拦截说明。完成指南不代表拥有者已掌握。保留Phase6E指南中原用户改动，前序main与远端baa354d已核对，无业务源码修改。

## 60. 学习指南聚焦重点难点，覆盖旧完整版模板（2026-10-08）

- 拥有者反馈上一版过细：重点在项目重点/难点，不要图、不需要读源码的文件导览（旧第5节），代码只挑重要位置；去掉末尾自检和参考答案（所称14、15），并记入以后指南。本要求覆盖第59节的穷尽式模板，仍保留每片验证记录+指南双交付。
- 当前订单分页指南已精简为7节：业务闭环、本人过滤/分页/序列化、稳定排序与漂移、刷新/请求竞态、页码与替代方案取舍、证据/缺口、下一片。保留两段关键代码，不再展开常规fetch/computed/watch、接口字段和每个源码文件；移除指南图与末尾练习章节，历史图文件不作删除或新验收。
- AGENTS.md、PROJECT_INSTRUCTIONS.md与知识库写作规范同步：完整性指关键问题讲清楚，不指篇幅长、图多、代码多；通常1–2处关键代码，没有则不凑数量；不自动加图、源码清单或末尾练习，用户另行明确要求时再提供。按用户明确记录要求新增个人偏好更新小文件。
- 验证记录补精简说明，原应用测试与raw不变；本轮只做文档内容/代码摘录/链接/差异检查，没有业务源码、依赖、数据或配置操作。保留原Phase6E指南用户改动；开始前main/远端c47e934一致。

## 61. 真实订单查询基线与隔离索引候选（2026-10-09）

- 用户选中分页指南“真实订单查询基线”并要求下一步。本片Supporting/Pair，能力目标为完整SQL成本分层、实际计划及联合索引判断；以可解释证据收束，不扩大游标/缓存或并发负载。开始前main/远端92709b1一致，原Phase6E指南用户修改保留。
- 新增显式启用的test_order_query_baseline实验，默认skip；collection限制test环境/测试库且默认库不同，实际连接再次核对MySQL/test_project_one/autocommit，拒绝覆盖已有证据JSON。初次新test_order_query_baseline建库被1044权限拒绝，保留失败raw；没有改权限/配置，转为现有可丢弃test_project_one。前后两测试schema不存在、可见测试连接0、开发schema30表。
- 合成本人/他人各5,000单、10,000地址/20,000明细；created_at明确更新为固定UTC基准+ID置换，含ties。独立期望顺序/快照校验。全量/首页/201深页各热身1、样本5，单线程热缓存、形状轮转；当前API固定身份的SQL/参数及响应字节与分段采集一致。捕获完整SQL/三表SHOW INDEX/估计与实际计划，分别保存物化/序列化/渲染/驱动execute/字节及原始波动。
- 原首页订单索引步骤实际输出5,000后Sort；临时(user_id,created_at,id)自动被选为反向扫描，输出20且无订单Sort。原/候选COUNT仍读5,000；深页OFFSET4000下候选仍经过4,020订单与地址loops，items仍40行及自身排序。基线全量4,005,001字节、首页16,108字节；本地三阶段之和中位数全量711.57ms、首页10.38ms、深页14.52ms，候选分别717.28/7.42/13.55ms，非HTTP/网络/并发指标。候选全量execute单次208.86ms波动保留，不选最好值。
- 候选finally删除；静态比较恢复索引定义、各形状内容哈希相同，删后首页恢复5,000+Sort；pytest销毁库。实验1passed21.24秒；分页/订单API相关16passed9子场景及1默认skip13.51秒。没有修改views/models/迁移/前端/配置/依赖/库存支付MQ规则，没有开发业务写入或Lua/ES实现；未重跑浏览器/Redis/MQ矩阵。
- 新增配套docs/verification/phase-3-order-query-baseline.md与JSON/raw、docs/learning/phase-3-order-query-baseline-study-guide.md；指南无图/源码导览/末尾练习，只有两处关键SQL。当前README/Charter及上一片入口补进度。下一片建议只用模型迁移正式表达这个候选并验证查询/归属/排序/快照与订单回归；开发库迁移须单独说明确认，再进入Lua原子准入。索引写成本/空间、冷缓存、并发/真实网络、统计快照和分页漂移仍缺证据，个人独立理解未验收。

## 62. 正式订单列表联合索引模型与迁移（2026-10-09）

- 用户选中“下一片建议只正式落地这一个订单列表联合索引”并要求开始。本片Supporting/Pair，能力目标是证据→模型声明→可维护迁移→真实结构/查询验收；达到最小迁移/路径/订单回归后收片。开始前main/远端e85e592一致，保留Phase6E指南用户修改。
- Order.Meta声明普通(user,created_at,id)索引ordering_order_user_created_id，Django生成0005_order_list_index仅AddIndex，保留原(user,scope,key)幂等唯一约束；views/Serializer/分页契约/前端/库存支付规则不改。旧性能实验按SHOW INDEX列序识别并拒绝带正式索引状态，防止错误原基线与重复候选；历史e85e592 JSON/raw未改，复现说明指向隔离旧版本。
- 新test_order_list_index在真实可销毁MySQL默认验证0004→0005→0004→0005：两笔已有订单、地址/明细三表字段与完整响应不变，原唯一约束保留，旧实验guard在0004允许、0005拒绝，finally恢复0005。显式启用正式计划检查用本人/他人各5,000单、10,000地址/20,000明细，固定时间置换含ties及独立期望；首页/深页当前响应和生产查询字段保持完整归属。
- 正式索引无FORCE INDEX自动被选为反向扫描：首页订单步骤实际20行/地址loops20；201页OFFSET4000为4,020行/地址loops4,020；均无订单Sort。COUNT仍覆盖读5,000，items仍40行及自身排序；各页20单40items/16,108与16,146字节/3订单数据SQL。仅一次计划观察，不新报时延提升比例或生产/HTTP证据。
- 目标2passed13.33秒；分页/订单API/秒杀API/迁移回归29passed171子场景2默认skip27.44秒。manage.py check0，makemigrations --check --dry-run无变化；sqlmigrate正反只收集预览未执行，反向预览因当前默认库元数据可补user外键支撑索引，不能当作真实逆迁移逐条日志；最终真实SHOW INDEX无额外独立user_id索引。新计划JSON/raw与正反SQL预览见docs/verification/phase-3-order-list-index*。
- 前后test_project_one不存在、可见测试连接0、开发schema30表，未运行开发库migrate/业务写入/服务或依赖修改；未重跑支付/MQ/Redis/浏览器矩阵，Lua/ES仍未实现。新增精简指南与验证记录互链（无图/源码导览/末尾题，仅一处重要模型代码），README/Charter与上片进度同步。
- 订单查询方向达到收束条件，下一片进入已确认Lua原子流量准入：先确定位置、忙时/Redis故障规则与到达MySQL计量口径，仍MySQL事实，不预扣Redis库存。开发库生效要另外说明确认；写成本/空间、大表DDL、冷缓存/并发/网络、COUNT快照/分页漂移与个人独立理解仍缺证据。

## 63. Lua秒杀入口原子窗口准入（2026-10-09）

- 拥有者选中“下一片进入Lua原子流量准入”要求开始，并两次明确确认：Redis无法确认名额503；满额原Key确认也先429，恢复原Key同参重试。本片Depth/Learning，目标是区分库存正确性、入口保护和确认可用性；达到额度/真实事务/故障/幂等最小证据后停止。开始前main与远端59b54b9一致，Phase6E指南原用户改动保留，不纳入本片。
- 新admission.lua用单个全入口Key：首次SET PX开启窗口，后续合法INCR保留期限，满额不增加计数/不续期；坏计数/无TTL/错误类型不自动修复。admission.py独立Redis client各100ms超时、16连接、零自动重试，返回未知/故障503；超时可能已消耗名额，不退款。Gate在Session/CSRF与输入/Key格式校验后、HTTP等待适配和订单事务前；429/503带Retry-After，原业务规则及前端代码未改。
- settings与.env.example增加默认关闭、本地/测试的显式配置（20次/1000ms仅示例）。没有新增依赖、迁移、部署配置或Redis库存预扣；不复制商品缓存fail-open政策。预算是尝试次数，业务404也消耗，不代表库存或严格在途上限；同一部署实例须共享URL/前缀。
- 真实Redis24独立client Barrier同时竞争，预算4，准确4准入/20拒绝。真实HTTP对照8线程处理24顾客、24库存、89元单价：关闭24 BEGIN/24单/480请求SQL，开启4 BEGIN/4单/80SQL/20次429；各429固定身份后0下单SQL。预占、快照、资格、RESERVE流水各24/4，库存分别0/24与20/4，无核销。固定身份跳过Session，批次297/125ms仅用于未跨10秒窗口检查，不生产吞吐或同产出提速。
- 坏状态值/类型和PEXPIRETIME未改；真实非监听端口连接失败实际TimeoutError，旧Key/新Key均503，无新增效果，恢复旧Key200同单。真实EVAL后注入丢失响应：计数增1但应用只执行一次脚本、503、无下单，恢复原Key只建一单。原Key满额同参/异参先429，窗口恢复200同单/409冲突，业务效果一次；自然到期、业务拒绝不退次数、认证/CSRF/格式不消耗、默认关闭不创建client均通过。
- 首轮测试使用不存在sold_stock、factory patch影响fixture清理，12fail/2pass/1error19.67秒保留initial raw；修正后14passed13.49秒。复审补PEXPIRETIME及旧Key503恢复Oracle，最终目标+相关API/HTTP等待/checkout/生命周期53passed325子场景61.45秒。check0、makemigrations dry-run无变化。新docs/verification/phase-6f-flash-sale-admission.md与JSON/三个raw、docs/learning/phase-6f-flash-sale-admission-study-guide.md互链；指南100行、两处关键Lua片段，无图/源码导览/末尾题。
- 使用已有redis-test6381、随机单Key精确清理，没FLUSHDB；测试schema销毁、可见连接0，开发schema仍30表，隔离Redis结束后停止。未改实际.env、开发缓存/数据库，没有浏览器/MQ重跑。429/503前端意图保留仅静态核对，未展示倒计时；Redis重启/驱逐/主从、真实慢网络、公平性和生产阈值未测。Lua首片已收束，下一片建议商品搜索基线及ES可重建派生索引，ES仍未实现，开发索引迁移/启用准入和个人独立理解待单独处理。

## 64. 商品关键词搜索与MySQL完整查询基线（2026-10-09）

- 用户选旧基线“正式落地订单列表联合索引”要求下一片；核对main/远端36dc6d1一致，索引59b54b9与Lua36dc6d1均已完成，继续当前尚未完成的搜索方向；拥有者明确选择商品名称＋描述。Supporting/Pair，以功能规则与实际COUNT/列表计划形成后续ES可比较基线；停止于一个规模三个查询，不扩索引/负载矩阵。
- catalog新q QuerySerializer可选空词、trim/max80/NUL拒绝；View名称OR描述icontains，与原有效商品/分类、分类筛选AND。原name/id、固定20页、缺货/无SKU可见、有效SKU最低价及布尔库存提示保留，详情/缓存/库存/订单/支付/Lua不改。前端增加q/API传参/搜索框/URL状态，改词或分类重置页码、翻页保留q、取消旧请求；空结果取消分类按钮准确写“查看全部分类”。
- 新功能8项＋显式完整查询实验1项真实MySQL9passed12.84秒；公开目录/模型/Admin/新功能相关24passed6子场景1默认skip10.99秒；最终vue-tsc/Vite build通过。check0、makemigrations dry-run无变化。无真实/模拟浏览器运行验收，前端URL流程仅静态核对，构建不是端到端证据。
- 合成5000可见＋2隐藏Product、10004SKU/Inventory，名称统一前缀四位编号，描述明确成员集独立Oracle。关键词500/无匹配0/关键词加分类250，总SQL2/1/2；捕获真实API完整COUNT/list SQL参数、四表索引/collation、EXPLAIN及ANALYZE。全局商品FK步骤约2500rows×2loops（平均/舍入不能称精确5000读取），分类限定2501×1；匹配500/250后聚合/排序再LIMIT20，COUNT仍联表/分组，分类COUNT的Exists投影250loops。keyword为包含LIKE、无词序/分词/相关性；不是慢SQL优化/ES性能对照或生产容量。
- 显式实验collection限制test环境/隔离库且不同默认库，连接再断言MySQL/autocommit；报告存在拒绝覆盖。pytest前后test schema0/可见连接0，开发schema仍30表，没开发migrate/业务写入；没新增依赖、索引、ES/Redis服务或部署配置，原Phase6E指南改动保留。一次中断后确认文件完整再测试；读取报告的控制台编码错误用Python-Xutf8修正，不影响测试/JSON。
- 双交付docs/verification/phase-7a-search-baseline.md及JSON/target/regression/frontend raw、docs/learning/phase-7a-product-search-baseline-study-guide.md互链；指南聚焦规则、完整查询成本、替代方案/代价/证据缺口，少量关键代码，无图/源码清单/末尾题。当前搜索基线收束，下一片建议隔离ES可重建商品搜索原型；字段/中文分析/排序与MySQL事实回读、索引同步及故障分片确认。ES、个人独立理解和开发运行不冒称完成。

## 65. ES可重建商品索引原型及测试隔离事故（2026-10-10）

- 用户选中“ES可重建商品搜索索引原型”要求开始。Supporting/Pair实现＋Depth机制解释；面试价值为事实来源、文本语义与重建失败边界，止于隔离原型，不自动接全API/同步。main与远端b0d58a5在生成前一致，Phase6E指南原用户改动保留。
- 新独立compose.search-index-test.yaml使用ES8.19.23/9201、单节点0副本、512MB堆/2GB上限；官方客户端8.19.3加入依赖，pip check与Compose config通过。原型限制test环境、本地端口/集群/版本和配置及实际test_project_one/autocommit；物化可见Product/Category最小5字段，无价格库存，无外层事务等待ES。
- 内置CJK/strict mapping、name^3/description best_fields AND、精确分类、score/ID、最多20条；每次新随机候选，Bulk逐项/refresh/count通过才标ready。12件人工商品10件可见，完整/分开/反向词ES5/6/6，旧MySQL5/1/0；单字标ES0/MySQL6，保存桥接词漏匹配，不冒充中文词典质量或性能。固定名称/描述对6.543/5.808仅该对观察。
- 改名/下架/隐藏分类/新增后重建10→9，关键词旧5/新3；旧索引陈旧保留。真实Bulk注入错误long类型：HTTP200但一项400、失败候选仅1文档且不可查、旧完整10不变。最终在线5passed1skip10.69秒、相关24passed6子场景7默认skip9.70秒；独立离线首轮只接受ConnectionError而实际ConnectionTimeout失败，修正Oracle后1passed2.18秒，2秒单请求不是全重建期限。5个自建索引精确清理全部404，独立服务down --volumes完成。
- 首轮在线虽5passed/1skip，但错误guard改真实connection.NAME再CaptureQueriesContext，活连接开发库且pytest flush误清4表。binlog.000025 FULL确认1用户/1Session/104权限/26内容类型删除，其他业务表无该次删除行事件。暂停后只读备份/核查，guard改为模块connection替身，增加实际DATABASE核验；修正后测试与两次隔离恢复演练开发快照不变。
- 拥有者明确授权后，以列/主键/InnoDB/当前快照门槛、SERIALIZABLE事务锁后复核、精确捕获主键删除和参数化插入恢复139条；提交后新连接逐字段相同、Session解析原用户。原始binlog/行值/恢复候选在Git外私密目录，无秘密提交；4表自增计数未倒退。不称本轮开发数据未触碰，事故详见docs/verification/phase-7b-test-isolation-incident.md与脱敏JSON。
- 双交付docs/verification/phase-7b-search-index-prototype.md及最终/首轮raw+JSON、docs/learning/phase-7b-search-index-prototype-study-guide.md互链；学习导航34篇、前片后续/README/Charter v1.11同步。指南仅两处重点代码、无图/源码清单/末尾题，保留反例与代价。
- 公开API/前端/模型迁移/库存支付Lua业务未改，实际.env和部署配置未改，Redis/MQ/浏览器/性能矩阵未重跑。当前无API回读、增量版本/同步、alias切换、副本/重建并发/崩溃孤儿清理；创建成功ACK丢失孤儿窗口已明示。下一片仅ES候选ID→MySQL当前可见性与摘要，先明确陈旧项/分页/故障合同，不加MQ搜索任务；个人掌握和项目整体收尾另行验收。

## 66. ES候选读入口与MySQL当前摘要（2026-10-10）

- 用户选中“ES候选ID回读MySQL可见性和当前价格库存摘要”要求开始，确认独立接口/允许短页与ES故障503不回退。Supporting/Pair实现＋Depth分页与副本边界解释。开始前main/远端04df68f一致，已有6E指南用户改动保留。
- 新GET /api/catalog/search/ q必填非空trim/max80、category可选max80、page1..500；candidate_count与整数next/previous_candidate_page独立于当前results。每页20候选，MySQL重查商品/分类可见性和当前类属、共用旧有效SKU最低价/有货摘要，按ES ID顺序组装、不再LIKE/不补页。无命中首屏200，合法窗口越尾404，超过10000候选400缩小范围。默认关或ES故障503；旧products接口和前端仍MySQL。
- 读client仅test实际test_project_one、无外层事务、固定loopback9201/测试集群/8.19族/精确随机index。builder从未完成到逐项Bulk/refresh/count成功后写_meta完成/来源标记，读API自身不写索引；标记只有库名无epoch，测试库重建后旧index失效，必须同fixture lifecycle。未松绑7Bguard，module connection替身不改真实NAME。
- 最终新在线8passed1skip12.37秒（5真ES/MySQL＋3unit/injection），离线1passed8deselected12.22秒；旧共享原型另5项通过，catalog原目录24passed6subtests10.47秒。词序新ES2/旧MySQL0；陈旧5候选当前3，旧分类4候选当前2，改名价89/有货来自MySQL，新增和新词/新分类漏召回保留；21候选页20→19→0仍next2，二页唯一candidate-20。真实离线ConnectionTimeout约2.017秒，503且仅1来源SQL/0摘要SQL，普通browse200。超量与不完整响应为注入，未冒充真实多节点故障。
- 首轮新/旧联合13passed2skip14.84秒，离线1passed12.52秒，但JSON过晚读取CaptureQueriesContext在下次request清空后错误记录SQL0；断言时实际2/1。root固化list(queries)后补跑新在线/离线，初始和最终raw/JSON都保留，最终记录SQL2/1。控制台编码报错以-Xutf8重读，不当测试失败。
- 前后开发30表整表只读摘要相同（用户Session各1、权限104、内容类型26、迁移35），只公开counts/unchanged，摘要Git外；测试schema/连接0。所有DBpytest串行，fixture前后实际SELECT DATABASE；共15个确认自建索引逐个精确清理，独立client确认无剩余；最后独立Compose down --volumes，仅MySQL继续，Redis/MQ未启动。本轮无开发写入/迁移/实际.env修改。
- 双交付docs/verification/phase-7c-search-read-api.md及raw/JSON、docs/learning/phase-7c-search-read-api-study-guide.md互链；指南只1处核心代码，无图/目录/末尾题。导航35篇、README/7B后续/Charter v1.12同步；未改库存订单支付Lua逻辑、详情缓存、模型迁移、前端或部署配置。两秒是每个串行ES请求等待，不是总HTTP期限；无连接复用、alias/PIT/自动同步/前端启用/容量或个人掌握证据。
- 下一片建议手动重建成功后安全切换读入口，失败保留旧索引；在隔离环境证明边界即止，不自动扩MQ或部署。开发来源建索引及启用需要后续单独说明和确认。

## 67. ES手动重建与安全别名切换（2026-10-10）

- 用户选中7C下一步要求开始。面试价值为可重建副本、发布结果不确定、保留在途旧读；Supporting/Pair＋Depth解释。开始前main/远端14b59f3一致，已有6E指南改动保留。限于隔离test、单操作者和同一构建会话/数据库生命周期，不启用开发环境或自动同步。
- 新search_alias.py SearchIndexPublisher复用builder，新候选逐项Bulk/refresh/count/meta完成后才一次精确remove旧must_existTrue＋add新。expected实际指针与owned ready不符写前拒绝；更新正常/异常均精确回读state switched/unchanged/unknown，ack/errors/peraction另记response_ok。未知不重发/逆切/删候选，构建异常尚未提交alias切换。publisher失败不调用builder.close。
- CATALOG_SEARCH_ALIAS默认空，与INDEX只填一个，沿用defaultoff/test-source guard；读API先info校验集群，再每请求resolve一次唯一空配置alias并查exact index，meta后MySQL摘要仍当前。多目标/filter/routing拒绝503。source guard提取require_test_source(db_connection)与完成标记/name检查共用，只patch模块替身，无实际NAME变更。
- 在线新5项＋7C8/7B5共18passed2skip19.82秒。旧2文档→新3，新词0→2；Bulk HTTP200实际一项400，候选仅1且未complete，alias更新0/旧入口200；有序暂停已resolveold→主切new→旧exact read200，后新请求读new，线程核验实际testDB并close，未退出禁止cleanup。两轮实际alias POST后注入timeout/readbacktimeout，分别switched/unknown，control都确认actualnew，各更新1次/所有候选保留。注入不是实际网络/多节点故障；ackFalse/actionfailed/unchanged返回分支仅静态检查。
- 停隔离ES后7C真实离线1passed8deselected10.54秒，ConnectionTimeout约2.007秒，503/1来源SQL/0摘要SQL，旧browse200；目录24passed6subtests8.67秒。静态compile/check0/nodrift/pipcheck/Composeconfig通过，无Redis/MQ/浏览器/性能矩阵重跑。
- 开发30表前后只读整表摘要相同（用户Session各1、104权限/26contenttype/35migration），hashGit外无真实行值。测试schema/可见conn0；共21确认自建索引（新11＋回归10）精确删/独立核验0剩余，5alias全部404，standalone down --volumes，只MySQL运行。无开发写入/索引/迁移/实际.env或部署配置更改。
- 双交付docs/verification/phase-7d-search-index-switch.md及raw/JSON与docs/learning/phase-7d-search-index-switch-study-guide.md互链；指南2个重点摘录，无图/源码目录/末尾题。导航36、README/7C衔接/Charterv1.13同步。旧索引保留带来空间/人工清理；alias读多1GET，各2秒不总2秒。发布器单builder ready不能跨进程沿用，没有独立管理命令/开发运行入口；source meta无epoch、非CAS/跨页快照/自动同步/生产零停机证据。
- 本片证据足以解释建好再发布、未知和在途旧读，停止追加同步/发布锁/故障矩阵。下一步整理个人理解，再评估启用环境、完整前后端演示与项目收尾最小缺口，开发数据操作需单独确认；不能把工程通过当个人掌握或项目全部完成。

## 68. 本机部署候选与逐步指南（2026-10-11）

- 拥有者确认项目准备收尾，下一步部署，复习随后；不要求面试时打开项目或专门建设展示网站。采用完整指南＋逐步执行、解释命令与结果。9A为Supporting/Pair候选准备，不是已部署；本轮开始时main与远端6a159ee相同，6E指南用户修改保留。
- 新compose.deployment-local.yaml独立project-one-deployment-local，只db/web/nginx；新库project_one_deployment_local、独立用户/卷，无开发init脚本/源码bind。Nginx仅127.0.0.1:8080，DB/Web无hostport；四可选模块显式关闭。development加DEBUGfalse用于本机HTTP，既有staging/production Secure Cookie和模块guard不变。
- Web非root Linux镜像安装base＋Gunicorn26.2.0，2workers/60秒/stdout，包含源码/迁移/管理命令/Lua资源；独立ignore不改Worker根context。前端Node24构建→Nginx仅dist/conf；Host保留端口，API/Admin/health路径不截断，SPA回退不吞assets/API错误。Django collectstatic经独立卷供Nginx只读。
- Compose quiet与进程内公开占位渲染结构断言通过，空凭据拒绝；实际独立.env未创建。宿主npm build/typecheck通过56modules；pip dry-run退出0/Would installgunicorn26.2.0，未安装。官方PyPI确认版本存在/Python>=3.10，不称最新。记录见docs/verification/phase-9a-local-deployment.md/static.json/frontend-build.txt；双交付指南phase-9a-local-deployment-study-guide.md，导航37、Charterv1.14。
- 没有pull/build镜像、启动/停容器、建新卷、迁移、collectstatic、账号/商品创建、HTTP/Session或旧数据库回归。本轮无开发数据访问/修改；准备前后只有原MySQL容器健康，8080无监听。Node/Nginx标签可移动，Docker/Linux构建语法、挂载权限和实际请求待验证，不能把hostbuild冒充镜像运行。
- 首次运行需按聊天AGENTS安全边界确认下载/镜像空间、独立网络卷及新库迁移，之后逐步执行并收集证据。down保留卷，默认不删除卷；中间件适配/HTTPS/运维分片，不放宽现有保护或自动补演示功能。原compose/settings/业务/base依赖/开发.env不改。

## 69. 本机部署首次运行与公开入口验收（2026-10-11）

- 用户理解部署前后变化后要求开始，后续“继续”；首次下载/镜像构建、独立凭据/网络/卷、新库迁移与回环8080启动已有具体影响授权，无重复许可。Supporting/Pair，目标是独立解释源码→镜像→进程/代理/静态资源→指定数据库，不是扩展示站。开始main/远端2722c98一致；文档收尾前另一聊天5495e44已推送，沿当前HEAD续写，未知/6E用户修改不纳入。
- 独立.env排他创建随机凭据、Git忽略、quiet config退出0，不打印值或完整环境。真实Linux web/nginx镜像构建通过，Vite8.3.0/56模块；新db健康后实际SELECT DATABASE/CURRENT_USER先核对project_one_deployment_local/project_one_deployment@%和db:3306，再一次性迁移36项、collectstatic154文件；db/web/nginx全部healthy。
- 实际Python3.11.16/Django5.2.17/Gunicorn26.2.0/MySQL8.4.10/Nginx1.30.5；Gunicorn master＋2worker、UID10001，静态卷web可写/nginx只读，无源码bind/后端镜像.env。DB/Web无hostport，仅127.0.0.1:8080；环境development/DEBUGfalse，缓存/Lua/MQ/ES四开关false，原guard不变。新库待迁移0，用户/Session/商品/订单0；Django系统权限资料由迁移生成。
- 14项HTTP含预期403/404通过，health数据库ok、商品/分类空、Admin和CSS200、错误API/静态不被SPA吞。后续独立Playwright只读实际Vue空页、深路由刷新、两个API200、目录0错误/0警告、Admin所有已请求资源200；没有登录提交或创建数据。首次CLI help尾部Windows Node断言退出1，后续专用会话各命令退出0且已关闭，不修改依赖/全局安装。
- 双交付docs/verification/phase-9b-local-deployment-run.md、docs/learning/phase-9b-local-deployment-run-study-guide.md及build/db/source/migrate/static/start raw和runtime/http/browser JSON互链；HTTP JSON保留浏览器之前的false，browser JSON另证实Vue执行。9A只补后续链接不改历史边界，导航39、Charterv1.15同步；文档静态/秘密检查不冒称业务测试。
- 未访问/写入开发库或缓存、未迁移旧库，没有旧库整表指纹核验；原业务/Compose/settings根.env未改。没有业务pytest、登录Session/CSRF、交易、中间件启用、生产HTTPS、容量、重启/更新/备份恢复或个人掌握验收；两个新卷和三个部署容器保留运行。Node/Nginx可移动标签与传递依赖未全锁，记录当次digest而不承诺逐字节重现。
- 当前最小基础部署证据已足够，停止自动加数据/中间件/故障矩阵。下一片建议只获准创建独立学习账号并验Session/CSRF成功/拒绝；随后整理更新/停机/备份/公网部署流程，再按用户安排整体复习。部署前面试无展示要求继续保留。

## 70. 代理入口Session/CSRF验收与运维参考（2026-10-11）

- 用户选中9B下一片要求开始，Supporting/Pair目标是解释Nginx后身份、Origin端口与CSRF拒绝，最小闭环至正确退出，不加商品或交易演示。生成前main/远端4673b9f一致，未知项目总览候选与6E用户修改保留；本轮另有知识库聊天修改，均不混入本片提交。
- 新scripts/verify_local_session_csrf.py只固定独立Compose和127.0.0.1:8080；stdin传随机密码/具体Session Key、禁环境代理和重定向、actualDATABASE/user/host/port/env/UID核对。AST与只读preflight通过，新库四计数0。创建响应未知保留核查，清理匹配具体Key/auth用户及随机账号ID/名，锁内再查角色，有未知关联数据拒绝，不flush/全表清会话；这些失败guard为静态复核，未故障注入。
- 用户明确回答允许仅新库执行清理后，真实脚本退出0/15HTTP passed：匿名me403、csrf204、缺Header登录CSRF403/Session0；同源正确登录200、Session1/last_login、CSRF轮换；me与公开页面导航后me同一身份；缺Header/旧Header(当前Cookie)/错Origin localhost退出各403，后me200/Session1；当前Header退出204、me403/Session0。Cookie属性与用户名/ID核对在内存，不保存密码/Cookie/Token/Session Key。
- 成功退出已删Session，最终精确删随机普通账号，残留Session删除数0，用户/Session/商品/订单全部0；不倒退自增或宣称数据库字节回滚。仅新部署库发生获准写入，开发库/缓存未访问、商品订单支付业务/配置/依赖/四开关未改，3服务继续运行。实际为独立CookieJar HTTP，不是浏览器UI/SameSite浏览器执行/重启会话证据。
- 双交付docs/learning/phase-9c-session-csrf-operations-study-guide.md与docs/verification/phase-9c-session-csrf-operations.md互链，含preflight/http/static JSON和脚本；docs/deployment/local-operations-runbook.md补更新/保卷停机/备份/独立空卷恢复，PowerShell7参考，无秘密argv/PowerShell转码SQL/覆盖旧库/清卷。9个PS块AST、sh-n语法/实际mysqldump帮助7选项通过，没执行任何运维正文/SQL备份/恢复或重启。
- 9B只补后续不改历史；README/Charterv1.16更新。临时数据许可与结果分开记录，15项是HTTP状态检查，不是15条业务或性能实验；没有旧pytest/Redis/MQ/ES/浏览器矩阵重跑，也没有生产HTTPS/容量/零停机/自动回滚/PITR或个人掌握证据。
- 当前部署基础与认证证据足够，先按用户安排整体梳理与复习；可按收益另选一次明确批准的隔离备份恢复，不自动加公网/中间件/商品演示或故障矩阵。

## 71. 最终版本验收与本期收尾口径（2026-10-12）

- 用户选中最终验收要求开始；Supporting/Delivery目标是让本人准确区分工程完成、环境启用和个人理解，停止追加模块/压测。开始main与远端0a94676一致，原6E指南用户修改保留。
- 对精确SHA查询GitHub push run38165837589/job114548088997：completed/success；配置check0、无迁移漂移、真实迁移包含ordering.0005，437collected/313passed124skipped162.93秒。CI只有MySQL，不运行前端build或真实Redis/MQ/ES，空旧status或PR-only结果不作成功证据。
- 本机默认完整pytest串行313passed124skipped685subtests208.66秒，前端类型检查/Vite56modules构建通过，pipcheck与Djangocheck通过。四模块false、无集成URL/性能开关；默认占位库project_one_validation_parent不存在，TEST.NAME精确test_project_one，预检testschema/连接0，pytest生命周期后仍0。开发30表全行只读摘要前后相同，原始值与hashGit外；部署库未访问，不执行默认migrate。
- 124skip分为Redis/Lua85、ES17、RabbitMQ/Worker12、MySQL显式诊断/负载10；不是失败或通过，也不是marker筛选deselected。历史9组证据提交均为基线祖先，保存相关代码变化与源码blob清单；Lua/订单索引/ES/部署相关实现未变，MQ services后加秒杀资格锁/状态，当前回归保护组合；不重写旧JSON/日期或假称重杀Worker/重跑负载。
- Charterv1.17统一顶部与Done/阶段勾选：已验证交易主链/三旗舰、Lua准入与ES测试扩展、基础部署与认证。旧完整能力单栈/三新负载门槛按拥有者不需演示、准备收尾决定收窄，未做范围不勾全；ES开发启用/自动同步、周期MQ接管、公网HTTPS、更新/备份恢复实测本期延期或可选。LearningDone、独立改错和简历口述仍待本人完成。
- 双交付docs/verification/phase-10a-final-acceptance.md与docs/learning/phase-10a-final-acceptance-study-guide.md互链；保存CI摘要/日志摘录、pytest raw/JUnit/隔离结果、前端raw、来源清单。只改当前文档/证据，业务/测试/依赖/工作流/部署Compose/.env未改；不操作开发缓存或真实支付，不运行恢复演练。
- 下一步按总览复习完整交易，再库存→支付/MQ→Redis/Lua→MySQL查询→ES→部署，最后简历与独立表达；不自动进入新的实现。提交后CI精确run在最终回复核实，基线CI不冒充材料提交结果。
