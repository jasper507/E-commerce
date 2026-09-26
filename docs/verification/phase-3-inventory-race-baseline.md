# Phase 3：库存为 1 时的双顾客并发对照

## 问题与范围

两个不同顾客同时购买同一 SKU，各买 1 件，初始 `available_stock=1`、
`reserved_stock=0`。正确结果是最多一笔订单成功；失败顾客的购物车不清空。

这是正确性实验，不是 1000 人压测，也不测吞吐量或延迟。
错误示范只存在于 `test_inventory_race_experiment.py`：它保留了最小的
“读库存 → 判断 → 建订单 → 写回旧快照 → 建预占”步骤，没有复制正式结账的全部字段与流程。
正式下单仍调用项目的 `create_order_from_cart()`。

## 可复现条件

- 运行日期：2026-09-26；Windows；Python 3.11.9、Django 5.2.17、pytest 9.1.1。
- MySQL 容器镜像：`mysql:8.4.10`；pytest 使用隔离的 `test_project_one` 数据库。
- Git 基线：`418b022`，另有未提交的库存锁快速失败与结账提示改动。
- 每组两个线程、两个不同用户与购物车、同一 SKU；测试在读库存后用 Barrier
  固定错误示范的交错顺序。每组在新建的隔离测试数据上运行一次。

运行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/apps/ordering/tests/test_inventory_race_experiment.py -vv
```

## 2026-09-26 观察结果

| 路径 | 成功订单 | 预占总数 | 最终 available / reserved | 其他结果 |
| --- | ---: | ---: | --- | --- |
| 错误的先读后写 | 2 | 2 | 0 / 1 | 两个请求都读到 1；库存的 reserved 被旧快照覆盖 |
| 正式下单 | 1 | 1 | 0 / 1 | 另一人收到 `stock_busy` 或 `insufficient_stock`，购物车保留；事后重试为 `insufficient_stock` |

最终运行输出：`2 passed in 8.33s`。第一次运行中，错误示范通过；正式流程因实验商品分类
未设置上架而返回 `category_inactive`。修正测试数据后重新运行，两组均通过。

这个结果说明：把建单和写库存放进事务，仍不能使事务外读到的旧库存快照自动变新；
当前正式流程在此双顾客场景中没有超卖。它不证明所有交错顺序都正确，也不证明
1000 个请求下的等待时间、吞吐量或数据库承载能力。行锁与条件更新在库存预占边界上的
下一组对照，见 `phase-3-inventory-strategy-comparison.md`；完整下单负载与性能比较仍待进行。
