# Stage 2 of the git setup: push to GitHub.
#
# Credential prompts are DISABLED on purpose. If GitHub needs authentication,
# this fails immediately with a readable error instead of hanging forever on an
# invisible prompt that nobody can answer (this runs detached, with no terminal).

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

Log "=============================================================="
Log "== PUSH to GitHub"
Log "=============================================================="
Log "  Remote : https://github.com/TehreemAyesha/Urban-Green-Cover-Heat-Mitigation-Planner.git"
Log "  Branch : main"
Log "  Prompts: disabled (fail fast rather than hang)"
Log ""

& git -c credential.interactive=never push -u origin main 2>&1 | ForEach-Object {
    $line = $_.ToString()
    Write-Output $line
    Add-Content -Path $log -Value $line
}
$code = $LASTEXITCODE
Log ""
Log "-- push exit code: $code"

if ($code -eq 0) {
    Log ""
    Log "PUSH SUCCEEDED."
    & git log --oneline -1 2>&1 | ForEach-Object { Log $_.ToString() }
    & git status -sb 2>&1 | ForEach-Object { Log $_.ToString() }
} else {
    Log ""
    Log "PUSH FAILED - see the error above. The commit is safe locally;"
    Log "nothing was lost. Most likely cause is missing GitHub credentials."
}
