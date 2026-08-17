<#
.SYNOPSIS
    Stop the local dev servers.

.DESCRIPTION
    Kills whatever is listening on the project's ports. Postgres runs in Docker
    and is left alone unless -IncludeDatabase is passed, because stopping it is
    rarely what you want and restarting costs a container start.

    Written for Windows PowerShell 5.1, so no ternary or null-coalescing.

.EXAMPLE
    ./scripts/stop.ps1
    ./scripts/stop.ps1 -Port 8103
    ./scripts/stop.ps1 -IncludeDatabase
#>
[CmdletBinding()]
param(
    # Stop only this port. Omit to stop every application port.
    [int]$Port,

    # Also stop the Postgres container.
    [switch]$IncludeDatabase
)

$services = [ordered]@{
    8000 = 'host backend'
    8001 = 'notes auth server'
    8101 = 'notes MCP'
    8102 = 'tasks MCP'
    8103 = 'public-tools MCP'
    8104 = 'gdrive-lite MCP'
    8105 = 'crm MCP'
    8106 = 'google-workspace MCP'
    3001 = 'frontend'
}

if ($PSBoundParameters.ContainsKey('Port')) {
    # An [ordered] hashtable keys on the boxed object, so an [int] lookup misses
    # keys that were written as literals. Match on the string form instead.
    $label = 'service'
    foreach ($known in $services.GetEnumerator()) {
        if ([string]$known.Key -eq [string]$Port) { $label = $known.Value; break }
    }
    $targets = [ordered]@{ $Port = $label }
} else {
    $targets = $services
}

$stopped = 0
foreach ($entry in $targets.GetEnumerator()) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $entry.Key -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $listener) { continue }

    $pids = @($listener.OwningProcess)

    # A --reload supervisor and its worker both hold the port, and the listener
    # is sometimes the child. Include any sibling whose command line names this
    # port so both halves die together.
    $related = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match "\b$($entry.Key)\b" -and $_.CommandLine -match 'uvicorn|server\.py|next' }
    if ($related) { $pids += $related.ProcessId }
    $pids = $pids | Select-Object -Unique

    $targetPid = $listener.OwningProcess
    $name = (Get-Process -Id $targetPid -ErrorAction SilentlyContinue).ProcessName

    # wslrelay is Docker's port proxy, not our process. Killing it damages the
    # Docker networking stack instead of stopping the container.
    if ($name -eq 'wslrelay') {
        Write-Host ("  skip  {0,-5} {1} (Docker - use -IncludeDatabase)" -f $entry.Key, $entry.Value)
        continue
    }

    foreach ($id in $pids) {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        # taskkill reaches some processes Stop-Process cannot, including ones
        # started detached from another shell.
        cmd /c "taskkill /PID $id /T /F >nul 2>&1"
    }

    Start-Sleep -Milliseconds 400
    $still = Get-NetTCPConnection -State Listen -LocalPort $entry.Key -ErrorAction SilentlyContinue
    if ($still) {
        Write-Warning ("port {0} ({1}) is still bound by pid {2}. If no such process exists, the socket is orphaned - close the terminal that started it, or reboot to release it." -f $entry.Key, $entry.Value, $still.OwningProcess)
    } else {
        Write-Host ("  stop  {0,-5} {1} (pid {2})" -f $entry.Key, $entry.Value, ($pids -join ', '))
        $stopped++
    }
}

if ($IncludeDatabase) {
    $compose = Join-Path $PSScriptRoot '..\infra\docker-compose.yml'
    docker compose -f $compose stop | Out-Null
    Write-Host "  stop  postgres container"
}

if ($stopped -eq 0 -and -not $IncludeDatabase) {
    Write-Host "  nothing was running"
} else {
    Write-Host ""
    Write-Host ("Stopped {0} process(es)." -f $stopped)
}
