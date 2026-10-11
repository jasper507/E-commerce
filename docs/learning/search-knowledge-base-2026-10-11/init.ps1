$ErrorActionPreference = 'Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER = '1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER = '1'
Set-Location -LiteralPath $PSScriptRoot
function Save-Json($value, $path) {
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot $path), ($value | ConvertTo-Json -Depth 100)+[Environment]::NewLine, [Text.UTF8Encoding]::new($false))
}
$pages = @(
    @{key='search'; title='商品搜索：MySQL → Elasticsearch'; guide='search-current-overview-study-guide.md'; board='read-sequence'},
    @{key='search-01'; title='为什么用ES，MySQL搜索不够吗？'; guide='search-interview/01-why-search.md'; board=''},
    @{key='search-02'; title='中文商品怎样被搜出来？'; guide='search-interview/02-chinese-index.md'; board=''},
    @{key='search-03'; title='为什么搜到以后还要查MySQL？'; guide='search-interview/03-read-path.md'; board=''},
    @{key='search-04'; title='怎样重建并安全切换索引？'; guide='search-interview/04-index-switch.md'; board='switch-sequence'},
    @{key='search-05'; title='ES故障或数据陈旧怎么办？'; guide='search-interview/05-failure-consistency.md'; board=''}
)
if (Test-Path './draft-workspaces.json') { throw 'Drafts already initialized; inspect saved identities rather than initialize again.' }
$workspaces=@()
foreach ($page in $pages) {
    $blocks=@()
    if ($page.board) { $blocks=@(@{type='whiteboard';min_count=1;purpose='解释真实读流程或索引发布、失败回读与剩余边界'}) }
    $decision=@{audience='电商项目拥有者，准备中国后端实习面试';reader_task='独立讲清业务问题、完整流程、失败例子、替代代价、真实证据与重做选择';genre_contract='router.knowledge';adapter='knowledge-base';presentation_mode='normal';visual_plan=@{reason='以业务解释和核心代码为主，关键交互用时序图';blocks=$blocks}}
    $raw=& lark-cli docs +script --command init-draft --presentation-decision ($decision|ConvertTo-Json -Depth 20 -Compress) --as user --format json
    $code=$LASTEXITCODE
    $value=$raw|ConvertFrom-Json
    Save-Json $value ($page.key+'-init.json')
    if ($code -ne 0 -or -not $value.ok) { throw ('Initialization failed: '+$page.key) }
    $workspaces+=@{key=$page.key;title=$page.title;guide=$page.guide;board=$page.board;decision=$decision;cwd=$value.data.cwd;workspace=$value.data.workspace;draft_path=$value.data.draft_path}
    Save-Json @($workspaces) 'draft-workspaces.json'
    Write-Output ($page.key+': draft initialized')
}
$raw=& lark-cli docs +fetch --doc P0LPd9AkmoAhq8xCgOdcqEL2nag --detail full --doc-format xml --as user --format json
$value=$raw|ConvertFrom-Json
Save-Json $value 'project-before.json'
if (-not $value.ok) { throw 'Root fetch failed.' }
$raw=& lark-cli wiki +node-list --space-id 7693476782908935142 --parent-node-token WN1jwXDj0iPXm7kwLyHcmneenlb --page-all --page-limit 0 --as user --format json
$value=$raw|ConvertFrom-Json
Save-Json $value 'project-before-children.json'
if (-not $value.ok -or $value.data.has_more) { throw 'Root children fetch incomplete.' }
Write-Output ('Root revision='+((Get-Content './project-before.json' -Raw|ConvertFrom-Json).data.document.revision_id)+', children='+@($value.data.nodes).Count)
