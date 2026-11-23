# Non-secret smoke checks. Authenticated /models requires real Codex ChatGPT sign-in.
$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:8787'
$health = Invoke-RestMethod "$base/health"
if ($health.status -ne 'ok') { throw 'Gateway did not return healthy status' }
Write-Host 'Health: OK'
$key = [Environment]::GetEnvironmentVariable('CODEX_LOCAL_GATEWAY_KEY', 'Process')
if (-not $key) { $key = [Environment]::GetEnvironmentVariable('CODEX_LOCAL_GATEWAY_KEY', 'User') }
if (-not $key) { throw 'Missing CODEX_LOCAL_GATEWAY_KEY in Windows environment' }
$headers = @{ 'X-Local-Gateway-Key' = $key }
$routes = Invoke-RestMethod "$base/routes" -Headers $headers
Write-Host 'Configured provider routes:'
$routes.active | ConvertTo-Json -Depth 5
Write-Host 'Skipped routes (disabled or missing credentials):'
$routes.skipped | ConvertTo-Json -Depth 5
try {
    Invoke-WebRequest "$base/routes" -UseBasicParsing | Out-Null
    throw 'Security test failed: unauthenticated routes access was allowed'
} catch {
    $code = $null
    if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
    if ($code -ne 401) { throw "Expected HTTP 401 without local gateway key; got $code" }
    Write-Host 'Local gateway key enforcement: OK'
}
Write-Host 'Next: open Codex and inspect its model picker / agent tool loop.'
