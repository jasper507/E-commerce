# 支付与 MQ Wiki 实际发布记录

2026-10-06 已创建私有 **Project One 电商项目** 知识空间，使用当前用户身份写入支付模块。[打开支付与 MQ 总览](https://ocnbkcx1ug58.feishu.cn/wiki/PCQ7wP9kMikz0eklmNYcH1J1nwh)。

## 页面与层级

总览下有七个子页，实际列表返回7项、`has_more=false`。标题、父节点和空间编号均已核对。

| 页面 | 实际 Wiki 链接 |
| --- | --- |
| 总览与业务边界 | [支付与 MQ 总览](https://ocnbkcx1ug58.feishu.cn/wiki/PCQ7wP9kMikz0eklmNYcH1J1nwh) |
| 数据模型与两个状态机 | [01 数据模型](https://ocnbkcx1ug58.feishu.cn/wiki/N37xwtJ27iZ4XmkYa1uchxswnbc) |
| 正常付款与事务提交 | [02 正常付款](https://ocnbkcx1ug58.feishu.cn/wiki/Dn70wUIlKixYzMk0X4kcCvGsnR3) |
| 超时、UNKNOWN与原付款查单 | [03 UNKNOWN](https://ocnbkcx1ug58.feishu.cn/wiki/JyJGwzL2CiiFKhkzrLUcx3pgnUb) |
| 重复、乱序与业务拒绝 | [04 幂等与拒绝](https://ocnbkcx1ug58.feishu.cn/wiki/I0a5wc7LUibRArkMkUHct0swnvc) |
| MQ、有界重试与人工恢复 | [05 MQ恢复](https://ocnbkcx1ug58.feishu.cn/wiki/CDXkw84eWiDj5ZkzitPcs1PhnIh) |
| 支付与到期关闭、迟到模拟退款 | [06 到期与退款](https://ocnbkcx1ug58.feishu.cn/wiki/Kc9EwXMzni8ttTkYgk6cxSFknSd) |
| 实验入口与面试证据 | [07 证据与面试](https://ocnbkcx1ug58.feishu.cn/wiki/KsqfwyvRZiYtsnkB09HcBsQWnpe) |

各页含返回总览和相邻页导航；总览提供七个子页的直接入口。随后按用户授权创建[电商项目总目录](https://ocnbkcx1ug58.feishu.cn/wiki/WN1jwXDj0iPXm7kwLyHcmneenlb)，支付总览改名“支付”并连同七个子页整体移入；[Redis 学习与面试总结](https://ocnbkcx1ug58.feishu.cn/wiki/O4iUwSvl1i4Je7keOL0ci1iAnLf)使用原文档迁入同一总目录。支付导航段落已更新。

层级整理已完成读回验证：原 Redis 正文和四张画板保留，支付八页内容及六张画板保留，支付正文仅更新一段相关模块导航。初次发布记录与原始草稿继续保留；最终层级及 revision 见 [hierarchy/record.md](./hierarchy/record.md) 和 `hierarchy/verification-summary.json`。

## 六张原生画板

| 图 | 原生节点数 | 飞书实际导出预览 |
| --- | --- | --- |
| 支付知识地图 | 33 | [预览](/C:/project-one-ecommerce/docs/learning/payment-wiki-draft/remote/board-00-preview.jpg) |
| 189元订单数据关系 | 106 | [预览](/C:/project-one-ecommerce/docs/learning/payment-wiki-draft/remote/board-01-preview.jpg) |
| 正常付款与事务提交 | 27 | [预览](/C:/project-one-ecommerce/docs/learning/payment-wiki-draft/remote/board-02-preview.jpg) |
| 超时与原付款查单 | 24 | [预览](/C:/project-one-ecommerce/docs/learning/payment-wiki-draft/remote/board-03-preview.jpg) |
| MQ故障与人工恢复 | 32 | [预览](/C:/project-one-ecommerce/docs/learning/payment-wiki-draft/remote/board-05-preview.jpg) |
| 支付与到期竞争 | 18 | [预览](/C:/project-one-ecommerce/docs/learning/payment-wiki-draft/remote/board-06-preview.jpg) |

共240个原生节点，含思维导图、文字、连线、生命线和矢量形状。六个正文画板资源编号与实际写入/导出记录一致；各图通过固定幂等标识一次写入空画板。六张飞书实际预览已逐张查看，未见文字缺失、遮挡或布局错乱。

本地思维导图的自动布局坐标曾触发静态重叠警告；实际飞书自动布局正常。此结果只说明本次发布后的六张图可读，不表示后续编辑无需重新验收。

## 实际检查

- 八份实际发布候选通过 Draft Profile Check。
- 八次正文写入均返回 success、warnings=[]，读回为revision 2。
- 八页标题、全文文字、表格单元格及全部导航链接与发布候选一致。
- 五段真实代码摘录与当前源码相符；飞书读回恢复换行后，文字、缩进和换行均一致。
- 八份草稿和发布候选的文件指纹匹配发布计划；六份原生节点数匹配创建结果。
- 发布结果与检查汇总保存于 `remote/`，完成状态为 `publication-plan.json` 中的 `published_verified`。

节点快捷命令需要额外的旧空间读取权限；依据当前schema，直接使用已授权的 `wiki.nodes.create` 完成节点创建。全程用户身份，没有再次增加授权或改变空间成员。

本次修改仅涉及知识库资料与项目上下文。业务代码、迁移、前端、依赖、部署配置和开发数据没有修改，应用测试未重跑。历史实验数字均注明为已有证据；没有 commit/push。
