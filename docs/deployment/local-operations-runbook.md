# 独立本机部署：更新、停机与备份恢复参考

2026-10-11。[9C学习指南](../learning/phase-9c-session-csrf-operations-study-guide.md) · [9C验收记录](../verification/phase-9c-session-csrf-operations.md) · [9A完整启动步骤](../learning/phase-9a-local-deployment-study-guide.md) · [9B运行证据](../verification/phase-9b-local-deployment-run.md)。

**本手册是操作参考，以下更新、停机/恢复、备份与恢复尚未实际演练。执行影响需另行确认；读过手册不等于已有恢复能力。** 命令按PowerShell 7的现代原生参数传递方式编写，在`C:\project-one-ecommerce`执行；Windows PowerShell 5.1的嵌套引号兼容性未验证。

## 最小方案与边界

当前只维护`compose.deployment-local.yaml`、独立环境文件和`project-one-deployment-local`项目；web/nginx/db、命名卷与本机8080入口保持原设计。停机保留卷，更新先构建，再停写、备份和迁移，最后重建入口；恢复先另建空卷演练，不直接覆盖现库。

这能让你解释部署故障怎样定位、代码回退为何不等于数据恢复。代价是短暂停机、额外备份空间、版本与迁移状态记录，以及一次隔离恢复验证；本片不引入Kubernetes、CI或公网HTTPS实施。

不合并原开发Compose，不修改根`.env`，不删除任何已有卷。配置校验用`config --quiet`，不打印展开后的秘密；日志和SQL备份可能含数据，保存在Git外，不粘贴真实值。

## 1. 检查目标与定位失败

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local config --quiet
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local ps
```

目的：确认独立配置可解析、当前服务状态。成功判据：校验退出码0、目标服务属于本项目；失败则先修独立变量或Docker状态，不开始迁移。不要通过输出完整环境定位密码。

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local logs --tail 80 web nginx
```

目的：定位web启动或Nginx502。成功判据是找到对应错误，不是日志没有输出；排查时核对`db:3306`、服务健康与Host端口。只分享去除秘密和个人数据后的必要片段。

## 2. 正常停机与再次启动

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local down
```

目的：停止本项目容器与网络。成功判据：本项目运行容器停止，命名卷保留；失败先检查残留服务，禁止追加`--volumes`解决。保留卷的设计不等于已经实测重启后的数据持久性。

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local up -d --wait db
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local up -d --wait --force-recreate web nginx
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/health/'
```

目的：先恢复数据库，再重新建立后端与入口，避免Nginx沿用旧web地址解析结果。成功判据：等待成功、health数据库ok，再核对预先记录的数据；失败保留卷并查看对应日志，不能把空库初始化当成数据恢复。

## 3. 最小更新流程

先记录旧代码提交、web/nginx镜像ID及迁移状态，保留旧镜像，不做镜像清理。更新涉及现有数据时，先确认备份、恢复目标与可接受停机时间。

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local build web nginx
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local stop web nginx
```

目的：在旧服务运行时先完成新镜像构建，再停止业务写入。成功判据：构建退出码0、web/nginx停止而db保留；构建失败不停止旧服务，停机失败则不迁移。此后按第4节备份，并确认没有其他操作者执行写入或DDL。

```powershell
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local run --rm --no-deps web python manage.py migrate --noinput
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local run --rm --no-deps web python manage.py collectstatic --noinput
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local up -d --wait --force-recreate web nginx
```

目的依次是更新表结构、收集静态资源、应用新镜像。每一步退出码0才继续；迁移或收集失败保持业务停止，先判断数据库和共享静态卷已改变到哪一步，不盲目启动旧镜像。

成功判据还包括health、商品读取、Admin样式与相关登录/业务检查。镜像回退不会恢复数据；迁移回退也可能丢数据或不可逆。`collectstatic`写共享卷，静态文件可能不兼容旧应用；本方案不保证零停机或自动回滚。

## 4. MySQL逻辑备份：容器写文件，再复制到Git外

先停止web/nginx，并确认没有其它写入、迁移或DDL。`--single-transaction`提供InnoDB读取快照，但不保护并行表结构变化或所有非事务表；业务停写让本项目最小备份边界更清楚。

备份使用容器已有root环境变量，通过`MYSQL_PWD`传入客户端，不把密码放命令参数。SQL文件先在容器内生成，避免PowerShell把mysqldump标准输出重定向时改变编码：

```powershell
$backupDirectory = 'C:\Users\youyu\Documents\ProjectOneBackups'
New-Item -ItemType Directory -Force -Path $backupDirectory | Out-Null
$backupStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupFile = Join-Path $backupDirectory ('deployment-' + $backupStamp + '.sql')
if (Test-Path -LiteralPath $backupFile) { throw '备份文件已存在，停止以免覆盖。' }
$dumpCommand = 'umask 077; dump="$(mktemp /tmp/project-one-dump.XXXXXX.sql)" || exit 1; MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump --user=root --single-transaction --routines --triggers --events --no-tablespaces --set-gtid-purged=OFF --result-file="$dump" "$MYSQL_DATABASE" || exit 1; printf "%s\n" "$dump"'
$containerDump = docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local exec -T db sh -c $dumpCommand
if ($LASTEXITCODE -ne 0) { throw '数据库备份失败，不复制不完整文件。' }
$containerDump = ($containerDump | Select-Object -Last 1).Trim()
if (-not $containerDump.StartsWith('/tmp/project-one-dump.')) { throw '临时备份路径异常，停止。' }
docker compose --env-file docker/deployment/.env -f compose.deployment-local.yaml -p project-one-deployment-local cp ('db:' + $containerDump) $backupFile
if ($LASTEXITCODE -ne 0) { throw '备份复制失败，保持原库，检查磁盘与路径。' }
Get-Item -LiteralPath $backupFile | Select-Object Name, Length
Get-FileHash -LiteralPath $backupFile -Algorithm SHA256
```

目的：得到Git外的字节保持不变的SQL备份并记录校验值。成功判据：dump/cp退出码0、文件非空、SHA256已记录；哈希只能辅助检查文件变化，不能证明备份可恢复。任何一步失败先处理空间、权限或数据库错误，不删除源卷或把失败文件当作可用备份。

备份同时记录源数据库名、时间、MySQL版本、代码/迁移版本和重要表行数，保存在同目录说明文件；原始SQL含账号和业务数据，限制访问，不提交Git。临时文件仍留在容器中，按后续明确清理范围处理，不在失败时自动删候选备份。

恢复能力要靠第5节独立导入验证。本片未验证binlog开启、保留或重放，因此不承诺PITR（按时间点恢复），逻辑备份之外产生的更新不自动包含在内。

## 5. 恢复只在另一个获准的空卷中验证

**以下恢复文件尚不存在，命令不是现在可直接执行的覆盖入口。** 先另行批准并准备`compose.recovery-local.yaml`、`docker/recovery/.env`、`project-one-recovery-local`项目及独立空卷。它不得引用现有mysql_data卷，不映射生产/开发数据库端口，不启动业务写入；凭据独立且不打印。

批准准备完成后，先启动并检查恢复目标：

```powershell
docker compose --env-file docker/recovery/.env -f compose.recovery-local.yaml -p project-one-recovery-local config --quiet
docker compose --env-file docker/recovery/.env -f compose.recovery-local.yaml -p project-one-recovery-local up -d --wait db
docker compose --env-file docker/recovery/.env -f compose.recovery-local.yaml -p project-one-recovery-local exec -T db sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --user=root "$MYSQL_DATABASE" --execute="SELECT DATABASE(); SELECT COUNT(*) AS table_count FROM information_schema.tables WHERE table_schema=DATABASE();"'
```

目的：核对批准的新库和实际空目标。成功判据：配置/健康通过，实际库身份与批准目标一致、table_count为0；不是0或卷来源不明立即停止。不能通过修改连接NAME来假装身份正确，也不先migrate创建表后再称“空库”。

仅在目标核对通过、`$backupFile`已指向批准备份且校验值一致后导入：

```powershell
docker compose --env-file docker/recovery/.env -f compose.recovery-local.yaml -p project-one-recovery-local cp $backupFile db:/tmp/approved-restore.sql
if ($LASTEXITCODE -ne 0) { throw '复制失败，不执行导入。' }
docker compose --env-file docker/recovery/.env -f compose.recovery-local.yaml -p project-one-recovery-local exec -T db sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --user=root "$MYSQL_DATABASE" < /tmp/approved-restore.sql'
if ($LASTEXITCODE -ne 0) { throw '导入失败，保留原库，检查恢复库中的部分结果。' }
```

目的：在另一空卷恢复逻辑数据。成功判据不止导入退出码0，还需核对表结构、迁移状态、关键表行数和字段、账号登录与最小业务读取；当前未演练，不能声称通过。导入失败可能留下部分表，禁止向原库重试或自动清空任何卷。

恢复验证成功后是否切换业务、如何处理备份之后新增数据，属于新的确认与切换计划。本手册没有开发库覆盖、自动清卷或现库原地恢复命令。

## 收束

当前实际运行证据见9B与9C记录；本手册新增的是可审阅流程，不是演练结果。下一次只选择一次获准的停机/恢复或独立备份/恢复演练，保存身份、数据前后值与失败边界；足够解释过程后停止，不自动扩为公网、集群或持续部署。
