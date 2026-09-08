$token = $env:GH_TMP_TOKEN
if (-not $token) { Write-Output "NO TOKEN IN ENV"; exit 1 }
$h = @{ Authorization = "Bearer $token"; Accept = "application/vnd.github+json" }
$jobs = Invoke-RestMethod -Uri "https://api.github.com/repos/CookieCN/evoblue-video-mcp/actions/runs/$($args[0])/jobs" -Headers $h
foreach ($j in $jobs.jobs | Where-Object { $_.conclusion -eq "failure" }) {
  Write-Output "===== $($j.name)"
  $log = Invoke-WebRequest -Uri "https://api.github.com/repos/CookieCN/evoblue-video-mcp/actions/jobs/$($j.id)/logs" -Headers $h -TimeoutSec 240
  $lines = $log.Content -split "`n"
  $hits = $lines | Select-String -Pattern "FAIL|Traceback|SystemExit|error:" | Select-Object -First 4
  foreach ($hit in $hits) {
    $i = $hit.LineNumber - 1
    $from = [Math]::Max(0, $i - 6)
    $to = [Math]::Min($lines.Count - 1, $i + 10)
    Write-Output ("--- context around line {0}" -f $hit.LineNumber)
    Write-Output ($lines[$from..$to] | Out-String)
  }
}
