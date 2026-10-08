# 页 05 内容核对

- 主模式 Explanation，应用/业务问题先行；源码仅两段真实核心节选。
- 适用源码 HEAD：18ffbe2f2bf674d31f3d2dc055976aedd41af010；origin 为 jasper507/E-commerce。
- 核对 `frontend/src/views/FlashSalesView.vue`、`frontend/src/flashSaleIntent.ts`、`frontend/src/api/flashSales.ts`、`backend/apps/promotions/services.py`、`views.py` 和 Phase 6E 验证记录及 JSON。
- 精确区分 201 首次、200 原 Key 同摘要重放、409 改地址冲突；新请求和重放判断顺序已核对。
- 每次发送前 me 核对；Session 为服务器身份依据；CSRF 为写请求防护；存储 userId 不作服务器授权。
- 原意图不被当前无货、活动消失或地址列表不可见挡住。存储失败不 POST。
- 未确认网络、5xx、解析、403、繁忙与冲突保留；只有确认或明确业务拒绝后允许主动新购买。
- 只声称同标签页刷新恢复，不声称跨设备/跨标签页/关闭标签恢复。
- 历史证据：真实上游 201 后 route.abort 的客户端响应丢失注入；不是实际断网或不确定 DB 提交。503/解析/403 是响应注入。
- 下单库存 1/0 → 0/1，后续 Mock 支付 0/1 → 0/0；历史 RESERVE/COMMIT 各一。
- 工程已验收与拥有者独立讲述待完成分开。本次未运行业务测试、未操作业务 DB 或远端飞书。
- draft.xml 含唯一 title/h1、连续 h2 seq=auto、合法 table/pre-code、一个白板占位和导航占位。
- 图：`../diagrams/05-lost-response/diagram.mmd`，由 root 统一填充原生画板。
- 本地 XML 外包 `<doc>` 后解析通过：title 1、h1 1、h2 6、whiteboard 1，中文字符 1523。
- whiteboard-cli 0.2.13 实际渲染通过；`diagram.json` 为 `{nodes: [...]}` 原生 OpenAPI 节点数据（21 nodes / 11 connectors）。
- Mermaid `--check` 0 errors / 0 warnings / 0 overflow / 0 overlap / 0 occlusion，保留 `check-result.json`。
- 已通过 view_image 实际看图：参与者、蓝/绿事务区域、提交后响应丢失、刷新原 Key 重放、只一次预占及付款核销文字完整，无遮挡。
- 源码与验证链接为当前提交固定 GitHub blob 链接；本地精确行号核对通过。
