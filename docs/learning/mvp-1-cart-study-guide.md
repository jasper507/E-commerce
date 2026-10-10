# MVP-1 Authenticated Cart 学习指南

## 本片前后变化

[学习导航](README.md) · [本片验证记录](../verification/mvp-1-authenticated-cart.md)。以下是2026-09-18完成本片时的状态，后续进度见导航。

- **开始前：** 顾客能浏览商品和登录，但还不能保存准备购买的SKU与数量。
- **本片改变：** 增加本人购物车；重复加入合并数量，服务端决定当前价格，用户隔离、数量和唯一约束保护写入。
- **完成后与边界：** 形成“浏览 → 登录 → 加入/调整购物车”的闭环。加入时只检查库存，不预占；本片没有地址、订单或支付，浏览器操作也尚未验收。

**后续衔接：** [MVP-2 地址](mvp-2-minimum-address-study-guide.md)已完成；购物车中的意图在[MVP-3 下单](mvp-3-order-inventory-study-guide.md)才成为交易事实。

## 1. 先记住结论

MVP-1 不是“把四个 CRUD 接口背下来”。它真正训练的是：把顾客的购物意图转换为明确业务规则，再用权限、数据库约束、事务和测试保证这些规则不能被绕过。

本阶段的学习优先级是：

1. 业务规则和失败场景；
2. 数据不变量与用户所有权；
3. 一条请求怎样穿过前端、HTTP、DRF、业务服务、ORM 和 MySQL；
4. 测试如何证明规则；
5. 最后快速浏览重复 CRUD 与 CSS。

## 2. 已实现的业务规则

| 问题 | 当前决定 | 为什么 |
|---|---|---|
| 何时创建 Cart | 第一次成功加入 SKU 时创建；空 GET 不写数据库 | 查询尽量无副作用，游客和空购物车不制造无用数据 |
| 同 SKU 再次加入 | 合并数量 | 顾客关心总数量，数据库也只保留一行事实 |
| 数量边界 | 每行 1–99；越界拒绝，原值不变 | 规则清晰，失败不会产生半修改状态 |
| 当前失效 SKU | 不允许新增或更新 | 不让顾客产生“现在可以买”的错误预期 |
| 已存在后失效 | 条目保留并标记不可用，允许删除 | 不偷偷删除用户选择，也保留可解释反馈 |
| 价格 | 前端不能提交或决定价格；始终读取 SKU 当前价格 | 防止篡改；Cart 价格仍不是订单快照 |
| 库存 | 加入/更新时检查当前库存，但不扣减、不预占 | 提供快速反馈；最终正确性留给创建订单时重新校验 |
| 数据归属 | 所有查询和修改都带 `request.user` 范围 | 仅知道 SKU code 不能操作其他人的购物车 |

## 3. 推荐代码阅读顺序

### 第一步：先读测试，理解系统承诺

从 `backend/apps/ordering/tests/test_cart_api.py` 开始，只看测试函数名和 Arrange/Act/Assert：准备了什么数据、发了什么请求、期望什么结果。

再读 `test_cart_models.py`，重点回答：为什么 Serializer 验证之后，MySQL 仍然需要唯一约束和 CheckConstraint？

这一遍不要先看实现。你应该先能预测这些场景：重复加入、数量超过 99、伪造价格、越权修改、下架、库存不足、CSRF 缺失。

### 第二步：读模型和迁移，学习不变量

阅读顺序：

1. `backend/apps/ordering/models.py`
2. `backend/apps/ordering/migrations/0001_initial.py`

重点不是字段语法，而是三个不能被绕过的不变量：

- `Cart.user` 是 OneToOne，一个用户最多一个购物车；
- `(cart, sku)` 唯一，同一 SKU 不出现两行；
- `quantity` 必须在 1–99。

工程能力问题：如果有人绕过 API，直接执行 SQL 或调用 ORM `update()`，哪些规则仍然有效？答案应来自数据库约束，而不是相信前端。

### 第三步：读业务服务，学习规则集中与事务边界

阅读 `backend/apps/ordering/services.py`：

- `_purchasable_sku()` 集中可售检查；
- `_ensure_stock()` 给出购物车阶段的快速库存反馈；
- `add_cart_item()` 在事务中锁定 Cart、合并相同 SKU，并保证失败时原值不变；
- `update_cart_item()` 先用当前用户限制查询，再更新数量。

这里要会解释 `transaction.atomic()`（一组数据库操作要么全部成功，要么全部失败）和 `select_for_update()`（事务内锁住选中的行，避免同时修改产生丢失更新）。不要把它误说成库存预占：这里只锁购物车行，事务结束就释放。

### 第四步：读 HTTP 与权限链

阅读顺序：

1. `backend/apps/ordering/urls.py`
2. `backend/apps/ordering/views.py`
3. `backend/apps/ordering/serializers.py`
4. `backend/config/urls.py`

用一次“加入 2 个 M1-BLACK”串起来：

```text
Vue POST /api/cart/items/
→ SessionAuthentication 确认用户 + CSRF
→ AddCartItemSerializer 验证输入 1–99
→ add_cart_item 执行业务规则与事务
→ ORM 写 Cart/CartItem
→ MySQL 唯一/数量约束兜底
→ CartItemSerializer 从 SKU 读取当前价格
→ 返回 Cart JSON
→ Vue 更新数量和小计
```

### 第五步：最后读薄前端

阅读顺序：

1. `frontend/src/api/auth.ts`：如何获取 CSRF Cookie 并登录；
2. `frontend/src/api/cart.ts`：请求方法、CSRF Header 和错误传播；
3. `frontend/src/views/ProductDetailView.vue`：加入购物车入口；
4. `frontend/src/views/CartView.vue`：加载、修改、删除和不可用状态；
5. Router、类型和 CSS 快速浏览即可。

前端的职责是收集输入、展示状态和调用后端，不是决定价格、权限或库存正确性。

## 4. 业务能力训练法

对每个功能用六句话回答：

1. 谁在操作：已登录 Customer；
2. 想解决什么问题：暂存准备购买的 SKU 与数量；
3. 前置条件：SKU/商品/分类有效，当前库存足够，数量合法；
4. 成功后改变什么：自己的 CartItem 被创建、合并或修改；
5. 失败时必须保持什么：原数量不变，别人的数据不变，库存不变；
6. 哪个系统是事实来源：Cart/SKU/Inventory 当前事实来自 MySQL，前端不是事实来源。

完成后，用“商品先加入购物车，随后被下架”重新回答六句话。这能训练你处理业务变化，而不只是正常路径。

## 5. 工程能力训练法

按四层证据复盘同一条规则：

- 输入层：Serializer 拒绝明显非法数量；
- 业务层：Service 处理合并、可售与库存规则；
- 数据层：MySQL 唯一约束和 CheckConstraint 防止绕过；
- 验证层：API/Model 测试证明正常、失败、越权和回滚行为。

建议亲自完成两个小练习：

1. 不看实现，先给 `product_inactive` 补一个 API 测试，再运行目标测试；
2. 在纸上画出两名用户拥有同一个 SKU 时，DELETE 请求为什么只会删除当前用户的数据。

## 6. Learning Done 标准

你不需要背每行 Vue 或 DRF 语法。能够独立做到以下事情，就可以进入 MVP-2：

- 从测试讲出五条核心业务规则和两个失败场景；
- 解释为什么价格不能相信前端、为什么 Cart 不是订单价格快照；
- 解释“检查库存”和“预占库存”的区别；
- 沿请求链找到权限、规则、数据库约束分别在哪一层；
- 说明为什么既有 Serializer 验证，又有数据库约束；
- 修改或补充一个测试，并根据失败输出定位到正确层。
