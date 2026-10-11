# 部署前后变化与完整请求链：一次浏览怎样到达数据库并返回页面

2026-10-11。[学习导航](README.md) · [项目总览](project-current-overview-study-guide.md) · [本轮归档核验](../verification/deployment-request-chain-knowledge-base-publication.md)。源码依据固定为 `57329d80fd6afd13f78b2f6e2ea45f61a898ea6b`；真实运行证据来自已有9B、9C，本轮只整理知识库与时序图。

**先记住完整顺序：准备镜像和数据库 → 启动应用与入口 → 浏览器取得前端文件 → 浏览器运行Vue → Vue发送API请求 → Nginx转发 → Gunicorn中的Django处理 → 按需访问MySQL → JSON返回 → Vue更新页面。**

本专题属于Supporting（支撑主业务的工程能力）。它帮助你在项目面试中独立解释“页面从哪里来、API在哪里执行、部署后连接哪套数据库”，并根据请求停在哪一步定位问题。内容按真实请求顺序展开，图中的参与者与配置对应；已有运行证据足够支撑这条说明，不为整理资料追加部署或业务实验。

## 本专题前后变化

开发时，Vite提供前端，Django开发服务器处理API，数据库使用已有开发环境。部署后，Vue先构建成文件，Nginx作为统一入口，Gunicorn运行Django，三个容器连接独立部署库。业务规则沿用既有实现，应用的打包、运行和访问方式发生了变化。

| 环节 | 开发时 | 本机部署后 |
| --- | --- | --- |
| 前端文件 | Vite开发服务器按开发方式提供，支持开发热更新 | `npm run build`生成`dist`，复制到Nginx镜像 |
| 浏览器访问 | 前端使用开发服务器地址 | 统一使用`http://127.0.0.1:8080` |
| API入口 | Vite把`/api`代理到`127.0.0.1:8000` | Nginx把`/api/`代理到`web:8000` |
| Django进程 | `runserver`开发服务器 | Gunicorn主进程管理2个工作进程，工作进程运行Django |
| 数据库 | 宿主开发程序连接既有开发库，常见入口为`127.0.0.1:3307` | web通过`db:3306`连接`project_one_deployment_local` |
| Django静态资源 | 开发方式提供 | 提前`collectstatic`收集到共享卷，由Nginx读取 |
| 修改代码之后 | 开发服务器可检测修改 | 应用源码已在镜像中，要构建并重建相应运行容器 |

Vite（前端开发与构建工具）的现有开发代理是：

```typescript
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
      },
    },
  },
```

来源：[Vite配置](https://github.com/jasper507/E-commerce/blob/57329d80fd6afd13f78b2f6e2ea45f61a898ea6b/frontend/vite.config.ts)。浏览器发送相对路径`/api/...`，开发时由Vite转发，部署时由Nginx转发；同一套前端业务调用可以继续使用相对路径。前端构建完成后，运行网站不需要启动Vite或Node开发服务器。

**独立部署库没有自动复制开发数据。** 9B看到商品列表为空，是新环境的正常起点；Django迁移生成的系统权限与内容类型也说明“没有业务数据”不等于所有表都没有行。

## 1. 请求发生之前，先准备什么

Image（镜像）是源码、依赖和启动方式的打包结果；Container（容器）是从镜像启动的运行实例。Compose是按一份配置组织容器、网络和数据卷的工具。本项目这套独立部署只包含`db`、`web`、`nginx`三个服务。

首次准备按以下顺序进行，前一步失败就先定位原因：

1. **构建web和nginx镜像。** web镜像安装Python依赖并复制Django源码；前端构建阶段运行`npm ci`与`npm run build`，最终nginx镜像只取得前端`dist`和入口配置。
2. **启动db并等到健康。** 新MySQL初始化独立数据库与持久卷。web需要数据库可连接，才能执行管理命令和业务读取。
3. **用一次性web容器执行`migrate`。** 迁移建立这套新库的表结构；先确认实际库名与连接目标，再执行。它不是导入开发库里的用户、商品和订单。
4. **用一次性web容器执行`collectstatic`。** Django将Admin等静态资源集中到`/app/staticfiles`，写入独立`django_static`卷。
5. **启动web，再启动nginx。** Gunicorn开始接收8000端口的请求；Compose等web健康后，再启动统一入口。Nginx将共享静态卷挂载为只读，同时提供镜像里的Vue文件。
6. **分别检查网页、API、数据库连通和浏览器执行。** HTML返回200只说明入口返回了文件；还要确认JS真正运行、API响应正确、Vue显示出结果。

`migrate`和`collectstatic`属于准备步骤，不会在每次请求时运行，也没有塞进每个Gunicorn工作进程的启动流程。具体命令、预期结果和首次故障处理见[9A操作指南](phase-9a-local-deployment-study-guide.md)，实际执行结果见[9B运行验收](../verification/phase-9b-local-deployment-run.md)。

准备与运行分开，才能读懂“镜像构建成功但网页仍打不开”：构建成功之后，仍可能出现数据库认证失败、应用进程未启动或入口端口被占用。

## 2. 完整时序图：从准备到页面、API与Admin

<!-- BOARD:REQUESTCHAIN -->

图中先画一次性的部署准备，再画浏览器首次访问与API往返，最后展开深路由刷新和Admin静态资源。Gunicorn与Django属于同一个web容器；它们之间是进程内调用。数据库、web、nginx之间才通过容器网络通信。

浏览器可能并行获取JS与CSS，Vue的商品和分类读取也不要求固定先后。图用顺序表达依赖关系：必须先取得并执行必要的JavaScript，Vue才能发起对应API请求；没有把所有独立网络请求规定成串行。

本地保留[图的预览](assets/deployment-request-chain-2026-10-11/request-sequence/diagram.png)与[可编辑Mermaid来源](assets/deployment-request-chain-2026-10-11/request-sequence/diagram.mmd)。Mermaid是一种用文本描述图的格式，便于以后随着入口或部署方式变化修订。

## 3. 第一次打开商品页：先拿文件，再运行页面

以打开`http://127.0.0.1:8080/products`为例。

**第一步，浏览器请求页面地址。** 宿主机的8080端口映射到nginx容器的80端口，Nginx接收`GET /products`。

**第二步，Nginx返回前端入口。** `/products`是Vue页面地址，并不是镜像中一个名叫`products`的HTML文件。Nginx按配置返回`index.html`，让浏览器有一个可启动前端应用的入口。

**第三步，浏览器根据HTML取得JS和CSS。** `/assets/...`文件由Nginx直接从前端构建目录读取。CSS决定页面样式，JavaScript在浏览器中运行Vue；这些资源读取不需要经过Django，也不查询MySQL。

**第四步，Vue开始运行。** `main.ts`创建Vue应用、安装Vue Router并挂载到页面的`#app`。Vue Router（浏览器中的页面路由器）根据当前`/products`地址选择商品列表页面。

**第五步，页面发起数据请求。** 商品页面读取商品列表和分类，发送相对路径`/api/catalog/products/`与`/api/catalog/categories/`。浏览器将它们补成同一个8080入口下的地址。因此网页能展示外壳时，数据可能仍在加载；HTML与API是不同请求。

源码依据：[前端入口](https://github.com/jasper507/E-commerce/blob/57329d80fd6afd13f78b2f6e2ea45f61a898ea6b/frontend/src/main.ts)、[Vue路由](https://github.com/jasper507/E-commerce/blob/57329d80fd6afd13f78b2f6e2ea45f61a898ea6b/frontend/src/router/index.ts)、[商品页面](https://github.com/jasper507/E-commerce/blob/57329d80fd6afd13f78b2f6e2ea45f61a898ea6b/frontend/src/views/ProductListView.vue)、[商品API调用](https://github.com/jasper507/E-commerce/blob/57329d80fd6afd13f78b2f6e2ea45f61a898ea6b/frontend/src/api/catalog.ts)。

## 4. 一次商品API请求怎样进入Django并返回

接着跟踪`GET /api/catalog/products/`。

**① Nginx选择API代理。** 路径匹配`/api/`，Nginx将请求交给容器网络中的`web:8000`，保留原来的`/api/catalog/products/`路径：

```nginx
    location /api/ {
        proxy_pass http://web:8000;
    }
```

来源：[Nginx入口配置](https://github.com/jasper507/E-commerce/blob/57329d80fd6afd13f78b2f6e2ea45f61a898ea6b/docker/deployment/nginx.conf)。Reverse Proxy（反向代理）在这里的作用是接收浏览器请求，再把适合后端处理的请求转给应用。它没有执行商品查询、判断订单归属或核销库存。

**② Gunicorn接收请求并调用Django。** Gunicorn（Python应用服务器）的工作进程通过WSGI（服务器调用Python Web应用的接口）调用`config.wsgi:application`。Django在这个进程中运行。`web:8000`是网络目标，Gunicorn再调用Django并没有多一次HTTP网络跳转。

**③ Django建立请求、经过中间件与路由。** Middleware（中间件）是围绕请求和响应运行的通用处理，包括安全、Session、CSRF和身份相关处理。Django解析URL：项目路由把`api/catalog/`交给目录模块，再找到商品列表View（处理接口的入口）。适用的View前检查和DRF认证、权限规则决定能否继续执行。

这里要按接口区分：商品和分类列表明确关闭DRF认证并允许游客访问，公开GET不要求先登录，也不需要CSRF写请求令牌。个人订单等接口则要识别Session、检查登录及数据归属；登录、退出等POST还要满足各自的CSRF保护。中间件存在，不代表每个请求都一定读取Session表和用户表。

**④ View校验查询参数，ORM按规则读取MySQL。** ORM（将Python查询表达式转成数据库SQL的工具）按上架商品、有效分类、筛选、关键词与分页规则构造查询；当前普通列表按`name, id`排序，固定每页20条，并计算有效SKU的最低价和有货提示。数据库保存和返回数据，业务查询条件由Django应用决定。

web连接`db:3306`中的独立部署库。商品列表与分类接口各有自己的查询，不能把图上的一次“读取MySQL”理解成整个页面只有一条SQL。商品摘要也只是浏览提示，真正下单仍需重新校验价格、库存与业务规则。

**⑤ Django组织JSON，沿原路径返回。** Serializer（将数据整理成接口字段的组件）组织响应；Django与DRF生成HTTP响应，经Gunicorn、Nginx回到浏览器。商品列表的正常响应形状为`{count, next, previous, results}`。

**⑥ Vue读取JSON并更新页面。** 前端检查HTTP状态、解析JSON、更新页面状态。9B的独立新库返回`count=0`、`results=[]`，Vue据此显示空列表。API失败时页面显示失败与重试入口；收到HTML并不能代替收到正确的API JSON。

源码依据：[Gunicorn启动配置](https://github.com/jasper507/E-commerce/blob/57329d80fd6afd13f78b2f6e2ea45f61a898ea6b/docker/deployment/web.Dockerfile)、[Django设置](https://github.com/jasper507/E-commerce/blob/57329d80fd6afd13f78b2f6e2ea45f61a898ea6b/backend/config/settings.py)、[项目路由](https://github.com/jasper507/E-commerce/blob/57329d80fd6afd13f78b2f6e2ea45f61a898ea6b/backend/config/urls.py)、[目录接口](https://github.com/jasper507/E-commerce/blob/57329d80fd6afd13f78b2f6e2ea45f61a898ea6b/backend/apps/catalog/views.py)。

## 5. 三个地址为什么不同，Host为什么保留8080

| 地址 | 谁使用 | 作用 |
| --- | --- | --- |
| `127.0.0.1:8080` | 宿主机浏览器 | 进入Nginx；Compose映射到nginx容器80端口 |
| `web:8000` | nginx容器 | 经Compose网络找到web容器中的Gunicorn |
| `db:3306` | web容器 | 经Compose网络找到独立MySQL |

`web`、`db`是Compose服务名，由这套容器网络解析；顾客浏览器不使用这些地址。容器里的`127.0.0.1`指自己这个容器，不能拿它代替另一个容器的服务名。web与db没有宿主端口映射，浏览器不会直接连接MySQL。

Nginx使用`proxy_set_header Host $http_host;`，让Django继续看到浏览器原始入口`127.0.0.1:8080`。如果丢掉8080，后端生成的绝对分页链接或来源判断可能不符合实际入口。传给后端的HTTP头部描述浏览器入口，网络连接的目标仍然是`web:8000`。

Origin（请求来源）由协议、主机、端口组成。有效请求固定使用`http://127.0.0.1:8080`，`localhost`与`127.0.0.1`是不同主机。Cookie又不按端口隔离，因此本机开发站与部署站使用同一主机时可能互相覆盖Cookie；9C使用独立CookieJar保存自己的会话，避免混入开发登录。

部署后的登录仍由Django应用验证密码哈希、建立Session；MySQL存用户与会话数据，不负责替应用执行Python密码校验。后续请求带回Session Cookie，后端按接口识别本人；这类基于Session的浏览器写请求还带当前CSRF Header。完整机制见[认证、CSRF与数据权限总览](auth-current-overview-study-guide.md)，经代理的实际HTTP证据见[9C指南](phase-9c-session-csrf-operations-study-guide.md)。

## 6. 深路由刷新与Admin：两个容易混淆的分支

### 刷新`/products`为什么还能显示Vue页面

在页面内点击链接时，Vue Router可以在浏览器内切换页面；直接打开或刷新`/products`时，浏览器会重新向Nginx请求这个完整路径。Nginx的普通页面处理为：

```nginx
    location / {
        try_files $uri $uri/ /index.html;
    }
```

来源：[Nginx入口配置](https://github.com/jasper507/E-commerce/blob/57329d80fd6afd13f78b2f6e2ea45f61a898ea6b/docker/deployment/nginx.conf)。`try_files`先找真实文件或目录，找不到就返回前端入口；Vue启动后再解释`/products`。这就是SPA（单页应用，在浏览器中切换页面）的深路由刷新链。

API、Admin、健康检查、`/assets/`和`/static/`各有独立规则。未知API应由后端返回404，缺失静态文件返回404；不能把它们都兜底成200的`index.html`，否则前端可能把HTML当JSON或把缺失JS当成正常资源。

### Admin页面与它的CSS为什么走不同路径

请求`/admin/login/`会经Nginx代理到Django，Django生成Admin HTML。浏览器收到HTML后，再请求其中引用的`/static/admin/...`CSS、JS等文件；这些文件由Nginx直接从共享静态卷读取。

文件是在请求发生前，由`collectstatic`准备到共享卷中的。web挂载它作为`/app/staticfiles`，nginx将同一卷只读挂载到`/usr/share/nginx/django-static`；`/static/`的alias配置指向这里。Vue的`/assets/...`文件来自nginx镜像里的`dist`，两者来源不同。

因此“Admin登录HTML能打开，但样式丢失”应先检查静态收集结果、卷与URL映射；“网页能打开，但API返回502”则先检查Nginx到web的连接和应用状态。502是代理无法正常获得上游响应的信号，要结合日志定位，不能只凭状态码断定数据库一定坏了。

## 7. 当前实际验证到哪里

这里列的是已有阶段的真实证据，本轮没有重新构建、启停容器、迁移、登录或访问数据库。

| 证据 | 已有观察 | 结论边界 |
| --- | --- | --- |
| [9A静态准备](../verification/phase-9a-local-deployment.md) | 独立Compose、Dockerfiles、入口配置、依赖与前端构建核对 | 当时仅准备候选；运行证据看后续9B |
| [9B首次真实运行](../verification/phase-9b-local-deployment-run.md) | 两镜像构建；独立库36项迁移；154个静态文件；三个服务健康；14项入口HTTP检查 | 证明该次基础部署链路，含预期403/404；不是14项交易验收 |
| [9B只读浏览器](../verification/phase-9b-local-deployment-browser.json) | Vue显示空商品页，商品与分类API200，深路由刷新正常；Admin登录页与资源可读取 | 实际执行了JavaScript；没有登录表单或交易操作 |
| [9C真实HTTP身份链](../verification/phase-9c-session-csrf-operations.md) | 15项HTTP检查通过：CSRF取得/轮换、登录、本人读取、错误退出拒绝、正确退出；临时账号与会话精确清理 | 使用HTTP客户端；不冒称浏览器Cookie策略、浏览器登录UI或完整交易验收 |
| [本轮知识库核验](../verification/deployment-request-chain-knowledge-base-publication.md) | 正文、源码摘录、链接、时序图及飞书回读按记录核对 | 文档检查与画板检查不代替运行实验 |

9B记录的环境包括Python3.11.16、Django5.2.17、Gunicorn26.2.0、MySQL8.4.10、Nginx1.30.5，web/Gunicorn运行身份UID10001。基础镜像部分标签可移动，不能据此承诺以后构建结果逐字节相同；具体镜像身份在原运行记录中保留。

当前独立部署配置为`development`、`DEBUG=false`，只绑定本机HTTP。四个能力开关明确关闭：

| 配置开关 | 当前值 | 这套部署里的含义 |
| --- | --- | --- |
| `CATALOG_CACHE_ENABLED` | `false` | 商品详情不启用已有Redis缓存链 |
| `FLASH_SALE_ADMISSION_ENABLED` | `false` | 秒杀不启用已有Lua准入链 |
| `PAYMENT_EVENTS_ASYNC` | `false` | 支付事件沿同步基线处理，未在这套部署启用RabbitMQ/Worker链 |
| `CATALOG_SEARCH_ENABLED` | `false` | 独立ES读入口关闭，普通商品列表仍是MySQL读取与关键词包含匹配 |

源码依据：[独立Compose](https://github.com/jasper507/E-commerce/blob/57329d80fd6afd13f78b2f6e2ea45f61a898ea6b/compose.deployment-local.yaml)。这些能力已有各自实现与隔离证据，不表示已在本次三容器部署中启用。配置为关闭也不能反推原业务实现被删除。

尚未在这套部署完成完整下单、支付和履约验收；公网HTTPS、更新/回退、重启持久性、备份恢复和长期稳定性也没有实测。命名卷提供持久数据的位置，需要实际重启或恢复核对才能宣称相应能力通过。更新、停机和恢复步骤已有[运维参考手册](../deployment/local-operations-runbook.md)，本专题只说明请求链，不执行这些动作。

阅读到这里应能顺着图区分三个问题：哪个进程返回文件、哪个进程执行业务、哪套数据库保存事实。今后部署入口、运行进程或开关状态变化时，同步更新图和当前状态；9A、9B、9C继续保留原日期及证据边界。
