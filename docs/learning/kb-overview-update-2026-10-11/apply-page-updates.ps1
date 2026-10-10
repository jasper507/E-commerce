param([switch]$SkipProjectAddition)
$ErrorActionPreference = 'Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER = '1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER = '1'
$journalPath = 'docs/learning/kb-overview-update-2026-10-11'

function Invoke-LarkCapture([string]$name, [string[]]$arguments) {
    $raw = & lark-cli @arguments
    if ($LASTEXITCODE -ne 0) { throw "CLI failed: $name" }
    $result = ($raw -join "`n") | ConvertFrom-Json
    ($raw -join "`n") | Set-Content -LiteralPath "$journalPath/$name.json" -Encoding utf8
    if ($result.ok -ne $true) { throw "Non-success: $name" }
    return $result
}

function Update-Block([string]$doc, [string]$name, [string]$match, [string]$content, [string]$command = 'block_replace') {
    $before = Invoke-LarkCapture "$name-before" @('docs', '+fetch', '--doc', $doc, '--detail', 'with-ids', '--as', 'user', '--format', 'json')
    [xml]$xml = '<doc>' + $before.data.document.content + '</doc>'
    $matches = @($xml.SelectNodes('//*[@id]') | Where-Object { $_.InnerText -eq $match })
    if ($matches.Count -ne 1) { throw "Expected one current block: $name" }
    if ($command -eq 'block_insert_after' -and $before.data.document.content.Contains('从点击付款到库存核销') -and $name -eq 'payment-addition') {
        throw 'Payment addition already exists; review rather than append again'
    }
    if ($command -eq 'block_insert_after' -and $before.data.document.content.Contains('六、怎样阅读这套知识库') -and $name -eq 'project-addition') {
        throw 'Project addition already exists; review rather than append again'
    }
    $result = Invoke-LarkCapture "$name-write" @('docs', '+update', '--doc', $doc, '--as', 'user', '--command', $command,
        '--block-id', $matches[0].GetAttribute('id'), '--revision-id', [string]$before.data.document.revision_id,
        '--doc-format', 'xml', '--content', $content, '--format', 'json')
    if ($result.data.result -ne 'success' -or @($result.data.warnings).Count -ne 0) { throw "Write requires review: $name" }
    $after = Invoke-LarkCapture "$name-after" @('docs', '+fetch', '--doc', $doc, '--detail', 'with-ids', '--as', 'user', '--format', 'json')
    [pscustomobject]@{ operation = $name; result = $result.data.result; revision = $after.data.document.revision_id }
}

if (-not $SkipProjectAddition) {
    Update-Block 'P0LPd9AkmoAhq8xCgOdcqEL2nag' 'project-addition' 'Project One 电商项目的学习资料按模块归档。通过下面的入口阅读业务流程、核心代码、操作指南、实验记录和面试问题。' '@docs/learning/kb-overview-update-2026-10-11/project-body.xml' 'block_insert_after'
}
Update-Block 'P0LPd9AkmoAhq8xCgOdcqEL2nag' 'project-intro' 'Project One 电商项目的学习资料按模块归档。通过下面的入口阅读业务流程、核心代码、操作指南、实验记录和面试问题。' '<p>Project One 当前项目入口：先看交易闭环，再按业务问题进入模块。2026-10-11按源码4673b9f、项目总纲v1.15核对；原模块链接和历史证据保留，本轮没有新增业务实现。</p>'
Update-Block 'P0LPd9AkmoAhq8xCgOdcqEL2nag' 'project-new-board' '交易闭环：待支付预占、可信支付核销、未确认结果保留原意图。' '<whiteboard type="blank"></whiteboard>' 'block_insert_after'

Update-Block 'T6O0deWUnowaBsxM6LgcJfuynjb' 'payment-addition' '可信成功通知处理完后，PaymentAttempt变SUCCEEDED，Order变PAID，Reservation变COMMITTED，库存变19、0。COMMIT流水只有1笔。之后Admin模拟发货、顾客确认收货，完成最小商业闭环。' '@docs/learning/kb-overview-update-2026-10-11/payment-wire.xml' 'block_insert_after'
Update-Block 'T6O0deWUnowaBsxM6LgcJfuynjb' 'payment-intro' '适合谁：第一次接触支付、已知道Django请求和MySQL基本概念的项目作者。读完本模块，你应该能用一笔189元鼠标订单解释正常付款、超时、重复通知、MQ故障和订单到期。先读本页，再按具体问题进入子页。' '<p>当前支付总览：2026-10-11按源码4673b9f核对。先用189元订单理解业务流程，再看设计取舍、核心代码、证据与重做选择；原学习地图和七个问题子页保留。默认同步，MQ仅本地开发/测试可选，当前独立部署未开启；材料存在不代表个人已经掌握。</p>'
Update-Block 'T6O0deWUnowaBsxM6LgcJfuynjb' 'payment-authoring-rule' '每页固定顺序：业务例子 → 必须守住的规则 → 流程图或对照表 → 方案与替代 → 真实源码 → 正常与失败前后值 → 历史证据 → 限制 → 面试自检。复杂图有等价文字解释，避免只能靠图片理解。' '<p>最新专题写法：业务场景与完整流程 → 必须守住的规则和设计理由 → 关键机制、替代与少量核心代码 → 实际证据和剩余代价 → 如果重新做。关键图配等价文字；不机械填满章节、不新增末尾测验。模块总览讲当前状态，历史切片保留日期与证据。</p>'
Update-Block 'T6O0deWUnowaBsxM6LgcJfuynjb' 'payment-maintenance' '维护信息：2026-10-06；适用Project One当前工作区，Git基点2dacc9a，C4/5D仍含本地增量。业务集成使用后端Mock Gateway，无真钱；远端Wiki与画板发布结果由归档记录单独确认。' '<p>维护信息：2026-10-11；当前总览核对源码4673b9f8ea95664ce4ab886e1a9e791e6ffbd42e及总纲v1.15。C4/5D等已在历史提交中保存，旧子页版本说明仍按各自日期阅读。本轮只核对源码、既有证据、文图和归档，没有重跑业务实验；Mock无真钱，本期恢复仍由人工触发。</p>'
Update-Block 'T6O0deWUnowaBsxM6LgcJfuynjb' 'payment-legacy-questions-heading' '面试与自检' '<h1>旧口述提示：按实际困惑选读</h1>'
