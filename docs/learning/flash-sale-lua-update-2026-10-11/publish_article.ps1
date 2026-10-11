param([ValidateSet('create','fill','verify')][string]$Mode='create')
$ErrorActionPreference='Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1'
Set-Location -LiteralPath $PSScriptRoot
$python=Join-Path $PSScriptRoot '../../../.venv/Scripts/python.exe'
New-Item -ItemType Directory -Force './journal'|Out-Null
function Save-Json($value,$path) {[IO.File]::WriteAllText((Join-Path $PSScriptRoot $path),($value|ConvertTo-Json -Depth 100)+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))}
function Invoke-Lark([string[]]$Arguments,[string]$Label,[switch]$Write) {
    $raw=& lark-cli @Arguments --as user --format json
    $code=$LASTEXITCODE
    $value=$raw|ConvertFrom-Json
    if ($Label -eq 'lua-create') {
        $node=$value.data
        if (-not $node.node_token -and $value.data.node) {$node=$value.data.node}
        Save-Json @{ok=$value.ok;identity=$value.identity;node_token=$node.node_token;obj_token=$node.obj_token;title=$node.title} ('journal/'+$Label+'.json')
    } else {Save-Json $value ('journal/'+$Label+'.json')}
    if ($code -ne 0 -or -not $value.ok) {throw ('Inspect effects before retry: '+$Label)}
    if ($Write -and ($value.data.result -ne 'success' -or @($value.data.warnings).Count)) {throw ('Incomplete/warnings: '+$Label)}
    return $value
}
function Fetch-Article($doc) {
    $value=Invoke-Lark @('docs','+fetch','--doc',$doc,'--detail','full','--doc-format','xml') 'lua-fetch'
    Save-Json $value 'lua-latest.json'
    return $value
}
if ($Mode -eq 'create') {
    if (Test-Path './created.json') {throw 'Known node exists; do not duplicate create.'}
    $result=Invoke-Lark @('wiki','+node-list','--space-id','7693476782908935142','--parent-node-token','B49NwtiePiCqqFkz61kc4ac9n79','--page-all','--page-limit','0') 'current-children'
    $before=Get-Content './before-children.json' -Raw -Encoding UTF8|ConvertFrom-Json
    if ($result.data.has_more -or @(Compare-Object ($result.data.nodes.node_token|Sort-Object) ($before.data.nodes.node_token|Sort-Object)).Count) {throw 'Children changed; inspect before create.'}
    [xml]$profile='<doc>'+(Get-Content './candidates/lua-profile.xml' -Raw -Encoding UTF8)+'</doc>'
    $title=$profile.SelectSingleNode('/doc/title').InnerText
    if ([string]::IsNullOrWhiteSpace($title)) {throw 'Missing title; do not send creation.'}
    $result=Invoke-Lark @('wiki','+node-create','--space-id','7693476782908935142','--parent-node-token','B49NwtiePiCqqFkz61kc4ac9n79','--obj-type','docx','--node-type','origin','--title',$title) 'lua-create'
    $node=$result.data
    if (-not $node.node_token -and $result.data.node) {$node=$result.data.node}
    if (-not $node.node_token -or -not $node.obj_token) {throw 'Unknown created identity; inspect journal.'}
    Save-Json @{node_token=$node.node_token;obj_token=$node.obj_token;title=$node.title;parent_node_token='B49NwtiePiCqqFkz61kc4ac9n79'} 'created.json'
    Write-Output ('Created Lua page '+$node.node_token)
} else {
    $created=Get-Content './created.json' -Raw -Encoding UTF8|ConvertFrom-Json
    $latest=Fetch-Article $created.obj_token
    if ($Mode -eq 'fill') {
        [xml]$tree='<doc>'+$latest.data.document.content+'</doc>'
        $nonTitle=@($tree.doc.ChildNodes|Where-Object {$_.Name -ne 'title' -and ($_.Name -ne 'p' -or $_.InnerText.Trim())})
        if ($nonTitle.Count) {throw 'Nonempty article; inspect instead of reappend.'}
        $null=Invoke-Lark @('docs','+update','--doc',$created.obj_token,'--command','append','--revision-id',[string]$latest.data.document.revision_id,'--doc-format','xml','--content','@./candidates/lua-body.xml') 'lua-fill' -Write
        $null=Fetch-Article $created.obj_token
    }
    & $python './inspect_article.py'
    if ($LASTEXITCODE -ne 0) {throw 'Article read-back failed.'}
}
