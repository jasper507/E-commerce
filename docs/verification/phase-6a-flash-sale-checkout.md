# Phase 6A：秒杀下单基线的实现与静态检查

日期：2026-10-06。状态：已实现候选，真实MySQL验证未运行。

## 业务问题与判定标准

服务库存并发/秒杀主线。库存充足时，同Customer+FlashSaleItem不同Key并发，
最多一张待支付订单；数量1、活动价由服务端生成快照，名额、订单与库存共同提交。
用户在本聊天接受上述规则并明确要求开始最小测试与下单实现。

## 代码与方案

- Git基点2dacc9a，当前分支codex/flash-sale-baseline；分支从有本地C4/5D和Wiki材料的checkout创建。
  既有修改保留，没有commit/push或额外worktree。Git diff包含此前修改，不能全算成本片改动。
- 新增promotions活动/活动item/购买记录模型与服务、两组目标测试和迁移。
- Order新增creation_scope；普通下单限定cart_checkout，秒杀为flash_sale_checkout。
  唯一键为(user, scope, key)，旧订单默认普通范围；输入摘要区分活动item与地址。
- Purchase使用普通可空联合唯一键，严格检查状态与marker对应；当前服务仅创建HELD。
- 现有Order/快照/Inventory/Reservation/Movement复用；库存仍为正式NOWAIT基线。
- 支付创建/事件、取消、到期入口保护未接生命周期的秒杀订单。没有新HTTP/前端、Redis Lua或秒杀MQ。

替代方案与学习流程见[指南](../learning/phase-6a-flash-sale-checkout-study-guide.md)。

## 本轮实际观察

- `docker compose ps --format json`：Docker Linux Engine命名管道不存在，不能连接。
- 只读TCP检查：127.0.0.1:3307不可连接；3306可连接。没有改用3306、尝试凭据或启动/停止服务。
- `manage.py makemigrations ordering promotions --noinput`：生成两份迁移文件，未执行migrate；
  一致性历史读取因3307不可连接产生warning。
- `manage.py check`：0 issues。
- `manage.py makemigrations --check --dry-run`：No changes detected，同时保留上述数据库历史warning。
- pytest目标收集：10 tests collected in 0.06s（8项下单合约、2项直接模型约束），没有执行断言。
  收集成功只表示测试可发现，不表示业务断言已执行或并发正确。
- 新增与相关订单/支付测试合并收集：179 tests collected in 1.65s，未运行回归断言。
- 15份本片相关Python文件通过AST语法解析；两份文档的4个本地链接存在。
- 不连接数据库的迁移图检查通过，promotions/0001依赖ordering/0004_order_creation_scope。
  这只验证依赖图，不验证MySQL实际DDL或迁移执行。
- `git diff --check`通过；另有仓库LF/CRLF转换提示，没有空白错误。
- 独立只读复核未发现阻断问题；它不能替代真实数据库测试。

## 目标验证与未验证范围

目标：库存5/0，两独立MySQL连接、同用户不同Key，一次创建一次限购拒绝，
Order/OrderItem/AddressSnapshot/Reservation/Movement/Purchase各1，库存4/1；
补幂等、跨scope、归属、写后回滚及服务入口保护；直接模型测试检查唯一/CHECK。

本轮没有创建、迁移、销毁测试数据库，没有MySQL目标/回归passed结果，没有性能数字或原始并发JSON。
因此不能声称限购、存储约束、迁移运行或普通订单回归已由真实数据库验证。
没有运行真实MQ，旧C4/5D证据不作为本片新验证。

后续：环境恢复后按指南运行隔离目标测试与相关订单/支付回归；
再逐片接正常支付资格核销、未付合法关闭释放及迟到事件保护。
本片不支持秒杀订单支付/取消/过期，不能宣称秒杀业务闭环完成。
新迁移默认scope保留旧普通订单语义；已有跨scope同Key数据时，逆向恢复旧唯一键会冲突。
没有更改开发数据、前端、依赖、部署配置、Redis实现、MQ任务/恢复预算或发布飞书。
