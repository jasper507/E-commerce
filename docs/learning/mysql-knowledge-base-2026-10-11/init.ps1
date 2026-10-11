$ErrorActionPreference='Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1'
Set-Location -LiteralPath $PSScriptRoot
function Save-Json($value,$path) {
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot $path),($value|ConvertTo-Json -Depth 100)+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))
}
$pages=@(
    @{key='mysql';title='MySQL与订单：事务、幂等与查询优化';guide='mysql-current-overview-study-guide.md';board='order-sequence'},
    @{key='mysql-01';title='下单为什么要保存快照并使用事务？';guide='mysql-interview/01-data-transaction.md';board=''},
    @{key='mysql-02';title='幂等、行锁和数据库约束怎样配合？';guide='mysql-interview/02-idempotency-locks.md';board=''},
    @{key='mysql-03';title='订单分页为什么先过滤本人，再排序？';guide='mysql-interview/03-pagination.md';board=''},
    @{key='mysql-04';title='每页20单，数据库究竟做了多少工作？';guide='mysql-interview/04-query-cost.md';board='query-sequence'},
    @{key='mysql-05';title='联合索引改善了什么，还剩哪些成本？';guide='mysql-interview/05-composite-index.md';board=''}
)
if (Test-Path './draft-workspaces.json') {throw 'Drafts already initialized; reuse saved identities.'}
$workspaces=@()
foreach ($page in $pages) {
    $blocks=@()
    if ($page.board) {$blocks=@(@{type='whiteboard';min_count=1;purpose='解释建单提交/回滚或订单查询的工作与边界'})}
    $decision=@{audience='项目拥有者，准备中国后端实习面试';reader_task='讲清MySQL交易事实、事务/锁/约束和查询优化证据，比较替代与重做选择';genre_contract='router.knowledge';adapter=$null;presentation_mode='normal';visual_plan=@{reason='核心业务交互配时序图，问题页用正文和代码解释取舍';blocks=$blocks}}
    $raw=& lark-cli docs +script --command init-draft --presentation-decision ($decision|ConvertTo-Json -Depth 20 -Compress) --as user --format json
    $code=$LASTEXITCODE
    $value=$raw|ConvertFrom-Json
    Save-Json $value ($page.key+'-init.json')
    if ($code -ne 0 -or -not $value.ok) {throw ('Initialization failed: '+$page.key)}
    $workspaces+=@{key=$page.key;title=$page.title;guide=$page.guide;board=$page.board;decision=$decision;cwd=$value.data.cwd;workspace=$value.data.workspace;draft_path=$value.data.draft_path}
    Save-Json @($workspaces) 'draft-workspaces.json'
    Write-Output ($page.key+': draft initialized')
}
$raw=& lark-cli docs +fetch --doc P0LPd9AkmoAhq8xCgOdcqEL2nag --detail full --doc-format xml --as user --format json
$value=$raw|ConvertFrom-Json
Save-Json $value 'project-before.json'
if (-not $value.ok) {throw 'Root fetch failed.'}
$raw=& lark-cli wiki +node-list --space-id 7693476782908935142 --parent-node-token WN1jwXDj0iPXm7kwLyHcmneenlb --page-all --page-limit 0 --as user --format json
$value=$raw|ConvertFrom-Json
Save-Json $value 'project-before-children.json'
if (-not $value.ok -or $value.data.has_more) {throw 'Root children fetch incomplete.'}
Write-Output ('Root children='+@($value.data.nodes).Count)
