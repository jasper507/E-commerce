"""Build local authoring candidates. This script does not call any remote API."""

import ast
import hashlib
import html
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[4]
PAGES = Path(__file__).resolve().parent
PAGE_MAP = json.loads((PAGES / "page-map.json").read_text(encoding="utf-8-sig"))
SOURCE_RECORDS = []


def esc(value):
    return html.escape(str(value), quote=True)


def p(text):
    return f"<p>{esc(text)}</p>"


def heading(text):
    return f'<h1 seq="auto">{esc(text)}</h1>'


def bullet(items):
    return "<ul>" + "".join(f"<li>{esc(item)}</li>" for item in items) + "</ul>"


def steps(items):
    return "<ol>" + "".join(f"<li>{esc(item)}</li>" for item in items) + "</ol>"


def table(headers, rows):
    head = "".join(f'<th background-color="light-gray">{p(item)}</th>' for item in headers)
    body = "".join("<tr>" + "".join(f"<td>{p(item)}</td>" for item in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def remind(text):
    return '<callout emoji="💡" background-color="light-blue">' + p(text) + "</callout>"


def board(description):
    return p(description) + '<whiteboard type="blank"/>'


def source_function(path, name):
    source = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name)
    start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
    lines = source.splitlines()
    return "\n".join(lines[start - 1 : node.end_lineno]), start, node.end_lineno


def source_slice(path, first, last):
    source = (ROOT / path).read_text(encoding="utf-8")
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines) if line == first)
    end = next(i for i in range(start, len(lines)) if lines[i] == last)
    return "\n".join(lines[start : end + 1]), start + 1, end + 1


def code(path, excerpt, explanation):
    text, first, last = excerpt
    SOURCE_RECORDS.append({"path": path, "first": first, "last": last, "sha256": hashlib.sha256(text.encode()).hexdigest()})
    return p(f"真实源码摘录：{path}，第{first}至{last}行。对应当前工作区，含尚未提交的C4增量。") + f'<pre lang="python" caption="关键实现"><code>{esc(text)}</code></pre>' + p(explanation)


def evidence(paths, claim, limit):
    return heading("证据与限制") + p("证据状态：历史真实实验，本次读取核对；本次知识库创作没有重跑应用测试。") + p(claim) + p("仓库证据入口：" + "；".join(paths)) + p(limit)


def question(rows):
    return heading("面试与自检") + table(["追问", "需要能自己解释的答案"], rows)


def footer():
    return p("维护信息：2026-10-06；适用Project One当前工作区，Git基点2dacc9a，C4/5D仍含本地增量。业务集成使用后端Mock Gateway，无真钱；远端Wiki与画板发布结果由归档记录单独确认。")


body = {}

body[0] = [
    remind("先掌握一句话：支付负责查清原付款，可信结果统一入箱，再让付款、订单和库存一起改变；故障恢复继续处理原记录。"),
    p("适合谁：第一次接触支付、已知道Django请求和MySQL基本概念的项目作者。读完本模块，你应该能用一笔189元鼠标订单解释正常付款、超时、重复通知、MQ故障和订单到期。先读本页，再按具体问题进入子页。"),
    heading("从一笔189元订单开始"),
    p("顾客买1只189.00元鼠标，仓库初始可售20件。下单时，Order保存189.00/CNY金额快照，OrderItem保存商品与价格快照；Inventory从available=20、reserved=0变成19、1。此时商品被预占，订单仍是PENDING_PAYMENT，尚不能叫付款成功。"),
    p("可信成功通知处理完后，PaymentAttempt变SUCCEEDED，Order变PAID，Reservation变COMMITTED，库存变19、0。COMMIT流水只有1笔。之后Admin模拟发货、顾客确认收货，完成最小商业闭环。"),
    heading("支付在项目里守什么边界"),
    board("图1：支付学习地图。围绕原订单连接数据、正常付款、UNKNOWN、幂等、MQ恢复和到期竞争；Redis与秒杀的衔接见下表。"),
    table(["模块", "主要职责", "与支付的衔接"], [
        ["订单与库存", "下单快照、预占、核销、释放和履约", "支付确认后才核销原预占；关闭需要检查原付款"],
        ["Redis", "商品读取缓存、一致性和依赖故障降级", "浏览读取可以降级；交易事实仍由MySQL保存"],
        ["支付与MQ", "原付款结果、事件处理、消息故障恢复", "本知识库模块；MQ仅承担事件任务传递"],
        ["秒杀与并发", "集中抢购、库存保护和限购", "产生订单后复用现有付款流程，不另造秒杀支付"],
    ]),
    bullet([
        "金额来自订单快照：商品后来涨到209元，原189元交易仍按189元校验。",
        "UNKNOWN表示不知道付款结果，不能猜失败、直接回滚或重新付款。",
        "重复通知、任务重投、重试不能造成第二次核销、释放或模拟退款。",
        "MySQL是事实来源；HTTP202、Broker接收、Worker ACK各自只说明本层进度。",
        "Gateway和Broker网络等待在数据库事务之外，不能占着订单锁等网络。",
    ]),
    '<p>已有 Redis 学习与面试资料：<a href="https://ocnbkcx1ug58.feishu.cn/docx/KKBXdHFQqoZRCNxJv1YcFb29nkf">电商项目学习与面试总结</a>。本次保留原文档位置，支付与秒杀分别组织。</p>',
    heading("阅读路线与完成标准"),
    table(["页面", "读完应能回答"], [
        ["01 数据模型与两个状态机", "Order、Attempt、Event为什么分开，UNKNOWN和ERROR有什么区别"],
        ["02 正常付款与事务提交", "前端请求怎样到MySQL、Gateway、MQ、Worker，哪些写入一起提交"],
        ["03 UNKNOWN与查单", "为什么查询原Attempt，而不是创建新订单或重新扣款"],
        ["04 重复、乱序与拒绝", "哪些消息可重复成功，哪些矛盾或伪造消息必须拒绝"],
        ["05 MQ恢复", "漏投、暂态故障、Worker中断各怎样恢复，为什么有界且保留原ID"],
        ["06 到期与迟到退款", "先拿锁是否必然能关单，已关闭后成功为什么模拟退款"],
        ["07 实验与面试证据", "一句面试主张对应哪份实际证据，还缺什么"],
    ]),
    p("推荐顺序：先正常成功，再学习UNKNOWN，然后重复/乱序，最后MQ故障与到期竞争。一次只解释一个分支：输入是什么、原状态是什么、变化在哪个事务中提交、失败后留下什么。自己能解释才算掌握，文档存在不代表已经学会。"),
    heading("文档规范与整理SOP"),
    p("每页固定顺序：业务例子 → 必须守住的规则 → 流程图或对照表 → 方案与替代 → 真实源码 → 正常与失败前后值 → 历史证据 → 限制 → 面试自检。复杂图有等价文字解释，避免只能靠图片理解。"),
    steps([
        "先读当前源码和已有证据，辨别旧待办与已经完成的实验。",
        "用原189元订单写清状态、库存和副作用次数，先确认正确行为。",
        "按源码画参与者、提交点、锁和网络边界，摘少量真实关键代码。",
        "只有新规则或未验证风险才新跑实验；已有证据足够就注明历史日期后引用。",
        "核对结果、失败过程、原始数据和限制，禁止把测试耗时当QPS或生产延迟。",
        "归档到本项目Wiki问题节点，读回正文和画板；本地源码、证据继续保留。",
    ]),
    evidence(["docs/verification/phase-5c4-payment-worker-recovery.md", "docs/verification/phase-5d-payment-expiration-concurrency.md"], "当前已有真实隔离RabbitMQ/Worker故障实验与MySQL锁等待证据；本模块统一解释和导航这些成果。", "范围是本地模拟业务、真实技术实验。本期人工恢复，没有定时自动接管、第三方Sandbox、真实资金、生产流量或多机容灾结论。"),
    question([["一句话怎样介绍这一块？", "原付款结果与处理进度分开；原事件持久入箱，业务事务保持一致，MQ重复用幂等接住。"], ["秒杀属于支付吗？", "两条独立技术主线；秒杀保护创建订单和库存，支付保护已创建交易的付款确认。"]]),
]

body[1] = [
    remind("PaymentAttempt问“钱付成功了吗”；PaymentEvent问“这份通知处理完成了吗”。这两个状态可以不同，不能合并成一个字段。"),
    p("目标：知道每张表记录什么事实，能根据前后值判断该查供应商还是处理已有Inbox。前置：先认识189元订单与预占库存；本页只解释已经实现的模型，不新增表、索引或拆库方案。"),
    heading("为什么一个Order不够"),
    p("Order描述顾客买了什么以及交易是否履约；一次付款尝试可能失败、未知或成功；同一次付款又可能产生PENDING、SUCCEEDED及重复通知。把所有信息塞在Order.status，会丢掉付款历史与通知处理进度，无法回答“收到了成功但处理失败”这种问题。"),
    board("图2：支付数据关系。先看Order→OrderItem→Reservation→Movement，再看Order→Attempt→Event/Refund。图仅列核心关系；"),
    table(["模型", "记录的事实", "实际关系或约束"], [
        ["Order / OrderItem", "订单金额、明细价格和数量快照", "Order拥有多条OrderItem；金额用Decimal和CNY"],
        ["PaymentAttempt", "一次原付款、供应商引用、金额和状态", "属于一张Order；每单最多一个活跃Attempt"],
        ["PaymentEvent / Inbox", "通知原文、处理进度、次数、失败与租约", "(provider,event_id)唯一；attempt可空，未知/无效通知可先保留"],
        ["InventoryReservation", "一条明细占用了多少库存及其去向", "order_item非空且一对一；inventory非空外键"],
        ["InventoryMovement", "库存available/reserved增减审计", "reservation非空；(reservation,movement_type)唯一；字段为available_delta/reserved_delta"],
        ["RefundSimulation", "原成功Attempt的一次模拟退款", "attempt一对一，Order可有退款记录；不是资金总账"],
        ["MockGatewayPayment", "模拟供应商自己的结果", "attempt一对一；查单读取原记录"],
    ]),
    p("本期保持单MySQL库和Django模块化单体，按职责分表。替代方案是独立Payment状态表、拆支付库或财务总账；它们会增加需要保持一致的事实，当前Interview Core没有对应业务需求，故不引入。"),
    heading("两种状态机放在同一个189元例子里"),
    table(["状态所属对象", "当前值", "通俗含义", "接下来做什么"], [
        ["Attempt", "CREATED", "已保存本次付款，原供应商结果尚未确认", "继续原创建/查单流程，不能绕过活跃尝试"],
        ["Attempt", "PENDING", "供应商报告仍在处理中", "查原付款或等可信结果"],
        ["Attempt", "UNKNOWN", "网络丢失了结果，本地不知道", "查询原provider_reference"],
        ["Attempt", "SUCCEEDED", "可信付款事实已确认", "不能再发起新付款，禁止被迟到失败倒退"],
        ["Event", "RECEIVED", "通知已持久保存，还没合法领取", "同步处理或发布原Inbox ID"],
        ["Event", "PROCESSING", "某次处理资格已领取并提交", "完成原业务；硬中断可留下过期租约"],
        ["Event", "PROCESSED", "这份通知通过校验并处理完", "重放短路；不等于所有通知都代表付款成功"],
        ["Event", "REJECTED", "原通知违反签名、金额或状态规则", "留证调查，不自动重试非法输入"],
        ["Event", "ERROR", "已有通知处理过程出错", "按FailureKind与预算判断恢复资格"],
    ]),
    p("重要例子：有效FAILED通知也能Event.PROCESSED，因为“通知处理完成”和“付款成功”不同。另一例：供应商成功通知已经保存，但库存事务出现暂态故障，Event.ERROR；此时不是新的付款，应处理原Inbox。前端要读后端提交的Order/Attempt，不能只看Event.PROCESSED。"),
    table(["对比", "Attempt.UNKNOWN", "Event.ERROR"], [
        ["缺什么", "缺供应商付款结果", "缺本地成功处理结果"],
        ["已有材料", "原订单、原Attempt、原供应商引用", "已经保存的原通知与签名"],
        ["恢复入口", "reconcile_payment_attempt查原付款", "process_stored_payment_event或合格Inbox恢复"],
        ["共同规则", "保持原189元和原身份", "保持原189元和原身份"],
    ]),
    heading("唯一键与业务检查各负责一层"),
    bullet([
        "(user,idempotency_key)唯一保存同一请求身份；服务还核对request_fingerprint，异参重放409。",
        "(order,active_marker)唯一：活跃填1，终态填NULL；MySQL允许多个NULL历史，但同订单只一个1。",
        "状态与active_marker一致由Service维护；数据库只检查marker是NULL或1，不能夸大为完整状态约束。",
        "Event(provider,event_id)唯一防重复入箱；锁、终态检查和副作用约束继续防重复业务。",
        "Reservation不能既核销又释放，跨表规则靠事务和状态判断，单个唯一键不足以保证全部一致。",
    ]),
    p("源码入口：backend/apps/payments/models.py；backend/apps/inventory/models.py；backend/apps/ordering/models.py。按上述表逐项找关系，不先背所有字段。"),
    evidence(["docs/verification/phase-5b-payment-event-retry.md", "docs/verification/phase-5d-payment-expiration-concurrency.md"], "5B历史目标26 passed、12 subtests、18.08秒验证持久预算与事务门槛；5D观察两个业务流程共享Order记录锁。", "关系图不是完整schema。无效事件允许attempt为空；数据库唯一键不代替归属校验、金额匹配和跨表事务。旧MVP记录的SQLite验证不能直接称为MySQL并发证据。"),
    question([["为什么不能把UNKNOWN改成ERROR？", "不知道付款和知道通知但处理失败是两件事，分别查单和恢复原事件。"], ["Event.PROCESSED是否说明已付款？", "不一定，有效PENDING/FAILED也能处理完成；最终读Order与Attempt。"]]),
]

receive_excerpt = source_slice("backend/apps/payments/services.py", "    with transaction.atomic():", "    return event")
# The first matching block belongs to start_payment; identify the receive function's lines instead.
receive_source, receive_first, _ = source_function("backend/apps/payments/services.py", "receive_payment_event")
receive_lines = receive_source.splitlines()
receive_start = next(i for i, line in enumerate(receive_lines) if line == "    with transaction.atomic():")
receive_excerpt = ("\n".join(receive_lines[receive_start:]), receive_first + receive_start, receive_first + len(receive_lines) - 1)

body[2] = [
    remind("正常付款有两个关键提交点：先保存Attempt，再调用Gateway；先保存原通知，再交给Worker处理。网络调用不放在这些数据库锁里。"),
    p("目标：沿前端→HTTP→权限→业务服务→MySQL→Gateway→Inbox→MQ→Worker说明一次付款。示例固定为189.00元、CNY、购买1件；本项目Gateway是后端Mock，不接真钱。默认可以同步，下面用已实现的异步模式展示完整链路。"),
    heading("从顾客点击付款到订单PAID"),
    board("图3：正常付款时序。重点找Attempt提交、Gateway事务外调用、Inbox提交、消息发布、T1领取、T2业务提交与ACK。"),
    steps([
        "顾客登录后POST /api/payments/attempts/，提交order_public_id和Idempotency-Key；Session/CSRF与归属由后端检查。前端不决定付款金额。",
        "start_payment调用create_payment_attempt：短事务锁用户和原订单，先检查旧Key，再核对待付款状态及活跃Attempt，从Order复制189.00/CNY，提交本次CREATED。",
        "提交后调用Mock Gateway，使用原provider_reference。网络成功、失败或超时都是原Attempt的结果，不新建订单。",
        "供应商结果或模拟Webhook进入receive_payment_event。预验签只决定安全去重身份；原文独立持久入Inbox。完整验证仍在后续业务处理。",
        "Inbox提交后发布内部整数ID；异步HTTP202只表示通知已保存并待处理。发布失败保留原Inbox，不伪装成付款失败。",
        "Worker收到ID，T1独立事务提交PROCESSING、count+1和租约。T2再次锁Event并复查领取版本，再验签、核对金额/币种/原订单。",
        "可信SUCCEEDED在T2一起提交Attempt.SUCCEEDED、Order.PAID、Reservation.COMMITTED、库存核销、COMMIT流水与Event.PROCESSED；之后Worker ACK。",
        "顾客读取后端已提交的订单/付款状态，再显示结果。Admin发货与顾客收货属于后续履约。",
    ]),
    heading("各个确认到底确认了什么"),
    table(["信号", "已经确定", "尚不能据此确定"], [
        ["HTTP201/200创建结果", "原付款尝试已创建或幂等复用", "异步业务是否已经PAID"],
        ["Webhook HTTP202", "原通知已保存，可能待处理/调查", "Broker一定收到、付款一定成功"],
        ["Broker发布确认", "消息层接受了任务", "库存已经核销"],
        ["Event.PROCESSED", "这份通知已经处理完成", "通知一定是SUCCEEDED"],
        ["Worker ACK", "Worker向Broker确认任务完成", "以后永远不会有重复任务"],
        ["后端Order.PAID与Attempt.SUCCEEDED", "本地可信支付确认已提交", "真实供应商/真实资金结算，因为这里使用Mock"],
    ]),
    heading("关键实现：先入箱，再离开事务发布"),
    code("backend/apps/payments/services.py", receive_excerpt, "with transaction.atomic结束后，原Inbox已经独立提交。之后才调用发布器或同步处理器；Event原文变化则409。这个片段省略了前面的安全去重键构造，并没有省略完整业务校验。"),
    heading("189元订单的前后值"),
    table(["时刻", "订单/付款", "可售/预占", "副作用"], [
        ["下单后", "PENDING_PAYMENT / 尚未付款", "19 / 1", "RESERVE 1"],
        ["保存成功通知但未处理", "PENDING_PAYMENT / 原Attempt仍活跃", "19 / 1", "Inbox已保存；不能提前COMMIT"],
        ["可信成功业务提交", "PAID / SUCCEEDED", "19 / 0", "COMMIT 1，Refund 0"],
        ["重复任务处理后", "PAID / SUCCEEDED", "19 / 0", "COMMIT仍1；Event次数不再增加"],
    ]),
    p("替代方案是在同一HTTP请求内同步处理全部事件。它更容易调试，项目保留这条MySQL/Mock基线；异步把业务处理交给Worker，必须额外考虑消息漏投、重复和进程中断。MQ并没有替代原状态机。"),
    evidence(["docs/verification/phase-5c1-payment-mq.md", "docs/verification/phase-5c1-payment-mq-data.json"], "5C1历史真实隔离RabbitMQ/独立Worker：最终原始记录3 passed、44.27秒，覆盖业务执行、重复任务和伪造通知；C4期另有C1回归3 passed、41.98秒，属于不同执行。", "本次没有启动Worker。网络失败可能留下已保存但未发布事件，需要05页人工补投；无生产吞吐、第三方对账或浏览器全路径重验结论。"),
    question([["为什么不能锁着订单等Gateway？", "外部网络可能超时，长期占锁阻塞别的订单操作；先持久提交，再等待网络。"], ["MySQL和MQ怎样做到一致？", "先可靠入箱再发原ID，并接受提交到发布之间的窗口，用Inbox人工补漏。"]]),
]

body[3] = [
    remind("付款请求超时，只能推断“本地不知道”。保留原Order、原Attempt、原189元金额，查询原供应商引用；不能直接重新付款。"),
    p("目标：分清浏览器超时、网关超时与业务失败，能解释为什么查原付款。前置：认识01页Attempt.UNKNOWN；本页没有新建订单、修改商品价格或真实扣款操作。"),
    heading("具体场景：供应商成功了，响应没回来"),
    p("顾客点189元付款。后端保存了Attempt A并调用Gateway；Gateway可能已经成功，但响应因网络中断丢失。若后端把A当失败并允许B，顾客可能付两次。若这时商品改价209元，创建新订单还会让原交易与新报价混在一起。"),
    p("现有方案把仍活跃的CREATED/PENDING标UNKNOWN，保持原Attempt活跃。更换Idempotency-Key也不能绕过活跃尝试；如果并发成功通知已提交，mark_attempt_unknown锁内检查避免把SUCCEEDED倒退。"),
    board("图4：UNKNOWN查原付款。沿原Attempt和provider_reference查Gateway；结果继续进同一Inbox。再次超时仍UNKNOWN，可信成功才PAID。"),
    heading("查单怎样运行"),
    steps([
        "顾客POST /api/payments/attempts/<原public_id>/reconcile/，后端Session/CSRF与用户归属检查继续生效。",
        "reconcile_payment_attempt限定public_id和user，读取原Attempt；终态直接返回，活跃状态才查供应商。",
        "Gateway.query_payment查询原provider_reference，事务外等网络；这不是发起新付款。",
        "TimeoutError/ConnectionError继续标UNKNOWN。没有可信结果时，不猜失败，不释放库存。",
        "有结果就receive_payment_event，继续原签名、金额、币种和状态校验；查询没有第二套状态转换。",
        "同步时可读到本次提交结果；异步时Inbox刚入箱，Attempt仍可能UNKNOWN，需要随后再读取。",
    ]),
    heading("真实源码：查单只接收原Attempt"),
    code("backend/apps/payments/services.py", source_function("backend/apps/payments/services.py", "reconcile_payment_attempt"), "filter同时限定付款public_id和当前用户，UUID不是权限凭据。查询原付款获得的原文交给同一个receive_payment_event；refresh_from_db读取当时已提交事实，不保证异步Worker已经完成。"),
    table(["查单返回", "本地变化", "库存与顾客下一步"], [
        ["再次网络超时", "UNKNOWN保持", "19/1；继续查原付款，不创建新订单"],
        ["可信PENDING", "原Attempt变PENDING，通知处理完成", "19/1；等待或再查"],
        ["可信SUCCEEDED且订单可确认", "原Attempt成功、Order.PAID", "19/0；只核销一次"],
        ["可信FAILED", "原Attempt失败，释放活跃marker", "此通知本身不自动把Order判失败或直接释放；后续关闭/合法重试仍检查规则"],
        ["错误金额/币种/签名", "原事件REJECTED", "保持原业务状态，供排查"],
    ]),
    p("替代方案是“超时就标失败并重付”，实现短但会把网络未知当业务失败，破坏资金和库存边界；本项目不采用。查询也可能持续失败，因此UNKNOWN会占用库存超过普通15分钟期限，需人工查清原付款。"),
    p("Mock特例：本地供应商记录缺失时可以为同一Attempt补建测试替身结果。它不代表真实供应商能安全再次扣款，不能把Mock实现直接换成生产支付请求。"),
    evidence(["backend/apps/payments/tests/test_payment_flow.py", "docs/verification/mvp-4-mock-payment.md", "docs/verification/phase-5d-payment-expiration-concurrency.md"], "UNKNOWN与原Attempt查单有项目规则测试；旧MVP验证明确记录SQLite烟测与当时MySQL待验证。5D后续真实MySQL六例包含UNKNOWN阻止到期错误释放，不能反推本次重新验证了第三方查询。", "本次仅核对当前函数与历史材料；没有真实支付供应商、自动对账SLA、无限查单或真钱重付。"),
    question([["UNKNOWN是否需要回滚原订单？", "不能靠网络超时判断资金结果；保持原订单与预占，查原Attempt。"], ["查单成功但前端仍显示UNKNOWN呢？", "异步Inbox处理可能尚未提交；再次读后端状态，不能先乐观显示PAID。"]]),
]

match_excerpt = source_slice("backend/apps/payments/services.py", "    if (", "        return _reject(event, \"payment_mismatch\")")
# Select the match condition after Decimal conversion rather than an earlier unrelated if.
payment_text = (ROOT / "backend/apps/payments/services.py").read_text(encoding="utf-8").splitlines()
match_end = next(i for i, line in enumerate(payment_text) if line == '        return _reject(event, "payment_mismatch")')
match_start = max(i for i in range(match_end) if payment_text[i] == "    if (")
match_excerpt = ("\n".join(payment_text[match_start : match_end + 1]), match_start + 1, match_end + 1)

body[4] = [
    remind("“重复”可以安全短路；“同编号异内容”“伪造金额”“终态矛盾”必须拒绝。幂等不是把任何输入都当成功。"),
    p("目标：用真实例子区分重放、乱序与业务拒绝，能说明库存最多一次如何由多层保护共同完成。前置：已读正常付款与两个状态机。"),
    heading("为什么消息可能重复"),
    p("HTTP响应丢失会让供应商重发；RabbitMQ未收到ACK会重投；恢复人员可能补投原ID。因此消息不是恰好出现一次。Idempotency（幂等）在这里指同一合法业务再次到来，最终事实和库存/退款副作用不增加。"),
    table(["具体输入", "正确处理", "189元订单后果"], [
        ["同顾客同付款Key、同订单", "返回原Attempt", "不会新建第二次付款"],
        ["同Key换另一订单", "409 idempotency_conflict", "不把旧请求身份用于新交易"],
        ["同(provider,event_id)同原文重发", "复用Inbox；终态短路", "库存核销仍1，处理预算不重复花"],
        ["同事件号但原文改变", "409 event_id_conflict", "不覆盖保存的原文"],
        ["不同事件号、相同成功事实", "检查原Attempt/Order终态", "不会第二次COMMIT"],
        ["成功后迟到FAILED/PENDING", "REJECTED terminal_attempt_conflict", "SUCCEEDED不倒退，PAID保留"],
        ["FAILED后订单仍待付却收到矛盾成功", "当前规则REJECTED terminal_attempt_conflict", "不擅自重新打开付款；已关闭迟到成功另见06"],
    ]),
    heading("验签、匹配、锁与约束共同保护"),
    steps([
        "入口预验签只决定去重身份。无效签名产生内部invalid摘要号，保留审计输入，不抢占合法事件号。",
        "业务处理重新验签与解析，找到原provider_reference对应Attempt；未知引用不能改订单。",
        "锁住Order和原Attempt，核对供应商、原订单public_id、金额有限值与189.00、币种CNY。",
        "根据原终态和新结果判断合法分支，再核销或记录模拟退款；更新事件处理结果同事务提交。",
        "Inbox唯一键、Attempt活跃唯一键、Movement同类唯一关系、Refund一对一兜底；跨表判断仍由Service维护。",
    ]),
    heading("真实源码：可信签名之后仍要匹配交易"),
    code("backend/apps/payments/services.py", match_excerpt, "amount此前由Decimal解析。签名正确也不能跳过供应商、订单、金额和币种匹配；有限值检查拒绝NaN/Infinity。既核对order.total又核对attempt.amount，避免确认另一笔交易。"),
    p("替代方案只靠Event唯一ID，能防同编号重复入箱，却挡不住不同编号重复成功、成功后迟到失败和错误交易关联。现有方案保留多层校验；代价是规则较多，须用明确场景逐项测试，而不是靠一个万能“幂等锁”。"),
    heading("业务拒绝和系统错误不要混在一起"),
    table(["类别", "例子", "恢复策略"], [
        ["REJECTED", "签名不合法、payment_mismatch、终态矛盾", "保留原文和稳定错误码；不自动重新尝试非法事件"],
        ["ERROR/TRANSIENT", "MySQL1205锁等待超时、1213死锁", "事务回滚后保留次数；到期且预算允许时恢复"],
        ["ERROR/BLOCKED", "库存不变量被破坏、未知异常", "调查根因，不自动强改库存或重置预算"],
        ["ERROR/EXHAUSTED", "已用完3次合法处理资格", "人工调查；没有第四次自动领取"],
    ]),
    evidence(["docs/verification/phase-5c1-payment-mq.md", "docs/verification/phase-5b-payment-event-retry.md", "backend/apps/payments/tests/test_payment_flow.py"], "C1真实MQ实验覆盖重复任务与伪造事件；5B覆盖业务拒绝与处理失败分流、并发资格与预算。精确场景与断言在测试源码和阶段记录。", "拒绝码是程序/审计标识。不要把所有REJECTED叫作“钱没付”；也不要凭更换事件号、Key或人工强制状态绕过终态与预算。"),
    question([["消息至少一次，业务能否只一次？", "靠持久原文、锁内资格、事务和副作用约束，让重复不改变终值；消息仍可重复。"], ["签名正确为何还核对金额？", "签名证明来源与内容完整，不证明这份结果属于当前189元交易。"]]),
]

body[5] = [
    remind("RabbitMQ传任务，Celery执行任务，MySQL保存事实与预算。先入箱不代表已经投递；已投递不代表已经完成。"),
    p("目标：区分提交后漏投、可重试数据库错误、Worker硬中断与提交后未ACK四个窗口，能按原Inbox ID说明恢复。前置：已认识正常付款T1/T2与Event状态。当前选择人工兜底，未实现周期自动接管。"),
    heading("为什么接MQ之后仍需要Inbox"),
    p("189元成功通知已经保存到MySQL，进程可能在发RabbitMQ前崩溃，或者Broker正停机。数据库提交和消息发布在两个系统，不能默认原子完成。Inbox让恢复人员知道“还有哪份原文未处理”，MQ自身不能发现漏投数据库记录。"),
    board("图5：MQ故障与人工恢复。分开展示漏投、暂态失败、租约到期与提交后未ACK；修复元数据后再补投原ID。"),
    table(["窗口", "MySQL持久事实", "恢复动作", "不能做什么"], [
        ["入箱后发布失败/进程消失", "RECEIVED或到期TRANSIENT，原文还在", "Broker恢复后人工--publish原ID", "不能新建付款或声称自动秒级恢复"],
        ["T2锁超时/死锁回滚", "T1次数保留；T3记录ERROR/TRANSIENT", "Worker按ETA重发合格原ID，或人工补投", "不能因事务回滚把次数退回0"],
        ["T1后硬中断、T2未提交", "PROCESSING与租约；业务仍原状态", "到期后人工--recover-stale，再--publish", "不能见PROCESSING就强改，不能抢持锁T2"],
        ["T2已提交、ACK前中断", "PROCESSED、PAID、核销已存在", "原消息重投，终态短路后ACK", "不能第二次核销、退款或增加count"],
        ["BLOCKED或预算耗尽", "原文与稳定失败信息保留", "人工调查", "没有无限重试/默认清预算"],
    ]),
    heading("T1/T2/T3与持久预算"),
    p("T1是短领取事务：锁Event、检查资格、count+1、写PROCESSING与租约并提交。T2是业务事务：再次锁Event，核对本次count版本，再统一处理。可捕获异常时，T2回滚后T3独立记录失败。硬中断不会跑except/T3，因此需要租约提供恢复候选。"),
    p("预算最多3次，包括首次；暂态故障后下一次资格门槛分别5秒、10秒。ETA是可以执行的时间门槛，不保证准时恢复。重复/提前消息不一定有领取资格，因此消息数和处理次数不相等。T1已提交后，即使T2没开始，也已经消耗一次。"),
    code("backend/apps/payments/services.py", source_function("backend/apps/payments/services.py", "_process_claimed_payment_event"), "旧Worker持有第1次资格，新一轮已变count=2或状态已修复时，锁内复查挡住旧执行者。业务处理调用原process_payment_event，没有恢复专用的绕过校验路径。成功/拒绝清租约截止，最后开始时间留作调查。"),
    heading("租约到期为什么仍不能直接接管"),
    p("Lease（处理租约）记录本轮何时开始、何时可考虑检查。默认60秒，C4实验8秒；它不是进程死亡证明、Celery超时设置或订单15分钟库存期限。人工恢复用skip_locked跳过正持Event锁的T2，再核对原status/count/deadline快照，避免覆盖新版本。"),
    table(["锁内结果", "动作"], [
        ["持锁、未到期、状态/版本变化、空租约或时间异常", "跳过并保留调查信息"],
        ["同一过期PROCESSING，count1或2", "改ERROR/TRANSIENT，next_retry_at=修复时刻；原次数保留"],
        ["同一过期PROCESSING，count3", "改ERROR/EXHAUSTED，不安排第四次"],
    ]),
    heading("人工操作SOP：预览、修复、补投、核对"),
    steps([
        "先确定专用test环境、test_project_one、真实测试Broker/队列，读取阶段指南；不要直接对开发或真实数据运行。",
        "运行原命令--recover-stale --dry-run查看候选；空租约、BLOCKED、EXHAUSTED先调查。",
        "租约到期后显式--recover-stale，只修复Event元数据，不付款、不发布。该动作会修改测试数据库。",
        "然后--publish --dry-run检查合格原ID，再--publish把原ID交给Worker。--recover-stale与--publish互斥，需要分两步。",
        "检查Order/Attempt/Event/Reservation/Movement/Refund终值；published表示发布成功，不能代替业务正确。保留原未ACK任务用于重投证据，不擅自清队列。",
    ]),
    p("确切环境变量、执行命令和清理边界以docs/learning/phase-5c3-payment-publication-recovery-study-guide.md与phase-5c4-payment-worker-recovery-study-guide.md为准。本页SOP解释动作，不冒充本次已经执行。"),
    p("替代方案：增加Beat周期扫描或Transactional Outbox可提高自动恢复/可靠发布能力，但引入调度、发布记录与运行边界。本期选择已有持久Inbox加人工恢复，仍接受人工发现和操作延迟；MQ必选不等于自动化必选。"),
    evidence(["docs/verification/phase-5c2-payment-mq-retry.md", "docs/verification/phase-5c3-payment-publication-recovery.md", "docs/verification/phase-5c4-payment-worker-recovery.md"], "C2最终原始真实MQ三例3 passed、70.58秒；C3两例2 passed、71.69秒；C4四个真实SIGKILL场景4 passed、81.71秒。C4观察原任务UUID重投，最终核销/模拟退款保持一次。", "实验是专用容器、单Worker与合成事件；本次没有重跑。无无人值守恢复时限、心跳、多机容灾、生产性能或全部进程排列保证。"),
    question([["有late ACK为什么还加租约？", "MQ能重投，但MySQL PROCESSING仍挡住领取；租约加锁内版本检查让合格旧领取可人工修复。"], ["租约60秒到了说明什么？", "仅进入候选，仍需跳过持锁事务并核对原版本；不证明进程死亡。"]]),
]

body[6] = [
    remind("先拿到Order锁，不代表一定有资格关单。CREATED/PENDING/UNKNOWN阻止到期关闭；已经合法关闭后的迟到成功，则保留终态并最多模拟退款一次。"),
    p("目标：分清“两个流程竞争”与“关闭后迟到成功”两个分支，说明库存核销、释放与恢复如何避免重复。前置：认识Order、Attempt及Reservation状态。示例仍为189元、20件库存买1件。"),
    heading("分支一：付款成功与到期同时运行"),
    p("支付处理和expire_pending_order都先锁同一Order行；锁让它们按先后读取与改变事实。拿到锁后必须重新判断订单和付款状态。时间到期字段只是业务时间点，不会自动释放；未确认付款可能使预占超过15分钟。"),
    board("图6：两种Order获锁顺序，外加已关闭后迟到成功。注意到期先拿锁仍可能payment_unresolved；"),
    table(["先获锁", "锁内看到什么", "该流程动作", "最终可信成功后"], [
        ["支付", "订单待付，原Attempt可确认", "原子确认PAID并核销，释放锁", "到期随后读PAID，无操作；库存19/0"],
        ["到期", "原Attempt为CREATED/PENDING/UNKNOWN", "payment_unresolved，保持订单与预占", "支付随后PAID，只一次COMMIT，库存19/0"],
    ]),
    heading("真实源码：拿锁后重新检查资格"),
    code("backend/apps/ordering/lifecycle_services.py", source_function("backend/apps/ordering/lifecycle_services.py", "expire_pending_order"), "select_for_update锁同一订单。非待付或没到期直接无操作；最后调用_close_pending_order_locked，它会锁原Attempt并拒绝未确认付款，随后才验证预占与释放库存。锁只提供串行检查，业务规则决定能不能关。"),
    p("只有Barrier同时起跑的替代测试更简单，但可能没有实际数据库竞争。本项目5D把先获锁线程暂停在真实SELECT FOR UPDATE返回后，另一线程执行原SQL，再从performance_schema观察同订单PRIMARY记录WAITING/GRANTED，随后放行验证终值。未替换业务返回或用sleep猜竞争。"),
    heading("分支二：已经合法关闭，再收到成功"),
    p("例如原Attempt已有可信FAILED，订单随后合法EXPIRED并释放库存到20/0；后来供应商又送来可信SUCCEEDED。现有特殊分支将原Attempt确认成功，Order保持EXPIRED，Reservation保持RELEASED，创建一次LATE_SUCCESS RefundSimulation；不重新打开订单，不再核销或释放库存。"),
    table(["实际状态", "正确副作用", "库存/退款"], [
        ["待付且未确认付款", "拒绝关闭，继续查原付款", "19/1；Refund0"],
        ["无未确认付款且合法关闭", "原预占最多释放一次", "20/0；RELEASE1"],
        ["关闭后可信迟到成功", "订单终态保留；原Attempt成功；模拟退款一次", "20/0；COMMIT0；Refund1"],
        ["已支付未发货主动取消", "恢复原核销库存；PAID_CANCEL模拟退款", "available恢复；RESTORE1；Refund1"],
        ["已发货或完成取消", "拒绝简单取消", "不进入复杂售后"],
    ]),
    p("别把“FAILED后迟到成功”概括成总能接受：FAILED且订单仍PENDING_PAYMENT时，当前实现拒绝terminal_attempt_conflict；合法CANCELED/EXPIRED后的SUCCEEDED才走迟到退款分支。六个5D锁竞争场景只涉及CREATED/PENDING/UNKNOWN，不覆盖所有终态冲突排列。"),
    p("RefundSimulation使用原Attempt一对一兜底，同业务事务提交；它只记录模拟退款事实，不表示真实钱已返回。替代方案强行恢复EXPIRED为PAID会重新占用可能已卖出的库存，并破坏订单终态，本项目不采用。"),
    evidence(["docs/verification/phase-5d-payment-expiration-concurrency.md", "docs/verification/phase-5d-payment-expiration-concurrency-data.json", "docs/verification/phase-2b-refund-simulation.md", "docs/verification/phase-5c4-payment-worker-recovery.md"], "5D两种Order获锁顺序×三种未确认状态，共6 passed、11.47秒；每例捕获真实记录锁等待，最终PAID/SUCCEEDED/PROCESSED，COMMIT1、RELEASE0、Refund0。C4迟到成功退款提交后ACK前中断场景重投仍Refund1。", "5D不是秒杀压测，也不证明所有死锁排列；不支持“到期先拿锁总是关单”。本期接受未确认付款延长预占，需要查清原付款。"),
    question([["到期先拿锁为什么没关单？", "原付款未确认，不满足关闭资格；行锁不能替代业务判断。"], ["关闭后支付成功为什么不恢复订单？", "预占可能已释放给别人，保留终态并幂等模拟退款，避免重开交易影响库存。"]]),
]

body[7] = [
    remind("每一句面试主张都要对应真实场景、运行条件、终值和剩余限制。测试通过数量不是QPS；历史通过不是本次重跑。"),
    p("目标：找到该看哪份证据，能以业务问题→观察限制→方案取舍→实际验证→剩余代价讲支付主线。前置：已经能读懂正常付款和四种故障窗口；本页是证据导航，不替代阶段实验原文。"),
    heading("历史证据卡：按问题检索"),
    table(["阶段/日期", "真实结果", "支持的主张", "原始入口"], [
        ["5A / 2026-10-01", "12 passed、5 subtests；10.75秒", "原Inbox人工恢复、两个真实MySQL并发扫描", "phase-5a-payment-inbox-recovery.md"],
        ["5B / 2026-10-01", "26 passed、12 subtests；18.08秒", "持久预算、到期门槛、受控死锁次数保留", "phase-5b-payment-event-retry.md"],
        ["5C1 / 2026-10-01", "3 passed；44.27秒", "真实Broker/独立Worker、重复与伪造", "phase-5c1-payment-mq.md / -data.json"],
        ["5C2 / 2026-10-04", "3 passed；70.58秒", "真实1205故障、TRANSIENT/ BLOCKED/耗尽", "phase-5c2-payment-mq-retry.md / -data.json"],
        ["5C3 / 2026-10-05", "2 passed；71.69秒", "Broker首次/重试漏投，人工补发布原ID", "phase-5c3-payment-publication-recovery.md / -data.json"],
        ["5C4 / 2026-10-05", "4 passed；81.71秒", "T1后、T2未提交、支付/退款提交后未ACK的SIGKILL", "phase-5c4-payment-worker-recovery.md / -data.json"],
        ["5D / 2026-10-05", "6 passed；11.47秒", "两种Order锁顺序×三个未确认状态，真实锁等待", "phase-5d-payment-expiration-concurrency.md / -data.json"],
    ]),
    p("上述相对文件统一位于docs/verification/。5D当轮支付与订单回归157 passed、12 skipped、83 subtests、100.14秒；12项MQ组当轮未配置，不能算当轮通过。C4时独立重跑C1/C2/C3分别3/3/2通过，再加C4四项共12项真实MQ，但属于不同命令，不拼成一次执行。"),
    heading("失败过程也属于证据"),
    table(["真实问题", "调整与最后证据", "能学到什么"], [
        ["C2早到重复消息在定时边界提前触发", "补充资格门槛观察并修正最早ETA；最终独立场景三例通过", "用数据库领取时间证明门槛，不能只看任务结束时间"],
        ["C3原进程保留已关闭Broker连接导致补投复查失败", "按真实CLI使用新进程补投，原记录保留；最终两例通过", "复现环境与执行方式必须明确，不篡改历史失败"],
        ["C4第一次退款准备直接取消尚未确认的CREATED被拒绝", "先可信FAILED再合法取消，最后注入迟到SUCCEEDED；四例通过", "遵守原业务规则，不改预期绕过payment_unresolved"],
        ["5D应用账号看诊断表1142、连接未建测试库1049", "同一MySQL管理观察连接只读诊断；没有GRANT或读开发数据", "工具失败与业务失败分开解释，不能伪造锁等待"],
    ]),
    heading("复现实验前的最小SOP"),
    steps([
        "读git status、Git基点和本地增量；仅检出2dacc9a不足以复现C4/5D，当前材料包含尚未提交变化。",
        "按对应学习指南检查test_project_one、127.0.0.1:3307，以及专用RabbitMQ vhost/队列/Worker；不要并行共享测试库的pytest进程。",
        "明确命令将建/迁移/清理测试库、终止专用Worker或清理专用队列的影响；C4要保留原未ACK消息，不能套用C3清队列。",
        "分别运行纯规则、MySQL事务与真实MQ场景；开启对应显式开关。eager/mocks不替代实际Broker和进程中断。",
        "保存原输出、日期、版本、终值与原始数据；失败先解释第一个错误。核对业务副作用，不拿任务执行次数替代正确性。",
        "按指南收尾测试组件；报告passed/skipped和未验证项；更新证据卡和问题页。",
    ]),
    p("本次仅做知识库材料、源码摘录和图的核对。没有创建测试数据库、运行pytest、补投事件、停止Worker或操作开发库。下一次复现必须依对应阶段操作说明重新建立隔离环境。"),
    heading("两分钟面试口述参考"),
    p("我的单商家Django项目使用MySQL保存交易事实。189元订单先预占库存；支付先保存原Attempt，事务外调用Mock，网络超时保持UNKNOWN并查询原引用。通知先持久入Inbox，异步Worker独立提交处理预算，再把支付、订单、库存与事件结果一起提交。唯一键、状态检查与行锁让重复消息不产生第二次副作用。我用真实隔离RabbitMQ/Worker验证重复、锁超时、漏投和进程中断，用MySQL两种Order锁顺序×三种未确认状态，共六场景验证到期不会误释放。本期人工修复与补投，没有无人值守恢复时限和真实资金接入。"),
    p("这段是口述参考，不是“你已经掌握”的结论。练习时拿原189元例子，解释每个状态与提交点，再把一个失败场景讲完整。"),
    question([
        ["为什么Order、Attempt、Event分开？", "交易、一次付款、收到结果的处理进度分别是不同事实。"],
        ["UNKNOWN与ERROR区别？", "UNKNOWN缺供应商结果，查原付款；ERROR有原文但处理失败，恢复原Inbox。"],
        ["有MQ为何需要Inbox？", "MySQL提交到发布前会崩溃，Broker也会停；原文持久保存才能补漏。"],
        ["为什么预算先独立提交？", "T2回滚或硬中断不应清零已消耗资格，避免无限重试。"],
        ["租约过期就抢任务吗？", "不是死亡证明，要跳过持锁事务并核对原count/deadline。"],
        ["凭什么说库存只核销一次？", "检查Order/Attempt/Event终值、Reservation、COMMIT流水与重复后快照；真实实验支持特定场景。"],
        ["支付目前最大代价是什么？", "UNKNOWN继续占库存，人工恢复依赖发现与操作；模拟供应商、本地单Worker证据范围有限。"],
    ]),
    heading("知识库与源码维护"),
    p("本模块在Project One Wiki下独立组织；复用已有Redis学习与面试总结，秒杀/并发是邻接模块。页面正文讲问题，阶段指南讲操作，verification讲真实证据；通过链接关联，避免在八页里复制整套代码与日志。"),
    p("更新支付规则时，同步核对图、摘录、前后值和证据边界。源码链接若只指向旧main，必须注明不包含当前未提交增量；后续提交后再更新可复现revision，不能虚构线上结果。"),
]


stats = []
for item in PAGE_MAP:
    index = item["page_index"]
    xml = f"<title>{esc(item['title'])}</title>\n" + "\n".join(body[index] + [footer()]) + "\n"
    node = ET.fromstring("<document>" + xml + "</document>")
    assert len(node.findall("title")) == 1
    assert len(node.findall("whiteboard")) == int(item["whiteboard"])
    for heading_node in node.findall("h1"):
        assert heading_node.attrib == {"seq": "auto"}
    path = Path(item["cwd"]) / item["draft_path"]
    path.write_text(xml, encoding="utf-8")
    text = "".join(node.itertext())
    stats.append({"index": index, "title": item["title"], "path": str(path), "char_count": len(text), "cjk_count": len(re.findall(r"[\u3400-\u9fff]", text)), "board_count": len(node.findall("whiteboard")), "code_count": len(node.findall("pre"))})

(PAGES / "source-excerpts.json").write_text(json.dumps(SOURCE_RECORDS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(PAGES / "local-check.json").write_text(json.dumps({"status": "passed", "scope": "XML syntax, title/headings, board mapping and actual source extraction only; no app tests or remote writes", "pages": stats, "source_excerpts": SOURCE_RECORDS}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"pages": stats, "total_cjk": sum(item["cjk_count"] for item in stats), "total_chars": sum(item["char_count"] for item in stats), "code_excerpt_count": len(SOURCE_RECORDS)}, ensure_ascii=True, indent=2))
