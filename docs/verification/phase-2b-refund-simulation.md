# 支付异常状态与模拟退款

## 业务规则

- 待付款订单取消或过期：`reserved → available`，只写一次 `RELEASE`，不退款。
- 可信的支付成功事件在订单已 `CANCELED/EXPIRED` 后到达：保留订单终态，
  将对应 PaymentAttempt 记为 `SUCCEEDED`，将异常写入 PaymentEvent，
  按 Attempt 创建一次 `RefundSimulation`。库存保持已经释放的结果，
  不再核销，也不再增加可售库存。
- `PAID` 且尚未发货的订单取消：已核销的库存从 `COMMITTED` 变为
  `RESTORED`，`available += quantity`、`reserved` 不变，写一次 `RESTORE`
  流水，同时创建一次 `RefundSimulation`，订单变为 `CANCELED`。
- `SHIPPED/COMPLETED` 拒绝此取消入口；重复取消、重复事件不重复恢复库存
  或产生模拟退款。

`RefundSimulation` 是数据库里的模拟事实，`SIMULATED` 不表示真钱已退。
退款金额与币种取自服务端的 PaymentAttempt；每个 Attempt 最多一条，
由一对一约束保证。MockGateway 提供 `simulate_refund` 适配层方法。
顾客查询自己的支付尝试时，`refund_status` 为 `SIMULATED` 或 `null`，
可用于演示，但不作为真实到账凭据。
支付事件处理和取消流程均在持有 Order 行锁的事务内调用它；
退款记录写入失败时，本事务内的订单与库存修改一起回滚。

## 验证

使用 pytest 的隔离 MySQL 测试库，覆盖：迟到成功在取消和过期之后到达、
同事件及不同事件号重复投递、错误金额事件、已支付取消与重复取消、
已发货拒绝取消、模拟退款写入失败回滚、支付成功与取消竞争。

```powershell
.\.venv\Scripts\python.exe -m pytest backend\apps\ordering\tests\test_order_lifecycle.py backend\apps\ordering\tests\test_order_api.py backend\apps\ordering\tests\test_order_concurrency.py backend\apps\payments\tests\test_payment_api.py backend\apps\payments\tests\test_payment_models.py -q
.\.venv\Scripts\python.exe backend\manage.py check
.\.venv\Scripts\python.exe backend\manage.py makemigrations --check --dry-run
```

结果：相关回归 `56 passed, 8 subtests passed in 51.38s`；加入只读退款状态后，
生命周期与支付 API 目标用例 `40 passed, 6 subtests passed in 46.46s`。
`manage.py check` 无问题，
`makemigrations --check --dry-run` 无待生成迁移，`git diff --check` 通过
（仅提示 Windows 换行符转换）。没有对开发数据库运行迁移或到期命令。

## 面试解释与限制

核心不变量是：一笔支付尝试最多一条退款模拟；一个预占最多一条
`RELEASE` 或 `RESTORE` 流水；终态订单不能被迟到支付重新打开。
取消与支付事件都锁同一张 Order 行，竞争时以实际取得锁的顺序决定结果。
当前规则会阻止 `CREATED/PENDING/UNKNOWN` 支付尝试的订单取消或过期，
所以本分支重点处理供应商先报失败、后更正为成功的异常，
以及已退款后的重复成功通知；它不表示普通的未确认支付会被强行关闭。

Mock 退款是本地数据库写入，因此能与库存和订单在同一 MySQL 事务回滚。
真实支付供应商是外部系统，不能直接共享此事务；若未来接入真钱，
必须设计供应商退款幂等键、请求/结果状态、失败重试与对账，
不能把这里的本地事务当成真实退款已到账的证明。
