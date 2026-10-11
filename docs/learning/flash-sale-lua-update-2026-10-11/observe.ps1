$ErrorActionPreference='Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1'
Set-Location -LiteralPath $PSScriptRoot
New-Item -ItemType Directory -Force './before'|Out-Null
function Save-Json($value,$path) {
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot $path),($value|ConvertTo-Json -Depth 100)+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))
}
$root=& lark-cli wiki +node-get --node-token B49NwtiePiCqqFkz61kc4ac9n79 --space-id 7693476782908935142 --as user --format json|ConvertFrom-Json
if (-not $root.ok -or $root.data.obj_token -ne 'MVBrdxZAPoDcPzxQnTPctXXgn3g') {throw 'Unexpected target identity'}
Save-Json @{ok=$root.ok;node_token=$root.data.node_token;obj_token=$root.data.obj_token;space_id=$root.data.space_id;title=$root.data.title} 'target.json'
$children=& lark-cli wiki +node-list --space-id 7693476782908935142 --parent-node-token B49NwtiePiCqqFkz61kc4ac9n79 --page-all --page-limit 0 --as user --format json|ConvertFrom-Json
Save-Json $children 'before-children.json'
if (-not $children.ok -or $children.data.has_more -or @($children.data.nodes).Count -ne 6) {throw 'Unexpected children, inspect before writing'}
$pages=@(@{key='overview';node=$root.data.node_token;doc=$root.data.obj_token;title=$root.data.title})
$index=1
foreach ($node in $children.data.nodes) {
    $pages+=@{key=('existing-{0:D2}' -f $index);node=$node.node_token;doc=$node.obj_token;title=$node.title}
    $index+=1
}
Save-Json @($pages) 'existing-pages.json'
foreach ($page in $pages) {
    $raw=& lark-cli docs +fetch --doc $page.doc --detail full --doc-format xml --as user --format json
    $value=$raw|ConvertFrom-Json
    Save-Json $value ('before/'+$page.key+'.json')
    if (-not $value.ok) {throw ('Fetch failed '+$page.key)}
    Write-Output ($page.key+': revision='+$value.data.document.revision_id)
}
