# Project One Frontend

Vue 3 + TypeScript/Vite 顾客端。当前实现 Guest Catalog Browse、MVP-1 Authenticated Cart 和 MVP-2 Minimum Address：

- 商品列表与分类过滤；
- Django 后端分页；
- 商品详情与 SKU 状态；
- loading、empty、error、404 和重试。
- Session/CSRF 登录；
- 商品详情加入购物车；
- 查看、修改数量和删除自己的购物车项。
- 查看、新增、编辑、删除自己的收货地址，并显式设置默认地址。

## 本地运行

本轮使用 Node.js `24.16.0`、npm `11.13.0`。依赖精确版本记录在 `package-lock.json`。

先确保本地 Django 数据库已应用 Catalog/Inventory 迁移，然后在项目根目录启动后端：

```powershell
.\.venv\Scripts\python.exe backend\manage.py runserver 127.0.0.1:8000
```

迁移会改变数据库结构，不应在未确认目标库前顺手执行。本轮没有迁移开发库；如要复现本轮验证，请使用下文文档中的隔离 `testserver` 方法。

另开终端进入本目录：

```powershell
npm ci
npm run dev
```

访问 Vite 输出的本地地址。开发服务器会把 `/api` 请求代理到 Django，浏览器仍使用同源相对 URL。

前端路由为 `/products`、`/products/:slug`、`/login`、`/cart` 和 `/addresses`；列表的 `category` 和 `page` 保存在 URL 中。分类变化重置第一页，详情返回保留原查询参数。分页仅根据后端 `next/previous` 判断按钮状态，不直接请求可能跨源的绝对 URL。

Vite proxy 只在开发期生效。`npm run preview` 不是前后端部署方案；生产环境仍需同源反向代理与 SPA 路由 fallback（把前端路径交回 `index.html`），本次没有实现或验证这些部署配置。

## 验证

```powershell
npm run type-check
npm run build
```

当前不包含 Vue Admin、注册/退出 UI、结算、AddressSnapshot、库存预占、支付、搜索、图片上传、Axios 或 Vitest。购物车只显示后端当前价格与库存状态，不锁价、不预占库存。地址页不决定默认地址不变量或所有权；这些规则由 Django Service 与 MySQL 保证。

Catalog 完整证据与隔离浏览器复现步骤见 `../docs/verification/guest-catalog-browse.md`；购物车自动验证与学习边界见 `../docs/verification/mvp-1-authenticated-cart.md`；地址切片证据见 `../docs/verification/mvp-2-minimum-address.md`。
