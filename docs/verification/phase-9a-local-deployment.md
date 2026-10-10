# Phase 9A：本机部署候选准备与静态核对

日期：2026-10-11，Asia/Shanghai。应用基线 `6a159ee`。Supporting / Pair；[学习指南](../learning/phase-9a-local-deployment-study-guide.md)。**本片完成配置准备，不代表已经部署运行。** 项目拥有者授权按“完整指南＋逐步操作”开始；首次启动尚待具体影响确认。

## 要解决的问题与停止条件

已有 Docker 主要提供数据库、缓存、队列和 Worker，尚未组成 Vue/Nginx/Gunicorn/Django 的 Web 运行链。本片准备独立候选，支持解释前端构建、反向代理、应用进程、容器网络及持久数据的责任。停止于配置、依赖及材料核对；镜像构建、迁移、账号准备和实际请求留到获准后的运行片，不自动推进所有中间件。

候选只包含 db/web/nginx。项目名 `project-one-deployment-local`；独立库 `project_one_deployment_local`、独立用户及两个项目卷。仅 Nginx 绑定 `127.0.0.1:8080`，DB/Web 没有宿主端口。Django 使用 development、DEBUG=false，本机 HTTP Cookie；缓存/Lua/MQ/ES显式关闭，现有 staging/production 与各模块环境保护保持。不是公网生产配置。

## 本轮实际执行

| 检查 | 实际结果 | 边界 |
| --- | --- | --- |
| Git 保存门槛 | 本地 main 与远端 main 同为 `6a159ee`；已有6E指南用户修改保留 | 未把未知修改纳入本片 |
| Docker 只读检查 | Docker Server 29.7.2；准备前只有原 MySQL 容器运行 | 没有启停已有服务 |
| Compose config | 使用独立示例 env 文件和进程内公开占位值，`config --quiet`成功 | 没有生成真实凭据文件或启动容器 |
| 空凭据拒绝 | 清除进程 DEPLOY_* 后，空示例值导致 config 非零退出 | 证明缺少值会拒绝；不证明任意非空值足够安全 |
| 渲染结构核对 | 服务集合、回环端口、库/卷名称、DB地址、四开关、无源码bind、静态卷只读均通过 | 渲染仅在内存；不输出真实配置或秘密 |
| 前端当前源码 | `npm run build`成功，含vue-tsc检查，56 modules | 宿主Node构建；不是Docker Node镜像构建或浏览器验收 |
| 部署依赖 | 官方PyPI实际JSON为Gunicorn26.2.0/Python>=3.10；`pip install --dry-run -r requirements/deployment.txt`退出0，Would install gunicorn-26.2.0 | 未安装到Windows虚拟环境，未验证Linux安装/运行；不宣称全网最新 |

[Compose结构结果](phase-9a-local-deployment-static.json)、[前端构建原始输出](phase-9a-local-deployment-frontend-build.txt)。配置检查主体为 `docker compose --env-file docker/deployment/.env.example -f compose.deployment-local.yaml -p project-one-deployment-local config --quiet`；公开占位值仅供检查，不能用于实际部署。默认运行命令改用独立未跟踪的 `docker/deployment/.env`。

## 静态源码核对与设计依据

Web Dockerfile安装独立deployment依赖，复制运行源码、迁移/管理命令及Lua资源；非root UID10001运行Gunicorn，预建可写 `/app/staticfiles`。专属忽略规则采用允许列表，保留原Worker根 `.dockerignore`；没有复制根.env、Git、虚拟环境或测试事故资料。前端多阶段镜像先npm ci/build，再仅把dist与入口配置交给Nginx；前端源码/构建工具不在最终Nginx运行层。

Nginx静态配置保留API/Admin/health路径，Host使用 `$http_host`保留8080；Vue深路由回到index.html，但缺失assets及Django static不回到SPA。collectstatic输出通过独立卷供Nginx只读读取，迁移/collectstatic不塞进每个Gunicorn Worker启动流程。实际挂载权限及Nginx配置语法仍需Linux运行验证。

[Django部署清单](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)支持替换runserver、关闭DEBUG及独立静态文件处理；[Gunicorn部署说明](https://gunicorn.org/deploy/)支持前置代理与保留Host；[Docker构建上下文规则](https://docs.docker.com/build/concepts/context/)支持Dockerfile专属忽略文件。这些是设计依据，不代替本项目运行证据。

## 改动、授权边界与尚未验证

新增独立Compose、Web与前端Dockerfiles及各自ignore、Nginx配置、空独立env模板和deployment依赖；补本片双文档与导航。原compose、根.env、根.dockerignore、Worker依赖、settings环境保护及订单/库存/支付/缓存/搜索业务均未改。前端构建仅生成忽略的dist及类型缓存。未访问或修改开发数据库。

没有pull/build新镜像、创建新容器/网络/卷、执行迁移/collectstatic/账号创建、启动网站、运行HTTP/Session测试或旧业务/MQ/Redis/ES回归；Nginx `-t`、Gunicorn启动、容器DNS/文件权限、持久化和重启行为均待验证。Node24与Nginx stable标签可移动，首次构建须记录实际版本/镜像ID或digest；当前未锁定所有系统依赖，不承诺逐字节构建一致。

首次运行的具体影响：下载构建依赖并占用镜像空间，创建独立网络及MySQL/static卷，迁移只写新库，网站仅供本机8080访问；原开发MySQL容器/库及其他卷保留。新库默认空，没有用户或商品；后续合成账号准备另行说明。停止用本片独立命令down，保留卷；不默认使用down --volumes。拥有者在本聊天提供的AGENTS.md指令“安全边界”要求部署前确认；[当前总纲](../../PROJECT_CHARTER.md)也保留数据与环境启用确认边界。这里不把该聊天要求伪写成现有AGENTS.md文件的一段原文。

下一步获得上述首次运行许可后，按指南分步构建、迁移、启动，并验证网页深路由、API、Admin静态及后续Session边界；只处理实际出现的问题，不从候选准备推导部署成功或个人已掌握。

最后核对7个双文档本地链接、两处配置摘录及既有Compose/ignore/base/settings未改变；16个暂存文件排除了6E用户修改与秘密文件，env模板值为空，cached diff检查通过。独立只读审查未发现阻断候选的P1/P2；实际Docker/Linux检查仍未执行。后续登录使用独立浏览器配置，因为Cookie不按端口隔离。
