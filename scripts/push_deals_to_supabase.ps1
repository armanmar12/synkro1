param(
    [Parameter(Mandatory = $false)]
    [string]$TenantId = "globalfruit",

    [Parameter(Mandatory = $false)]
    [string]$TableName = "deals",

    [Parameter(Mandatory = $false)]
    [switch]$DebugHttp,

    [Parameter(Mandatory = $false)]
    [string]$DialogsPath = "test api/radist_amo_match/dialogs.json",

    [Parameter(Mandatory = $false)]
    [string]$PipelinesPath = "amo testapi/pipelines.json",

    [Parameter(Mandatory = $false)]
    [int]$LocalUtcOffsetHours = 5
)

$ErrorActionPreference = "Stop"

# Note: amoCRM is read-only for Synkro. This script only uploads prepared data to Supabase.
# It does not call amoCRM APIs and must never be extended to write back into amoCRM.

function Get-EnvFromDotEnv {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        throw "Missing $Path"
    }

    $map = @{}
    foreach ($line in (Get-Content $Path -Encoding utf8)) {
        $t = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($t)) { continue }
        if ($t.StartsWith("#")) { continue }
        $eq = $t.IndexOf("=")
        if ($eq -lt 1) { continue }
        $k = $t.Substring(0, $eq).Trim()
        $v = $t.Substring($eq + 1).Trim()
        if (($v.StartsWith("'") -and $v.EndsWith("'")) -or ($v.StartsWith('"') -and $v.EndsWith('"'))) {
            $v = $v.Substring(1, $v.Length - 2)
        }
        $map[$k] = $v
    }
    return $map
}

function Build-StatusMap {
    param([object]$PipelinesJson)

    $map = @{}
    foreach ($p in $PipelinesJson.data._embedded.pipelines) {
        foreach ($s in $p._embedded.statuses) {
            $map[[string]$s.id] = $s.name
        }
    }
    return $map
}

function Get-ShortResponsible {
    param([string]$Responsible)
    if ([string]::IsNullOrWhiteSpace($Responsible)) { return "agent" }
    $at = $Responsible.IndexOf("@")
    if ($at -gt 0) { return $Responsible.Substring(0, $at) }
    return $Responsible
}

function Fix-Mojibake {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) { return $Text }

    $utf8 = [System.Text.Encoding]::UTF8
    $latin1 = [System.Text.Encoding]::GetEncoding(28591)
    $cp1251 = [System.Text.Encoding]::GetEncoding(1251)

    # Heuristic:
    # - Latin1-mojibake often contains 'Ð'/'Ñ'
    # - CP1251-mojibake of UTF-8 often contains a lot of 'Р'/'С' (every other char)
    $len = [Math]::Max(1, $Text.Length)
    $rsCount = ([regex]::Matches($Text, "[РС]")).Count
    $dnCount = ([regex]::Matches($Text, "[ÐÑ]")).Count

    if (($dnCount -ge 2) -or (($rsCount / $len) -ge 0.2)) {
        if ($dnCount -ge 2) {
            return $utf8.GetString($latin1.GetBytes($Text))
        }
        return $utf8.GetString($cp1251.GetBytes($Text))
    }

    return $Text
}

function Get-Attachments {
    param([object]$Message)

    $names = New-Object System.Collections.Generic.List[string]

    foreach ($k in @("file", "image", "audio", "video", "voice")) {
        if ($Message.PSObject.Properties.Name -notcontains $k) { continue }
        $obj = $Message.$k
        if ($null -eq $obj) { continue }
        if ($obj.PSObject.Properties.Name -contains "name") {
            $n = [string]$obj.name
            if (-not [string]::IsNullOrWhiteSpace($n)) { $names.Add($n) }
        }
    }

    return $names
}

function Get-MessageText {
    param([object]$Message)

    if ($Message.PSObject.Properties.Name -contains "text" -and $null -ne $Message.text) {
        if ($Message.text.PSObject.Properties.Name -contains "text") {
            $t = [string]$Message.text.text
            if (-not [string]::IsNullOrWhiteSpace($t)) { return (Fix-Mojibake -Text $t) }
        }
    }

    if ($Message.PSObject.Properties.Name -contains "waba_interactive" -and $null -ne $Message.waba_interactive) {
        if ($Message.waba_interactive.PSObject.Properties.Name -contains "body" -and $null -ne $Message.waba_interactive.body) {
            if ($Message.waba_interactive.body.PSObject.Properties.Name -contains "text") {
                $t = [string]$Message.waba_interactive.body.text
                if (-not [string]::IsNullOrWhiteSpace($t)) { return (Fix-Mojibake -Text $t) }
            }
        }
    }

    foreach ($k in @("file", "image", "audio", "video", "voice")) {
        if ($Message.PSObject.Properties.Name -notcontains $k) { continue }
        $obj = $Message.$k
        if ($null -eq $obj) { continue }
        if ($obj.PSObject.Properties.Name -contains "caption") {
            $t = [string]$obj.caption
            if (-not [string]::IsNullOrWhiteSpace($t)) { return (Fix-Mojibake -Text $t) }
        }
    }

    return ""
}

function Format-DialogNorm {
    param(
        [object[]]$Messages,
        [string]$Responsible,
        [int]$LocalUtcOffsetHours
    )

    $offset = New-TimeSpan -Hours $LocalUtcOffsetHours
    $short = Get-ShortResponsible -Responsible $Responsible

    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($m in ($Messages | Sort-Object { [datetime]$_.created_at })) {
        $dto = [DateTimeOffset]::Parse([string]$m.created_at)
        $local = $dto.ToOffset($offset)
        $ts = $local.ToString("yyyy-MM-dd HH:mm:ss")

        $who = "agent"
        if ([string]$m.direction -eq "inbound") { $who = "client" }
        elseif ([string]$m.direction -eq "outbound") { $who = $short }

        $text = Get-MessageText -Message $m
        $files = Get-Attachments -Message $m

        $line = "$ts  ${who}:"
        if (-not [string]::IsNullOrWhiteSpace($text)) {
            $line += " $text"
        }
        if ($files.Count -gt 0) {
            $line += (" [files: {0}]" -f (($files | Select-Object -Unique) -join ", "))
        }

        $lines.Add($line)
    }

    return ($lines -join "`n")
}

$envMap = Get-EnvFromDotEnv -Path ".env"
$supabaseUrl = $envMap["SUPABASE_URL"]
$serviceJwt = $envMap["SUPABASE_SERVICE_ROLE_JWT"]
if ([string]::IsNullOrWhiteSpace($supabaseUrl)) {
    throw "SUPABASE_URL missing in .env"
}
if ([string]::IsNullOrWhiteSpace($serviceJwt)) {
    throw "SUPABASE_SERVICE_ROLE_JWT missing in .env (need the legacy JWT service_role key that starts with eyJ...)"
}

$pipelines = Get-Content $PipelinesPath -Raw -Encoding utf8 | ConvertFrom-Json
$statusMap = Build-StatusMap -PipelinesJson $pipelines

$dialogs = Get-Content $DialogsPath -Raw -Encoding utf8 | ConvertFrom-Json

$rows = New-Object System.Collections.Generic.List[object]
foreach ($d in $dialogs) {
    $statusName = $null
    $sid = [string]$d.status_id
    if (-not [string]::IsNullOrWhiteSpace($sid) -and $statusMap.ContainsKey($sid)) {
        $statusName = $statusMap[$sid]
    }

    $messages = @()
    if ($d.PSObject.Properties.Name -contains "messages" -and $null -ne $d.messages) {
        $messages = @($d.messages | Sort-Object { [datetime]$_.created_at })
    }

    $rows.Add([pscustomobject]@{
        tenant_id          = $TenantId
        deal_id            = [int64]$d.lead_id
        deal_name          = (Fix-Mojibake -Text ([string]$d.lead_name))
        status_id          = if ($null -ne $d.status_id) { [int64]$d.status_id } else { $null }
        status             = $statusName
        responsible        = [string]$d.responsible
        phone              = [string]$d.phone
        chat_id            = if ($null -ne $d.chat_id) { [int64]$d.chat_id } else { $null }
        first_message_at   = [string]$d.first_message_at
        last_message_at    = [string]$d.last_message_at
        messages_count     = if ($null -ne $d.messages_count) { [int]$d.messages_count } else { $messages.Count }
        deal_attrs_json    = @{}
        contact_attrs_json = @{}
        dialog_raw         = $messages
        dialog_norm        = (Format-DialogNorm -Messages $messages -Responsible ([string]$d.responsible) -LocalUtcOffsetHours $LocalUtcOffsetHours)
        comment            = ""
    })
}

$headers = @{
    "apikey"        = $serviceJwt
    "Authorization" = "Bearer $serviceJwt"
    "Content-Type"  = "application/json; charset=utf-8"
    "Prefer"        = "resolution=merge-duplicates,return=minimal"
    "User-Agent"    = "synkro-etl/1.0"
}

$endpoint = "$supabaseUrl/rest/v1/${TableName}?on_conflict=tenant_id,deal_id"
$body = ($rows | ConvertTo-Json -Depth 100)
$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)

if ($DebugHttp) {
    Write-Output ("Endpoint: {0}" -f $endpoint)
    Write-Output ("Payload bytes: {0}" -f $bodyBytes.Length)
    try {
        Invoke-RestMethod -Method Get -Uri ("$supabaseUrl/rest/v1/${TableName}?select=tenant_id,deal_id&limit=1") -Headers $headers | Out-Null
        Write-Output "GET check: OK"
    } catch {
        Write-Output ("GET check: FAIL: {0}" -f $_.Exception.Message)
    }
}

try {
    Invoke-RestMethod -Method Post -Uri $endpoint -Headers $headers -Body $bodyBytes | Out-Null
} catch {
    $statusCode = $null
    $statusDesc = $null
    $errBody = $null
    if ($_.Exception.Response) {
        try { $statusCode = $_.Exception.Response.StatusCode.value__ } catch {}
        try { $statusDesc = $_.Exception.Response.StatusDescription } catch {}
        try {
            $sr = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            $errBody = $sr.ReadToEnd()
        } catch {}
    }
    throw ("HTTP POST failed ({0} {1}): {2}" -f $statusCode, $statusDesc, $errBody)
}
Write-Output ("Upserted deals: {0}" -f $rows.Count)
