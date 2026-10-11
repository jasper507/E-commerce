param([ValidateSet('parse','apply','verify')][string]$Mode='parse',[string]$Only='')
$ErrorActionPreference='Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1'
Set-Location -LiteralPath $PSScriptRoot
$python=Join-Path $PSScriptRoot '../../../.venv/Scripts/python.exe'
$plan=Get-Content './patch-plan.json' -Raw -Encoding UTF8|ConvertFrom-Json
foreach ($folder in @('latest','verified','patch-journal')) {New-Item -ItemType Directory -Force $folder|Out-Null}
$script:sequence=@(Get-ChildItem './patch-journal' -Filter '*.json').Count
function Save-Json($value,$path) {[IO.File]::WriteAllText((Join-Path $PSScriptRoot $path),($value|ConvertTo-Json -Depth 100)+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))}
function Invoke-Lark([string[]]$Arguments,[string]$Label,[switch]$Write) {
    $script:sequence+=1
    $raw=& lark-cli @Arguments --as user --format json
    $code=$LASTEXITCODE;$value=$raw|ConvertFrom-Json
    Save-Json $value ('patch-journal/{0:D3}-{1}.json' -f $script:sequence,$Label)
    if ($code -ne 0 -or -not $value.ok) {throw ('Inspect effects before retry: '+$Label)}
    if ($Write -and ($value.data.result -ne 'success' -or @($value.data.warnings).Count)) {throw ('Incomplete/warnings: '+$Label)}
    return $value
}
function Fetch($page) {
    $value=Invoke-Lark @('docs','+fetch','--doc',$page.doc,'--detail','full','--doc-format','xml') ($page.key+'-fetch')
    Save-Json $value ('latest/'+$page.key+'.json')
    return $value
}
function Check($kind,$key) {
    & $python './inspect_patches.py' $kind $key
    if ($LASTEXITCODE -ne 0) {throw ('Protection/semantic check failed '+$key)}
}
foreach ($page in $plan) {
    if ($Only -and $Only -ne $page.key) {continue}
    if ($Mode -eq 'parse') {
        foreach ($op in $page.operations) {
            $value=Invoke-Lark @('docs','+script','--command','parse','--content',('@./'+$op.path)) ($page.key+'-'+$op.label+'-profile')
            if ($value.data.assessment.status -ne 'passed') {throw 'Patch profile failed'}
        }
        Write-Output ($page.key+': patch profiles passed')
        continue
    }
    $latest=Fetch $page
    if ($Mode -eq 'apply' -and -not (Test-Path ('verified/'+$page.key+'.json'))) {
        if ($latest.data.document.revision_id -ne $page.base_revision) {throw ('Unexpected revision '+$page.key)}
        Check 'before' $page.key
        foreach ($op in $page.operations) {
            [xml]$currentTree='<doc>'+$latest.data.document.content+'</doc>'
            if (-not $currentTree.SelectSingleNode("//*[@id='$($op.block)']")) {throw ('Target ID no longer present; inspect fresh state '+$op.label)}
            $null=Invoke-Lark @('docs','+update','--doc',$page.doc,'--command',$op.command,'--block-id',$op.block,'--revision-id',[string]$latest.data.document.revision_id,'--doc-format','xml','--content',('@./'+$op.path)) ($page.key+'-'+$op.label) -Write
            $latest=Fetch $page
        }
    }
    Check 'after' $page.key
}
