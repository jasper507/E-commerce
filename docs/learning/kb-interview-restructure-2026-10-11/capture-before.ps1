$ErrorActionPreference = 'Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER = '1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER = '1'
$folder = Join-Path $PSScriptRoot 'before'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$targets = @(
    @{ name='project'; node='WN1jwXDj0iPXm7kwLyHcmneenlb'; doc='P0LPd9AkmoAhq8xCgOdcqEL2nag' },
    @{ name='payment'; node='PCQ7wP9kMikz0eklmNYcH1J1nwh'; doc='T6O0deWUnowaBsxM6LgcJfuynjb' },
    @{ name='redis'; node='O4iUwSvl1i4Je7keOL0ci1iAnLf'; doc='KKBXdHFQqoZRCNxJv1YcFb29nkf' },
    @{ name='payment-01'; node='N37xwtJ27iZ4XmkYa1uchxswnbc'; doc='Onm2dT0Two29z9xQwi5cdrrAncT' },
    @{ name='payment-02'; node='Dn70wUIlKixYzMk0X4kcCvGsnR3'; doc='Gpgdd1UeAo4fPIx9biBc9gn4nxd' },
    @{ name='payment-03'; node='JyJGwzL2CiiFKhkzrLUcx3pgnUb'; doc='Qrgid0JhEo91HcxEjktcVk0fnbe' },
    @{ name='payment-04'; node='I0a5wc7LUibRArkMkUHct0swnvc'; doc='DTCsdol7JoPH8hxKznfcHTAInUc' },
    @{ name='payment-05'; node='CDXkw84eWiDj5ZkzitPcs1PhnIh'; doc='PHvqdJ10bob22mxCHpccJHKgn4b' },
    @{ name='payment-06'; node='Kc9EwXMzni8ttTkYgk6cxSFknSd'; doc='AU7EdZEQ7oZRHexUSYicaQU5nHe' },
    @{ name='payment-07'; node='KsqfwyvRZiYtsnkB09HcBsQWnpe'; doc='RMlTdRE77oLPyBxAE9GcuUuTnwh' }
)
foreach ($target in $targets) {
    $argv = @('docs','+fetch','--doc',$target.doc,'--detail','full','--doc-format','xml','--as','user','--format','json')
    $raw = & lark-cli @argv
    if ($LASTEXITCODE -ne 0) { throw "Fetch failed: $($target.name)" }
    $result = $raw | ConvertFrom-Json
    if (-not $result.ok) { throw "Fetch not ok: $($target.name)" }
    $saved = $result | ConvertTo-Json -Depth 100
    [IO.File]::WriteAllText((Join-Path $folder ($target.name+'.json')), $saved.TrimEnd()+[Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    Write-Output ($target.name + ': revision=' + $result.data.document.revision_id + ', characters=' + $result.data.document.content.Length)
}
foreach ($target in $targets | Select-Object -First 3) {
    $raw = & lark-cli wiki +node-list --space-id '7693476782908935142' --parent-node-token $target.node --page-all --page-limit 0 --as user --format json
    if ($LASTEXITCODE -ne 0) { throw "List failed: $($target.name)" }
    $result = $raw | ConvertFrom-Json
    if (-not $result.ok -or $result.data.has_more) { throw "List incomplete: $($target.name)" }
    [IO.File]::WriteAllText((Join-Path $folder ($target.name+'-children.json')), ($result | ConvertTo-Json -Depth 100).TrimEnd()+[Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    Write-Output ($target.name + ': children=' + @($result.data.nodes).Count)
}
