# Installs dependencies and runs the FortyGuard test, mirroring all output to a log
# file so it can be read back even if the console stream is not captured.
#
# This exists only because Git Bash on this machine cannot fork (0xC0000142),
# so commands must be launched outside it.

$ErrorActionPreference = 'Continue'
$project = 'C:\claude code projects\urban-green-heat-planner'
$log = Join-Path $project 'run_log.txt'

Set-Location $project
Remove-Item $log -ErrorAction SilentlyContinue

function Log([string]$text) {
    Write-Output $text
    Add-Content -Path $log -Value $text
}

function Run([string]$label, [string]$exe, [string[]]$exeArgs) {
    Log ""
    Log "=============================================================="
    Log "== $label"
    Log "== > $exe $($exeArgs -join ' ')"
    Log "=============================================================="
    try {
        & $exe @exeArgs 2>&1 | ForEach-Object {
            $line = $_.ToString()
            Write-Output $line
            Add-Content -Path $log -Value $line
        }
        Log "-- exit code: $LASTEXITCODE"
    } catch {
        Log "-- LAUNCH FAILED: $($_.Exception.Message)"
    }
}

Log "Working directory: $(Get-Location)"

# Find a usable Python: 'python' first, then the 'py' launcher.
$python = 'python'
$found = Get-Command $python -ErrorAction SilentlyContinue
if (-not $found) {
    Log "'python' not on PATH; trying the 'py' launcher instead."
    $python = 'py'
    $found = Get-Command $python -ErrorAction SilentlyContinue
}
if (-not $found) {
    Log "FATAL: neither 'python' nor 'py' is on PATH. Python may not be installed."
    exit 1
}
Log "Using interpreter: $((Get-Command $python).Source)"

Run 'STEP 0 - Python version' $python @('--version')
Run 'STEP 1 - Upgrade pip' $python @('-m', 'pip', 'install', '--upgrade', 'pip')
Run 'STEP 2 - Install requirements' $python @('-m', 'pip', 'install', '-r', 'requirements.txt')
Run 'STEP 3 - Installed package versions' $python @('-m', 'pip', 'list')
Run 'STEP 4 - FortyGuard API test' $python @('scripts/test_fortyguard.py')

Log ""
Log "=============================================================="
Log "== DONE - full transcript saved to run_log.txt"
Log "=============================================================="
