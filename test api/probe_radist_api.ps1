param(
    [Parameter(Mandatory = $true)]
    [string]$ApiKey,

    [int]$CompanyId = 205113,
    [int]$HoursBack = 24,
    [int]$PageLimit = 100
)

$ErrorActionPreference = "Stop"

$baseUrl = "https://api.radist.online/v2"
$headers = @{ "X-Api-Key" = $ApiKey }
$outDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$thresholdUtc = (Get-Date).ToUniversalTime().AddHours(-$HoursBack)

function Invoke-RadistGet {
    param([string]$PathAndQuery)
    $attempt = 0
    $maxAttempts = 6
    while ($true) {
        try {
            return Invoke-RestMethod -Method Get -Uri "$baseUrl$PathAndQuery" -Headers $headers
        } catch {
            $attempt++
            $msg = $_.Exception.Message
            if ($attempt -ge $maxAttempts -or $msg -notmatch "429") {
                throw
            }
            Start-Sleep -Seconds ([Math]::Min(2 * $attempt, 12))
        }
    }
}

function Get-MessagesForChat {
    param(
        [int]$CompanyId,
        [int]$ChatId
    )

    $all = New-Object System.Collections.Generic.List[object]
    $seen = @{}
    $until = $null

    while ($true) {
        $uri = "/companies/$CompanyId/messaging/messages/?chat_id=$ChatId&limit=100"
        if ($null -ne $until) {
            $uri += "&until=$([uri]::EscapeDataString($until.ToString('o')))"
        }

        $batch = Invoke-RadistGet -PathAndQuery $uri
        if ($null -eq $batch -or $batch.Count -eq 0) {
            break
        }

        $oldest = $null
        foreach ($m in $batch) {
            if (-not $seen.ContainsKey($m.message_id)) {
                $seen[$m.message_id] = $true
                $all.Add($m)
            }
            $dt = [datetime]$m.created_at
            if ($null -eq $oldest -or $dt -lt $oldest) {
                $oldest = $dt
            }
        }

        if ($batch.Count -lt 100) {
            break
        }

        $until = $oldest.AddMilliseconds(-1)
    }

    return $all
}

$sources = Invoke-RadistGet -PathAndQuery "/companies/$CompanyId/messaging/chats/sources/"
$sources | ConvertTo-Json -Depth 20 | Out-File -FilePath (Join-Path $outDir "probe_sources.json") -Encoding utf8

$waConnectionIds = @(
    $sources |
        Where-Object { $_.type -in @("whatsapp", "waba") } |
        ForEach-Object { [int]$_.connection_id }
)

$contacts = New-Object System.Collections.Generic.List[object]
$cursor = $null
while ($true) {
    $uri = "/companies/$CompanyId/messaging/chats/with_contacts/?limit=$PageLimit"
    if ($cursor) {
        $uri += "&cursor=$([uri]::EscapeDataString($cursor))"
    }

    $page = Invoke-RadistGet -PathAndQuery $uri
    foreach ($item in $page.data) {
        $contacts.Add($item)
    }

    $cursor = $page.response_metadata.next_cursor
    if ([string]::IsNullOrWhiteSpace($cursor)) {
        break
    }
}

$contacts | ConvertTo-Json -Depth 60 | Out-File -FilePath (Join-Path $outDir "probe_all_contacts_with_chats.json") -Encoding utf8

$dialogsToProcess = New-Object System.Collections.Generic.List[object]
foreach ($contact in $contacts) {
    foreach ($chat in $contact.chats) {
        if ($waConnectionIds -notcontains [int]$chat.connection_id) {
            continue
        }

        $updatedAt = [datetime]$contact.last_chat_updated_at
        if ($updatedAt -lt $thresholdUtc) {
            continue
        }

        $dialogsToProcess.Add([pscustomobject]@{
            contact_id = $contact.contact_id
            contact_name = $contact.contact_name
            contact_last_chat_updated_at = $contact.last_chat_updated_at
            chat = $chat
        })
    }
}

$result = New-Object System.Collections.Generic.List[object]
foreach ($d in $dialogsToProcess) {
    $chat = $d.chat
    $messages = Get-MessagesForChat -CompanyId $CompanyId -ChatId ([int]$chat.chat_id)
    if ($messages.Count -eq 0) {
        continue
    }

    $sorted = $messages | Sort-Object { [datetime]$_.created_at }
    $first = $sorted[0]
    $last = $sorted[$sorted.Count - 1]

    $result.Add([pscustomobject]@{
        contact_id = $d.contact_id
        contact_name = $d.contact_name
        phone = $chat.phone
        connection_id = $chat.connection_id
        connection_chat_id = $chat.chat_id
        source_chat_id = $chat.source_chat_id
        first_message_at = $first.created_at
        last_message_at = $last.created_at
        messages_count = $sorted.Count
        messages = $sorted
    })
}

$result | ConvertTo-Json -Depth 100 | Out-File -FilePath (Join-Path $outDir "probe_dialogs_last_${HoursBack}h_full.json") -Encoding utf8

$summary = $result | Select-Object `
    contact_name, phone, connection_id, connection_chat_id, source_chat_id, `
    first_message_at, last_message_at, messages_count

$summary | ConvertTo-Json -Depth 10 | Out-File -FilePath (Join-Path $outDir "probe_dialogs_last_${HoursBack}h_summary.json") -Encoding utf8

Write-Output ("Saved dialogs: {0}" -f $result.Count)
Write-Output ("Threshold UTC: {0}" -f $thresholdUtc.ToString("o"))
