$ErrorActionPreference = 'Stop'
$processFile = Join-Path $PSScriptRoot 'runtime\public-process.json'
if (-not (Test-Path -LiteralPath $processFile)) {
    Write-Host 'No public Tractor service is recorded.'
    exit 0
}
$record = Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json
$serviceProcess = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
if (-not $serviceProcess) {
    Write-Host 'The recorded service has already stopped.'
    exit 0
}
$recordTime = [DateTimeOffset]::Parse($record.started)
$actualTime = [DateTimeOffset]$serviceProcess.StartTime.ToUniversalTime()
if (-not [String]::Equals($serviceProcess.Path, $record.executable, [StringComparison]::OrdinalIgnoreCase) -or
    [Math]::Abs(($recordTime - $actualTime).TotalSeconds) -gt 15) {
    throw 'Process identity does not match. No process was stopped.'
}
# The launcher owns a Windows job that also closes its cloudflared child.
Stop-Process -Id ([int]$record.pid)
Write-Host 'The public Tractor service and its tunnel have stopped.'
