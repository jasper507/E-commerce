# MVP-2 Minimum Address — 自动验证记录

阅读入口（2026-10-10补链）：[本片学习指南](../learning/mvp-2-minimum-address-study-guide.md)。仅增加导航，以下日期、结果与限制保留原实验记录。

日期：2026-09-18（Asia/Shanghai）。基于 `88f408c` 实现并完成验证，随后已提交并快进到本地 `main`；尚未 push。

## 1. 实现范围

- Django `Address` Model 与 `0003_address` 迁移；
- 收件人、手机号、省/市/区、详细地址与默认标记；
- 手机号空格、连字符、可选 `+86` 规范化及后端格式校验；
- 首地址自动默认、事务内切换默认、拒绝直接取消唯一默认；
- 删除默认后由最早创建的剩余地址接任；
- User 行锁串行化同一用户的地址写操作；
- MySQL 唯一/Check 约束保证每用户最多一个默认标记；
- Session/CSRF + 用户所有权 API；
- Vue 地址列表、创建、编辑、删除、设为默认及必要错误状态；
- Model、API、权限、字段、失败回滚与双线程并发测试；
- 学习指南与项目事实文档更新。

未包含 AddressSnapshot、Order、库存预占、Redis、MQ、Payment、省市联动、地图或地址补全。

## 2. 已确认 API

| Method | Path | 成功行为 |
|---|---|---|
| GET | `/api/addresses/` | 只返回当前用户地址，默认地址优先 |
| POST | `/api/addresses/` | 创建地址；首地址自动默认 |
| PATCH | `/api/addresses/{id}/` | 局部编辑自己的地址，可设为默认 |
| DELETE | `/api/addresses/{id}/` | 删除自己的地址；默认地址删除后确定性补位 |
| POST | `/api/addresses/{id}/default/` | 幂等地设为默认 |

所有写请求要求有效 Session 和 CSRF。不存在和不属于当前用户的地址统一返回 `404`。

## 3. 验证环境

- Python 3.11.9
- Django 5.2.17
- Django REST Framework 3.18.1
- pytest 9.1.1 / pytest-django 4.14.0
- MySQL 8.4.10，Docker 服务 `project-one-ecommerce-db-1` 为 healthy
- Node.js 24.16.0 / npm 11.13.0

实施期间的 Codex 工作树没有自己的 Python `.venv`，因此测试复用 `C:\project-one-ecommerce\.venv` 中的锁定依赖；命令工作目录和被测代码始终指向当时的实现工作树。前端先执行 `npm ci`，只根据现有 lockfile 安装到被 Git 忽略的 `node_modules`，没有更改依赖声明。

## 4. 实际运行结果

| 命令 | 结果 |
|---|---|
| `C:\project-one-ecommerce\.venv\Scripts\python.exe backend\manage.py check` | 通过，0 issues |
| `...\python.exe backend\manage.py makemigrations --check --dry-run` | 通过，No changes detected |
| `...\python.exe -m pytest backend\apps\accounts\tests\test_address_models.py backend\apps\accounts\tests\test_address_api.py backend\apps\accounts\tests\test_address_concurrency.py -q` | **16 passed, 6 subtests passed in 16.66s** |
| `...\python.exe -m pytest backend\apps\accounts\tests -q` | **44 passed, 6 subtests passed in 32.00s** |
| `...\python.exe -m pytest -q` | **80 passed, 18 subtests passed in 46.58s** |
| `npm run type-check`（`frontend/`） | 通过 |
| `npm run build`（`frontend/`） | 通过，Vite 8.3.0，43 modules transformed |
| `git diff --check` | 通过；只有 Git 的 LF/CRLF 工作树提醒 |

pytest 结束后查询不到 `test_project_one`，说明隔离测试数据库已销毁。本轮没有运行 `manage.py migrate`，没有迁移或写入开发数据库。

## 5. 关键证据对应关系

| 规则 | 证据 |
|---|---|
| 首地址自动默认 | API 测试提交 `is_default=false` 后仍断言默认 |
| 最多一个默认 | MySQL `UNIQUE(user, default_marker)` 与重复默认 IntegrityError 测试 |
| marker 只能为 1/NULL | MySQL CheckConstraint 测试拒绝 2 |
| 切换默认原子完成 | Service `transaction.atomic()` + User 行锁 + API 状态断言 |
| 并发切换不产生双默认 | `TransactionTestCase` 两线程同时切换后断言默认数为 1 |
| 删除默认补位 | API 测试断言最早剩余地址接任 |
| 越权防护 | PATCH/DELETE/设默认访问他人地址均为 404 且原数据不变 |
| 失败保持原状态 | 取消唯一默认返回 400，字段与默认状态均保持 |
| 字段边界 | 手机号规范化、非法号、空收件人和空详细地址测试 |

## 6. 尚未验证与限制

- 没有迁移开发数据库，也没有写入真实或演示地址；
- 没有执行浏览器人工点击、响应式截图或 Network 面板验收；Vue 只完成类型检查和生产构建；
- 没有持续浏览器 E2E 测试；
- 并发测试证明两个 Service 调用最终只有一个默认，但不承诺哪个请求一定最后胜出；
- 数据库独立保证“最多一个默认”，Service 保证“存在地址时至少一个默认”。绕过 Service 直接写全部 `NULL` 仍可能破坏后者；
- AddressSnapshot、历史订单不变性和创建订单事务将在 MVP-3 验证；
- 改动已进入本地 `main`，但尚未 push，远端 CI 未观察。

因此当前结论是：MVP-2 代码、MySQL 自动测试和 Vue 静态/生产构建验证通过；不是浏览器人工验收完成、开发数据库升级完成或完整下单闭环完成。
