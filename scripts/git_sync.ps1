# Commits and pushes any remaining changes, re-running the same secret gate.
# Used to tidy up the helper scripts created during the git setup itself.

$ErrorActionPreference = 'Continue'
$project = 'C:\claude code projects\urban-green-heat-planner'
$log = Join-Path $project 'git_log.txt'

Set-Location $project
Remove-Item $log -ErrorAction SilentlyContinue

$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'never'

function Log([string]$t) {
    Write-Output $t
    Add-Content -Path $log -Value $t
}

function Pipe([string[]]$gitArgs) {
    & git @gitArgs 2>&1 | ForEach-Object {
        $line = $_.ToString()
        Write-Output $line
        Add-Content -Path $log -Value $line
    }
}

Log "== Stage remaining changes"
Pipe @('add', '-A')
Log ""
Log "== Staged files"
Pipe @('diff', '--cached', '--name-only')

# ---- secret gate -----------------------------------------------------------
Log ""
Log "== Secret gate"
$staged = @(& git diff --cached --name-only)
$leaked = @($staged | Where-Object {
    $_ -eq '.env' -or $_ -like '*.env' -or $_ -like '*_log.txt' -or $_ -like '*.log'
})
if ($leaked.Count -gt 0) {
    Log "  DANGER: refusing to commit these:"
    $leaked | ForEach-Object { Log "    - $_" }
    Pipe @('reset')
    exit 1
}

$secret = $null
$envFile = Join-Path $project '.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*FORTYGUARD_API_KEY\s*=\s*(\S+)\s*$') { $secret = $Matches[1] }
    }
}
if ($secret) {
    $diffText = (& git diff --cached) -join "`n"
    if ($diffText.Contains($secret)) {
        Log "  DANGER: API key present in staged content. ABORTING."
        Pipe @('reset')
        exit 1
    }
    Log "  PASS - no API key in staged content."
}
Log "  PASS - no secrets or logs staged."

if ($staged.Count -eq 0) {
    Log ""
    Log "Nothing to commit; working tree already clean."
    Pipe @('status', '-sb')
    exit 0
}

# ---- commit + push --------------------------------------------------------
Log ""
Log "== Commit"
Pipe @('commit', '-m', 'Add git setup and push helper scripts (bash-free workflow)')

Log ""
Log "== Push"
& git -c credential.interactive=never push 2>&1 | ForEach-Object {
    $line = $_.ToString()
    Write-Output $line
    Add-Content -Path $log -Value $line
}
$code = $LASTEXITCODE
Log ""
Log "-- push exit code: $code"

Log ""
Log "== Final state"
Pipe @('status', '-sb')
Pipe @('log', '--oneline', '-3')
