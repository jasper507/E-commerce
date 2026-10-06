# Project One：支付与 MQ Wiki 创建材料

已于2026-10-06创建私有 **Project One 电商项目** 飞书 Wiki，八页正文和六张原生可编辑画板均已写入并读回核对。[打开支付与 MQ 总览](https://ocnbkcx1ug58.feishu.cn/wiki/PCQ7wP9kMikz0eklmNYcH1J1nwh)。

随后按项目拥有者要求整理到[电商项目总目录](https://ocnbkcx1ug58.feishu.cn/wiki/WN1jwXDj0iPXm7kwLyHcmneenlb)，支付与原 Redis 文档并列归档，详见[层级整理记录](./hierarchy/record.md)。

[查看八页正文与六张图的完整预览](./review.md)，或查看[实际发布记录](./publication-record.md)。PNG 仅用于本地查看；远端已确认包含240个原生节点。

## 页面层级

```text
Project One 电商项目（Wiki 空间）
└─ 电商项目
   ├─ 支付
   │  ├─ 01 数据模型与两个状态机
   │  ├─ 02 正常付款与事务提交
   │  ├─ 03 超时、UNKNOWN与原付款查单
   │  ├─ 04 重复、乱序与业务拒绝
   │  ├─ 05 MQ、有界重试与人工恢复
   │  ├─ 06 支付与到期关闭、迟到模拟退款
   │  └─ 07 实验入口与面试证据
   └─ Redis 学习与面试总结
```

沿用现有 Redis 资料的“业务问题 → 流程 → 方案 → 关键源码 → 证据 → 限制 → 面试”结构。[Redis 学习与面试总结](https://ocnbkcx1ug58.feishu.cn/wiki/O4iUwSvl1i4Je7keOL0ci1iAnLf)已将原文迁入总目录，保留原 Docx 编号、正文和四张画板；秒杀与并发后续作为独立模块，产生订单后复用支付链路。

## 六张新图

| 页面 | 图 | 原生来源 |
| --- | --- | --- |
| 总览 | [支付学习地图](./diagrams/knowledge-map/diagram.png) | Mermaid 思维导图 |
| 01 | [189元订单的数据关系](./diagrams/data-model/diagram.png) | SVG 图形与文字节点 |
| 02 | [正常付款与事务提交](./diagrams/normal-payment/diagram.png) | Mermaid 时序图 |
| 03 | [超时与查询原付款](./diagrams/timeout-query/diagram.png) | Mermaid 时序图 |
| 05 | [MQ故障与人工恢复](./diagrams/mq-recovery/diagram.png) | Mermaid 时序图 |
| 06 | [付款与到期竞争](./diagrams/payment-expiration/diagram.png) | Mermaid 时序图 |

已使用 `@larksuite/whiteboard-cli@0.2.13` 渲染，并逐张查看预览。六图无静态检查错误；五图无警告。思维导图节点使用自动布局坐标，静态检查产生528条重叠警告；本地渲染与发布后的飞书实际预览均已查看，布局清晰。检查结果各存于 `diagrams/<图名>/check-result.json`，飞书原生节点和实际预览存于 `remote/board-XX-raw.json` 与 `remote/board-XX-preview.jpg`。

## 检查与证据边界

- 八份 XML 通过 `lark-cli docs +script --command parse` 的 Draft Profile Check。
- 五段关键代码逐字匹配当前工作区，行号与 SHA256 保存在 `pages/source-excerpts.json`。
- 正文引用已有真实 MySQL/RabbitMQ/Worker 实验；本次没有运行应用测试或操作数据库。
- 修正了历史证据措辞：两种锁顺序乘三个状态共六场景；C4首次退款准备被拒绝的是未确认的 CREATED。
- 没有修改业务代码、迁移、前端、依赖或部署配置；保留已有未提交增量，没有 commit/push。

`pages/build_drafts.py` 生成本地创作草稿；各草稿目录中的 `publish.xml` 是实际发布候选，含真实 Wiki 导航。`build_preview.py` 只用于发布前生成审阅版和计划；检测到已创建资源时会停止，防止清空发布记录。两者均不调用远端 API。

## 实际发布与后续维护

1. 用户 OAuth 完成，使用 `--as user` 创建私有空间及八个节点，归属和父子层级已核对。
2. 节点快捷命令额外要求 `wiki:space:read`；已根据 schema 使用只需现有 `wiki:node:create` 的原生节点创建接口完成，没有扩展授权或切换身份。
3. 八份带真实导航的发布候选通过 Profile Check，正文写入全部返回 success、warnings=[]。
4. 六组 CLI 转换后的原生节点以固定幂等标识写入空画板，共240个节点，没有覆盖已有资料。
5. 八页正文、标题、表格、链接、五段代码与六个画板 token 读回一致；七个子页正确，六张实际预览逐张查看。

后续修改按同一节点局部更新，先读取当前状态，保留已有画板与导航，再读回确认。创建/写入/读回结果保存在 `remote/`；完成状态与文件指纹见 `publication-plan.json`。OAuth device code、Token、密钥和二维码不放入 Git 项目。
