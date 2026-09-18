# Project One — Domain Context v1.5

## 0. 文档状态

- 当前阶段：Interview Core MVP-1 / Authenticated Cart 已实现并通过自动验证，等待业务能力与工程能力复盘；本地改动尚未 commit/push
- 文档版本：v1.6
- 范围来源：`PROJECT_CHARTER.md` v1.1
- 当前内容：记录项目拥有者已经确认的稳定术语、业务规则、技术采用边界和实验候选

本文件用于统一 Project One 中的业务与工程语言，不替代 `PROJECT_CHARTER.md`。如果两份文件发生冲突，以 `PROJECT_CHARTER.md` 和后续正式 ADR 为准。

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
| Order Expiration | 未支付过期 | `PENDING_PAYMENT` 默认 15 分钟后转为 `EXPIRED` 并幂等释放预占库存；先使用简单可验证触发机制，不因此强制引入消息队列。`Confirmed` |
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
| Webhook Inbox / Event Inbox | 回调收件箱 | 先可靠保存原始事件，再在明确事务边界内校验并处理；重复、乱序、验证失败和处理失败都不能丢失原文。同步 baseline 先行，不默认依赖 Celery。`Required Later` |
| Mock Payment Gateway | 模拟支付网关 | 项目后端自己控制的测试替身，必须制造成功、失败、处理中、超时/UNKNOWN、重复、乱序以及错误金额/币种/签名。场景选择器只在测试或受保护演示环境开放。`Confirmed` |
| Sandbox Provider | 第三方支付测试环境 | 只在 Mock Evidence Pack 完成、100 小时审计允许且账号可快速使用时考虑；支付宝优先做最多 2 小时可用性检查，受阻可换 Stripe 或延期。它是 Optional Bonus，不阻塞核心完成。`Conditional` |
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
| Redis | 内存数据系统 | 用于可重建目录缓存、热点保护和必要的原子准入。Redis 是缓存正确性旗舰主线，但不能成为订单、支付和核心库存的唯一事实来源。`Required Later` |
| Cache | 缓存 | 保存可重新获得的数据副本，以减少数据库压力和响应时间。缓存更快不代表缓存一定正确。 |
| Cache-Aside | 旁路缓存模式 | 第一批缓存 Category、Product 等可重建公开目录数据；未命中读 MySQL 并回填，数据库更新事务提交后使用 `transaction.on_commit` 触发失效。`Required Later` |
| Cache Consistency | 缓存一致性 | 目录缓存使用 Cache-Aside、数据库事务提交后失效、TTL 随机抖动；Redis 不可用时回退 MySQL。这些是必须实现并验证的方向。`Required Later` |
| Cache Experiment Parameters | 缓存实验参数 | 具体 Key 结构、基础 TTL 与抖动范围、空值缓存及热点保护参数必须由 baseline 和实验决定。`Experiment Candidate` |
| Resilience | 故障韧性 | Redis 或其他辅助系统故障时，核心系统按明确规则降级、恢复或对账，不能丢失核心事实；下单仍重新读取 MySQL 权威价格与库存。`Confirmed` |
| Redis Lua | Redis Lua 脚本 | 作为秒杀流量准入或原子前置判断的实验候选，必须通过并发测试、崩溃窗口和 MySQL 对账决定最终路径。`Experiment Candidate` |
| Modular Monolith | 模块化单体 | 整个 Django/DRF 后端是一个部署单元，但内部按 Accounts、Catalog、Inventory、Ordering、Payments 等业务职责划分。第一版不拆微服务。`Confirmed` |
| Django App | Django 应用模块 | 第一批固定为 `accounts`、`catalog`、`inventory`、`ordering`、`payments`；闭环后按需增加 `promotions`、`engagement`，Search/Async 只有通过 Gate 才建立。`Confirmed` |
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

## 9. 条件性技术

以下技术在路线图中存在，但不是 Interview Core 的完成阻塞项。只有出现真实问题、比较过简单方案并通过范围与证据审计后才引入；No-Go（暂不采用）也是有效工程结论。一旦引入，必须进行真实集成与故障验证。

| English Term | 通俗中文 | 引入门槛 |
|---|---|---|
| Celery | Python 异步任务框架 | 出现不能合理同步完成的任务、重试或可靠消费需求。`Conditional` |
| RabbitMQ | 消息代理 | Celery 等消费者确实需要任务投递、确认、重试和故障实验。`Conditional` |
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
6. 支付集成：Mock Gateway 必做；Alipay/Stripe Sandbox 是 Optional Bonus；Production Payment 不做。
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
16. Event Inbox 使用 `(provider, event_id)` 唯一并保留原始事件，先建立同步 baseline，再按 Gate 判断是否异步。
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
- 动态运费、税务引擎、多层 Category、复杂优惠规则和通用 EAV 商品属性系统；
- 商业产品级 UI 动画与视觉精修。

---

## 12. 按纵向切片确定的实现细节

Charter v1.1 是当前设计基线，但下列机械性或证据驱动细节不需要在 Day 0 假装全部冻结：

- 每个 Model 的非业务字段名、精确长度、外键删除动作和查询驱动索引；
- API URL、分页、过滤和错误详情等局部契约；Order 与 PaymentAttempt 的幂等规则不得因此改变；
- Redis Key、TTL 秒数、抖动范围、热点阈值和最终 Lua 路径；
- MySQL 隔离级别、条件更新与行锁在目标环境中的实测结果；
- 性能目标值；必须先得到 baseline，再根据证据设定；
- 四个条件技术 Gate 和 Optional Sandbox 的未来 Go/No-Go 记录。

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
- 工作区尚未 commit/push，远端 CI 未验证；具体操作、限制与工程能力映射见 `docs/verification/guest-catalog-browse.md`。

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
