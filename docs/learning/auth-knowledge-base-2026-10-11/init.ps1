$ErrorActionPreference='Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1'
Set-Location -LiteralPath $PSScriptRoot
function Save-Json($value,$path) {
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot $path),($value|ConvertTo-Json -Depth 100)+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))
}
$pages=Get-Content './pages.json' -Raw -Encoding UTF8|ConvertFrom-Json
if (Test-Path './draft-workspaces.json') {throw 'Reuse initialized draft identities'}
$workspaces=@()
foreach ($page in $pages) {
    $blocks=@()
    if ($page.board) {$blocks=@(@{type='whiteboard';min_count=1;purpose='Explain the login/CSRF or owner-filtered read/write sequence'})}
    $decision=@{audience='Project owner preparing backend interviews';reader_task='Explain session identity, CSRF and resource authorization with current code, evidence and redesign tradeoffs';genre_contract='router.knowledge';adapter=$null;presentation_mode='normal';visual_plan=@{reason='Two distinct security boundaries need sequence diagrams; all have equivalent prose';blocks=$blocks}}
    $raw=& lark-cli docs +script --command init-draft --presentation-decision ($decision|ConvertTo-Json -Depth 20 -Compress) --as user --format json
    $code=$LASTEXITCODE;$value=$raw|ConvertFrom-Json
    Save-Json $value ($page.key+'-init.json')
    if ($code -ne 0 -or -not $value.ok) {throw ('Draft initialization failed '+$page.key)}
    $workspaces+=@{key=$page.key;title=$page.title;guide=$page.guide;board=$page.board;decision=$decision;cwd=$value.data.cwd;workspace=$value.data.workspace;draft_path=$value.data.draft_path}
    Save-Json @($workspaces) 'draft-workspaces.json'
    Write-Output ($page.key+': initialized')
}
