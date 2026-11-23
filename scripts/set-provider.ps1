param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('Z_AI_KEY', 'DEEPSEEK_API_KEY', 'MOONSHOT_API_KEY', 'AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_BASE_URL', 'AZURE_OPENAI_DEPLOYMENT')]
    [string]$Name,
    [switch]$PlainText
)
$ErrorActionPreference = 'Stop'
$envFile = Join-Path (Split-Path -Parent $PSScriptRoot) '.env'
if (-not (Test-Path $envFile)) { throw 'Run scripts/setup.ps1 first' }
if ($PlainText) { $value = Read-Host "Value for $Name" }
else {
    $secret = Read-Host "Secret value for $Name (hidden)" -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try { $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}
if ($value -match '[\r\n]') { throw 'Line breaks are invalid in .env values.' }
$lines = @(Get-Content -LiteralPath $envFile)
$found = $false
for ($i=0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match "^$Name=") { $lines[$i] = "$Name=$value"; $found=$true }
}
if (-not $found) { $lines += "$Name=$value" }
[IO.File]::WriteAllText($envFile, ($lines -join "`n") + "`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Updated $Name in .env. Restart Docker services to apply."
