# Run from the project root: .\scripts\setup.ps1
# Create local-only secrets; do not replace an existing .env.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root '.env'
if (Test-Path $envFile) { throw '.env exists; refusing to overwrite it.' }
function New-RandomKey([string]$prefix) {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return $prefix + [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
}
function Read-ExistingKey([string]$name) {
    $value = [Environment]::GetEnvironmentVariable($name, 'Process')
    if (-not $value) { $value = [Environment]::GetEnvironmentVariable($name, 'User') }
    return $value
}
function Read-Hidden([string]$question) {
    $secret = Read-Host $question -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
$zai = Read-ExistingKey 'Z_AI_KEY'
if (-not $zai) { $zai = Read-Hidden 'Z.ai API key (leave blank to configure later)' }
$gatewayKey = New-RandomKey ''
$litellmKey = New-RandomKey 'sk-'
[Environment]::SetEnvironmentVariable('CODEX_LOCAL_GATEWAY_KEY', $gatewayKey, 'User')
$env:CODEX_LOCAL_GATEWAY_KEY = $gatewayKey
$fields = [ordered]@{
    GATEWAY_LOCAL_KEY = $gatewayKey
    LITELLM_MASTER_KEY = $litellmKey
    Z_AI_KEY = $zai
    AZURE_OPENAI_API_KEY = (Read-ExistingKey 'AZURE_OPENAI_API_KEY')
    AZURE_OPENAI_BASE_URL = (Read-ExistingKey 'AZURE_OPENAI_BASE_URL')
    AZURE_OPENAI_DEPLOYMENT = (Read-ExistingKey 'AZURE_OPENAI_DEPLOYMENT')
    DEEPSEEK_API_KEY = (Read-ExistingKey 'DEEPSEEK_API_KEY')
    MOONSHOT_API_KEY = (Read-ExistingKey 'MOONSHOT_API_KEY')
    GEMINI_API_KEY = (Read-ExistingKey 'GEMINI_API_KEY')
}
$lines = @()
foreach ($pair in $fields.GetEnumerator()) {
    $value = [string]$pair.Value
    if ($value -match '[\r\n]') { throw "Invalid line break in $($pair.Key)" }
    $lines += "$($pair.Key)=$value"
}
[IO.File]::WriteAllText($envFile, ($lines -join "`n") + "`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host 'Created .env. Keep it private: it contains API credentials.'
Write-Host 'Saved CODEX_LOCAL_GATEWAY_KEY as Windows User environment variable.'
Write-Host 'Start: docker compose up -d --build'
Write-Host 'Optional Google/Moonshot translation: docker compose --profile translation up -d --build'
Write-Host 'Fully exit and relaunch Codex so it inherits the key.'
