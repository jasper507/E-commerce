# Guest Catalog Browse UI — 本地交付与验证

日期：2026-09-17（Asia/Shanghai）。在保存项目的本地 `main` 上实现，尚未 commit/push；没有创建 branch、worktree 或 PR。

## 1. 解决的问题与闭环

后端已有公开商品目录 API，但没有可操作的浏览器入口。本次新增游客商品浏览界面，让使用者不必手写请求就能发现商品与查看规格。

业务闭环是：**进入商品目录 → 分类筛选 → 后端翻页 → 查看商品详情和 SKU → 返回原分类/页码继续浏览**。

这是商品发现闭环，不是交易闭环：没有购物车、登录 UI、下单、库存预占或支付。Vue Admin、搜索、图片上传和视觉精修也明确排除。

真实请求链是：

```text
浏览器操作
→ Vue Router 更新路径或 category/page
→ Vue View 的 watch / loading 状态
→ 原生 fetch Client（GET、检查 response.ok）
→ Vite 开发期 /api proxy
→ Django URL → DRF 公开 View
→ QuerySet 组合公开可见性与分类条件
→ MySQL 查询
→ DRF Serializer 输出公开 JSON
→ TypeScript 类型约定的响应对象
→ success / empty / error / 404 UI
```

Mermaid 源文件与 PNG：`diagrams/2026-09-17T023151/diagram.mmd`、`diagram.png`。旧的 `diagrams/2026-09-16T234554/` 两个未跟踪文件原样保留，未混入本次实现或暂存。

## 2. 改动地图

| 文件/区域 | 作用 |
|---|---|
| `frontend/package.json`、lockfile、Vite/TS 配置 | Vue/TypeScript 工程和可复现依赖；增加 Vue Router、类型检查命令、开发 proxy |
| `frontend/src/router/index.ts`、`App.vue`、`main.ts` | 列表/详情/未知路由 404；应用入口与公共 Layout |
| `frontend/src/types/catalog.ts` | 对齐后端公开数据类型；金额保持字符串，不添加不存在的图片/库存数量字段 |
| `frontend/src/api/catalog.ts` | 原生 fetch GET 封装、HTTP 状态检查、API 错误和取消信号 |
| `frontend/src/views/ProductListView.vue` | 分类独立加载、列表、20 条后端分页、empty/error/retry、返回第一页 |
| `frontend/src/views/ProductDetailView.vue`、`NotFoundView.vue` | SKU/缺货、详情错误重试、API 404 和前端路由 404 |
| `frontend/src/style.css`、`index.html` | 基本可读布局、手机宽度适配、页面元数据；无图片上传/外部图源 |
| `tests/fixtures/catalog_browser.json` | 合成浏览器验证数据，不会自动写入开发库 |
| README、Charter、Context、本文件 | 记录当前进度、启动限制、真实验证证据与学习边界 |
| `.gitignore` | 排除浏览器本地日志/截图；不排除或删除用户 diagram |

没有修改后端业务代码、API 契约、模型、迁移、认证、数据库配置、Compose 或 CI；也未操作其他 detached worktree。模板演示组件与模板图片已移除，它们均是本轮刚生成的脚手架文件，不是用户原有内容。

## 3. 关键正确性边界

- API 金额是字符串，例如 `"129.00"`；前端只展示，不决定订单金额。`lowest_price=null` 是无有效 SKU 价格，不等于零元。
- 有价格仍可能缺货；有效但缺货的 SKU 继续显示。详情没有有效 SKU 时显示“暂无可售规格”。
- `in_stock` 只是浏览快照，不能承诺未来订单一定成功。购买阶段必须重新读取 MySQL 事实并处理并发。
- 上下架由 Django 过滤，不能只在 Vue 中隐藏。详情不存在、Product 下架、Category 下架统一 404。
- 公开 Catalog GET 不读取 Session、不要求 CSRF；公开 POST 等写方法为 405。本次不实现或改变认证写请求。
- 分类过滤组合进 QuerySet 的最终 SQL，不是先查询全部再追加独立分类查询。分页通常为 COUNT + 当前页 SELECT；详情为商品主体 + SKU 预取。
- `next/previous` 可以是绝对 URL，因此只取其是否为 null 判断按钮，再用相对 API URL 构造页码请求。
- 每次请求使用独立局部 AbortController；快速切换后旧请求不能写入新 UI 状态。请求取消不是面向用户的错误。
- TypeScript 接口是编译期契约，不是运行时 JSON 校验器。本轮没有引入额外运行时 schema 库。

## 4. 实际验证结果

环境：Node.js 24.16.0、npm 11.13.0、Vue 3.5.42、Vue Router 4.6.4、Vite 8.3.0；项目虚拟环境 Python 3.11.9、Django 5.2.17、pytest 9.1.1、MySQL 8.4.10。浏览器 CLI 启动真实 Chromium 152。

| 实际运行 | 观察结果 |
|---|---|
| `npm --prefix frontend run type-check` | 通过，vue-tsc 没有报告错误 |
| `npm --prefix frontend run build` | 通过，34 modules transformed；构建产物在被忽略的 `frontend/dist/` |
| `.venv/Scripts/python.exe -m pytest backend/apps/catalog/tests backend/apps/inventory/tests --create-db` | **19 passed in 4.81s** |
| `.venv/Scripts/python.exe -m pytest --create-db` | **50 passed in 15.75s** |
| `manage.py check` | System check identified no issues |
| `manage.py makemigrations --check --dry-run` | No changes detected |
| lark-whiteboard 渲染与 `--check` | PNG 可渲染，已视觉查看；0 errors、0 warnings |

本轮 pytest 输出没有单列 subtest 数量，因此这里不沿用旧文档的 subtest 数字。

### 浏览器 UI + Network

所有正常 Catalog 响应来自 Django 和真实隔离 MySQL，不使用 Mock 成功 JSON。

| 操作 | Network / UI 观察 |
|---|---|
| 打开 `/products` | 分类与列表 GET 各 200；count=24，第一页 20 条，显示金额与库存状态 |
| 点击空分类 | `?category=empty-category` 返回 200、count=0、results=[]；显示 empty，能返回全部商品 |
| 点击鼠标分类 | `?category=mice` 返回 200；只显示鼠标 |
| 点击下一页、上一页 | 下一页发出 `?page=2`，返回剩余 4 条；末页下一页禁用；上一页重新请求第一页 |
| 点击无线鼠标详情 | `/api/catalog/products/wireless-mouse-m1/` 返回 200；显示 2 个已上架 SKU，价格分别为字符串 129.00/139.00；黑色有货、白色缺货；下架灰色 SKU 不显示 |
| 直接刷新详情 URL | 路由可重新加载，并再次请求真实详情接口 |
| 无 SKU 商品详情 | 返回 200，显示 0 个 SKU、默认介绍、当前缺货和“暂无可售规格” |
| 不存在商品详情 | 真实 API 404，显示专用“商品不存在或已下架”和返回列表 |
| 越界 `?page=999` | 真实 API 404，显示错误与“返回第一页”；点击后请求第一页并恢复 |
| 未知前端 `/no-such-page` | 前端路由 404，不误当作商品数据为空 |
| 浏览器 offline → 返回列表 | 列表与分类 GET 失败（ERR_INTERNET_DISCONNECTED），分别显示 error |
| online → 列表/分类重试 | 两次新 GET 各 200，恢复商品和分类按钮 |
| offline → 打开详情 → online 重试 | 首次详情请求失败，点击重试发出同一个详情 GET 并返回 200 |
| 真实请求人为延迟，鼠标 → 立即键盘 | 取消旧请求后新请求仍显示 loading，无旧卡片；最终只呈现键盘 1 件；无伪造 JSON |
| 390px 手机宽度 | 查看详情截图，未见横向内容溢出或规格被遮挡 |

局部截图保存在被忽略的 `output/playwright/catalog/`。API 404 与 offline 故障注入期间浏览器出现相应资源失败日志是预期结果，不等同于未捕获 Vue 异常；正常首屏刷新后控制台 0 errors、0 warnings。

### 数据保护与隔离

开发库尚未应用 Catalog/Inventory 初始迁移；本轮**没有**迁移或写入开发库。浏览器使用 `testserver` 创建的 `test_project_one`，装载 34 条合成对象：3 分类、24 商品、4 SKU、3 Inventory。

Django `testserver` 停止后保留测试库；本轮随后运行 pytest `--create-db` 重建并在结束时销毁它。只读查询 `information_schema.SCHEMATA` 确认 `test_project_one` 已不存在。

## 5. 安全复现浏览器链路

先确认没有其他测试任务/服务正在使用 `test_project_one`；`--noinput` 会重建这个隔离测试库，不能与 pytest 并行运行。确认目标是可丢弃测试数据库后，在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe backend\manage.py testserver tests\fixtures\catalog_browser.json --noinput --addrport 127.0.0.1:8000
```

另一个终端：

```powershell
npm --prefix frontend ci
npm --prefix frontend run dev -- --host 127.0.0.1
```

打开 Vite 输出的地址，按上表操作并在浏览器 Network 查看 GET、状态码和 JSON。当前 Vite 不直接跟随 DRF `next` 的绝对地址。

停止 testserver 和 Vite 后，再执行目标 pytest `--create-db` 完成回归与测试库清理。不要把 testserver fixture 加载到开发或真实数据库。

## 6. 学会后可以锻炼的能力

| 能力 | 应能亲自解释或完成的任务 |
|---|---|
| 前后端契约建模 | 从 DRF JSON 写出 TS 接口，处理 nullable/string/数组；识别前端不存在的字段 |
| 路由与状态管理 | 用 URL 表达 category/page/slug，解释 watch 为什么发请求、分类为何回第一页、详情为何保留返回上下文 |
| 异步工程正确性 | 区分 loading/empty/error/404，解释 fetch HTTP 错误不会自动抛出，修复旧请求覆盖与 loading 竞态 |
| HTTP 联调与诊断 | 在 Network 看请求 URL、参数、状态码、响应，定位 UI、代理、后端、数据库哪一层出错 |
| 电商业务边界 | 解释 Product/SKU、金额精度、缺货仍可展示、上架可见性、浏览库存为何不是购买承诺 |
| 可复现验证与数据保护 | 使用 lockfile、类型检查、构建、真实 MySQL 回归和隔离 fixture；区分测试通过与真实部署完成 |
| 纵向切片交付 | 把用户操作、接口、后端规则、数据库和 UI 反馈串成一个小闭环，并用证据说明哪些阶段仍未做 |

用户本次明确取消 Catalog 学习检查，直接要求交付。**AI 已实现并验证，不代表用户的 Learning Done 已被证明**；以上能力需要本人读代码、重建请求链、修改条件和调试后才能算掌握。

## 7. 未验证与交付边界

- 生产同源反向代理、SPA fallback、部署、远端 CI：未实现/未验证，本轮没有发布。
- 没有 Vitest、组件自动化回归套件或持续运行的浏览器 E2E 测试框架。浏览器验证属于本轮实际验收，不冒充未来可持续自动回归。
- 浏览器本轮没有单独注入每一种 5xx、超时或格式错误 JSON；没有显式 fetch 超时机制。HTTP 状态检查和通用 error 路径已静态 Review，offline/retry 已实际验证。
- 浏览器前进/后退、详情返回保留 query 的实现已静态检查；没有单独把全部历史导航组合列为本轮实际测试。
- commit/push 需要用户确认；本次不暂存文件。建议 commit message：`feat(frontend): add guest catalog browse UI and verification evidence`。
