# 本机部署第一片：把浏览器、入口和Django连起来

2026-10-11。[学习导航](README.md) · [验证记录](../verification/phase-9a-local-deployment.md)。

**本轮只准备部署候选并进行静态核对，尚未构建镜像、创建部署库、启动容器或验证HTTP。下文命令全部待首次运行确认后执行，不是本轮已完成记录。**

后续：[9B首次运行指南](phase-9b-local-deployment-run-study-guide.md)与[运行验收](../verification/phase-9b-local-deployment-run.md)已记录获准后的真实镜像、迁移、HTTP及只读浏览器结果。本文保留9A准备阶段的证据边界，下面步骤仍可用作操作参考。

## 本片前后变化

- **开始前：** 项目有业务实现与隔离测试，没有本片独立的本机容器部署闭环。
- **本片改变：** 准备独立Compose、后端/前端镜像、Nginx入口和环境模板；把首次启动顺序与隔离边界写清楚。
- **完成后与边界：** 得到可审阅的部署候选。是否真正能构建、启动、访问网页与API，需首次运行确认后逐项验证；不能把配置文件存在称作部署成功。

## 1. 先理解谁在运行项目

- **Docker**是运行隔离应用环境的工具，不需要在你的Windows上直接安装整套部署版Python/MySQL。
- **Image（镜像）**是应用程序、依赖和启动方式的打包结果；**Container（容器）**是从镜像启动的运行实例。
- **Compose**按一份配置组织多个容器、网络和数据卷。数据卷保存数据库数据等需要跨容器重建保留的文件。
- **Nginx**是浏览器的统一入口：提供前端文件，把API、健康检查和Admin请求转给后端。
- **Gunicorn**是后端应用服务器，本片启动2个工作进程；**Django**在这些进程里处理HTTP、业务规则与数据库查询。

Node 24只在构建阶段把Vue项目编译成前端文件，Nginx运行时直接提供构建产物。它不启动Vite开发服务器，也不需要让每位顾客运行Node。

浏览器访问本机8080 → Nginx提供页面或转发请求 → Gunicorn里的Django处理 → 独立MySQL保存事实。这片是Supporting/Pair：学会部署顺序、依赖故障定位与数据保护，能解释为什么“程序能运行”还需要入口和持久存储；不要求面试时展示机器。

整体部署按三层收束：先打通基础网页/API/MySQL链路；后续单独确认缓存、Lua、MQ、ES的部署边界；公网HTTPS、更新与恢复再单独处理。不把本机容器启动冒充全部生产工程完成。

## 2. 最重要的设计：独立环境与本机入口

本片固定使用`compose.deployment-local.yaml`，项目名`project-one-deployment-local`；数据库名`project_one_deployment_local`，使用独立数据卷。所有Compose命令都显式指定这些文件与名称，避免误用原开发服务。

Nginx唯一对外端口的核心配置是：

```yaml
ports:
  - "127.0.0.1:8080:80"
```

它只接受本机连接，不向局域网所有网卡开放。新MySQL没有主机端口映射，不能从Windows的3307连接它；原`compose.yaml`、根目录`.env`和原数据库不改。

容器中的Django连接`db:3306`：`db`由本Compose网络解析到数据库容器。容器里的`127.0.0.1`指它自己，不是MySQL容器；原开发映射的`127.0.0.1:3307`也不是这套部署的连接地址。

本片为`development`环境、`DEBUG=false`、仅本机HTTP。缓存、Lua、MQ和ES显式关闭，既有保护规则保留；已实现的中间件能力不等于已经随本片部署。公网HTTPS与生产安全配置尚未完成。

另一种选择是复用开发数据库，少建一套服务，但首次迁移、试错与后续数据准备容易影响学习数据。独立库和卷增加磁盘占用，换来明确可核对的边界。本片不自动加载已有测试夹具，也不创建账号。

## 3. 两个容易踩坑的入口规则

代理需要保留浏览器的Host端口；Vue深路由刷新需要退回前端入口。下面两条来自不同Nginx位置，省略其他配置：

```nginx
proxy_set_header Host $http_host;
try_files $uri $uri/ /index.html;
```

`$http_host`保留`127.0.0.1:8080`里的8080，使后端看到正确入口地址，生成的分页链接与同源判断才不会丢端口。整个验收固定用`127.0.0.1:8080`，不要中途换成localhost；浏览器会按不同主机管理Cookie。

Cookie不按端口隔离；后续登录验证使用独立浏览器配置或隐私窗口，避免同一主机的开发站和学习站互相覆盖Session/CSRF Cookie。

Vue的`/products`不是服务器上的文件。直接刷新时，Nginx应返回`index.html`，再由Vue Router还原页面。API、Admin和Django静态资源各有独立处理，不能把API错误也当成前端页面。

Django `collectstatic`把Admin等静态资源集中到共享`django_static`卷，Nginx只读提供它们。Vue构建文件与Django静态文件来源不同；网页能打开但Admin没有样式，常见原因就是尚未collectstatic或静态映射错误。

## 4. 待确认后的首次启动步骤

以下在**PowerShell、`C:\project-one-ecommerce`项目根目录**执行。每一步通过后再做下一步；失败先看原因，不跳过失败继续启动。

### 第一步：准备独立环境文件

从模板复制，已有文件就保留：

```powershell
Set-Location C:\project-one-ecommerce
$deploymentEnv = Join-Path (Get-Location) 'docker/deployment/.env'
if (Test-Path -LiteralPath $deploymentEnv) {
    Write-Host '独立环境文件已存在，请检查后继续，未覆盖。'
} else {
    Copy-Item -LiteralPath 'docker/deployment/.env.example' -Destination $deploymentEnv
}
notepad $deploymentEnv
```

在本机编辑器填入独立的`DEPLOY_DJANGO_SECRET_KEY`、`DEPLOY_MYSQL_PASSWORD`、`DEPLOY_MYSQL_ROOT_PASSWORD`。模板故意留空，不能照着空值启动；不要复用旧数据库密码，不把真实值贴进聊天或提交Git。该文件与根目录`.env`不同。

### 第二步：只验证Compose配置

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local config --quiet
```

目的：检查语法、变量与服务关系。预期无输出、正常结束；`--quiet`避免打印展开后的配置与秘密。缺少变量时回到独立`.env`填写，不能改根环境文件绕过。

### 第三步：构建后端和前端入口镜像

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local build web nginx
```

目的：安装锁定依赖、打包Django、构建Vue并生成Nginx镜像。预期构建成功；Docker未运行、镜像下载失败和依赖安装失败是不同问题，构建失败还不能算应用运行错误。

### 第四步：只启动独立数据库

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local up -d --wait db
```

目的：先让数据库完成初始化与健康检查。预期等待成功；首次初始化较慢不等于坏了。若健康检查失败，先核对密码与数据库日志，不直接删除数据卷重新试。

### 第五步：建立这套新库的表结构

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local run --rm web python manage.py migrate --noinput
```

目的：用一次性后端容器执行迁移，目标为独立部署库。预期迁移正常结束；连接拒绝先检查db健康及`db:3306`，认证失败检查独立密码。`--rm`移除一次性容器，不删除数据库卷。

### 第六步：准备Django静态文件

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local run --rm web python manage.py collectstatic --noinput
```

目的：把Admin静态资源写入共享卷。预期完成收集；文件或卷权限失败要修定位原因，不能仅凭后端能启动就忽略。

### 第七步：启动后端和统一入口

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local up -d --wait web nginx
```

目的：运行Gunicorn与Nginx。预期服务健康；8080被占用会导致入口启动失败，应先确认占用者，不随意停掉其他项目。502通常表示Nginx未能连接到后端，需检查web状态。

### 第八步：分别检查服务、API和网页

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local ps
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/health/'
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/catalog/products/'
(Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8080/products').StatusCode
(Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8080/admin/login/').StatusCode
(Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8080/static/admin/css/base.css').StatusCode
```

预期容器状态正常、健康接口返回JSON、商品列表为`count=0`、`next=null`、`previous=null`、`results=[]`，后三项HTTP检查返回200。新库没商品是正常起点，不说明搜索或下单闭环已通过。

再在浏览器打开`http://127.0.0.1:8080/products`并刷新，确认Vue仍能显示空列表；打开`/admin/login/`确认登录页和样式加载。HTML200不等于页面已正确运行，Admin登录页200也不等于Session登录验收通过。

首次实际登录需要另行确认合成账号准备；本轮未创建账号或加载商品。若需看失败日志，只查看对应服务，避免把环境秘密或真实个人数据粘到聊天。

## 5. 停机与更新怎样保护数据

暂停这套部署时：

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local down
```

它停止并移除本片容器/网络，保留命名数据卷。下次启动仍使用原部署数据。默认不加`--volumes`；删卷等于删除持久数据，需要另行明确授权。

首次验收完成后，未来更新可以按以下顺序规划，不能把首次启动确认当成以后真实数据迁移的永久授权：

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local build web nginx
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local run --rm web python manage.py migrate --noinput
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local run --rm web python manage.py collectstatic --noinput
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local up -d --wait --force-recreate web nginx
```

新镜像先构建，迁移与静态收集成功后再换运行容器；重建Nginx也避免继续使用旧web地址的解析结果。`restart`只重启原容器，不等于应用新镜像。本序列没有保证更新期间可用性或旧代码/新结构兼容。

执行有数据迁移前应先明确备份与恢复办法。迁移回退只撤销指定结构变更，不等于恢复已经删除或修改的业务数据；重建容器也不是数据库备份。

## 6. 本片怎样验收，下一步在哪里停止

本轮证据只能支持“候选配置已经过静态核对”，具体项目见[验证记录](../verification/phase-9a-local-deployment.md)。尚未执行的镜像构建、容器健康、迁移、HTTP、浏览器路由和Admin静态检查不能提前写成通过。

本轮还实际通过了宿主前端类型检查和构建，以及部署依赖dry-run；它们不替代Linux镜像构建。Node24与Nginx stable基础镜像标签可移动，首次构建时记录实际版本/镜像指纹，不能称所有系统依赖已经完全锁定。

首次实际运行的最小闭环是：独立镜像构建 → 独立MySQL健康 → 迁移/静态收集 → 页面与公开API经同一入口访问。每一步保存结果与失败原因；证据足够说明部署路径就收片，不顺便开启所有中间件。

下一步先在确认后完成上述首次运行；需要账号与合成商品时另明确数据范围，再验证Session/CSRF及一条原业务流程。缓存、Lua、MQ、ES接入、公网HTTPS、备份恢复和更新演练均按独立边界推进，本机HTTP不等于项目全部部署收尾。
