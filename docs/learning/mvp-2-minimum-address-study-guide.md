# MVP-2 Minimum Address 学习指南

## 1. 先记住结论

MVP-2 不只是“地址表的增删改查”。真正需要掌握的是：一个看似普通的 `is_default`，会同时涉及跨行不变量、事务、并发、数据库能力边界和用户所有权。

本阶段的固定阅读顺序是：

1. 先读业务测试和规则；
2. 再读模型与数据库约束；
3. 再读 Service 与 Transaction；
4. 再读 HTTP、Session/CSRF 与权限；
5. 最后快速阅读薄 Vue。

不要先从页面和 Serializer 开始，否则很容易只看到 CRUD，而看不到真正的业务规则。

## 2. 业务问题与当前纵向切片

| 问题 | 当前答案 |
|---|---|
| Actor | 已登录 Customer |
| 前置条件 | 有有效 Session；写请求带 CSRF；输入字段通过后端校验 |
| 成功状态 | 只修改自己的地址；存在地址时恰好一个默认地址 |
| 失败状态 | 非法字段 `400`；别人的/不存在的地址统一 `404`；失败不留下半切换状态 |
| 事实来源 | 当前 Address 与默认关系以 MySQL 为准，Vue 不是事实来源 |
| 本阶段不做 | 省市联动、地图、地址补全、复杂 UX、AddressSnapshot、Order、库存和支付 |

当前规则：

- 首个地址自动成为默认地址；
- 新地址或旧地址被设为默认时，旧默认在同一事务中取消；
- 不能直接把当前默认取消而不给替代地址；
- 删除默认地址后，最早创建的剩余地址接任；
- 删除最后一个地址后，没有默认地址；
- 手机号去除空格、连字符和可选 `+86`，再校验为中国大陆 11 位手机号格式；
- 格式校验不代表号码真实，不发送短信也不验证归属。

## 3. 第一步：先读业务测试和规则

阅读顺序：

1. `backend/apps/accounts/tests/test_address_api.py`
2. `backend/apps/accounts/tests/test_address_concurrency.py`
3. `backend/apps/accounts/tests/test_address_models.py`

先只看测试名称和 Arrange / Act / Assert，回答：

- 为什么客户端提交 `is_default=false`，首地址仍必须成为默认？
- 为什么访问别人的地址返回 `404`，而不是把数据是否存在泄露为 `403`？
- 删除默认地址后，哪一条接任？为什么需要确定性规则？
- 两个线程同时设默认后，测试真正证明的是什么？
- 数据库唯一约束能保证“最多一个”，能否保证“至少一个”？

测试是行为合同。先理解预期，再去看实现怎样满足它。

## 4. 第二步：读模型约束

阅读顺序：

1. `backend/apps/accounts/models.py` 的 `Address`
2. `backend/apps/accounts/migrations/0003_address.py`

### 为什么没有只放一个普通 BooleanField？

如果每行只有 `is_default=true/false`，普通 `UNIQUE(user, is_default)` 会产生错误语义：它不仅限制一个 `true`，也会限制一个用户只能有一个 `false`。

MySQL 又不支持项目所需的 PostgreSQL 风格部分唯一约束。因此当前设计使用：

```text
默认地址：default_marker = 1
普通地址：default_marker = NULL
UNIQUE(user_id, default_marker)
```

MySQL 的唯一约束允许存在多个 `NULL`，但同一个用户不能出现两行 `1`。再用 CheckConstraint 限制 marker 只能是 `1` 或 `NULL`。

数据库能独立保证：

- 同一个用户最多一个默认地址；
- marker 不会变成 2、3 等无效值。

数据库不能仅靠单行约束保证：

- 首地址自动默认；
- 有地址时至少一个默认；
- 删除默认后选择哪一条接任。

这些是跨多行、带顺序的业务规则，要由 Service 保证。

## 5. 第三步：读 Service 与事务

阅读 `backend/apps/accounts/address_services.py`，顺序如下：

1. `_lock_user()`：锁定哪一行，为什么；
2. `_owned_address()`：所有权怎样进入查询条件；
3. `_make_default()`：旧默认与新默认怎样切换；
4. `create_address()`：首地址自动默认；
5. `update_address()` / `set_default_address()`：修改与显式切换；
6. `delete_address()`：删除默认后的补位。

`transaction.atomic()`（数据库事务）表示这一组写操作要么全部提交，要么全部回滚。设置默认不是一次布尔更新，而是至少两步：

```text
旧默认 1 → NULL
目标地址 NULL → 1
```

如果两步之间失败而没有事务，用户可能暂时或永久没有默认地址。

`select_for_update()`（事务内行锁）锁的是当前 User 行。这样即使用户当前还没有地址，两个首次创建请求也会竞争同一把明确存在的锁；如果只锁 Address 查询结果，零地址时没有任何行可锁。

### 两个并发请求同时设默认会怎样？

假设地址 A、B 同时收到“设为默认”：

```text
请求 1 锁 User → 切换到 A → 提交 → 释放锁
请求 2 等待      → 获得锁 → 切换到 B → 提交
最终：B 是唯一默认
```

谁最后获得锁并提交，谁的目标成为最终默认；但任何已提交状态都不会出现两个默认。MySQL 唯一约束是第二道防线，用于拦截绕过 Service 的错误写入。

## 6. 第四步：读 HTTP、Session/CSRF 与权限

阅读顺序：

1. `backend/config/urls.py`
2. `backend/apps/accounts/address_urls.py`
3. `backend/apps/accounts/views.py` 的 Address Views
4. `backend/apps/accounts/serializers.py` 的 Address Serializers

API：

| Method | Path | 作用 |
|---|---|---|
| GET | `/api/addresses/` | 读取自己的地址 |
| POST | `/api/addresses/` | 创建地址 |
| PATCH | `/api/addresses/{id}/` | 局部修改地址 |
| DELETE | `/api/addresses/{id}/` | 删除地址 |
| POST | `/api/addresses/{id}/default/` | 设为默认 |

一条“把地址 7 设为默认”的完整链路：

```text
Vue POST /api/addresses/7/default/
→ 浏览器发送 sessionid Cookie 与 X-CSRFToken
→ DRF SessionAuthentication 确认用户并校验 CSRF
→ AddressSetDefaultView 接收请求
→ set_default_address() 开启事务并锁当前 User
→ ORM 查询 id=7 AND user=request.user
→ MySQL 串行切换 default_marker，并执行唯一约束
→ AddressSerializer 输出当前地址
→ HTTP 200
→ Vue 重新加载列表，默认地址排在最前
```

所有权防护的关键不是“前端只显示自己的地址”，而是后端查询同时包含：

```python
pk=address_id, user=request.user
```

即使用户手改 URL 或请求体，也查不到别人的对象。

## 7. 第五步：最后读薄 Vue

阅读顺序：

1. `frontend/src/types/address.ts`
2. `frontend/src/api/addresses.ts`
3. `frontend/src/views/AddressesView.vue`
4. `frontend/src/router/index.ts` 与 `frontend/src/App.vue`
5. `frontend/src/style.css` 快速浏览即可

Vue 只负责：

- 收集和展示结构化字段；
- 调用 API 并携带 Session/CSRF；
- 展示 loading、空列表、登录要求和后端错误；
- 创建、编辑、删除和触发“设为默认”。

Vue 不负责：

- 决定用户是否拥有地址；
- 保证唯一默认；
- 决定删除默认后的补位；
- 判断手机号是否是最终合法数据；
- 创建订单地址快照。

## 8. 为什么订单必须保存 AddressSnapshot？

Address 是用户当前可修改、可删除的数据；Order 是已经发生的交易事实。

例子：用户下单时地址是“深圳市南山区科技园 1 号”，发货后把常用地址改成“广州市天河区 8 号”。如果历史订单永远通过外键读取当前 Address，那么旧订单会看起来像发往广州，审计、客服和物流记录全部失真。

因此 MVP-3 创建订单时必须复制：

```text
recipient_name / phone / province / city / district / detail
```

这份 AddressSnapshot 属于订单历史，之后不随 Address 修改或删除。MVP-2 只建立当前地址事实，不提前创建没有 Order 归属的快照。

## 9. 业务能力题

1. 当前功能解决了谁的什么问题？
2. 首地址为什么必须自动默认？如果不自动会出现什么流程分支？
3. 删除默认地址有哪三种候选规则：不补位、让用户先选、自动选一条？当前为何选择最早地址接任？
4. 为什么“取消默认”不能只是把布尔值改为 false？
5. 格式合法的手机号为什么仍不能称为真实号码？
6. 为什么 MVP-2 可以删除 Address，而未来 Order 的 AddressSnapshot 不能跟着删除？

## 10. 工程能力题

1. Serializer、Service、数据库约束分别保证什么？
2. `UNIQUE(user, default_marker)` 为什么允许多个普通地址？
3. 为什么锁 User 行比只锁现有 Address 行更能覆盖首次并发创建？
4. 两个并发切换的最终结果是否固定？哪些不变量是固定的？
5. 为什么越权查询采用 `id + user`，而不是先按 id 查询再比较？
6. 如果绕过 Service 直接插入三个 `default_marker=NULL` 的地址，数据库会拒绝吗？这说明了什么边界？
7. 如何用测试证明失败请求没有留下半修改状态？
8. 为什么 AddressSnapshot 必须在创建 Order 的事务中复制，而不是现在提前创建？

## 11. Learning Done 标准

你不需要背 Vue/CSS 或每个 DRF 类。能独立完成以下内容，才算 MVP-2 学习完成：

- 用自己的话说出 Actor、成功/失败状态和五条地址规则；
- 解释默认地址为何不是普通 Boolean CRUD；
- 区分应用层“恰好一个”与数据库“最多一个”的保证；
- 画出一次设默认请求的 Vue → HTTP → DRF → Service → ORM/MySQL → Response 链；
- 解释并发切换为何不会产生两个默认；
- 指出所有权防护实际写在哪个查询条件；
- 解释当前 Address 与未来 AddressSnapshot 的事实边界；
- 不看参考，新增一个字段校验或补一个失败测试并正确运行。
