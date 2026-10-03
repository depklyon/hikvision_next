<#
.SYNOPSIS
    Capture raw Hikvision event alerts (XML + pictures) from a camera/NVR.

.DESCRIPTION
    PowerShell-only version of capture_hikvision_events.py (no Python/Docker needed).
    Connects to the ISAPI alert stream (/ISAPI/Event/notification/alertStream), which
    works alongside the Home Assistant push notifications and requires no change to the
    device configuration.

    Every alert is saved to the output folder (raw XML and any attached images) and a
    one-line summary is printed, including all detection-target related tags, so we can
    see how the firmware reports human / vehicle detections.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\capture_hikvision_events.ps1 -CameraHost 192.168.1.7 -User admin

.EXAMPLE
    # Only summarize an already saved alert XML (offline test)
    powershell -ExecutionPolicy Bypass -File scripts\capture_hikvision_events.ps1 -ParseFile tests\fixtures\ISAPI\EventNotificationAlert\fielddetection_human.xml
#>
param(
    [string]$CameraHost = "192.168.1.7",
    [string]$User = "admin",
    [ValidateSet("http", "https")][string]$Scheme = "http",
    [string]$Output = "captures",
    [int]$ReadTimeoutSeconds = 120,
    [switch]$IncludeHeartbeat,
    [string]$ParseFile
)

$ErrorActionPreference = "Stop"
$AlertStreamPath = "/ISAPI/Event/notification/alertStream"
$HeartbeatEvents = @("videoloss")  # sent periodically with eventState=inactive

function Get-AlertSummary([byte[]]$Body) {
    <# Return @{ Type; State; Line } for an alert XML. #>
    $text = [System.Text.Encoding]::UTF8.GetString($Body).Trim()
    try {
        $doc = [xml]$text
    }
    catch {
        try {
            # some firmwares send non-escaped '&'
            $doc = [xml]([regex]::Replace($text, '&(?!amp;|lt;|gt;|quot;|apos;|#)', '&amp;'))
        }
        catch {
            return @{ Type = "unparsed"; State = ""; Line = "!! could not parse XML: $($_.Exception.Message)" }
        }
    }

    $values = [ordered]@{}
    foreach ($node in $doc.SelectNodes("//*")) {
        $value = $node.InnerText.Trim()
        if ($node.SelectNodes("*").Count -eq 0 -and $value) {
            if (-not $values.Contains($node.LocalName)) {
                $values[$node.LocalName] = New-Object System.Collections.ArrayList
            }
            [void]$values[$node.LocalName].Add($value)
        }
    }
    $first = {
        param($tag, $default = "")
        if ($values.Contains($tag)) { $values[$tag][0] } else { $default }
    }

    $eventType = & $first "eventType" "?"
    if ($eventType -eq "duration") { $eventType = "duration/" + (& $first "relationEvent" "?") }
    $eventState = & $first "eventState" "?"
    $channel = & $first "channelID" (& $first "dynChannelID" "-")
    $regions = if ($values.Contains("regionID")) { $values["regionID"] -join "," } else { "-" }

    # Everything that may carry the detected target, whatever the firmware calls it
    $targets = @()
    foreach ($tag in $values.Keys) {
        if ($tag -match 'target|object|human|vehicle|person|car' -and $tag -notmatch 'rect|^x$|^y$|width|height') {
            $targets += "$tag=$($values[$tag] -join '|')"
        }
    }
    $targetText = if ($targets.Count) { $targets -join "; " } else { "<none>" }

    $line = "event=$eventType  state=$eventState  channel=$channel  regions=$regions  " +
    "pictures=$(& $first 'detectionPicturesNumber' '-')  desc='$(& $first 'eventDescription' '-')'  TARGETS=$targetText"
    return @{ Type = $eventType; State = $eventState; Line = $line }
}

function Read-Line([System.IO.Stream]$Stream) {
    $buffer = New-Object System.IO.MemoryStream
    while ($true) {
        $b = $Stream.ReadByte()
        if ($b -lt 0) { throw "stream closed by device" }
        if ($b -eq 10) { break }
        if ($b -ne 13) { $buffer.WriteByte([byte]$b) }
    }
    return [System.Text.Encoding]::UTF8.GetString($buffer.ToArray())
}

function Read-Bytes([System.IO.Stream]$Stream, [int]$Count) {
    $buffer = New-Object byte[] $Count
    $offset = 0
    while ($offset -lt $Count) {
        $read = $Stream.Read($buffer, $offset, $Count - $offset)
        if ($read -le 0) { throw "stream closed by device" }
        $offset += $read
    }
    return , $buffer
}

# ---------- offline mode ----------
if ($ParseFile) {
    $summary = Get-AlertSummary ([System.IO.File]::ReadAllBytes((Resolve-Path $ParseFile)))
    Write-Host $summary.Line
    return
}

# ---------- live capture ----------
$securePassword = Read-Host "Password for $User@$CameraHost" -AsSecureString
$url = "${Scheme}://$CameraHost$AlertStreamPath"
$credential = New-Object System.Net.NetworkCredential($User, $securePassword)
$credentialCache = New-Object System.Net.CredentialCache
$credentialCache.Add([Uri]$url, "Digest", $credential)
$credentialCache.Add([Uri]$url, "Basic", $credential)
if ($Scheme -eq "https") {
    [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
}

$outDir = Join-Path (Get-Location) (Join-Path $Output (Get-Date -Format "yyyyMMdd_HHmmss"))
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
Write-Host "Saving captures to: $outDir"

$counter = 0
$lastXmlName = "unknown"
try {
    while ($true) {
        $response = $null
        try {
            Write-Host "Connecting to $url ..."
            $request = [System.Net.HttpWebRequest]::Create($url)
            $request.Credentials = $credentialCache
            $request.Timeout = 15000
            $request.ReadWriteTimeout = $ReadTimeoutSeconds * 1000
            $request.KeepAlive = $true
            $response = $request.GetResponse()
            Write-Host "Connected. Content-Type: $($response.ContentType)"
            Write-Host "Waiting for events... walk in front of the camera. Ctrl+C to stop.`n" -ForegroundColor Green
            $stream = New-Object System.IO.BufferedStream($response.GetResponseStream(), 65536)

            while ($true) {
                $line = Read-Line $stream
                if (-not $line.StartsWith("--")) { continue }  # wait for a boundary
                if ($line.EndsWith("--")) { continue }         # closing boundary

                $headers = @{}
                while (($headerLine = Read-Line $stream) -ne "") {
                    $index = $headerLine.IndexOf(":")
                    if ($index -gt 0) {
                        $headers[$headerLine.Substring(0, $index).Trim().ToLower()] = $headerLine.Substring($index + 1).Trim()
                    }
                }
                $partType = "$($headers['content-type'])".ToLower()
                $length = "$($headers['content-length'])"

                if ($length -match '^\d+$') {
                    $body = Read-Bytes $stream ([int]$length)
                }
                elseif ($partType -eq "" -or $partType -match "xml") {
                    $builder = New-Object System.Text.StringBuilder
                    while ($true) {
                        $xmlLine = Read-Line $stream
                        [void]$builder.AppendLine($xmlLine)
                        if ($xmlLine -match '</EventNotificationAlert>') { break }
                    }
                    $body = [System.Text.Encoding]::UTF8.GetBytes($builder.ToString())
                }
                else {
                    Write-Host "    ! part '$partType' without Content-Length skipped" -ForegroundColor Yellow
                    continue
                }

                $stamp = Get-Date -Format "HHmmss_fff"
                $isXml = $partType -match "xml" -or ([System.Text.Encoding]::ASCII.GetString($body, 0, [Math]::Min(64, $body.Length)).TrimStart().StartsWith("<"))

                if ($isXml) {
                    $summary = Get-AlertSummary $body
                    $isHeartbeat = ($HeartbeatEvents -contains $summary.Type.ToLower()) -and $summary.State -eq "inactive"
                    if ($isHeartbeat -and -not $IncludeHeartbeat) { continue }
                    $counter++
                    $safeType = $summary.Type -replace '[^a-zA-Z0-9_-]', '_'
                    $lastXmlName = "{0:D4}_{1}_{2}_{3}" -f $counter, $stamp, $safeType, $summary.State
                    [System.IO.File]::WriteAllBytes((Join-Path $outDir "$lastXmlName.xml"), $body)
                    $color = if ($summary.Line -match 'TARGETS=<none>') { "Gray" } else { "Cyan" }
                    Write-Host ("[{0}] #{1} {2}" -f (Get-Date -Format "HH:mm:ss"), $counter, $summary.Line) -ForegroundColor $color
                }
                elseif ($partType.StartsWith("image/")) {
                    $extension = ($partType.Split("/")[1].Split(";")[0]) -replace "^jpg$", "jpeg"
                    $name = "${lastXmlName}_img_$stamp.$extension"
                    [System.IO.File]::WriteAllBytes((Join-Path $outDir $name), $body)
                    Write-Host "    + image saved: $name ($($body.Length) bytes)"
                }
                elseif ($body.Length -gt 0) {
                    $name = "${lastXmlName}_part_$stamp.bin"
                    [System.IO.File]::WriteAllBytes((Join-Path $outDir $name), $body)
                    Write-Host "    + other part ($partType) saved: $name"
                }
            }
        }
        catch [System.Net.WebException] {
            $status = $null
            if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
            if ($status -eq 401 -or $status -eq 403) {
                Write-Host "Authentication/permission error ($status). Check user/password and the user's 'Remote: Notify Surveillance Center / Alarm' permission." -ForegroundColor Red
                break
            }
            Write-Host "Connection problem: $($_.Exception.Message). Retrying in 5 s..." -ForegroundColor Yellow
        }
        catch {
            Write-Host "Connection problem: $($_.Exception.Message). Retrying in 5 s..." -ForegroundColor Yellow
        }
        finally {
            if ($response) { $response.Close() }
        }
        Start-Sleep -Seconds 5
    }
}
finally {
    Write-Host "`nStopped. $counter alerts saved in $outDir"
}
