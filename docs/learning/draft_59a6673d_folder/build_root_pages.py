"""Build two local Wiki candidates; never calls a remote API or database."""

import html
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
SHA = "18ffbe2f2bf674d31f3d2dc055976aedd41af010"
SOURCE = f"https://github.com/jasper507/E-commerce/blob/{SHA}/"
PROJECT = "https://ocnbkcx1ug58.feishu.cn/wiki/WN1jwXDj0iPXm7kwLyHcmneenlb"
PAYMENT = "https://ocnbkcx1ug58.feishu.cn/wiki/PCQ7wP9kMikz0eklmNYcH1J1nwh"
REDIS = "https://ocnbkcx1ug58.feishu.cn/wiki/O4iUwSvl1i4Je7keOL0ci1iAnLf"


def p(text):
    return f"<p>{html.escape(text)}</p>"


def h(text):
    return f'<h1 seq="auto">{html.escape(text)}</h1>'


def table(headers, rows):
    head = "".join(f'<th background-color="light-gray">{p(x)}</th>' for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{p(x)}</td>" for x in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def link(path, label, line=None):
    url = SOURCE + path + (f"#L{line}" if line else "")
    return f'<p><a href="{url}">{html.escape(label)}</a></p>'


def questions(rows):
    return h("用自己的话解释") + table(["业务追问", "答案至少包含什么"], rows)


FOOT = p("维护：2026-10-08；源码快照18ffbe2；项目为单商家、CNY、单仓、单活动SKU、每单一件，支付和退款均为后端Mock模拟。本文引用历史隔离实验，本次归档未重跑应用测试。文档存在不代表作者已经独立掌握，完整口述验收仍待完成。")
NAV = p("导航将在发布时补齐。")

overview = [
    "<title>秒杀与库存并发</title>", NAV,
    '<callout emoji="💡" background-color="light-blue">' + p("本版结论：用MySQL事务把订单、购买资格和库存一起落地；条件UPDATE处理最后一件库存的竞争；同一次购买重试返回原订单。Redis Lua和ES尚未在此模块实现。") + "</callout>",
    p("适用读者：已经知道HTTP请求和Django模型、第一次独立学习交易并发的项目作者。阅读目标是用一张89元订单解释为什么这样设计、可以换什么方案、哪份证据支持收益，以及哪里仍有代价。先读总览，再按自己的疑问进入六个问题页；不用从阶段日志第一页开始背代码。"),
    h("业务起点：两个人想买最后一只鼠标"),
    p("普通SKU价129.00元，活动价89.00元，活动购买数量固定1。可售库存只有1件，两位顾客几乎同时点击购买。页面显示有货只是读取时的提示；最终谁买到，必须在后端交易时决定。系统要同时守住三件事：总预占不能超过库存，同一用户不能同时占多个购买名额，同一次请求重试不能建立第二张订单。"),
    p("Idempotency（幂等）识别同一次请求；购买资格识别同一顾客是否还能买；库存条件更新识别还有没有商品。三个规则分别保护不同业务边界。禁用按钮只改善操作体验，不能替代后端规则。"),
    h("从点击到付款：哪些事实一起改变"),
    p("下图画的是当前默认同步Mock支付路径。活动下单在MySQL中同步提交，支付也可启用已实现的MQ异步模式；两种支付模式共享相同业务处理器。图中的89元与一件库存是教学例子，与6E合成浏览器验收一致。"),
    '<whiteboard type="blank"/>',
    table(["业务时刻", "订单 / 购买资格", "库存available / reserved", "必须保持"], [
        ["点击前", "无本次订单", "1 / 0", "前端提示不等于购买承诺"],
        ["新单事务提交", "PENDING_PAYMENT / HELD", "0 / 1", "订单、快照、资格、预占、RESERVE流水一起提交"],
        ["原Key重复提交", "返回原订单与当前状态", "保持0 / 1", "同参数200；异参数409，不再次预占"],
        ["可信支付成功", "PAID / CONSUMED", "0 / 0", "核销原reserved；不再减available，COMMIT一次"],
        ["真正未付关闭", "CANCELED或EXPIRED / RELEASED", "1 / 0", "先确认无未确认支付，再释放库存和名额"],
        ["已付取消", "CANCELED / CONSUMED", "恢复available", "模拟退款与库存恢复，但成功限购资格不返还"],
    ]),
    p("Transaction（事务）把一组数据库修改当作一起成功或一起回滚的操作。下单时，服务端取本人地址、当前活动价和数量1，保存历史快照；库存UPDATE完成后仍要写Reservation和流水。任何后续异常都应退出事务，不能只留下库存扣减。支付再把原预占核销，不重新做一套下单逻辑。"),
    h("这一版为什么选择MySQL条件UPDATE"),
    p("业务要求是单SKU、数量1、集中竞争最后一件。正式代码用available>=1作为UPDATE条件，在数据库内同时可售减1、预占加1，避免应用用旧库存值覆盖新值。UPDATE仍取得写锁，可能等待，更新1行只代表库存步骤成功，整单还须提交。"),
    p("我们没有只凭术语选型：先保留NOWAIT快速拒绝基线，再把条件UPDATE放进相同完整事务，验证首单提交、回滚、限购和异常无残留，最后比较有限批次下的重试与连接成本。紧张库存的同产出条件下，条件UPDATE用较少调用达到同样6张订单，支持本期选择；不代表所有负载都更快。"),
    link("docs/adr/0001-flash-sale-inventory-conditional-update.md", "读策略决定：适用范围、实际数据与等待代价", 19),
    h("模块如何复用，MQ在什么地方发挥作用"),
    table(["模块", "当前职责", "为什么衔接"], [
        ["promotions", "活动、价格、每单一件、购买资格", "把业务限购表达为持久交易规则"],
        ["ordering / inventory", "订单快照、预占、核销、关闭、流水", "共享同一库存事实，避免秒杀另造一套库存账"],
        ["payments / RabbitMQ / Celery", "保存通知、后台处理、有限重试与人工恢复", "已入箱通知不必依赖顾客反复点击；重复任务仍只产生一次业务效果"],
        ["Vue", "活动展示、身份地址、保存与确认原购买意图", "浏览器丢失响应后仍能用原参数找到同一订单"],
        ["Redis商品缓存", "浏览读取的缓存和故障降级", "可丢弃副本；当前秒杀库存仍由MySQL权威处理"],
    ]),
    p("当前MQ承接支付事件，不是把本版秒杀下单改成排队建单。MQ的业务理由是通知持久化后能后台处理，并在暂时故障或Worker中断后继续原事件；它增加发布窗口、重复消息与恢复维护。支付成功仍由MySQL业务事务证明，消息已接收或已ACK不能单独证明订单已支付。"),
    f'<p>关联知识：<a href="{PAYMENT}">支付总览</a>；<a href="{REDIS}">Redis学习与面试总结</a>；<a href="{PROJECT}">电商项目总目录</a>。</p>',
    h("阅读路线与需要掌握的能力"),
    table(["问题页", "读完应能解释"], [
        ["01 重复下单", "为什么同Key重放与不同Key限购要分开"],
        ["02 抢最后一件", "条件UPDATE相对NOWAIT改善了什么，为什么仍要锁和事务"],
        ["03 下单失败", "0行、锁异常、未知错误怎样区分，为什么整单回滚"],
        ["04 付款到关闭", "资格何时消费、何时释放，MQ在支付哪里有业务价值"],
        ["05 响应丢失", "浏览器为什么保留原用户、Key、活动项和地址"],
        ["06 证据与候选", "怎样把一个结论连到实际数据，Lua与ES需要怎样的理由"],
    ]),
    questions([
        ["为什么不只在按钮上禁止连点？", "浏览器之外仍可并发请求；后端请求幂等、资格唯一和库存原子条件分别保护规则"],
        ["为什么下单与支付不是一次减两次库存？", "下单available→reserved；成功付款仅消耗reserved，最终流水各一次"],
        ["有知识库就代表模块完成学习了吗？", "工程证据已足够第一版；作者还要独立复述流程、选择、失败和证据边界"],
    ]),
    link("docs/verification/phase-6e-flash-sale-browser.md", "证据入口：真实浏览器与MySQL业务事实", 41), FOOT,
]

evidence = [
    "<title>06 证据与演进：哪些结论可以向面试官讲</title>", NAV,
    p("本页适用于读完秒杀主线、准备整理面试回答的项目作者。目标是把每一句设计主张连接到真实代码和证据，并知道什么时候停止追加实验。当前正式实现是MySQL单SKU条件UPDATE与极简顾客入口；Lua/ES仍是拟评估方向，不写成已实现。"),
    h("一个可信故事需要四层证据"),
    p("先说明业务问题，再说明必须守住的规则，最后用正常与失败终值证明选择。实验数量和技术列表本身不能证明你具备工程能力。下面是现有材料的阅读入口，而不是要求你重新运行全部测试。"),
    table(["阶段 / 问题", "已有证据", "能支持的说法", "必须保留的边界"], [
        ["Phase3：旧库存快照", "库存1的naive先读后写创建两张订单；正式流程只提交一单", "旧读值加事务不自动解决竞争，需要数据库边界", "最小库存路径历史实验，不是完整秒杀的本轮复现"],
        ["6A / 6B：交易与资格", "限购、同Key、支付消费、关闭释放、O1迟到不碰O2", "订单、资格与库存的副作用同事务且有幂等保护", "局部故障使用注入；独立口述尚未验收"],
        ["6C-2：完整事务策略对照", "NOWAIT真实3572；条件等待在首单提交后0行、回滚后1行", "两种策略在相同订单流程中的正确性与等待行为不同", "受控持锁调度，不是生产锁竞争频率"],
        ["6C-3：有限负载", "24顾客/8worker，多条件三轮；raw与独立重算", "紧张库存同产出时减少调用、旅程p95与连接开放成本", "服务层closed batch，无HTTP流量、生产容量或稳定p99结论"],
        ["6C-5 / 6D-2：失败合同", "真实1205与整单回滚、恢复会话、原Key重试", "锁超时不能伪装无货，失败不留下半张订单", "1213/2006及清理故障部分为注入；5秒非总HTTP期限"],
        ["6E：浏览器闭环", "真实201后客户端abort；刷新原Key200同单、异地址409、付款一次COMMIT", "同标签页保留原意图，不重复建单或预占", "响应丢失为客户端注入，非切断真实网络或全部提交不确定恢复"],
    ]),
    link("docs/verification/phase-3-inventory-race-baseline.md", "Phase3：错误基线", 29),
    link("docs/verification/phase-6b-flash-sale-lifecycle.md", "6B：资格与支付关闭", 35),
    link("docs/verification/phase-6c2-flash-sale-inventory-comparison.md", "6C-2：完整事务对照"),
    link("docs/verification/phase-6c3-flash-sale-inventory-load.md", "6C-3：有限负载原始证据与分析", 59),
    link("docs/verification/phase-6c5-flash-sale-inventory-failures.md", "6C-5：正式落地失败证据"),
    link("docs/verification/phase-6d2-flash-sale-http-wait.md", "6D-2：HTTP锁等待与会话恢复"),
    link("docs/verification/phase-6e-flash-sale-browser.md", "6E：浏览器、请求与数据库快照", 41),
    h("怎样解释显著提升，而不夸大"),
    p("业务产出相同才能有意义地谈成本。在库存6、24位顾客、8worker、NOWAIT最多3次总尝试的三轮里，两种策略每轮都建6单；条件UPDATE每轮24次调用，对照52–55次。这支持当前条件下减少繁忙重试的结论。旅程p95和连接区间也较低，但不能把范围的端点随意配对成固定提升百分比。"),
    p("库存充足且只有一次尝试时，NOWAIT更快返回的多数是拒绝；不能只看全结果延迟就说顾客更快买到了。库存充足、三次预算下连接总秒有交叠，不能说条件更新每轮都更省资源。当前方案减少重复调用的同时，仍可能等待并占住连接、事务及此前的锁。"),
    h("Redis Lua：先明确希望改变哪个业务问题"),
    p("Lua脚本可以原子组合Redis内部的多个操作。若下一步目标是减少进入MySQL的无效或超能力请求，可比较可关闭的原子准入与当前MySQL路径；它只是前置保护，MySQL仍核验库存、资格和订单。若选择Redis库存预扣，则还要定义MySQL建单失败补偿、重复补偿、Redis已执行但响应丢失、取消返还与重建对账。"),
    p("已有MySQL防超卖证据，所以不能用‘当前没有Lua就不正确’作为引入理由。已有订单的原Key重放也不能被新的售罄或准入标记挡住。原子性范围只有Redis，不跨MySQL。先取得明确数据库成本基线，再用同业务产出与失败终值对照；没有收益或失败边界未闭合，就不进入正式路径。"),
    '<p><a href="https://redis.io/docs/latest/develop/programmability/eval-intro/">Redis官方：Lua原子执行与阻塞边界</a></p>',
    h("ES：作为独立商品搜索故事评估"),
    p("Elasticsearch（ES）服务全文检索与相关性排序，适合回答‘用户怎样找到想买的商品’。当前顾客列表仅分类筛选，尚无关键词搜索基线；先确定具体查询与预期结果，再比较最小MySQL搜索和ES。不能用新增服务数量证明秒杀更强。"),
    p("若实际采用ES，要将商品索引视为可重建副本：定义改名/下架同步、漏同步修复、重复或乱序更新、服务故障以及从MySQL重建。即便ES写入成功，搜索可见也可能等refresh；下单价格、库存与权限仍以MySQL为准。最小查询集效果与这些一致性证据足够后停止，不扩成通用搜索平台。"),
    '<p><a href="https://www.elastic.co/docs/manage-data/data-store/near-real-time-search">Elastic官方：近实时搜索与refresh</a></p>',
    h("把自己的理解与AI参考答案分开"),
    p("建议闭卷用一张89元订单复述：浏览器先存意图→HTTP身份与CSRF→查旧Key→资格与订单→库存条件UPDATE→提交→原Key确认→付款核销→关闭/迟到事件。然后解释一条失败分支，并指出这条结论对应的记录。这里列的是参考要点，不代表作者已答对。"),
    questions([
        ["同Key同参数、同Key改地址、新Key但HELD分别怎样？", "原单重放；幂等冲突；限购拒绝，不能混成库存不足"],
        ["UPDATE影响0行、1行、抛异常有什么区别？", "无符合库存；库存步骤成功待整单提交；退出完整事务而非伪装0行"],
        ["流水写入失败，之前UPDATE已经执行怎么办？", "同一事务整体回滚，原Key可重试完整业务，不能只补最后SQL"],
        ["O1关闭、O2新单占资格，O1迟到支付怎么办？", "只处理O1退款模拟，保留O1关闭，不触碰O2资格与库存"],
        ["为什么MQ的ACK不能当支付成功？", "ACK说明消息消费层进度，付款事实取决于MySQL事务与业务校验"],
        ["为什么已有证据足够就先停止实验？", "先形成能独立解释的故事，保护算法、语言、数据库、网络与操作系统学习时间"],
    ]),
    FOOT,
]

PAGE_MAP = [
    {"id": 0, "title": "秒杀与库存并发", "workspace": ".", "diagram": "00-business-loop"},
    {"id": 1, "title": "01 重复下单：幂等与限购为什么分开", "workspace": "draft_8e380e20_folder", "diagram": "01-intent-and-quota"},
    {"id": 2, "title": "02 抢最后一件：为什么选择条件 UPDATE", "workspace": "draft_9296fc9d_folder", "diagram": "02-inventory-choice"},
    {"id": 3, "title": "03 下单失败：为什么整单回滚与等待5秒", "workspace": "draft_89ff66fc_folder", "diagram": "03-timeout-rollback"},
    {"id": 4, "title": "04 从付款到关闭：资格生命周期与 MQ 的业务价值", "workspace": "draft_bd1b8a9e_folder", "diagram": "04-payment-mq"},
    {"id": 5, "title": "05 响应丢失：为什么保留原购买意图", "workspace": "draft_15152a08_folder", "diagram": "05-lost-response"},
    {"id": 6, "title": "06 证据与演进：哪些结论可以向面试官讲", "workspace": "draft_948dc651_folder", "diagram": None},
]

if __name__ == "__main__":
    for content, folder in [(overview, BASE), (evidence, BASE / PAGE_MAP[6]["workspace"])]:
        (folder / "draft.xml").write_text("\n".join(content) + "\n", encoding="utf-8")
    (BASE / "page-map.json").write_text(json.dumps(PAGE_MAP, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Built overview, evidence, and page map; no remote operations.")
