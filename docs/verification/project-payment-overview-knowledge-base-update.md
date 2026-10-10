# 项目与支付总览知识库更新核对 — 2026-10-11

[项目总览指南](../learning/project-current-overview-study-guide.md) · [支付当前指南](../learning/payment-current-overview-study-guide.md)。本轮属于Supporting/Delivery文档维护，用户授权更新既有项目总览与支付总览，采用业务流程、设计理由/替代、少量核心代码、证据/代价和每篇重做选择，并显式要求图。

## 范围与来源

源码基点为`4673b9f8ea95664ce4ab886e1a9e791e6ffbd42e`，Project Charter v1.15；开始时main与远端相同，已有Phase6E指南用户改动保留。只更新两份既有Wiki入口，不删除/移动已有节点、不覆盖原生画板、不修改业务实现或新建其余栏目。

支付只读审计核对Attempt提交后网关调用、Inbox T0、领取T1、业务T2、异常T3、显式对象锁顺序、资格生命周期及取消/迟到分支。当前恢复命令已经存在；周期扫描只在重做节作为未采用的条件候选，不能冒称新实现。历史证据引用5C2/3/4、5D；项目总览引用缓存、秒杀、Lua、搜索与部署已有记录。

## 本轮检查

- 三张Mermaid图：交易闭环、默认同步支付、可选MQ故障恢复。仅用于解释当前机制，不是新故障实验。
- 已渲染本地图并逐图视觉检查；MQ图由独立子代理绘制并检查，保留源码与PNG；支付同步图发布前明确查询仍未知时不进入成功提交。
- 本地指南与XML候选、原页面读回、发布结果和归档检查位于`docs/learning/kb-overview-update-2026-10-11/`；最终记录以实际远端读回补充。

没有运行pytest、构建、数据库迁移、Redis/MQ/ES实验或部署验收。原始性能数字与故障结果来自历史记录；文档存在和图正确不等于拥有者已独立掌握。

## 实际归档结果

- [项目入口](https://ocnbkcx1ug58.feishu.cn/wiki/WN1jwXDj0iPXm7kwLyHcmneenlb)正文revision7；[支付入口](https://ocnbkcx1ug58.feishu.cn/wiki/PCQ7wP9kMikz0eklmNYcH1J1nwh)正文revision9。两个原节点及URL继续复用，未新建其他Wiki栏目。
- 新增三张原生可编辑画板：交易闭环`SDuuwOD4dhwiy6bzOgocuEuPn2b`、默认同步付款`Cee6wMjj5hbDqRb2AHVc1uqunbh`、可选MQ`VuDgwuZZNh0gYCb4vRCc5haDnph`。写前均确认为空，固定幂等键增量写入，没有overwrite；读回分别25/24/31节点。
- 三张实际远端预览均导出并查看，参与者、提交点、重要分支及文字完整。服务端改变排版、节点顺序及分支标题存储位置，已按可见文字核对；没有把原有学习地图重绘。原支付地图token`EALVw0LZphx322b6q93ctTDxnne`保留。
- 首次项目正文直接插入Mermaid返回partial_success与2107，缺少新画板；立即读回证明文字、代码文字和链接已完整写入，没有盲目重试正文。改为新空板加OpenAPI节点写入，最终实际验证完整。支付两张图随后采用同一路径，没有空占位遗留；不能把首次partial_success当完整成功。
- 独立审查发现交易图曾把管理员发货放在顾客箭头上，发布画板前修正为管理员→PAID/Shipment/SHIPPED→本人收货；支付UNKNOWN增加“尚未确认才转换，终态不倒退”限定，并重新渲染查看。
- 两份正文新增部分的文字、代码文字、链接与候选一致；五处旧块替换逐项检查只改一块且不改变原链接，其余材料保留。项目4个子节点和支付7个子节点前后完全相同。
- 本地109个相对链接存在，三处源码摘录与4673b9f逐行去缩进比对一致；两个DocxXML候选profile为passed，XML可解析，`git diff --check`通过。保存日志前去除非必要账号open_id，敏感凭据模式检查未匹配。

新增两份当前总览指南、三组本地与远端图资产、候选及归档记录；学习导航、旧支付详版入口和写作规范增加最小衔接说明。原Phase6E用户改动及并行Phase9C部署文档、脚本与证据不纳入本轮提交；总览来源仍明确截止4673b9f，不把未纳入的并行材料当本轮实测。

最终机器核对见候选目录`local-checks.json`、`publication-checks.json`及`publication-manifest.json`。本轮收束于两个入口的可读、可追溯归档，不扩大到其他模块重写、自动恢复实现或新的业务实验。
