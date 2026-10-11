$ErrorActionPreference='Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1'
Set-Location -LiteralPath $PSScriptRoot
function Save-Json($value,$path) {
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot $path),($value|ConvertTo-Json -Depth 100)+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))
}
$root=& lark-cli wiki +node-get --node-token WN1jwXDj0iPXm7kwLyHcmneenlb --space-id 7693476782908935142 --as user --format json|ConvertFrom-Json
if (-not $root.ok -or $root.data.obj_type -ne 'docx') {throw 'Unexpected project root'}
Save-Json @{ok=$root.ok;node_token=$root.data.node_token;obj_token=$root.data.obj_token;space_id=$root.data.space_id;title=$root.data.title} 'project-target.json'
$children=& lark-cli wiki +node-list --space-id 7693476782908935142 --parent-node-token WN1jwXDj0iPXm7kwLyHcmneenlb --page-all --page-limit 0 --as user --format json|ConvertFrom-Json
if (-not $children.ok -or $children.data.has_more) {throw 'Incomplete project hierarchy'}
Save-Json $children 'project-before-children.json'
$document=& lark-cli docs +fetch --doc $root.data.obj_token --detail full --doc-format xml --as user --format json|ConvertFrom-Json
if (-not $document.ok) {throw 'Root fetch failed'}
Save-Json $document 'project-before.json'
$children.data.nodes|Select-Object title,node_token,obj_token|ConvertTo-Json -Depth 5
