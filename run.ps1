param([switch]$Headless, [switch]$Serve, [int]$Port = 8765, [string]$BindAddress = '127.0.0.1')
Set-Location -LiteralPath $PSScriptRoot
$simArgs = @('-m', 'tractor_sim', '--port', "$Port", '--host', $BindAddress)
if ($Headless) { $simArgs += '--headless' }
if ($Serve) { $simArgs += '--serve' }
$pythonCommand = if (Test-Path -LiteralPath 'C:\Miniconda\python.exe') { 'C:\Miniconda\python.exe' } else { 'python' }
& $pythonCommand @simArgs
exit $LASTEXITCODE
