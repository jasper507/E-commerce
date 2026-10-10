# Phase 6E：极简秒杀入口与浏览器端到端

阅读入口（2026-10-10补链）：[本片学习指南](../learning/phase-6e-flash-sale-browser-study-guide.md)。仅增加导航，以下日期、结果与限制保留原实验记录。

日期：2026-10-08，Asia/Shanghai。实现提交 `67d8c82` 已推送 main。

面试/工程目标：沿浏览器、HTTP、业务事务和 MySQL 解释“服务器已建单，但浏览器没有确认”时如何避免重复购买。
购买意图冻结与失败解释属于 Depth/Learning；Vue 页面、API 接线与路由属于 Supporting/Pair。
最小证据是实际创建、原 Key 重放、关键拒绝和 Mock 支付后的订单/库存事实；本片达到该停止条件，不追加 Lua、MQ 或负载矩阵。
拥有者回答“地址不一样冲突，409”，已核对这适用于同 Key 改参数；还需用原 Key、原 item/address 确认原订单。
该单题答复不等于已经独立掌握整个秒杀链路。

## 1. 范围与当前实现

新增 [页面](../../frontend/src/views/FlashSalesView.vue)、[API](../../frontend/src/api/flashSales.ts)、
[类型](../../frontend/src/types/flashSale.ts)与[意图存储](../../frontend/src/flashSaleIntent.ts)，在 App 导航和 router 增加 `/flash-sales`。
共六个前端文件；没有改依赖、Vite 代理、全局样式、普通 Checkout、后端模型/迁移、业务服务、支付/关闭或部署配置。

复用公开活动 GET、Session/CSRF 秒杀 POST、当前用户与本人地址 API、既有登录和订单详情/Mock 支付。
页面不发送成交价或数量，客户端不能决定这些交易值；后端忽略额外字段，服务端活动价 89.00、数量 1，普通 SKU 价 129.00。

- 首次发送前成功写入 `sessionStorage` 的 `{userId,key,itemId,addressId}`，冻结对象；写入失败就停止，没有内存 fallback 后继续 POST。
- 每个用户使用独立存储名称；每次发送前读取 `/api/auth/me/` 核对身份。用户 ID 只用于客户端恢复检查，后端身份仍来自 Session。
- 刷新恢复为待确认，确认直接使用存储中的原参数，不依赖当前活动列表、库存提示或原地址仍在地址簿中。
- 提交中禁止再次点击；网络、5xx、解析/成功结构异常、403、繁忙与幂等冲突保留原意图。403 提示身份或安全校验问题，不直接断言未登录。
- 仅明确业务 code 与 404/409 的拒绝进入“本次购买未成功”；繁忙与冲突不算可直接释放的拒绝。
- 只有 200/201 与所需订单字段结构有效才确认；展示服务端当前状态，不写死待支付。收到成功也保留原意图，必须明确点击“开始新购买”才清理。
- 没有存储密码、Cookie、Token 或个人地址快照。页面只展示当前 API 返回的本人地址。

替代是只在内存保存 Key，实现短，但刷新后丢失原意图，不能支持本片恢复承诺。
这里选择同标签页会话存储，代价是关闭标签页或换设备后没有该恢复承诺；未实现跨标签页协调或全局身份切换的原子绑定。

## 2. 本轮实际浏览器与 MySQL 证据

Browser plugin not available，使用已有缓存 Playwright CLI，未安装/升级依赖。
前端 `http://127.0.0.1:5173/flash-sales`，后端 `127.0.0.1:8000`，保留既有 `/api` 代理。
桌面 1280×720、手机 390×844。使用专用浏览器 session；截图保存在项目外临时目录，其路径与 SHA256 在 JSON 中。

隔离 Django testserver 装载派生自 `tests/fixtures/catalog_browser.json` 的 36 个对象：将 Inventory401 初始值设为 1/0，追加活动701和活动项711，复用 SKU301。
活动时间在编排时生成 now−1小时至 now+2小时；合成用户通过已有注册/登录、两条合成地址通过已有 API 在测试库创建。
没有给开发库执行迁移或 loaddata，没有访问外部账号。

| 场景 | 实际观察 | 数据事实/边界 |
| --- | --- | --- |
| 首次创建后丢弃响应 | 真实上游 POST 201、总额89.00、qty1；route.fetch 收到真实响应后 route.abort；页面进入待确认 | Order/Item/AddressSnapshot/Reservation/Purchase各1；RESERVE一次；库存1/0→0/1 |
| 刷新后的无货提示 | 原四字段逐字保留，活动显示当前无货，确认原购买按钮仍可点击 | 不把 GET 的提示当作拒绝旧单重放的依据 |
| 原 Key 重放 | 真正 POST200，输入仍item711/address1/原Key，同一public_id | 前后完整事实快照相同，仍一单/一次预占 |
| 同 Key 改地址 | 浏览器 fetch 刻意提交address2，真实409/idempotency_conflict | 原地址快照A与所有订单/库存事实保持；这不是正常冻结UI允许改地址 |
| Mock支付 | 从秒杀结果进入既有订单详情并点击发起模拟支付，页面显示已支付 | PAID、Attempt SUCCEEDED、Event PROCESSED、Purchase CONSUMED、Reservation COMMITTED；库存0/0，RESERVE/COMMIT各1 |
| 支付后原Key重放 | 真正200/同单，返回PAID | 重放返回当前状态，不要求与第一次JSON逐字相同 |
| 明确开始新购买 | 已确认后点击按钮，原意图清理、地址选择重新出现 | 不自动创建新Key或新订单 |

订单 `6acb7918-f8e2-4a72-974a-fa712a45e5a1`；Key `flash-sale-99875397-c94f-4387-b569-3843cfd2aee5`。
[JSON](phase-6e-flash-sale-browser-data.json)保存请求/响应观察、四个数据库快照、源码与输出指纹。
读取 MySQL 前断言 vendor=mysql、NAME=test_project_one，只投影必要业务字段，不打印用户密码、签名或事件原文。

## 3. 客户端故障注入与页面检查

| 注入 | 验证结果 | 证据限制 |
| --- | --- | --- |
| Storage.setItem失败 | 下单POST0，意图未写入，提示尚未发送 | 模拟存储不可用，不是磁盘故障实验 |
| 503 JSON | pending、原四字段不变，没有开始新购买按钮 | 客户端响应替换，没有实际依赖停机 |
| 200但JSON不能解析 | pending、原参数重试不变 | 客户端替换响应，不是服务端实际输出畸形JSON |
| 当前Session有效但POST403 | 保留pending，提示身份或安全校验；不擅自换Key | 客户端注入CSRF拒绝，真实Session/CSRF权限基线沿用当前后端测试 |

响应丢失发生在实际 POST201 已返回以后；可以证明浏览器恢复原意图及后端重放，不声称切断真实网络或恢复不确定的数据库提交。
另一个合成账号登录后没有显示原意图；重新登录原账号后原四字段恢复。
测试中曾尝试恢复旧Cookie，但Django切换身份会注销旧Session，该恢复步骤超时；之后改为重新登录原合成账号。
仅为合成测试账号重新生成密码，临时编排文件已清理；无实际用户密码修改。

| 页面检查 | 结果 |
| --- | --- |
| 身份/地址 | 路径正确；沿用项目标题“Project One 商品目录”；本人两地址可选 |
| 内容与交互 | 页面非空；正常控件、待确认恢复、同单详情、Mock支付及明确新购买经过交互 |
| 框架异常 | 无Vite错误覆盖层、无未捕获Vue异常 |
| 正常控制台 | 正常刷新与支付后重放为0 errors/0 warnings；匿名me403、注入503/403、abort和真实冲突409的资源日志分别是预期场景 |
| 桌面与手机 | 已查看截图；390px viewport与scrollWidth均390，无横向溢出，订单号正常换行 |

## 4. 正式串行检查与环境清理

前端 `npm.cmd --prefix frontend run build` 通过 vue-tsc 与 Vite；两次独立静态审查无阻塞发现。
MySQL8.4.10、127.0.0.1:3307、READ-COMMITTED；同步Mock支付、目录缓存关闭。
执行前 test_project_one不存在、可见测试连接0、开发schema表数26；8000/5173空闲。

收尾的第一次进程路径断言使用了不匹配的斜杠形式，停止失败后误启动了目标pytest。
该首轮结果不作为正式串行验收证据。随后核对并停止本轮PID及其子进程、关闭专用浏览器，确认端口空闲、测试库不存在及连接0，再正式串行运行以下命令。

```powershell
# 仅在本地进程提供MYSQL_HOST/PORT、MYSQL_TEST_DATABASE等隔离配置；密码不写命令或文档。
# DJANGO_ENV=test; MYSQL_DATABASE=project_one; MYSQL_TEST_DATABASE=test_project_one
# PAYMENT_EVENTS_ASYNC=false; CATALOG_CACHE_ENABLED=false
.\.venv\Scripts\python.exe -X utf8 -m pytest backend/apps/promotions/tests/test_flash_sale_api.py -q --create-db --tb=short
.\.venv\Scripts\python.exe -X utf8 -m pytest backend/apps/promotions/tests/test_flash_sale_lifecycle.py backend/apps/payments/tests/test_payment_api.py backend/apps/accounts/tests/test_session_auth_api.py -q --create-db --tb=short
```

正式目标12 passed/162 subtests，19.95秒；相关回归51 passed/19 subtests，57.25秒。
[目标原始输出](phase-6e-flash-sale-browser-target-output.txt)、[回归原始输出](phase-6e-flash-sale-browser-regression-output.txt)。耗时不用于性能比较。
本片没有重新执行6D锁等待实验、MQ/Redis或负载矩阵；不存在把历史216项回归冒充本轮执行的情况。

后检 test_project_one不存在、可见测试连接0、开发schema仍26表，SESSION/GLOBAL行锁等待均50，8000/5173无监听。
pytest重建/迁移/写入/销毁可丢弃测试库；testserver和pytest正式证据为串行。

## 5. 最小浏览器复现与复述

按[已有testserver说明](guest-catalog-browse.md#5-安全复现浏览器链路)先确认独占测试库；Django testserver停止后会留下库，最后须用串行pytest生命周期清理。
派生fixture只在项目外生成：复制catalog_browser，将Inventory401设1/0，追加FlashSale701当前时间窗、Item711/SKU301/price89.00。
启动 `manage.py testserver <派生fixture> --noinput --addrport 127.0.0.1:8000` 与既有Vite；用合成账号和两条本人地址，不能加载到开发库。

下面为核心客户端注入，可通过CLI `run-code --filename <外部脚本>`运行；必须先在真实页面选好活动项和地址。

```javascript
async (page) => {
  let original;
  await page.route('**/api/flash-sales/orders/', async route => {
    const response = await route.fetch(); // 请求真正进入Django/MySQL
    if (response.status() !== 201) throw new Error('Expected new order');
    original = await response.json();
    await route.abort('failed');         // 已得到201后才丢弃客户端响应
  }, { times: 1 });
  await page.getByRole('button', { name: '购买一件', exact: true }).click();
  await page.getByRole('heading', { name: '购买结果待确认' }).waitFor();
  await page.reload();
  await page.getByText('当前无货', { exact: true }).waitFor();
  const returned = page.waitForResponse(r =>
    r.url().endsWith('/api/flash-sales/orders/') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '确认原购买', exact: true }).click();
  const response = await returned;
  const replay = await response.json();
  if (response.status() !== 200 || replay.public_id !== original.public_id) {
    throw new Error('Same intent replay failed');
  }
}
```

浏览器之外还要读MySQL核对同用户/FLASH_SALE scope/原Key只有一张Order，关联Item、Snapshot、Reservation、Purchase各一，RESERVE只一次；支付后COMMIT一次。
scope应使用 `Order.CreationScope.FLASH_SALE`，其实际值为 `flash_sale_checkout`，不要把枚举名称当成数据库值。

闭卷题：服务器已返回201但浏览器丢失响应，刷新后活动显示无货。请沿“浏览器→HTTP→业务服务→MySQL”解释，为什么确认原购买仍得到200和同一订单，库存只预占一次。
还需解释同Key改地址为何409、403为何不能直接等于未登录，以及这些注入证据没有证明什么。
项目拥有者独立讲述验收仍待完成；不自动进入下一基础设施切片。
