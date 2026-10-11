$ErrorActionPreference='Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1'
Set-Location -LiteralPath $PSScriptRoot
function Save-Json($value,$path) {[IO.File]::WriteAllText((Join-Path $PSScriptRoot $path),($value|ConvertTo-Json -Depth 100)+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))}
if (-not (Test-Path './project-before.json')) {
    $root=& lark-cli wiki +node-get --node-token WN1jwXDj0iPXm7kwLyHcmneenlb --space-id 7693476782908935142 --as user --format json|ConvertFrom-Json
    if (-not $root.ok -or $root.data.obj_token -ne 'P0LPd9AkmoAhq8xCgOdcqEL2nag') {throw 'Unexpected project target'}
    Save-Json @{ok=$root.ok;node_token=$root.data.node_token;obj_token=$root.data.obj_token;space_id=$root.data.space_id;title=$root.data.title} 'project-target.json'
    $children=& lark-cli wiki +node-list --space-id 7693476782908935142 --parent-node-token WN1jwXDj0iPXm7kwLyHcmneenlb --page-all --page-limit 0 --as user --format json|ConvertFrom-Json
    if (-not $children.ok -or $children.data.has_more -or @($children.data.nodes).Count -ne 8) {throw 'Unexpected project children; inspect before creating'}
    Save-Json $children 'project-before-children.json'
    $document=& lark-cli docs +fetch --doc $root.data.obj_token --detail full --doc-format xml --as user --format json|ConvertFrom-Json
    if (-not $document.ok) {throw 'Project fetch failed'}
    Save-Json $document 'project-before.json'
}
$pages=Get-Content './pages.json' -Raw -Encoding UTF8|ConvertFrom-Json
$drafts=@()
if (Test-Path './draft-workspaces.json') {$drafts=@(Get-Content './draft-workspaces.json' -Raw -Encoding UTF8|ConvertFrom-Json)}
foreach ($page in $pages) {
    if ($drafts.key -contains $page.key) {continue}
    $blocks=if ($page.board) {@(@{type='whiteboard';min_count=1;purpose='Show how the original two Order locking queries are controlled, observed and checked'})} else {@()}
    $decision=@{audience='Project owner preparing backend interviews';reader_task=$page.title;genre_contract='router.knowledge';adapter=$null;presentation_mode='normal';visual_plan=@{reason='Business facts and historical evidence first; comparison tables and equivalent prose, one verified concurrency sequence where needed';blocks=@($blocks)}}
    $raw=& lark-cli docs +script --command init-draft --presentation-decision ($decision|ConvertTo-Json -Depth 20 -Compress) --as user --format json
    $code=$LASTEXITCODE;$value=$raw|ConvertFrom-Json
    if ($code -ne 0 -or -not $value.ok) {throw ('Inspect draft initialization before retry '+$page.key)}
    $drafts+=@{key=$page.key;guide=$page.guide;title=$page.title;board=$page.board;cwd=$value.data.cwd;workspace=$value.data.workspace;draft_path=$value.data.draft_path}
    Save-Json @($drafts) 'draft-workspaces.json'
    Write-Output ($page.key+': initialized')
}
