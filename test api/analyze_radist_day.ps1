param(
    [Parameter(Mandatory = $true)]
    [string]$ApiKey,

    [int]$CompanyId = 205113,
    [string]$LocalDate = "2026-02-18",
    [string]$TimezoneOffset = "+05:00"
)

$ErrorActionPreference = "Stop"

$baseUrl = "https://api.radist.online/v2"
$headers = @{ "X-Api-Key" = $ApiKey }
$outDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$date = [datetime]::ParseExact($LocalDate, "yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)
$normalizedOffset = $TimezoneOffset.Trim()
if ($normalizedOffset.StartsWith("+")) {
    $normalizedOffset = $normalizedOffset.Substring(1)
}
if ($normalizedOffset.Length -eq 5) {
    $normalizedOffset = "$normalizedOffset`:00"
}
$offset = [System.TimeSpan]::Parse($normalizedOffset)
$startLocal = [datetimeoffset]::new($date.Year, $date.Month, $date.Day, 0, 0, 0, $offset)
$endLocal = $startLocal.AddDays(1).AddMilliseconds(-1)
$startUtc = $startLocal.ToUniversalTime().UtcDateTime
$endUtc = $endLocal.ToUniversalTime().UtcDateTime

function Invoke-RadistGet {
    param([string]$PathAndQuery)
    $attempt = 0
    $maxAttempts = 8
    while ($true) {
        try {
            return Invoke-RestMethod -Method Get -Uri "$baseUrl$PathAndQuery" -Headers $headers
        } catch {
            $attempt++
            $msg = $_.Exception.Message
            if ($attempt -ge $maxAttempts -or $msg -notmatch "429") {
                throw
            }
            Start-Sleep -Seconds ([Math]::Min(2 * $attempt, 15))
        }
    }
}

function Get-MessagesInWindowForChat {
    param(
        [int]$ChatId,
        [datetime]$StartUtc,
        [datetime]$EndUtc
    )

    $inWindow = New-Object System.Collections.Generic.List[object]
    $until = $null
    $seen = @{}

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
            if ($seen.ContainsKey($m.message_id)) {
                continue
            }
            $seen[$m.message_id] = $true

            $dt = [datetime]$m.created_at
            if ($dt -ge $StartUtc -and $dt -le $EndUtc) {
                $inWindow.Add($m)
            }
            if ($null -eq $oldest -or $dt -lt $oldest) {
                $oldest = $dt
            }
        }

        if ($batch.Count -lt 100) {
            break
        }
        if ($null -ne $oldest -and $oldest -lt $StartUtc) {
            break
        }

        $until = $oldest.AddMilliseconds(-1)
        Start-Sleep -Milliseconds 120
    }

    return $inWindow
}

$sources = Invoke-RadistGet -PathAndQuery "/companies/$CompanyId/messaging/chats/sources/"
$waConnectionIds = @(
    $sources |
        Where-Object { $_.type -in @("whatsapp", "waba") } |
        ForEach-Object { [int]$_.connection_id }
)

$contacts = New-Object System.Collections.Generic.List[object]
$cursor = $null
while ($true) {
    $uri = "/companies/$CompanyId/messaging/chats/with_contacts/?limit=100"
    if ($cursor) {
        $uri += "&cursor=$([uri]::EscapeDataString($cursor))"
    }
    $page = Invoke-RadistGet -PathAndQuery $uri
    foreach ($item in $page.data) {
        $contacts.Add($item)
    }
    $cursor = $page.response_metadata.next_cursor
    if ([string]::IsNullOrWhiteSpace($cursor)) { break }
}

$allChats = New-Object System.Collections.Generic.List[object]
foreach ($c in $contacts) {
    foreach ($ch in $c.chats) {
        if ($waConnectionIds -contains [int]$ch.connection_id) {
            $allChats.Add([pscustomobject]@{
                contact_id = $c.contact_id
                contact_name = $c.contact_name
                chat = $ch
            })
        }
    }
}

$dialogs = New-Object System.Collections.Generic.List[object]
$i = 0
foreach ($item in $allChats) {
    $i++
    $chat = $item.chat
    $msgs = Get-MessagesInWindowForChat -ChatId ([int]$chat.chat_id) -StartUtc $startUtc -EndUtc $endUtc
    if ($msgs.Count -eq 0) {
        continue
    }

    $sorted = $msgs | Sort-Object { [datetime]$_.created_at }
    $dialogs.Add([pscustomobject]@{
        contact_id = $item.contact_id
        contact_name = $item.contact_name
        phone = $chat.phone
        connection_id = $chat.connection_id
        connection_chat_id = $chat.chat_id
        source_chat_id = $chat.source_chat_id
        first_message_at = $sorted[0].created_at
        last_message_at = $sorted[$sorted.Count - 1].created_at
        messages_count = $sorted.Count
        messages = $sorted
    })
}

$fileBase = "day_${LocalDate}_utcplus5"
$dialogs | ConvertTo-Json -Depth 100 | Out-File (Join-Path $outDir "${fileBase}_full.json") -Encoding utf8
$dialogs | Select-Object contact_name,phone,connection_id,connection_chat_id,source_chat_id,first_message_at,last_message_at,messages_count |
    ConvertTo-Json -Depth 10 | Out-File (Join-Path $outDir "${fileBase}_summary.json") -Encoding utf8

$allMsgs = @($dialogs | ForEach-Object { $_.messages })
$stats = [pscustomobject]@{
    local_date = $LocalDate
    timezone_offset = $TimezoneOffset
    window_start_local = $startLocal.ToString("o")
    window_end_local = $endLocal.ToString("o")
    window_start_utc = $startUtc.ToString("o")
    window_end_utc = $endUtc.ToString("o")
    dialogs_count = $dialogs.Count
    unique_phones = (@($dialogs.phone | Sort-Object -Unique)).Count
    messages_count = $allMsgs.Count
    inbound_count = (@($allMsgs | Where-Object { $_.direction -eq "inbound" })).Count
    outbound_count = (@($allMsgs | Where-Object { $_.direction -eq "outbound" })).Count
}
$stats | ConvertTo-Json -Depth 10 | Out-File (Join-Path $outDir "${fileBase}_stats.json") -Encoding utf8
$stats | ConvertTo-Json -Depth 10
