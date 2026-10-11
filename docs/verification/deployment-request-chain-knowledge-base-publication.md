# 部署前后变化与完整请求链：知识库核验与发布记录

2026-10-11。Supporting / Delivery，文档归档。固定源码`57329d80fd6afd13f78b2f6e2ea45f61a898ea6b`。[学习指南](../learning/deployment-request-chain-study-guide.md)反向链接本记录。

拥有者先讨论后明确要求按已确认内容创建：只做一篇和一张完整时序图，以请求顺序讲部署前后变化、部署准备、页面/资源/API及返回、深路由、Admin与证据边界；明确省略替代方案、“如果重新做”、题库和参考答案。这覆盖通用知识库模板在本篇的相关要求。

本轮只读源码、配置和历史验证，创建本地资料并写入已有电商项目Wiki。没有获准或执行新的部署、容器/数据库/缓存操作、HTTP登录或交易测试。

## 历史证据来源

[9A准备阶段](phase-9a-local-deployment.md)、[9B首次本机运行](phase-9b-local-deployment-run.md)和[9C认证HTTP](phase-9c-session-csrf-operations.md)保留原日期与范围。9B的36条迁移、154个静态文件、14项HTTP及浏览器空目录/深路由是历史实测；9C15项使用HTTP客户端，不是浏览器登录UI或15种攻击。公网HTTPS、整条部署交易、中间件启用与更新/备份/恢复演练没有因此完成。

## 本轮执行

目标为实时读取确认的电商项目空间`7693476782908935142`、根节点`WN1jwXDj0iPXm7kwLyHcmneenlb`。只新建直接子页[部署与完整请求链](https://ocnbkcx1ug58.feishu.cn/wiki/LlbLwU9mSi2CoIkYi5tcELbKn4e)，没有创建空父栏目或问题子页；文档对象为`LJWQdWwpConHjpxumZhcNwwCnxg`。

| 实际核验 | 观察结果 | 可支持的结论 |
| --- | --- | --- |
| 本地正文与源码 | 1篇指南；15条正文相对链接存在；连同导航和记录共核对159个本地链接；Vite代理、Nginx API代理与SPA回退3段配置逐字匹配固定源码 | 正文机制与该源码版本相符，不证明运行状态 |
| CLI草稿 | 实际调用`init-draft`；创建前与填写前两次解析均`assessment.status=passed` | 内容结构通过CLI评估，不替代事实审查 |
| 飞书正文 | 写入`result=success`且无warnings；完整回读10个标题、27个链接、3段配置、一张画板，语义及顺序一致；文档revision2 | 该新页面实际可读，非仅创建链接或空文档 |
| 新画板 | 写前为空；固定键`deployment-request-chain-20261011-publish-1`写1次；生成与远端各60节点，类型、文字和40条连接的方向/端点一致 | 实际原生节点与本地设计相符 |
| 图的预览 | 本地PNG3503×9466、实际远端JPEG2560×2560均已查看；完整顺序及分支可见 | 图需竖向阅读与在原生画板中放大；不是网页运行测试 |
| 项目导航 | 只替换“阅读顺序是”段落；根文档revision14→15；其他语义、原标题、旧导航链接与原画板资源标识保留 | 既有项目资料未被整篇覆盖 |
| 知识库层级 | 根直接子节点7→8；原七节点标题、对象与父节点一致；新页子节点0 | 本轮只新增一个部署叶节点 |

画板token为`BuMWwID03hyZizbPhUqc7i4Dnog`。画板中5个参与者区分浏览器、Nginx、Gunicorn、Django、MySQL；明确Gunicorn与Django是同进程WSGI调用，JS/CSS和分类请求可并行，公开商品GET无需登录或CSRF Header。空商品结果明确标为9B历史示例，并补深路由、Admin HTML/静态卷与未知API/缺失文件404分支。

正文和初稿图经过独立只读事实审查；两处9B空目录标注修订后，由主代理查看最终本地图并执行发布、回读和远端预览检查。辅助审查后续被用量限制中断，未将中断任务算作新增通过检查。正文修正了UID10001仅指web/Gunicorn，以及CSRF写请求说明仅适用于相应Session浏览器入口。

CLI1.0.95、whiteboard-cli0.2.13；使用已配置PowerShell7.6.5。本地原生转换几何检查0errors、0warnings；原生画板source导出如实返回“no code blocks found in whiteboard”，可编辑[Mermaid源码](../learning/assets/deployment-request-chain-2026-10-11/request-sequence/diagram.mmd)保留。

一次连续脚本调用因脚本切换工作目录导致后续相对路径未找到，填写命令未执行；核对后使用绝对路径，继续填写已创建的唯一节点，没有重复创建。全部实际响应、完整回读、预览、节点和[最终检查](../learning/deployment-knowledge-base-2026-10-11/final-checks.json)保存在[发布材料](../learning/deployment-knowledge-base-2026-10-11/README.md)。保存前解析JSON并检查凭据/个人身份字段，没有读取真实.env。

本轮未运行pytest、构建、迁移、HTTP业务验收或服务启停。最终检查只验证文档、链接、源码摘录、画板与层级；9A/B/C继续作为历史运行记录。

## 保护范围与停止条件

只新增本篇、图和发布核验材料，项目与学习导航做最小入口更新。原七个模块与资源保留，既有6E指南修改不纳入本轮提交。业务、配置、开关、依赖、真实数据及原9A/B/C证据不修改；不读取或输出真实.env值。

达到“前后变化清楚、运行准备与请求阶段分开、静态与API路径准确、图文一致且实际远端可读、证据不过界”即收束。文字或图的检查不能宣称为本轮应用验收。
