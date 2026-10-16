# 订单列表 Locust 工具候选验证 2026-10-16

配套[学习指南](../learning/order-list-load-study-guide.md)。用户在简历准备期间明确请求新增压测、合成大数据和负载拐点观察，并选择订单列表。本次授权覆盖工具实现；实际启动服务、写专用测试库和加压仍须确认具体方案。本片扩展的是此前收尾范围之外的独立辅助工具，不改三条业务深度主线。

## 范围与版本

- 开始时main及远端均为926814b4cc78289875b8394002cad26736d0b93f，只存在原有Phase6E学习指南用户修改；始终保留，不纳入本次提交。
- 在codex/order-list-locust候选分支实现，尚未进行真实MySQL/HTTP容量验证，不合入main。
- 新增独立Compose/镜像、严格目标配置、分批造数、真实Session/CSRF的Locust场景、阶段控制与离线分析。依赖Locust2.46.7只加到requirements/load.txt及独立工具环境，原开发venv和base/dev依赖不变。
- 普通运行不写数据库；CLI显示计划，只有明确--execute才能准备目标或发起负载。项目名显式固定，不接受宿主COMPOSE_PROJECT_NAME或ORDER_LOAD_RUN劫持；拒绝远端Docker上下文。没有自动删库/清卷命令。
- 实际准备要求相关源码已提交，保存执行时Git版本和本轮独有镜像ID；运行前核对目标/负载镜像，避免计划创建时的旧HEAD或后续覆盖的镜像冒充实际来源。

## 本轮实际检查

1. 源码AST解析、CLI帮助与计划生成成功；创建了忽略提交的review10k-1016计划：10,000订单、20账号、每人500单、首页、1/5/10/20用户、10秒暖机/30秒测量。只生成文件，未触库。
2. 项目venv运行python -m unittest discover -s tests/offline -p 'test_order_load_*.py' -v：初版43项通过、1项因该环境未安装Locust跳过；覆盖类型/NaN/目标限制、排序ties/深页预计算、跨账号或错页拒绝、晚完成样本、拐点筛查、Compose归属、缺失/OOM目标检测及正常客户端结束不误判。[离线检查](order-list-load-offline-output.txt)保留最近一次完整输出，包含下述10月17日补测。
3. 忽略提交的独立artifacts/order-load/tool-venv安装Locust2.46.7成功；未向原项目venv安装。
4. 工具venv运行python -m unittest discover -s tests/offline -p test_order_load_runtime.py -v：1个流程测试通过（包含正常与错误响应两个场景），见[原始输出](order-list-load-runtime-output.txt)。真实Locust子进程仅访问自建127.0.0.1:18080假服务；正常验证独立Cookie登录、至少100测量样本与成功退出，错误商品归属触发停止；故意设置不可用代理仍成功，原始请求数据不含测试密码/令牌。假服务停止并释放端口。
5. docker compose带明确project-name、专用env-file和compose.order-load.yaml的config --quiet通过。只解析Compose，没有构建、拉镜像、启动容器或创建卷。
6. 独立静态审查发现并修正了项目名覆盖、已退出目标漏监控、代理继承、drain晚错误接受等问题；补充拒绝Docker HOST/CONTEXT冲突并处理负载端正常退出的采样竞态。Dockerfile专用ignore仅放行源码与所需requirements，根.env与artifacts不进入构建上下文；Linux bind使用宿主非root UID/GID。

本机Python3.11.9。Docker Desktop Linux Engine管道不可用，未擅自启动；读取Windows可用物理内存时约1GiB，低于工具要求。没有访问现有MySQL、Redis、MQ或ES，也没有执行原默认pytest/迁移/支付/秒杀负载。

## 统计口径与保护

JSONL保留稳态发起请求，延迟和失败包含有界drain后的慢完成；RPS只计该cohort在窗口内的完成。每档独立登录/暖机和样本集，不平均p95。压测端和目标资源不完整、样本不足、响应错误、容器异常或客户端饱和均不能形成有效容量结论。

每批500订单、500地址、1000明细；先核对配置和实际DATABASE()/CURRENT_USER()，只允许独立库/用户/主机/端口。已完成或部分失败的命名空间拒绝重用。without_read_index是显式可选基线，只允许在该库删除单条命名普通索引；实际DDL尚未执行。

拐点规则只输出候选区间，报告明确capacity_confirmed=false、database_bottleneck_confirmed=false。最多300万订单/200用户是输入限额，不是实测能力。资源门槛及磁盘预留是执行保护政策，不是数据体积测量。

## 未验证和下一步

未构建镜像，因此Linux依赖组合、首次MySQL迁移、百万批量写入、bind权限、真实HTTP响应、容器资源采集及实际拐点仍未验证。不得把自测假HTTP结果写成项目压测成绩。

下一步须用户确认：独立本机Docker命名空间，默认10,000合成订单及关联快照，1/5/10/20用户，预设延迟/失败/资源停止条件。先腾出至少5GiB可用内存并启动Docker，再做真实小规模验证。未经结果和额外规模确认，不自动生成百万订单或持续加压。

## 2026-10-17 执行授权与资源策略修正

用户明确授权关闭不需要的应用和旧项目服务，并启动已说明的1万订单首轮。已关闭Chrome、Claude、微信、抖音、Phone Link、OneDrive同步客户端及任务管理器，保留当前对话、Docker和系统服务；旧部署和后来出现的四个旧测试容器均已停止，未删除数据卷。

Docker重启时先后遇到Secrets Engine与Ingest的孤立AF_UNIX通信文件访问错误。核对目录只含零字节运行时端点后，将Local/docker-secrets-engine及Local/Docker/run分别改名保留为stale-20261017-002914、run.stale-20261017-003034；没有工厂重置或删除卷。随后docker info成功，无运行容器。相同故障的[官方仓库一手问题记录](https://github.com/docker/for-win/issues/15064)及[Ingest记录](https://github.com/docker/desktop-feedback/issues/531)只作为处理线索，本机成功以实际命令为准。

原5GiB空闲门槛是工具保守政策，不是实际内存测量；Docker恢复后Windows空闲约3.6GiB，WSL约1.2GiB文件缓存。仅对至多1万订单、20用户、30秒测量的小规模短测，将启动门槛设为3GiB；其它计划仍5GiB，Docker总内存要求不变。每次阶段资源采样保存宿主可用内存，低于1GiB即停止并使结果失效，避免通过关闭保护强行加压。首次运行前仅释放一次空闲Linux文件缓存，不在测量期间清缓存。

本次修正后AST解析及离线检查45项通过、1项Locust流程测试在原venv跳过。新增覆盖小规模与大规模门槛、宿主低内存使客户端自报成功的阶段仍失效、停止负载并禁止升档。历史独立工具venv的Locust流程检查未重跑，负载脚本未变。真实环境准备与结果另行记录。
