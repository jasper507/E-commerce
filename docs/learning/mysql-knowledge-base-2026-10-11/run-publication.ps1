param([ValidateSet('parse','create','fill','export','root','verify')][string]$Mode='parse', [string]$Only='')
$ErrorActionPreference='Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1'
Set-Location -LiteralPath $PSScriptRoot
$python=Join-Path $PSScriptRoot '../../../.venv/Scripts/python.exe'
$pages=(Get-Content './draft-workspaces.json' -Raw -Encoding UTF8|ConvertFrom-Json)
$journal=Join-Path $PSScriptRoot 'journal'
New-Item -ItemType Directory -Force -Path $journal|Out-Null
$script:sequence=@(Get-ChildItem $journal -Filter '*.json').Count
function Save-Json($value,$path) {
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot $path),($value|ConvertTo-Json -Depth 100)+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))
}
function Sanitize($value) {
    if ($null -eq $value -or $value -is [string] -or $value -is [ValueType]) { return $value }
    if ($value -is [System.Array]) { return ,@($value|ForEach-Object { Sanitize $_ }) }
    $clean=[ordered]@{}
    foreach ($property in $value.PSObject.Properties) {
        if ($property.Name -in @('creator','owner','creator_id','owner_id','obj_create_user','obj_edit_user')) { continue }
        $clean[$property.Name]=Sanitize $property.Value
    }
    return $clean
}
function Invoke-Lark([string[]]$Arguments,[string]$Label,[switch]$Write) {
    $script:sequence+=1
    $raw=& lark-cli @Arguments --as user --format json
    $code=$LASTEXITCODE
    $value=$raw|ConvertFrom-Json
    Save-Json (Sanitize $value) ('journal/{0:D3}-{1}.json' -f $script:sequence,$Label)
    if ($code -ne 0 -or -not $value.ok) { throw ('CLI failed; inspect recorded effects before retry: '+$Label) }
    if ($Write -and ($value.data.result -ne 'success' -or @($value.data.warnings).Count -gt 0)) { throw ('Write incomplete/warnings: '+$Label) }
    return $value
}
function Fetch-Page($doc,$key) {
    $value=Invoke-Lark @('docs','+fetch','--doc',$doc,'--detail','full','--doc-format','xml') ($key+'-fetch')
    Save-Json (Sanitize $value) ($key+'-latest.json')
    return $value
}
function Verify-Page($key) {
    $raw=& $python './inspect_publication.py' $key
    if ($LASTEXITCODE -ne 0) { throw ('Read-back check failed: '+$key) }
    $value=$raw|ConvertFrom-Json
    Write-Output ($key+': read-back passed, revision='+$value.revision+', boards='+@($value.boards).Count)
}
if ($Mode -eq 'parse') {
    foreach ($page in $pages) {
        if ($Only -and $Only -ne $page.key) { continue }
        $value=Invoke-Lark @('docs','+script','--command','parse','--content',('@./'+$page.draft_path)) ($page.key+'-profile')
        Save-Json $value ($page.key+'-profile.json')
        if ($value.data.assessment.status -ne 'passed') { throw ('Profile failed: '+$page.key) }
        Write-Output ($page.key+': profile passed')
    }
}
elseif ($Mode -eq 'create') {
    $created=@()
    if (Test-Path './created.json') { $created=(Get-Content './created.json' -Raw -Encoding UTF8|ConvertFrom-Json) }
    $children=Invoke-Lark @('wiki','+node-list','--space-id','7693476782908935142','--parent-node-token','WN1jwXDj0iPXm7kwLyHcmneenlb','--page-all','--page-limit','0') 'root-current-children'
    $expectedCount=5+[int]($created.Count -gt 0)
    if ($children.data.has_more -or @($children.data.nodes).Count -ne $expectedCount) { throw 'Unexpected root children; inspect before creating.' }
    $before=Get-Content './project-before-children.json' -Raw -Encoding UTF8|ConvertFrom-Json
    $expectedNodes=@($before.data.nodes.node_token)
    if ($created.Count -gt 0) { $expectedNodes+= $created[0].node_token }
    if (@(Compare-Object ($expectedNodes|Sort-Object) ($children.data.nodes.node_token|Sort-Object)).Count) { throw 'Root node identities changed; inspect before creating.' }
    if ($created.Count -gt 0) {
        $children=Invoke-Lark @('wiki','+node-list','--space-id','7693476782908935142','--parent-node-token',$created[0].node_token,'--page-all','--page-limit','0') 'mysql-current-children'
        if ($children.data.has_more -or @($children.data.nodes).Count -ne $created.Count-1) { throw 'Unexpected MySQL children.' }
    }
    for ($i=$created.Count;$i -lt $pages.Count;$i++) {
        $page=$pages[$i]
        $parent=if ($i -eq 0) {'WN1jwXDj0iPXm7kwLyHcmneenlb'} else {$created[0].node_token}
        $value=Invoke-Lark @('wiki','+node-create','--space-id','7693476782908935142','--parent-node-token',$parent,'--obj-type','docx','--node-type','origin','--title',$page.title) ($page.key+'-create')
        $node=$value.data
        if (-not $node.node_token -and $value.data.node) { $node=$value.data.node }
        if (-not $node.node_token -or -not $node.obj_token) { throw 'Unknown created identity; inspect journal before retry.' }
        $created+=@{key=$page.key;title=$page.title;node_token=$node.node_token;obj_token=$node.obj_token;parent_node_token=$parent}
        Save-Json @($created) 'created.json'
        Write-Output ($page.key+': created '+$node.node_token)
    }
}
elseif ($Mode -eq 'fill') {
    $created=(Get-Content './created.json' -Raw -Encoding UTF8|ConvertFrom-Json)
    if ($created.Count -ne 6) { throw 'Expected six known nodes.' }
    foreach ($page in $created) {
        if ($Only -and $Only -ne $page.key) { continue }
        $latest=Fetch-Page $page.obj_token $page.key
        if (Test-Path ($page.key+'-verified.json')) { Verify-Page $page.key; continue }
        [xml]$tree='<doc>'+$latest.data.document.content+'</doc>'
        $nonTitle=@($tree.doc.ChildNodes|Where-Object {$_.Name -ne 'title' -and ($_.Name -ne 'p' -or $_.InnerText.Trim())})
        if ($nonTitle.Count -gt 0) { throw ('Page already has content; inspect effects before retry: '+$page.key) }
        $null=Invoke-Lark @('docs','+update','--doc',$page.obj_token,'--command','append','--revision-id',[string]$latest.data.document.revision_id,'--doc-format','xml','--content',('@./candidates/'+$page.key+'-body.xml')) ($page.key+'-fill') -Write
        $null=Fetch-Page $page.obj_token $page.key
        Verify-Page $page.key
    }
}
elseif ($Mode -eq 'export') {
    foreach ($key in @('mysql','mysql-04')) {
        $verified=Get-Content ($key+'-verified.json') -Raw -Encoding UTF8|ConvertFrom-Json
        $board=$verified.boards[0]
        $prefix='../assets/mysql-interview-2026-10-11/'+$board.diagram+'/published'
        $value=Invoke-Lark @('whiteboard','+export','--whiteboard-token',$board.token,'--output-type','preview','--output',($prefix+'-preview')) ($key+'-preview')
        Save-Json $value ($key+'-export-preview.json')
        $value=Invoke-Lark @('whiteboard','+export','--whiteboard-token',$board.token,'--output-type','source','--output',($prefix+'-source')) ($key+'-source')
        Save-Json $value ($key+'-export-source.json')
        $value=Invoke-Lark @('whiteboard','+export','--whiteboard-token',$board.token,'--output-type','raw','--output',($prefix+'-nodes.json')) ($key+'-nodes')
        Save-Json $value ($key+'-export-raw.json')
        Write-Output ($key+': board exported')
    }
}
elseif ($Mode -eq 'root') {
    $latest=Fetch-Page 'P0LPd9AkmoAhq8xCgOdcqEL2nag' 'project'
    $raw=& $python './root_navigation.py' plan
    if ($LASTEXITCODE -ne 0) { throw 'Root protection/patch planning failed.' }
    $operation=$raw|ConvertFrom-Json
    $null=Invoke-Lark @('docs','+update','--doc','P0LPd9AkmoAhq8xCgOdcqEL2nag','--command','block_replace','--block-id',$operation.block,'--revision-id',[string]$operation.revision,'--doc-format','xml','--content','@./candidates/root-navigation.xml') 'root-navigation' -Write
    $null=Fetch-Page 'P0LPd9AkmoAhq8xCgOdcqEL2nag' 'project'
    & $python './root_navigation.py' verify
    if ($LASTEXITCODE -ne 0) { throw 'Root navigation verification failed.' }
}
elseif ($Mode -eq 'verify') {
    $created=(Get-Content './created.json' -Raw -Encoding UTF8|ConvertFrom-Json)
    foreach ($page in $created) { $null=Fetch-Page $page.obj_token $page.key; Verify-Page $page.key }
    foreach ($pair in @(@{key='project';node='WN1jwXDj0iPXm7kwLyHcmneenlb'},@{key='mysql';node=$created[0].node_token})) {
        $value=Invoke-Lark @('wiki','+node-list','--space-id','7693476782908935142','--parent-node-token',$pair.node,'--page-all','--page-limit','0') ($pair.key+'-final-children')
        Save-Json (Sanitize $value) ($pair.key+'-final-children.json')
        if ($value.data.has_more -or @($value.data.nodes).Count -ne $(if ($pair.key -eq 'project') {6} else {5})) { throw 'Unexpected final node count.' }
    }
}
