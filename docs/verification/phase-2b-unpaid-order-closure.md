# 未付款订单取消、到期关闭与库存释放

## 本次实现

- `POST /api/orders/{public_id}/cancel/`：登录顾客取消自己的 `PENDING_PAYMENT` 订单；
  重复取消返回同一终态，不重复释放。
- `python backend/manage.py expire_pending_orders`：运行一次到期扫描；只处理
  预占期限已过的待付款订单。命令可以重复运行，每单独立事务。
- 两条路径共用 `ordering.lifecycle_services`：锁订单、支付尝试、预占与库存；
  同一事务中把 `reserved` 返还 `available`，把预占设为 `RELEASED`，
  写一条唯一的 `RELEASE` 流水，并把订单设为 `CANCELED` 或 `EXPIRED`。

如果支付尝试仍处于 `CREATED/PENDING/UNKNOWN`，取消返回
`409 payment_unresolved`；到期扫描跳过并计入 `payment_unresolved`。
这笔库存暂时继续预占，必须先查清原支付结果，再重新取消或运行到期扫描。
该安全规则使待确认支付订单可能超过 15 分钟才释放库存。

到期扫描是**一次性命令**，尚未配置定时调度。仅记录 `expires_at`
不会自动触发它；部署时需另行安排定期运行。本次没有对开发库运行此命令。

## 2026-09-26 隔离 MySQL 验证

```powershell
.\.venv\Scripts\python.exe -m pytest backend/apps/ordering/tests/test_order_lifecycle.py backend/apps/ordering/tests/test_order_api.py backend/apps/ordering/tests/test_order_concurrency.py backend/apps/payments/tests/test_payment_api.py -q
```

结果：`47 passed, 6 subtests passed in 46.54s`。使用 pytest 创建的隔离
`test_project_one` 数据库；未修改开发数据库。新增的 7 个测试覆盖：

1. 只有订单所属顾客能取消；重复取消与同时取消最多释放一次。
2. 未到期不释放；到期命令关闭一次，重复运行没有第二次库存流水。
3. 未确认支付阻止取消和到期释放；已明确失败的支付可随后取消。
4. 已支付订单不能再次释放；支付成功与取消同时请求时，最终保持
   `PAID`、预占 `COMMITTED`、核销流水恰好一条、释放流水零条。

## 边界与下一步

这次只实现**未付款**取消。已支付未发货取消、迟到支付成功后的
Refund Simulation、到期命令的定时调度和完整生产故障恢复尚未实现。
有未确认支付时先查单再释放，是为避免顾客已付款却把商品重新卖出。
下一步应补迟到支付与退款模拟，再用更大范围的并发实验检查库存与支付账。

后续切片已实现上述两种模拟退款分支；当前行为与验证见
[支付异常状态与模拟退款](phase-2b-refund-simulation.md)。本页的测试数字保留为
当时未付款关闭切片的历史记录。
