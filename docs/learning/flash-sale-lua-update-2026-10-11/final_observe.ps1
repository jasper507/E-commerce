$ErrorActionPreference='Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1'
Set-Location -LiteralPath $PSScriptRoot
function Save-Json($value,$path) {
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot $path),($value|ConvertTo-Json -Depth 100)+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))
}
$node=& lark-cli wiki +node-get --node-token B49NwtiePiCqqFkz61kc4ac9n79 --space-id 7693476782908935142 --as user --format json|ConvertFrom-Json
if (-not $node.ok -or $node.data.obj_token -ne 'MVBrdxZAPoDcPzxQnTPctXXgn3g') {throw 'Target identity changed'}
Save-Json @{ok=$node.ok;node_token=$node.data.node_token;obj_token=$node.data.obj_token;space_id=$node.data.space_id;title=$node.data.title} 'final-target.json'
$children=& lark-cli wiki +node-list --space-id 7693476782908935142 --parent-node-token B49NwtiePiCqqFkz61kc4ac9n79 --page-all --page-limit 0 --as user --format json|ConvertFrom-Json
if (-not $children.ok -or $children.data.has_more) {throw 'Incomplete final hierarchy'}
Save-Json $children 'final-children.json'
Write-Output ('Final children: '+@($children.data.nodes).Count)
