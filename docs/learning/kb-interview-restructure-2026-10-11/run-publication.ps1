param([ValidateSet('parse','create','fill','existing','verify')][string]$Mode = 'parse', [string]$Only = '', [switch]$RewriteVerified)
$ErrorActionPreference = 'Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER = '1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER = '1'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '../../../.venv/Scripts/python.exe'
$journal = Join-Path $PSScriptRoot 'journal'
New-Item -ItemType Directory -Force -Path $journal | Out-Null
$plan = Get-Content -LiteralPath './publication-plan.json' -Raw | ConvertFrom-Json
$script:sequence = @(Get-ChildItem -LiteralPath $journal -Filter '*.json').Count

function Save-Json($Value, $Path) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 100).TrimEnd()+[Environment]::NewLine, [Text.UTF8Encoding]::new($false))
}

function Invoke-Lark([string[]]$Arguments, [string]$Label, [switch]$Write) {
    $script:sequence += 1
    $raw = & lark-cli @Arguments --as user --format json
    $commandExit = $LASTEXITCODE
    $value = $raw | ConvertFrom-Json
    Save-Json $value (Join-Path $journal ('{0:D3}-{1}.json' -f $script:sequence,$Label))
    if ($commandExit -ne 0) { throw "CLI failed: $Label. Inspect journal and side effects before retrying." }
    if (-not $value.ok) { throw "CLI not ok: $Label" }
    if ($Write -and ($value.data.result -ne 'success' -or @($value.data.warnings).Count -gt 0)) {
        throw "Write incomplete/warnings: $Label. Inspect journal before continuing."
    }
    return $value
}

function Fetch-Page([string]$Doc, [string]$Key) {
    $result = Invoke-Lark @('docs','+fetch','--doc',$Doc,'--detail','full','--doc-format','xml') ($Key+'-fetch')
    Save-Json $result (Join-Path $PSScriptRoot ($Key+'-latest.json'))
    return $result
}

function Verify-Page([string]$Command, [string]$Key) {
    $raw = & $python './inspect_publication.py' $Command $Key ($Key+'-latest.json')
    if ($LASTEXITCODE -ne 0) { throw "Read-back failed: $Key" }
    $verified = $raw | ConvertFrom-Json
    Save-Json $verified (Join-Path $PSScriptRoot ($Key+'-verified.json'))
    Write-Output ($Key + ': ' + $verified.status + ', revision=' + $verified.revision + ', boards=' + $verified.protected_boards)
}

if ($Mode -eq 'parse') {
    foreach ($page in $plan.existing_pages) {
        if ($Only -and $Only -ne $page.name) { continue }
        $result = Invoke-Lark @('docs','+script','--command','parse','--content',('@./candidates/'+$page.name+'-profile.xml')) ($page.name+'-parse')
        if ($result.data.assessment.status -ne 'passed') { throw "Profile failed: $($page.name)" }
        Write-Output ($page.name+': profile passed')
    }
    for ($i=0; $i -lt 5; $i++) {
        $key = 'redis-{0:D2}' -f ($i+1)
        if ($Only -and $Only -ne $key) { continue }
        $workspace = $plan.new_redis_pages[$i]
        $argv = @('docs','+script','--command','parse')
        if (Test-Path -LiteralPath $workspace.draft_path) {
            $argv += @('--content',('@./'+$workspace.draft_path))
        }
        else {
            $argv += @('--content',('@./candidates/'+$key+'-profile.xml'),'--presentation-decision',($workspace.decision | ConvertTo-Json -Depth 20 -Compress))
        }
        $result = Invoke-Lark $argv ($key+'-parse')
        if ($result.data.assessment.status -ne 'passed') { throw "Profile failed: $key" }
        Write-Output ($key+': profile passed')
    }
}
elseif ($Mode -eq 'create') {
    $created = @()
    if (Test-Path -LiteralPath './redis-created.json') { $created = @(Get-Content './redis-created.json' -Raw | ConvertFrom-Json) }
    $children = Invoke-Lark @('wiki','+node-list','--space-id','7693476782908935142','--parent-node-token','O4iUwSvl1i4Je7keOL0ci1iAnLf','--page-all','--page-limit','0') 'redis-current-children'
    if ($children.data.has_more -or @($children.data.nodes).Count -ne $created.Count) { throw 'Unexpected Redis child nodes; inspect before creating.' }
    for ($i=$created.Count; $i -lt 5; $i++) {
        $title = $plan.new_redis_pages[$i].title
        $result = Invoke-Lark @('wiki','+node-create','--space-id','7693476782908935142','--parent-node-token','O4iUwSvl1i4Je7keOL0ci1iAnLf','--obj-type','docx','--node-type','origin','--title',$title) ('redis-create-'+($i+1))
        # A created node is a side effect. Save its identity before any further action.
        $node = $result.data
        if (-not $node.node_token -and $result.data.node) { $node = $result.data.node }
        if (-not $node.node_token -or -not $node.obj_token) { throw 'Creation returned unknown identity; inspect journal before retrying.' }
        $created += @{ title=$title; node_token=$node.node_token; obj_token=$node.obj_token; key=('redis-{0:D2}' -f ($i+1)) }
        Save-Json @($created) (Join-Path $PSScriptRoot 'redis-created.json')
        Write-Output ($title+': created '+$node.node_token)
    }
}
elseif ($Mode -eq 'fill') {
    $created = @(Get-Content './redis-created.json' -Raw | ConvertFrom-Json)
    if ($created.Count -ne 5) { throw 'Expected all five created nodes.' }
    foreach ($page in $created) {
        if ($Only -and $Only -ne $page.key) { continue }
        if ((Test-Path ($page.key+'-verified.json')) -and -not $RewriteVerified) { Verify-Page 'verify-new' $page.key; continue }
        $latest = Fetch-Page $page.obj_token $page.key
        if ($RewriteVerified) {
            $previous = Get-Content ($page.key+'-verified.json') -Raw | ConvertFrom-Json
            if ($latest.data.document.revision_id -ne $previous.revision) { throw 'New page has a concurrent edit.' }
            $raw = & $python './inspect_publication.py' plan-new $page.key ($page.key+'-latest.json')
            if ($LASTEXITCODE -ne 0) { throw 'New page formatting plan failed.' }
            $operation = $raw | ConvertFrom-Json
            $null = Invoke-Lark @('docs','+update','--doc',$page.obj_token,'--command','block_replace','--block-id',$operation.block,'--revision-id',[string]$operation.revision,'--doc-format','xml','--content',('@./'+$operation.content)) ($page.key+'-formatting') -Write
            $null = Fetch-Page $page.obj_token $page.key
            Verify-Page 'verify-new' $page.key
            continue
        }
        [xml]$tree = '<doc>'+$latest.data.document.content+'</doc>'
        $nonTitle = @($tree.doc.ChildNodes | Where-Object { $_.Name -ne 'title' -and ($_.Name -ne 'p' -or $_.InnerText.Trim()) })
        if ($nonTitle.Count -gt 0) { throw ('New page has unexpected content: '+$page.key) }
        $result = Invoke-Lark @('docs','+update','--doc',$page.obj_token,'--command','append','--revision-id',[string]$latest.data.document.revision_id,'--doc-format','xml','--content',('@./candidates/'+$page.key+'-body.xml')) ($page.key+'-fill') -Write
        $null = Fetch-Page $page.obj_token $page.key
        Verify-Page 'verify-new' $page.key
    }
}
elseif ($Mode -eq 'existing') {
    foreach ($page in $plan.existing_pages) {
        if ($Only -and $Only -ne $page.name) { continue }
        $latest = Fetch-Page $page.doc $page.name
        $expectedRevision = $page.base_revision
        if (Test-Path ($page.name+'-verified.json')) {
            if (-not $RewriteVerified) { Verify-Page 'verify' $page.name; continue }
            $previous = Get-Content ($page.name+'-verified.json') -Raw | ConvertFrom-Json
            $expectedRevision = $previous.revision
        }
        if ($latest.data.document.revision_id -ne $expectedRevision) { throw ('Concurrent/interrupted edit: '+$page.name+'. Inspect journal before continuing.') }
        for ($i=$page.segments-1; $i -ge 0; $i--) {
            $raw = & $python './inspect_publication.py' plan $page.name ($page.name+'-latest.json') --segment $i
            if ($LASTEXITCODE -ne 0) { throw ('Resource/range check failed: '+$page.name) }
            $operation = $raw | ConvertFrom-Json
            if ($operation.command -eq 'skip') { continue }
            $argv = @('docs','+update','--doc',$page.doc,'--command',$operation.command,'--revision-id',[string]$operation.revision,'--doc-format','xml')
            if ($operation.start) { $argv += @('--start-block-id',$operation.start,'--end-block-id',$operation.end) }
            else { $argv += @('--block-id',$operation.block) }
            if ($operation.command -ne 'block_delete') { $argv += @('--content',('@./'+$operation.content)) }
            $null = Invoke-Lark $argv ($page.name+'-segment-'+$i) -Write
            $null = Fetch-Page $page.doc $page.name
            Write-Output ($page.name+': segment '+$i+' updated')
        }
        Verify-Page 'verify' $page.name
    }
}
elseif ($Mode -eq 'verify') {
    foreach ($page in $plan.existing_pages) {
        $null = Fetch-Page $page.doc $page.name
        Verify-Page 'verify' $page.name
    }
    $created = @(Get-Content './redis-created.json' -Raw | ConvertFrom-Json)
    foreach ($page in $created) {
        $null = Fetch-Page $page.obj_token $page.key
        Verify-Page 'verify-new' $page.key
    }
    foreach ($entry in @(@{name='project';node='WN1jwXDj0iPXm7kwLyHcmneenlb'},@{name='payment';node='PCQ7wP9kMikz0eklmNYcH1J1nwh'},@{name='redis';node='O4iUwSvl1i4Je7keOL0ci1iAnLf'})) {
        $value = Invoke-Lark @('wiki','+node-list','--space-id','7693476782908935142','--parent-node-token',$entry.node,'--page-all','--page-limit','0') ($entry.name+'-final-children')
        if ($value.data.has_more) { throw 'Incomplete final node list.' }
        Save-Json $value (Join-Path $PSScriptRoot ($entry.name+'-final-children.json'))
    }
}
