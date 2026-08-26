# Runs the FortyGuard test script, mirroring output to a log file.
#
# This exists only because Git Bash on this machine cannot fork (0xC0000142),
# so commands must be launched outside it.

$ErrorActionPreference = 'Continue'
$project = 'C:\claude code projects\urban-green-heat-planner'
$log = Join-Path $project 'run_log.txt'

Set-Location $project
Remove-Item $log -ErrorAction SilentlyContinue

# Force UTF-8 so the log is written cleanly regardless of console code page.
$env:PYTHONIOENCODING = 'utf-8'

# -u keeps stdout unbuffered so polling progress appears live rather than
# arriving all at once when the script exits.
& python '-u' 'scripts/test_fortyguard.py' 2>&1 | ForEach-Object {
    $line = $_.ToString()
    Write-Output $line
    Add-Content -Path $log -Value $line
}
Add-Content -Path $log -Value "-- exit code: $LASTEXITCODE"
Write-Output "-- exit code: $LASTEXITCODE"
