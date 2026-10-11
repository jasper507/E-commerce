param([ValidateSet('parse','create','fill','boards','export','root','verify')][string]$Mode='parse',[string]$Only='')
$ErrorActionPreference='Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1'
Set-Location -LiteralPath $PSScriptRoot
$python=Join-Path $PSScriptRoot '../../../.venv/Scripts/python.exe'
$pages=Get-Content './draft-workspaces.json' -Raw -Encoding UTF8|ConvertFrom-Json
New-Item -ItemType Directory -Force './journal'|Out-Null
$script:sequence=@(Get-ChildItem './journal' -Filter '*.json').Count
function Save-Json($value,$path) {[IO.File]::WriteAllText((Join-Path $PSScriptRoot $path),($value|ConvertTo-Json -Depth 100)+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))}
function Sanitize($value) {
    if ($null -eq $value -or $value -is [string] -or $value -is [ValueType]) {return $value}
    if ($value -is [System.Array]) {return ,@($value|ForEach-Object {Sanitize $_})}
    $clean=[ordered]@{}
    foreach ($property in $value.PSObject.Properties) {
        if ($property.Name -in @('creator','owner','creator_id','owner_id','obj_create_user','obj_edit_user')) {continue}
        $clean[$property.Name]=Sanitize $property.Value
    }
    return $clean
}
function Invoke-Lark([string[]]$Arguments,[string]$Label,[switch]$Write) {
    $script:sequence+=1
    $raw=& lark-cli @Arguments --as user --format json
    $code=$LASTEXITCODE;$value=$raw|ConvertFrom-Json
    if ($null -eq $value) {$value=@{ok=$false;identity='user';error=@{type='stderr_only';message='CLI failure details are in the tool output; no successful response was captured'}}}
    Save-Json (Sanitize $value) ('journal/{0:D3}-{1}.json' -f $script:sequence,$Label)
    if ($code -ne 0 -or -not $value.ok) {throw ('Inspect actual effects before retry: '+$Label)}
    if ($Write -and ($value.data.result -ne 'success' -or @($value.data.warnings).Count)) {throw ('Incomplete write/warnings: '+$Label)}
    return $value
}
function Fetch($doc,$key) {
    $value=Invoke-Lark @('docs','+fetch','--doc',$doc,'--detail','full','--doc-format','xml') ($key+'-fetch')
    Save-Json (Sanitize $value) ($key+'-latest.json')
    return $value
}
function Check($key) {
    & $python './inspect_publication.py' $key
    if ($LASTEXITCODE -ne 0) {throw ('Page read-back mismatch '+$key)}
}
if ($Mode -eq 'parse') {
    foreach ($page in $pages) {
        $value=Invoke-Lark @('docs','+script','--command','parse','--content',('@./'+$page.draft_path)) ($page.key+'-profile')
        Save-Json $value ($page.key+'-profile.json')
        if ($value.data.assessment.status -ne 'passed') {throw ('Profile failed '+$page.key)}
        Write-Output ($page.key+': profile passed')
    }
} elseif ($Mode -eq 'create') {
    $created=@()
    if (Test-Path './created.json') {$created=@(Get-Content './created.json' -Raw -Encoding UTF8|ConvertFrom-Json)}
    $children=Invoke-Lark @('wiki','+node-list','--space-id','7693476782908935142','--parent-node-token','WN1jwXDj0iPXm7kwLyHcmneenlb','--page-all','--page-limit','0') 'project-current-children'
    $before=Get-Content './project-before-children.json' -Raw -Encoding UTF8|ConvertFrom-Json
    $expected=@($before.data.nodes.node_token)
    if ($created.Count) {$expected+=$created[0].node_token}
    if ($children.data.has_more -or @(Compare-Object ($expected|Sort-Object) ($children.data.nodes.node_token|Sort-Object)).Count) {throw 'Project hierarchy changed'}
    if ($created.Count) {
        $children=Invoke-Lark @('wiki','+node-list','--space-id','7693476782908935142','--parent-node-token',$created[0].node_token,'--page-all','--page-limit','0') 'auth-current-children'
        $expected=@($created|Select-Object -Skip 1|ForEach-Object {$_.node_token})
        if ($children.data.has_more -or @(Compare-Object ($expected|Sort-Object) ($children.data.nodes.node_token|Sort-Object)).Count) {throw 'Auth hierarchy changed'}
    }
    for ($i=$created.Count;$i -lt $pages.Count;$i++) {
        $page=$pages[$i]
        $profile=Get-Content ($page.key+'-profile.json') -Raw -Encoding UTF8|ConvertFrom-Json
        if ($profile.data.assessment.status -ne 'passed') {throw 'Create requires a passed profile'}
        $parent=if ($i -eq 0) {'WN1jwXDj0iPXm7kwLyHcmneenlb'} else {$created[0].node_token}
        $value=Invoke-Lark @('wiki','+node-create','--space-id','7693476782908935142','--parent-node-token',$parent,'--obj-type','docx','--node-type','origin','--title',$page.title) ($page.key+'-create')
        $node=$value.data
        if (-not $node.node_token -and $value.data.node) {$node=$value.data.node}
        if (-not $node.node_token -or -not $node.obj_token) {throw 'Unknown created identity; inspect journal'}
        $created+=@{key=$page.key;title=$page.title;node_token=$node.node_token;obj_token=$node.obj_token;parent_node_token=$parent}
        Save-Json @($created) 'created.json'
        Write-Output ($page.key+': created '+$node.node_token)
    }
} elseif ($Mode -eq 'fill') {
    $created=@(Get-Content './created.json' -Raw -Encoding UTF8|ConvertFrom-Json)
    if ($created.Count -ne 5) {throw 'Expected five known nodes'}
    foreach ($page in $created) {
        if ($Only -and $Only -ne $page.key) {continue}
        $latest=Fetch $page.obj_token $page.key
        if (Test-Path ($page.key+'-verified.json')) {Check $page.key;continue}
        [xml]$tree='<doc>'+$latest.data.document.content+'</doc>'
        $nonTitle=@($tree.doc.ChildNodes|Where-Object {$_.Name -ne 'title' -and ($_.Name -ne 'p' -or $_.InnerText.Trim())})
        if ($nonTitle.Count) {throw ('Existing content; inspect before appending '+$page.key)}
        $null=Invoke-Lark @('docs','+update','--doc',$page.obj_token,'--command','append','--revision-id',[string]$latest.data.document.revision_id,'--doc-format','xml','--content',('@./candidates/'+$page.key+'-body.xml')) ($page.key+'-fill') -Write
        $null=Fetch $page.obj_token $page.key
        Check $page.key
    }
} elseif ($Mode -in @('boards','export')) {
    foreach ($page in $pages|Where-Object {$_.board}) {
        if ($Only -and $Only -ne $page.key) {continue}
        $verified=Get-Content ($page.key+'-verified.json') -Raw -Encoding UTF8|ConvertFrom-Json
        $token=$verified.boards[0].token
        $prefix='../assets/auth-interview-2026-10-11/'+$page.board+'/published'
        $assetDirectory=Join-Path $PSScriptRoot ('../assets/auth-interview-2026-10-11/'+$page.board)
        $writePath=Join-Path $PSScriptRoot ($prefix+'-write-result.json')
        Push-Location -LiteralPath $assetDirectory
        try {
        if ($Mode -eq 'boards' -and -not (Test-Path -LiteralPath $writePath)) {
            $before=Invoke-Lark @('whiteboard','+export','--whiteboard-token',$token,'--output-type','raw','--output','./published-before.json') ($page.key+'-board-before')
            Save-Json $before ($prefix+'-before-result.json')
            if ($before.data.msg -eq 'whiteboard is empty') {
                $knownEmpty=$true
            } else {
                $nodes=Get-Content './published-before.json' -Raw -Encoding UTF8|ConvertFrom-Json
                $knownEmpty=(@($nodes.nodes).Count -eq 0)
            }
            if (-not $knownEmpty) {throw 'New board not empty; inspect before writing'}
            $write=Invoke-Lark @('whiteboard','+update','--whiteboard-token',$token,'--input_format','raw','--source','@./diagram.json','--idempotent-token',('auth-20261011-'+$page.board+'-publish-1')) ($page.key+'-board-write')
            Save-Json $write ($prefix+'-write-result.json')
        }
        if ($Mode -eq 'boards') {
            Write-Output ($page.key+': native board written')
            continue
        }
        if (-not (Test-Path -LiteralPath $writePath)) {throw 'Write not yet recorded; inspect before exporting'}
        foreach ($type in @('raw','preview','source')) {
            $output=if ($type -eq 'raw') {'./published-raw.json'} else {'./published-'+$type}
            $value=Invoke-Lark @('whiteboard','+export','--whiteboard-token',$token,'--output-type',$type,'--output',$output,'--overwrite') ($page.key+'-board-'+$type)
            Save-Json $value ($prefix+'-'+$type+'-result.json')
        }
        Write-Output ($page.key+': native board exported')
        } finally {Pop-Location}
    }
} elseif ($Mode -eq 'root') {
    $target=Get-Content './project-target.json' -Raw -Encoding UTF8|ConvertFrom-Json
    $null=Fetch $target.obj_token 'project'
    $raw=& $python './root_navigation.py' plan
    if ($LASTEXITCODE -ne 0) {throw 'Protected root planning failed'}
    $operation=$raw|ConvertFrom-Json
    $profile=Invoke-Lark @('docs','+script','--command','parse','--content','@./candidates/root-navigation.xml') 'root-navigation-profile'
    if ($profile.data.assessment.status -ne 'passed') {throw 'Root patch profile failed'}
    $null=Invoke-Lark @('docs','+update','--doc',$target.obj_token,'--command','block_replace','--block-id',$operation.block,'--revision-id',[string]$operation.revision,'--doc-format','xml','--content','@./candidates/root-navigation.xml') 'root-navigation' -Write
    $null=Fetch $target.obj_token 'project'
    & $python './root_navigation.py' verify
    if ($LASTEXITCODE -ne 0) {throw 'Protected root verification failed'}
} elseif ($Mode -eq 'verify') {
    $created=@(Get-Content './created.json' -Raw -Encoding UTF8|ConvertFrom-Json)
    foreach ($page in $created) {$null=Fetch $page.obj_token $page.key;Check $page.key}
    foreach ($pair in @(@{key='project';node='WN1jwXDj0iPXm7kwLyHcmneenlb'},@{key='auth';node=$created[0].node_token})) {
        $value=Invoke-Lark @('wiki','+node-list','--space-id','7693476782908935142','--parent-node-token',$pair.node,'--page-all','--page-limit','0') ($pair.key+'-final-children')
        if ($value.data.has_more) {throw 'Incomplete hierarchy'}
        Save-Json $value ($pair.key+'-final-children.json')
    }
    $target=Get-Content './project-target.json' -Raw -Encoding UTF8|ConvertFrom-Json
    $null=Fetch $target.obj_token 'project'
    & $python './root_navigation.py' verify
    if ($LASTEXITCODE -ne 0) {throw 'Root verification failed'}
}
