"""Plan minimal edits from fresh snapshots, preserving the six legacy boards."""
from pathlib import Path
import copy
import html
import json
import xml.etree.ElementTree as ET
from new_article import BASE, ROOT, SOURCE, read

LUA_URL='https://ocnbkcx1ug58.feishu.cn/wiki/'+read(BASE/'created.json')['node_token']
SEARCH_URL='https://ocnbkcx1ug58.feishu.cn/wiki/KxWQwuGFsiqO3Wk4m7TcZxXBnih'
EVIDENCE='https://github.com/jasper507/E-commerce/blob/'+SOURCE+'/docs/verification/phase-6f-flash-sale-admission.md'
RECORD='https://github.com/jasper507/E-commerce/blob/main/docs/verification/flash-sale-lua-knowledge-base-update.md'


def text(node):
    return ''.join(node.itertext())


def p(value):
    return '<p>'+value+'</p>'


def a(url,label):
    return '<a href="'+html.escape(url,quote=True)+'">'+html.escape(label)+'</a>'


def without_ids(node):
    result=copy.deepcopy(node)
    for child in result.iter():
        child.attrib.pop('id',None)
    return result


class Page:
    def __init__(self,info):
        self.info=info
        self.before=read(BASE/'before'/(info['key']+'.json'))['data']['document']
        self.root=ET.fromstring('<doc>'+self.before['content']+'</doc>')
        self.expected=copy.deepcopy(self.root)
        self.ops=[]

    def find(self,tag,prefix):
        matches=[n for n in self.root if n.tag==tag and text(n).startswith(prefix)]
        assert len(matches)==1,(self.info['key'],tag,prefix,len(matches))
        return matches[0]

    def replace(self,old,xml,label):
        assert old.tag not in ('title','whiteboard','img','source')
        target=next(n for n in self.expected if n.get('id')==old.get('id'))
        index=list(self.expected).index(target)
        self.expected.remove(target)
        elements=list(ET.fromstring('<doc>'+xml+'</doc>'))
        assert len(elements)==1
        self.expected.insert(index,elements[0])
        self.ops.append({'command':'block_replace','block':old.get('id'),'content':xml,'label':label})

    def paragraph(self,prefix,value,label):
        self.replace(self.find('p',prefix),p(value),label)

    def table(self,prefix,edit,label):
        old=self.find('table',prefix)
        new=without_ids(old)
        edit(new)
        self.replace(old,ET.tostring(new,encoding='unicode'),label)

    def insert_after(self,old,xml,label):
        target=next(n for n in self.expected if n.get('id')==old.get('id'))
        index=list(self.expected).index(target)+1
        elements=list(ET.fromstring('<doc>'+xml+'</doc>'))
        for element in reversed(elements): self.expected.insert(index,element)
        self.ops.append({'command':'block_insert_after','block':old.get('id'),'content':xml,'label':label})


def new_row(values):
    row=ET.Element('tr')
    for value in values:
        cell=ET.SubElement(row,'td')
        cell.append(ET.fromstring(p(html.escape(value))))
    return row


def main():
    pages=[Page(info) for info in read(BASE/'existing-pages.json')]
    overview=pages[0]
    callout=without_ids(overview.find('callout','本版结论：'))
    callout.find('p').text='当前结论（2026-10-11）：MySQL事务提交订单、购买资格和库存，条件UPDATE处理库存竞争；Lua原子准入已实现/隔离验证，默认关闭。启用后原Key确认也先申请预算，满额429、未知503，恢复放行后再返回原单或异参409。ES已有独立搜索实现/隔离证据，不参与秒杀扣库存。'
    overview.replace(overview.find('callout','本版结论：'),ET.tostring(callout,encoding='unicode'),'current-status')
    overview.paragraph('适用读者：','适用读者：第一次独立学习交易并发的项目作者。以89元订单讲清规则、方案、替代与证据；先读总览，再按疑问进入七个问题页。原六页的18ffbe2历史代码/实验保留，当前更新以96904bc核对；新Lua流程单独说明启用条件。','reader')
    overview.paragraph('下图画的是当前默认同步Mock支付路径。','下图保留2026-10-08原交易画板，仍解释当前Lua默认关闭、Mock支付默认同步的MySQL路径。启用Lua时，准入发生在进入下单HTTP适配与MySQL事务之前；完整启用时序见'+a(LUA_URL,'07 Lua原子准入')+'。支付可启用既有MQ模式，仍共用业务处理器；89元与一件库存是教学例子。','legacy-board-context')
    def boundary(table):
        for row in table.findall('./tbody/tr'):
            cells=row.findall('td')
            if text(cells[0])=='原Key重复提交':
                cells[0].find('p').text='原Key重复提交（准入关闭或已放行）'
            if text(cells[0])=='点击前':
                cells[-1].find('p').text='前端提示不等于购买承诺；启用准入后预算也不是库存'
    overview.table('业务时刻',boundary,'trade-boundary')
    def modules(table):
        for row in table.findall('./tbody/tr'):
            cells=row.findall('td')
            if text(cells[0])=='Redis商品缓存':
                cells[0].find('p').text='Redis商品缓存 / Lua准入'
                cells[1].find('p').text='浏览副本与故障保护；启用Lua时共享秒杀尝试预算'
                cells[2].find('p').text='缓存与准入故障政策不同；Lua不扣库存，MySQL继续保存交易事实'
    overview.table('模块',modules,'responsibilities')
    lua_section='<h1 seq="auto">Lua前置准入：库存正确之外的入口保护</h1>'+p('Lua默认关闭，原MySQL交易继续可用。启用时的链路为：Session/CSRF及参数/Key格式 → Redis原子预算 → HTTP等待适配 → MySQL事务 → 恢复会话值 → HTTP结果。首次SET带PX，后续INCR不续期；所有有效POST共享入口Key，业务失败也不退预算。')+p('满额429、Redis结果无法确认503都不进入后面的下单处理；这包括已有订单的原Key确认。429/503不证明旧单不存在，前端保留原用户、Key、活动项与地址，恢复后仍确认原单。取得预算后，MySQL内幂等检查仍先于新单活动和库存检查；当前售罄不阻止已命中的旧单。')+p('6F真实24个Redis客户端竞争4预算，实际4放行/20拒绝；8线程处理24顾客的APIClient对照中，订单事务及建单24→4。被拒请求在固定认证范围0下单SQL，不是完整Session HTTP零SQL；两组建单数量不同，不能据此报告购买提速或生产吞吐。')+p('Redis可能已执行EVAL却响应丢失：不重试、不退未知预算、不进入本次MySQL下单。MySQL已提交却HTTP响应丢失则恢复确认原意图。Lua原子性不跨MySQL，窗口预算不代表严格在途数，也不代替购买资格。详见'+a(LUA_URL,'07完整问题页与时序图')+'及'+a(EVIDENCE,'6F历史验证')+'。')+'<h1 seq="auto">如果重新做，我会先明确旧单确认的可用性</h1>'+p('保留MySQL交易事实与Lua前置保护的分工。若业务要求旧单确认始终可用，先比较独立确认入口或先查原单的数据库成本；若需要平滑突发/严格在途，再分别评估令牌桶或带租约回收的并发槽。当前尚未实现这些候选，默认20次/1000ms不是测出的生产容量；现有证据够解释当前取舍就先收束。')
    overview.insert_after(overview.find('p','当前MQ承接支付事件'),lua_section,'lua-flow-and-redesign')
    overview.paragraph('关联知识：', '关联知识：'+a('https://ocnbkcx1ug58.feishu.cn/wiki/PCQ7wP9kMikz0eklmNYcH1J1nwh','支付总览')+'；'+a('https://ocnbkcx1ug58.feishu.cn/wiki/O4iUwSvl1i4Je7keOL0ci1iAnLf','Redis缓存')+'；'+a(SEARCH_URL,'商品搜索')+'；'+a('https://ocnbkcx1ug58.feishu.cn/wiki/WN1jwXDj0iPXm7kwLyHcmneenlb','项目总览')+'。','cross-links')
    def routes(table):
        for row in table.findall('./tbody/tr'):
            cells=row.findall('td')
            if text(cells[0])=='06 证据与候选':
                cells[0].find('p').text='06 证据与当前演进'
                cells[1].find('p').text='原MySQL证据与6F/搜索后续分开，真实、注入、静态与未验证分层'
        row=new_row(['07 Lua原子准入','共享原子预算、原Key先429/503、两个未知窗口、收益与可用性代价'])
        cell=row.find('td');cell.remove(cell.find('p'));cell.append(ET.fromstring(p(a(LUA_URL,'07 Lua原子准入'))))
        table.find('tbody').append(row)
    overview.table('问题页',routes,'reading-map')
    # List items are sibling blocks on the service; insert only one new item.
    old_list=next(n for n in overview.root if n.tag=='ul')
    last_li=list(old_list)[-1]
    new_li=ET.fromstring('<li>'+a(LUA_URL,'07 Lua原子准入：为什么先保护进入事务的尝试')+'</li>')
    list(overview.expected)[list(overview.root).index(old_list)].append(copy.deepcopy(new_li))
    overview.ops.append({'command':'block_insert_after','block':last_li.get('id'),'content':'<ul>'+ET.tostring(new_li,encoding='unicode')+'</ul>','label':'navigation-item'})
    overview.paragraph('维护：2026-10-08','维护更新：2026-10-11；当前源码96904bcc47562c51a1e4b9cc731f8ed5d40fdb75。原交易画板和6A–6E实验属于18ffbe2阶段快照，6F/搜索引用各自日期；支付退款为Mock模拟。这轮只更新知识库，不重跑应用测试、改开关或宣称个人已掌握。'+a(RECORD,'本轮发布与资源保护记录'),'maintenance')

    one=pages[1]
    one.insert_after(list(one.root)[1],p('当前衔接（2026-10-11）：本页解释已进入MySQL事务后的幂等与限购。Lua默认关闭；若启用，HTTP先申请共享预算，原Key也可能429/503，恢复放行后才执行这里的原单识别。详见'+a(LUA_URL,'07准入流程')+'。'),'outer-admission-context')
    two=pages[2]
    two.paragraph('最小充分证据已有：','MySQL策略选择所需的历史证据已足够，原对照日期和数据保留。后续6F已经加入默认关闭的Lua前置准入，并在隔离Redis/MySQL验证它限制进入事务的尝试；它不预扣库存、不改变这里的条件UPDATE，也不解决跨存储事务。'+a(LUA_URL,'Lua机制与代价')+'；'+a(EVIDENCE,'6F证据')+'。','later-lua-evidence')
    three=pages[3]
    three.paragraph('当前环境原会话值是 50 秒','6D-2历史环境观察到原会话值50秒；当前HTTP适配在每次请求读取原值，临时设SESSION为5秒，完整事务结束后恢复同一物理连接原值，恢复失败则丢弃连接。启用Lua时，先获准入预算才执行这些SQL；429/503拒绝不进入这里。5秒不是总HTTP期限，也没有修改全局配置。','wait-current-boundary')
    three.paragraph('正式路径没有自动重试。','正式路径没有自动重试。已知繁忙或响应丢失后仍用原Key重跑完整请求；前次回滚可以再建单，已有提交事实则返回原单。启用Lua时，重试与旧单确认也先准入，429/503保留原意图，恢复放行后才判断原订单，不能换Key猜测失败。只重跑库存SQL会遗漏活动、资格与幂等检查。'+a(LUA_URL,'准入的可用性代价'),'retry-policy')
    five=pages[5]
    five.paragraph('为什么列表已经无货仍能确认？','为什么列表已经无货仍能确认？在已经进入MySQL事务后，后端锁当前用户，先查同用户、秒杀scope、原Key订单；同摘要直接返回原单，再处理新购买的本人地址、活动和库存。因此活动结束、列表消失、地址变化或售罄不应由页面挡住原意图确认。启用Lua时外层HTTP先准入，原Key可以先429/503；它们不说明旧单不存在，恢复放行后仍走原单检查。原成交金额与地址快照不依赖今天地址簿重新创建。','replay-vs-admission')
    def browser_results(table):
        table.find('tbody').append(new_row(['Lua满额429 / 结果无法确认503','保留原用户、Key、活动项和地址，待恢复放行后确认原订单；不换Key、不把拒绝当作旧单不存在。当前未实现Retry-After倒计时，6F页面行为只静态核对。']))
    five.table('结果',browser_results,'429-503-intent')
    five.insert_after(five.find('p','Phase 6E 历史浏览器验收'),p('上述6E真实浏览器证据仍保留原日期。后来6F的429/503保留意图来自源码静态核对与APIClient恢复证据，未新增浏览器→真实HTTP→Redis→MySQL验收；两类证据不能合并成新端到端通过。'+a(LUA_URL,'07 Lua证据边界')+'。'),'browser-evidence-boundary')

    six=pages[6]
    six.paragraph('本页适用于读完秒杀主线','本页服务于把设计主张连接到实际代码和证据。当前已有MySQL条件UPDATE、极简顾客入口及默认关闭的Lua准入，6F已有隔离Redis/MySQL验证；搜索已推进到MySQL关键词、ES候选回读与手动别名切换。原6A–6E表格和18ffbe2阶段证据保留，各项新增证据使用自己的日期，不重跑旧实验。MySQL始终保存订单、库存与购买资格事实。','current-status')
    def evidence(table):
        table.find('tbody').append(new_row(['6F：Lua前置准入','24个Redis客户端竞争4预算→4放行/20拒绝；8线程处理24顾客的APIClient，事务与建单24→4','支持明确预算下减少下单尝试；原Key429/503后恢复同单','固定认证，非真实网络负载；成交产出不同，不证明吞吐/生产容量；浏览器只静态核对']))
    six.table('阶段 / 问题',evidence,'6f-evidence-row')
    six.replace(six.find('h1','Redis Lua：'),'<h1 seq="auto">Redis Lua：已实现的前置准入与代价</h1>','lua-heading')
    six.paragraph('Lua脚本可以原子组合Redis内部的多个操作。','6F已实现一个默认关闭、仅本地/测试的全入口固定持续窗口预算。Lua原子组合GET、校验、首次SET带PX和后续INCR；满额不递增/不续期，损坏状态或未知结果503，不擅自重置预算。它控制进入MySQL订单处理的尝试，失败不退预算，不扣库存、不保证严格在途数。'+a(LUA_URL,'07完整机制和图')+'；'+a(EVIDENCE,'6F真实验证')+'。','lua-implemented')
    six.paragraph('已有MySQL防超卖证据','已有MySQL防超卖机制，Lua增加入口保护。MySQL内原Key识别先于新单售罄检查；启用Lua的外层HTTP则让所有有效POST含原Key确认先申请预算，429/503后保持原意图，恢复放行后同参200、异参409。先查MySQL让旧单绕过预算是另一政策，会保留查询成本，当前没有采用。原子性只覆盖Redis，不跨MySQL；EVAL响应未知不自动重试、不退还预算。','old-key-policy')
    six.replace(six.find('h1','ES：'),'<h1 seq="auto">ES：已形成独立搜索专题，交易事实仍在MySQL</h1>','search-heading')
    six.paragraph('Elasticsearch（ES）服务全文检索','商品搜索已有MySQL名称/描述完整包含基线，ES可重建索引、候选ID回读MySQL当前可见性/摘要，以及手动别名切换与结果未知回读。独立ES接口默认关闭、仅指定测试来源，前端仍使用原MySQL列表；没有自动同步。它服务商品发现，不参与秒杀资格或库存扣减。'+a(SEARCH_URL,'搜索当前知识库')+'。','search-current')
    six.paragraph('若实际采用ES，要将商品索引视为可重建副本','当前ES已作为可重建查询副本：构建完成门槛、真实Bulk部分失败、陈旧候选回读、短空候选页、实际离线503、单操作者手动别名切换均有相应证据。MySQL回读不能补召回遗漏，旧在途读依赖旧精确索引保留，别名原子变更不等于跨存储事务或多人CAS。搜索速度/生产容量、自动同步和部署启用不在已验证范围。','search-evidence-boundary')
    six.paragraph('建议闭卷用一张89元订单复述：','复述完整请求时先区分Lua是否开启：保存原意图 → 身份/CSRF与格式 → 启用时申请共享预算 → HTTP临时等待适配 → MySQL原Key/资格/订单/库存条件UPDATE/提交 → 会话恢复 → HTTP结果 → 原意图确认 → 付款核销与关闭。解释一条429/503或提交后响应丢失，再指向对应证据。已有材料不替作者宣称独立掌握。','complete-flow')
    six.paragraph('维护：2026-10-08','维护更新：2026-10-11，当前源码96904bcc47562c51a1e4b9cc731f8ed5d40fdb75；原MySQL对照与六图属于18ffbe2阶段，后续6F/搜索各有日期。单商家/CNY/单活动SKU/每单一件，支付退款均Mock。这轮仅更新资料，未重跑业务或修改开关。'+a(RECORD,'本轮核对记录'),'maintenance')

    candidates=BASE/'candidates';candidates.mkdir(exist_ok=True)
    plans=[]
    for page in pages:
        operations=[]
        for index,op in enumerate(page.ops):
            path='candidates/'+page.info['key']+'-'+str(index)+'.xml'
            (BASE/path).write_text(op.pop('content')+'\n',encoding='utf-8')
            operations.append(dict(op,path=path))
        (candidates/(page.info['key']+'-expected.xml')).write_text(ET.tostring(page.expected,encoding='unicode'),encoding='utf-8')
        plans.append(dict(page.info,base_revision=page.before['revision_id'],operations=operations,protected_boards=[{'id':n.get('id'),'token':n.get('token')} for n in page.root.iter('whiteboard')]))
    (BASE/'patch-plan.json').write_text(json.dumps(plans,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'existing_pages':len(pages),'modified_pages':sum(bool(p['operations']) for p in plans),'block_operations':sum(len(p['operations']) for p in plans),'preserved_boards':sum(len(p['protected_boards']) for p in plans)}))


if __name__=='__main__':main()
