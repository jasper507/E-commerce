# MVP-1 Authenticated Cart — 自动验证记录

日期：2026-09-18（Asia/Shanghai）。本地 `main` 工作区实现，尚未 commit/push。

## 1. 实现范围

- Django `Cart`/`CartItem` 模型与初始迁移；
- 单用户单 Cart、`(cart, sku)` 唯一、数量 1–99 数据库约束；
- Authenticated + Session/CSRF 的 GET/POST/PATCH/DELETE API；
- 同 SKU 合并、可售检查、当前库存快速检查、用户隔离和服务端价格；
- Vue 极简登录、商品详情加入购物车、购物车查看/修改/删除；
- Model/API 自动测试和学习指南。

未包含 Address、Order、库存预占、Redis、MQ 或 Payment。

## 2. API 契约

| Method | Path | 作用 |
|---|---|---|
| GET | `/api/cart/` | 读取当前用户购物车；空购物车不创建数据库记录 |
| POST | `/api/cart/items/` | 用 `sku_code + quantity` 新增或合并 SKU |
| PATCH | `/api/cart/items/{sku_code}/` | 把当前用户该 SKU 数量改为新值 |
| DELETE | `/api/cart/items/{sku_code}/` | 删除当前用户该 SKU；失效条目仍可删除 |

所有写请求必须带有效 Session 和 CSRF。客户端提供的价格不参与计算。

## 3. 实际运行结果

| 命令 | 结果 |
|---|---|
| `.venv\Scripts\python.exe backend\manage.py check` | 通过，0 issues |
| `.venv\Scripts\python.exe backend\manage.py makemigrations --check --dry-run` | 通过，No changes detected |
| `.venv\Scripts\python.exe -m pytest backend\apps\ordering\tests -q` | **14 passed, 4 subtests passed in 11.08s** |
| `.venv\Scripts\python.exe -m pytest -q` | **64 passed, 12 subtests passed in 23.58s** |
| `npm run build`（`frontend/`） | 通过，vue-tsc + Vite；40 modules transformed |
| `git diff --check`（本切片代码） | 通过；只有 Windows LF/CRLF 提醒 |

MySQL 8.4 容器在测试前为 healthy。pytest 使用隔离测试数据库；没有迁移或写入开发数据库。

## 4. 尚未验证

- 没有迁移开发数据库，也没有对开发数据库写入演示用户或购物车；
- 没有执行浏览器人工操作、响应式截图或 Network 面板验收；
- 没有持续浏览器 E2E 套件；Vue 页面由类型检查和生产构建验证；
- 没有测试同一新用户第一次并发创建 Cart 的竞争；库存并发正确性属于 MVP-3 深度主线；
- 没有注册/退出 UI、地址、结算、订单和支付。

因此当前结论是“代码和自动测试通过”，不是“生产部署完成”或“完整交易闭环完成”。
