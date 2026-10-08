param([Parameter(Mandatory = $true)][string]$ArgumentPath)
$commandArguments = @(Get-Content -LiteralPath $ArgumentPath -Raw -Encoding utf8 | ConvertFrom-Json)
& lark-cli @commandArguments
exit $LASTEXITCODE
