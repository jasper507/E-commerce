# 认证、CSRF与数据权限：发布材料

2026-10-11，文档归档；源码固定`a5d4b04995552a340a471d31e68973e89f67e54c`。本轮没有业务测试、账号/Session写入或服务操作。

飞书[专题总览](https://ocnbkcx1ug58.feishu.cn/wiki/TS8SwlKxKi8gJ5kIjlScjyyFn3d)下有四个问题页：[Session](https://ocnbkcx1ug58.feishu.cn/wiki/ClJNwFzuDi4Fpskvrioc3WRpnVb)、[CSRF](https://ocnbkcx1ug58.feishu.cn/wiki/CGqPwW2tyirrVCkXOTZcr4fknho)、[本人资源](https://ocnbkcx1ug58.feishu.cn/wiki/PuHtwhyymi0GiLkdAMacUiRYnKd)、[角色与网关](https://ocnbkcx1ug58.feishu.cn/wiki/NRfSw08LXiNKW5ksGyKcldlynGc)。原项目根节点与其他模块保留。

本地阅读[认证总览](../auth-current-overview-study-guide.md)及[四页正文目录](../auth-interview/)；实际检查与边界见[验证记录](../../verification/auth-interview-knowledge-base-publication.md)。

- `project-target.json`、`project-before*.json`记录实时确认的项目身份、目录及正文基线。
- `pages.json`和`draft-workspaces.json`记录五份CLI初始化决策与草稿身份；原始草稿保留在忽略的`draft_*_folder/`中。
- `candidates/`保留正文与导航XML；`local-checks.json`核对8段核心代码、固定源码行号与本地链接。
- `created.json`记录唯一创建的总览与四个子页；`*-latest.json`与`*-verified.json`记录五页完整回读。
- `journal/`保留输入评估、创建、写入、读取和导出响应。格式评估看`assessment.status=passed`；写入要求`result=success`且无warnings。
- [两图资产](../assets/auth-interview-2026-10-11/)保留Mermaid、PNG、生成原生节点、实际远端节点/预览及核验结果。远端图无代码块，本地Mermaid可编辑。
- `root_navigation.py`只替换项目阅读段落，保留旧导航链接、其他正文及全部原资源标识。
- `final-checks.json`汇总实际层级、全文语义、8段代码和64个原生节点检查。

实际顺序：读取项目目标 → 五份草稿初始化 → 生成与评估XML → 创建五个唯一节点 → 重新生成Wiki互链并再评估 → 填写并逐页回读 → 核对两张新板为空，各写入1次 → 更新一个根导航段落 → 导出两图并查看实际预览 → 最终五页与目录回读和静态核验。

`run-publication.ps1 -Mode verify`只读取飞书并更新本地证据。`parse`只评估本地草稿，`export`只读取远端画板；`create/fill/boards/root`会写入远端，本轮已经执行，不应在复习时重复运行。脚本保留已创建身份、空正文/空板检查和固定画板写入键，失败先核对实际目标，不能盲目追加。

CLI运行使用已配置PowerShell7.6.5；初始化的PS5 JSON引号失败、本地导出路径与空板无文件的处理已记录在验证页。没有为这些辅助问题安装依赖、改变认证或新建第二套页面。
