param(
    [switch]$WriteEvidence,
    [ValidateSet('baseline', 'retry', 'recovery', 'crash')][string]$Stage = 'baseline'
)

$ErrorActionPreference = 'Stop'
$paymentWorkspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$paymentPreviousLocation = Get-Location
$paymentPreviousEnvironment = @{}
$paymentTestExitCode = 1
$paymentProfileValidated = $false
$paymentCleanupFailed = $false

try {
    Set-Location -LiteralPath $paymentWorkspace
    $paymentComposeArguments = @('compose')
    $paymentTestFile = 'backend/apps/payments/tests/test_event_mq.py'
    if ($Stage -eq 'retry') {
        $paymentComposeArguments += @('-f', 'compose.yaml', '-f', 'compose.payment-mq-retry-test.yaml')
        $paymentTestFile = 'backend/apps/payments/tests/test_event_mq_retry.py'
    }
    if ($Stage -eq 'recovery') {
        $paymentComposeArguments += @('-f', 'compose.yaml', '-f', 'compose.payment-mq-recovery-test.yaml')
        $paymentTestFile = 'backend/apps/payments/tests/test_event_mq_recovery.py'
    }
    if ($Stage -eq 'crash') {
        $paymentComposeArguments += @('-f', 'compose.yaml', '-f', 'compose.payment-mq-crash-test.yaml')
        $paymentTestFile = 'backend/apps/payments/tests/test_event_mq_crash.py'
    }
    # Compose 已解析本机配置；只在内存传递密码/签名密钥，不打印完整配置。
    $paymentComposeText = & docker @paymentComposeArguments --profile payment-mq-test config --format json
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read the test profile.' }
    $paymentCompose = ($paymentComposeText -join "`n") | ConvertFrom-Json
    $paymentWorkerEnvironment = $paymentCompose.services.'payment-worker-test'.environment
    $paymentBrokerEnvironment = $paymentCompose.services.'rabbitmq-test'.environment
    if ($paymentWorkerEnvironment.MYSQL_DATABASE -ne 'test_project_one' -or
        $paymentWorkerEnvironment.MYSQL_TEST_DATABASE -ne 'test_project_one' -or
        $paymentWorkerEnvironment.MYSQL_HOST -ne 'db' -or
        $paymentWorkerEnvironment.MYSQL_PORT -ne '3306' -or
        $paymentWorkerEnvironment.CELERY_TASK_DEFAULT_QUEUE -ne 'payment-events-test' -or
        $paymentBrokerEnvironment.RABBITMQ_DEFAULT_VHOST -ne 'project_one_test') {
        throw 'The MQ profile does not target the isolated database, queue and vhost.'
    }
    if ($Stage -in @('retry', 'recovery') -and $paymentWorkerEnvironment.PROJECT_ONE_MQ_RETRY_TEST_WORKER -ne 'true') {
        throw 'The retry experiment requires its isolated Worker override.'
    }
    if ($Stage -eq 'recovery' -and $paymentWorkerEnvironment.PROJECT_ONE_MQ_RECOVERY_PUBLICATION_WINDOW -ne 'true') {
        throw 'The recovery experiment requires its isolated publication window.'
    }
    if ($Stage -eq 'crash' -and (
        $paymentWorkerEnvironment.PROJECT_ONE_MQ_CRASH_TEST_WORKER -ne 'true' -or
        $paymentWorkerEnvironment.PAYMENT_EVENT_PROCESSING_LEASE_SECONDS -ne '8')) {
        throw 'The crash experiment requires its isolated Worker and eight-second lease.'
    }
    $paymentProfileValidated = $true
    # Stop old consumers before pytest creates or migrates a fresh database.
    & docker @paymentComposeArguments --profile payment-mq-test stop payment-worker-test
    if ($LASTEXITCODE -ne 0) { throw 'Unable to stop the isolated Worker before database setup.' }
    $paymentBrokerUrl = 'amqp://{0}:{1}@127.0.0.1:5673/project_one_test' -f
        [uri]::EscapeDataString($paymentBrokerEnvironment.RABBITMQ_DEFAULT_USER),
        [uri]::EscapeDataString($paymentBrokerEnvironment.RABBITMQ_DEFAULT_PASS)
    $paymentTestEnvironment = @{
        DJANGO_ENV = 'test'
        DJANGO_SECRET_KEY = $paymentWorkerEnvironment.DJANGO_SECRET_KEY
        MYSQL_DATABASE = 'test_project_one'
        MYSQL_TEST_DATABASE = 'test_project_one'
        MYSQL_HOST = '127.0.0.1'
        MYSQL_PORT = '3307'
        MYSQL_USER = $paymentWorkerEnvironment.MYSQL_USER
        MYSQL_PASSWORD = $paymentWorkerEnvironment.MYSQL_PASSWORD
        CATALOG_CACHE_ENABLED = 'false'
        PAYMENT_EVENTS_ASYNC = 'true'
        PAYMENT_EVENT_PROCESSING_LEASE_SECONDS = $(if ($Stage -eq 'crash') { '8' } else { '60' })
        CELERY_BROKER_URL = $paymentBrokerUrl
        CELERY_TASK_DEFAULT_QUEUE = 'payment-events-test'
        PROJECT_ONE_TEST_MQ_BROKER_URL = $paymentBrokerUrl
        PROJECT_ONE_TEST_MQ_STAGE = $Stage
        PROJECT_ONE_WRITE_MQ_EVIDENCE = $(if ($WriteEvidence) { 'true' } else { 'false' })
    }
    foreach ($paymentName in $paymentTestEnvironment.Keys) {
        $paymentPreviousEnvironment[$paymentName] = [Environment]::GetEnvironmentVariable($paymentName, 'Process')
        [Environment]::SetEnvironmentVariable($paymentName, $paymentTestEnvironment[$paymentName], 'Process')
    }
    Write-Host 'Running real RabbitMQ/Worker tests against test_project_one; the development database is not used.'
    & .\.venv\Scripts\python.exe -X utf8 -m pytest $paymentTestFile -q --tb=short
    $paymentTestExitCode = $LASTEXITCODE
} finally {
    # Even failed tests must stop this dedicated Worker before another pytest recreates its DB.
    if ($paymentProfileValidated -and (Get-Location).Path -eq $paymentWorkspace) {
        & docker @paymentComposeArguments --profile payment-mq-test stop payment-worker-test
        if ($LASTEXITCODE -ne 0) {
            $paymentCleanupFailed = $true
            Write-Warning 'Unable to stop the isolated Worker after verification.'
        }
        if ($Stage -eq 'recovery') {
            & docker @paymentComposeArguments --profile payment-mq-test up -d --no-deps --wait --wait-timeout 45 rabbitmq-test
            if ($LASTEXITCODE -ne 0) {
                $paymentCleanupFailed = $true
                Write-Warning 'Unable to restore the isolated Broker after verification.'
            }
        }
    }
    foreach ($paymentName in $paymentPreviousEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($paymentName, $paymentPreviousEnvironment[$paymentName], 'Process')
    }
    Set-Location -LiteralPath $paymentPreviousLocation.Path
}

if ($paymentCleanupFailed) { throw 'MQ verification cleanup failed; the isolated Worker may still be running.' }
if ($paymentTestExitCode -ne 0) { throw "MQ verification failed with exit code $paymentTestExitCode." }
