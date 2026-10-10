# Phase 9B：本机独立部署首次运行验收

2026-10-11，Asia/Shanghai。应用/部署配置基线 `2722c98`；Supporting / Pair。[学习指南](../learning/phase-9b-local-deployment-run-study-guide.md) · [前片9A](phase-9a-local-deployment.md)。

**结论：基础本机HTTP部署链及只读浏览器空目录验收通过。** 不是公网生产上线，也不是本轮重跑了交易或四中间件实验。

## 范围、授权与停止条件

用户选中首次运行的具体影响，先暂停询问部署前后变化，理解后明确“开始下一步”，本轮又要求“继续”。实际执行生成独立凭据、下载/构建镜像、创建独立网络及两个卷、只迁移新库、启动本机8080。没有再次要求相同许可。

能力目标是解释源码打包、应用进程、反向代理、静态资源、容器网络和数据库初始化怎样连起来。证据达到镜像可构建、指定库初始化、运行进程/挂载/端口、实际HTTP与公开页面即可停止；不扩登录数据、中间件或线上容量实验。

开始时main与远端同为`2722c98`，8080无监听、无该部署项目容器或卷，原MySQL容器健康。文档收尾前，另一聊天的文档提交`5495e44`已同时在本地/远端；本片沿该HEAD继续，仅纳入本片文件。6E指南原有用户修改保留。

## 实际执行顺序与结果

所有Compose命令显式使用`--env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local`，不合并开发Compose或读根`.env`。

| 步骤与目的 | 实际观察 | 原始证据 |
| --- | --- | --- |
| 生成独立随机凭据并`config --quiet` | 新环境文件由排他创建写入；Git忽略；Compose退出0；没有打印真实值 | 配置形状沿用[9A静态结果](phase-9a-local-deployment-static.json)；本轮现场执行未另保存秘密/完整渲染 |
| `build web nginx`，确认Linux打包链 | 两镜像Built；镜像内前端Vite8.3.0构建56模块；Gunicorn安装成功 | [build](phase-9b-local-deployment-build.txt) |
| `up -d --wait db`，只启动新库 | 独立网络和MySQL卷创建，db healthy | [db start](phase-9b-local-deployment-db-start.txt) |
| 一次性web容器实际查库，再决定迁移 | DATABASE为`project_one_deployment_local`，用户`project_one_deployment@%`，主机`db:3306`，UID10001 | [source check](phase-9b-local-deployment-source-check.txt) |
| `run --rm --no-deps web ... migrate --noinput` | 36条Applying均OK，包括订单0005联合索引；只在新库应用 | [migrate](phase-9b-local-deployment-migrate.txt) |
| `run --rm --no-deps web ... collectstatic --noinput` | 154文件复制到`/app/staticfiles` | [collectstatic](phase-9b-local-deployment-collectstatic.txt) |
| `up -d --wait web nginx` | db/web/nginx全部healthy | [web start](phase-9b-local-deployment-web-start.txt) |
| 容器内只读运行检查 | Django check、migrate --check、pip check、nginx -t均退出0；待迁移0 | [runtime](phase-9b-local-deployment-runtime.json) |
| 本机入口实际HTTP检查 | 14项符合预期，包含预期403/404；不是14个业务测试 | [HTTP JSON](phase-9b-local-deployment-http.json) |
| 独立Playwright会话只读浏览器 | Vue空商品页实际呈现，深路由刷新正常，两个API200；目录控制台0错误/0警告；Admin资源200 | [browser JSON](phase-9b-local-deployment-browser.json) |

执行路径中没有导入用户、商品或订单数据。Django迁移会生成系统权限和内容类型，因此“业务库为空”不意味着所有表均无行。运行核对的用户/Session/商品/订单数均为0。迁移raw与数据库记录均为36项，后续指南采用此实测值。

## 运行身份、入口和资源边界

[运行JSON](phase-9b-local-deployment-runtime.json)记录实际版本：Python3.11.16、Django5.2.17、Gunicorn26.2.0、MySQL8.4.10、Nginx1.30.5；两个应用镜像均linux/amd64，镜像ID与MySQLRepoDigest另存。构建raw保留Python/Node/Nginx当次解析的digest；Node24与Nginx标签可移动，Python传递依赖/系统包也未全部锁定，不能承诺未来逐字节相同。

实际Gunicorn为1个master＋2个worker，UID10001；`DEBUG=false`，环境仍development。缓存、Lua准入、异步支付、ES四开关均false。

db仅挂载`project-one-deployment-local_mysql_data`；web写独立`django_static`卷，nginx对应挂载只读，实际CSS文件存在且web可写。没有源码bind；运行镜像后台源码中没有`.env`文件。DB/Web没有宿主端口，只有Nginx的`127.0.0.1:8080→80`。

本轮没有访问原开发数据库或缓存、没有对它们执行迁移/写入；未建立旧库整表指纹，不将“隔离配置与运行来源检查”说成旧库逐字段审计。原开发容器、Compose、根环境文件及业务代码未改。独立凭据文件保留于Git外，不保存Cookie、CSRF Token或完整容器环境。

## HTTP与浏览器证据怎么读

HTTP记录中的`/`、`/products`、`/login`和构建JS/CSS为200；`/health/`为数据库ok；商品分页为空、分类为空；Admin登录与CSS200。未知API、assets及static分别404；未登录`/api/auth/me/`为403。这说明入口没有把错误全部吞成SPA页面。

HTTP检查发生在浏览器之前，其JSON中的`browser_javascript_execution_verified=false`保留当时边界。后续浏览器JSON另记录实际Vue执行：页面显示“共0件商品”和“没有找到商品”，商品/分类API均200；深路由刷新后仍显示相同内容。Admin只读取登录页面和静态资源，没有提交表单。

CLI的首次`--help`虽输出帮助，但Windows Node退出时出现一次UV_HANDLE_CLOSING断言，退出1；随后专用`phase9b`会话打开、刷新、快照、请求/控制台读取和关闭均退出0。没有修改项目依赖、全局安装或把首次命令称为通过；浏览器会话已关闭，三个部署容器继续运行。

## 未验证与下一步

- 没有真实登录Session/CSRF POST、账号/商品创建、购物车/下单/支付/履约验收；本轮没有运行业务pytest或重复前序并发/MQ/Redis/ES实验。
- 四中间件仍关闭，ES既有来源guard不变，不能称整套项目全部模块已部署。
- 没有公网TLS/HTTPS、生产配置、容量/长期稳定性、重启持久性、更新/回滚或备份恢复演练；restart_count为0只描述观察时状态。
- 命名卷提供持久存储位置，但未通过重建或重启验证；健康检查也不证明每条业务路径正确。

当前证据已足以支持首次基础部署说明，停止扩展。最小后续是确认一个独立学习账号后验证同一入口Session/CSRF成功与拒绝边界，再整理更新、停机、备份与公网部署流程。是否实际部署全部中间件按面试解释收益另行评估，不自动生成展示数据。

## 文档收尾检查

双交付互链并同步导航、README、Charter与CONTEXT；9A开头只补后续链接，保留原静态证据。[文档静态结果](phase-9b-local-deployment-document-check.json)记录本地链接、39份导航指南、36迁移/14HTTP计数、Compose quiet与diff检查通过；独立凭据唯一且被Git忽略，精确秘密扫描0匹配。原始日志仅统一UTF-8/换行及尾部空白，保留执行结果。独立只读复核未发现P1/P2，不把文档核对列为运行测试。
