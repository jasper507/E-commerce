param([ValidateSet('Create', 'Finish', 'Readback', 'Index')][string]$Command)
$ErrorActionPreference = 'Stop'
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER = '1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER = '1'
$taskBase = $PSScriptRoot
$remoteDirectory = Join-Path $taskBase 'remote'
$planPath = Join-Path $taskBase 'publication-plan.json'
$publication = Get-Content -LiteralPath $planPath -Raw -Encoding utf8 | ConvertFrom-Json
$projectNode = 'WN1jwXDj0iPXm7kwLyHcmneenlb'
$spaceNumber = '7693476782908935142'

function Save-Publication {
    $publication | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $planPath -Encoding utf8
}
function Set-Field($object, [string]$name, $value) {
    $object | Add-Member -NotePropertyName $name -NotePropertyValue $value -Force
}
function Invoke-LarkRecord([string[]]$commandArguments, [string]$recordName) {
    $commandOutput = & lark-cli @commandArguments
    $commandExit = $LASTEXITCODE
    $rawResponse = $commandOutput -join "`n"
    $rawResponse | Set-Content -LiteralPath (Join-Path $remoteDirectory "$recordName.json") -Encoding utf8
    $response = $rawResponse | ConvertFrom-Json
    if ($commandExit -ne 0 -or $response.ok -ne $true) {
        throw "CLI failed exit=$commandExit, action=$($commandArguments[0..1] -join ' '), error=$($response.error.message), hint=$($response.error.hint)"
    }
    if ($response.identity -ne 'user') { throw 'Expected user identity' }
    return $response
}

if ($Command -eq 'Create') {
    foreach ($page in $publication.pages) {
        $parentNode = if ($page.id -eq 0) { $projectNode } else { $publication.pages[0].node_token }
        if (-not $page.document_id) {
            if ($page.id -eq 0) {
                $node = (Invoke-LarkRecord @('wiki','+node-get','--node-token','B49NwtiePiCqqFkz61kc4ac9n79','--as','user','--format','json') 'page-00-wiki-created-readback').data
            } else {
                $node = (Invoke-LarkRecord @('wiki','+node-create','--space-id',$spaceNumber,'--parent-node-token',$parentNode,'--title',$page.title,'--obj-type','docx','--as','user','--format','json') ('page-{0:00}-wiki-create' -f $page.id)).data
            }
            if ($node.space_id -ne $spaceNumber -or $node.parent_node_token -ne $parentNode) { throw 'Wrong Wiki parent' }
            Set-Field $page 'document_id' $node.obj_token
            Set-Field $page 'node_token' $node.node_token
            Set-Field $page 'url' ('https://ocnbkcx1ug58.feishu.cn/wiki/' + $node.node_token)
            Set-Field $publication 'status' 'creating'
            Save-Publication
        }
        if (-not $page.body_written) {
            $before = Invoke-LarkRecord @('docs','+fetch','--doc',$page.document_id,'--as','user','--format','json') ('page-{0:00}-empty-before' -f $page.id)
            $beforeXml = [xml]('<doc>' + $before.data.document.content + '</doc>')
            foreach ($block in $beforeXml.doc.ChildNodes) {
                if ($block.Name -ne 'title' -and -not ($block.Name -eq 'p' -and [string]::IsNullOrWhiteSpace($block.InnerText))) { throw 'New document already contains body; do not append twice' }
            }
            $pageFolder = Join-Path $taskBase $page.workspace
            $draftText = Get-Content -LiteralPath (Join-Path $pageFolder 'draft.xml') -Raw -Encoding utf8
            $bodyText = [regex]::Replace($draftText, '<title>.*?</title>\s*', '', [System.Text.RegularExpressions.RegexOptions]::Singleline)
            $bodyPath = Join-Path $pageFolder 'initial-body.xml'
            $bodyText | Set-Content -LiteralPath $bodyPath -Encoding utf8
            $relativeBody = [IO.Path]::GetRelativePath($taskBase, $bodyPath).Replace('\','/')
            $written = Invoke-LarkRecord @('docs','+update','--doc',$page.document_id,'--command','append','--content',('@./'+$relativeBody),'--as','user','--format','json') ('page-{0:00}-body-write' -f $page.id)
            if ($written.data.result -ne 'success' -or $written.data.warnings.Count -ne 0) { throw 'Body write returned partial result or warnings' }
            $boards = @($written.data.document.new_blocks | Where-Object { $_.block_type -eq 'whiteboard' })
            if ($boards.Count -eq 1) { Set-Field $page 'board_token' $boards[0].block_token }
            Set-Field $page 'body_written' $true
            Set-Field $page 'creation_warnings' @($written.data.warnings)
            Save-Publication
        }
        @{created=$page.id;title=$page.title;url=$page.url} | ConvertTo-Json -Compress
    }
    Set-Field $publication 'creation_route' 'Wiki node-create and append to verified empty Docx; existing user scopes; direct PowerShell CLI'
    Set-Field $publication 'status' 'created'
    Save-Publication
}

if ($Command -eq 'Finish') {
    foreach ($page in $publication.pages) {
        $pageFolder = Join-Path $taskBase $page.workspace
        if (-not $page.navigation_written) {
            $before = Invoke-LarkRecord @('docs','+fetch','--doc',$page.document_id,'--detail','with-ids','--scope','keyword','--keyword','导航将在发布时补齐','--as','user','--format','json') ('page-{0:00}-navigation-before' -f $page.id)
            $beforeXml = [xml]('<doc>' + $before.data.document.content + '</doc>')
            $block = @($beforeXml.SelectNodes('//p') | Where-Object { $_.InnerText.Contains('导航将在发布时补齐') })[0]
            $blockIdentifier = $block.GetAttribute('id')
            if (-not $blockIdentifier) { $blockIdentifier = $block.GetAttribute('block-id') }
            if (-not $blockIdentifier) { $blockIdentifier = $block.GetAttribute('block_id') }
            if (-not $blockIdentifier) { throw 'Navigation block ID missing' }
            $relativeNavigation = [IO.Path]::GetRelativePath($taskBase, (Join-Path $pageFolder 'navigation.xml')).Replace('\','/')
            $written = Invoke-LarkRecord @('docs','+update','--doc',$page.document_id,'--command','block_replace','--block-id',$blockIdentifier,'--content',('@./'+$relativeNavigation),'--as','user','--format','json') ('page-{0:00}-navigation-write' -f $page.id)
            if ($written.data.result -ne 'success' -or $written.data.warnings.Count -ne 0) { throw 'Navigation write failed' }
            Set-Field $page 'navigation_written' $true
            Save-Publication
        }
        if ($page.diagram -and -not $page.board_written) {
            if (-not $page.board_token) {
                $before = Invoke-LarkRecord @('docs','+fetch','--doc',$page.document_id,'--as','user','--format','json') ('page-{0:00}-board-before' -f $page.id)
                $beforeXml = [xml]('<doc>' + $before.data.document.content + '</doc>')
                Set-Field $page 'board_token' $beforeXml.doc.whiteboard.token
            }
            if (-not $page.board_token) { throw 'Board token missing' }
            $rawPath = Join-Path $taskBase ('diagrams/' + $page.diagram + '/diagram.json')
            $nativeNodes = (Get-Content -LiteralPath $rawPath -Raw -Encoding utf8 | ConvertFrom-Json).nodes
            $relativeRaw = [IO.Path]::GetRelativePath($taskBase,$rawPath).Replace('\','/')
            $written = Invoke-LarkRecord @('whiteboard','+update','--whiteboard-token',$page.board_token,'--source',('@./'+$relativeRaw),'--input_format','raw','--idempotent-token',('20261008-flash-sale-board-{0:00}-v1' -f $page.id),'--as','user','--format','json') ('board-{0:00}-write' -f $page.id)
            Set-Field $page 'board_written' $true
            Set-Field $page 'expected_board_node_count' $nativeNodes.Count
            Save-Publication
        }
        @{finished=$page.id;board=[bool]$page.board_written} | ConvertTo-Json -Compress
    }
    Set-Field $publication 'status' 'published_pending_readback'
    Save-Publication
}

if ($Command -eq 'Readback') {
    foreach ($page in $publication.pages) {
        $fetched = Invoke-LarkRecord @('docs','+fetch','--doc',$page.document_id,'--as','user','--format','json') ('page-{0:00}-fetch' -f $page.id)
        if ($page.diagram) {
            $raw = Invoke-LarkRecord @('whiteboard','+export','--whiteboard-token',$page.board_token,'--output-type','raw','--output',('remote/board-{0:00}-raw.json' -f $page.id),'--as','user','--format','json') ('board-{0:00}-raw-result' -f $page.id)
            $preview = Invoke-LarkRecord @('whiteboard','+export','--whiteboard-token',$page.board_token,'--output-type','preview','--output',('remote/board-{0:00}-preview' -f $page.id),'--as','user','--format','json') ('board-{0:00}-preview-result' -f $page.id)
        }
        @{readback=$page.id;revision=$fetched.data.document.revision_id} | ConvertTo-Json -Compress
    }
    $children = Invoke-LarkRecord @('wiki','+node-list','--space-id',$spaceNumber,'--parent-node-token',$publication.pages[0].node_token,'--as','user','--format','json') 'children-final'
    if ($children.data.has_more -or $children.data.nodes.Count -ne 6) { throw 'Unexpected child list' }
}

if ($Command -eq 'Index') {
    $before = Invoke-LarkRecord @('docs','+fetch','--doc','P0LPd9AkmoAhq8xCgOdcqEL2nag','--as','user','--format','json') 'project-index-before'
    if (-not $before.data.document.content.Contains($publication.pages[0].url)) {
        $indexText = '<p><a href="' + $publication.pages[0].url + '">秒杀与库存并发</a>：业务限购、请求幂等、条件 UPDATE 选型、事务回滚与锁等待、支付资格生命周期和响应丢失恢复；总览下有六个问题子页及六张原生时序图。</p>'
        $indexText | Set-Content -LiteralPath (Join-Path $taskBase 'project-index-addition.xml') -Encoding utf8
        $written = Invoke-LarkRecord @('docs','+update','--doc','P0LPd9AkmoAhq8xCgOdcqEL2nag','--command','append','--content','@./project-index-addition.xml','--as','user','--format','json') 'project-index-write'
        if ($written.data.result -ne 'success' -or $written.data.warnings.Count -ne 0) { throw 'Index write failed' }
    }
    $after = Invoke-LarkRecord @('docs','+fetch','--doc','P0LPd9AkmoAhq8xCgOdcqEL2nag','--as','user','--format','json') 'project-index-after'
    if (-not $after.data.document.content.Contains($publication.pages[0].url)) { throw 'New navigation missing' }
    $roots = Invoke-LarkRecord @('wiki','+node-list','--space-id',$spaceNumber,'--parent-node-token',$projectNode,'--as','user','--format','json') 'modules-final'
    @{index='verified';modules=$roots.data.nodes.Count} | ConvertTo-Json -Compress
}
