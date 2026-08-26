# Stage 1 of the git setup: initialise, PROVE the ignore rules work, stage, and
# commit. Deliberately stops before pushing so the remote state can be checked
# first.
#
# Runs via the preview-server pattern because Git Bash on this machine cannot
# fork (see CLAUDE.md section 6).

$ErrorActionPreference = 'Continue'
$project = 'C:\claude code projects\urban-green-heat-planner'
$log = Join-Path $project 'git_log.txt'

Set-Location $project
Remove-Item $log -ErrorAction SilentlyContinue

# Never hang waiting for credentials: fail fast instead so this script cannot
# block forever on a hidden auth prompt.
$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'never'

function Log([string]$t) {
    Write-Output $t
    Add-Content -Path $log -Value $t
}

function Run([string]$label, [string[]]$gitArgs) {
    Log ""
    Log "=============================================================="
    Log "== $label"
    Log "== > git $($gitArgs -join ' ')"
    Log "=============================================================="
    & git @gitArgs 2>&1 | ForEach-Object {
        $line = $_.ToString()
        Write-Output $line
        Add-Content -Path $log -Value $line
    }
    Log "-- exit: $LASTEXITCODE"
}

function CheckIgnore([string]$path, [string]$expectation) {
    & git check-ignore -q -- $path
    if ($LASTEXITCODE -eq 0) { $actual = 'IGNORED' } else { $actual = 'tracked' }
    $flag = if ($actual -eq $expectation) { 'OK  ' } else { 'FAIL' }
    Log ("  [{0}] {1,-8} {2,-34} (expected {3})" -f $flag, $actual, $path, $expectation)
}

Log "Project: $project"

$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) {
    Log "FATAL: git is not on PATH. Cannot continue."
    exit 1
}
Log "git: $($gitCmd.Source)"
Run 'git version' @('--version')

# ---- 1. init ---------------------------------------------------------------
if (Test-Path (Join-Path $project '.git')) {
    Log ""
    Log "Repository already initialised (.git exists) - skipping git init."
} else {
    Run 'STEP 1 - git init (default branch: main)' @('init', '-b', 'main')
    if ($LASTEXITCODE -ne 0) {
        Log "  'init -b' unsupported on this git version; falling back."
        Run 'STEP 1b - git init (fallback)' @('init')
        Run 'STEP 1c - set default branch to main' @('symbolic-ref', 'HEAD', 'refs/heads/main')
    }
}

# ---- 2. identity -----------------------------------------------------------
Log ""
Log "=============================================================="
Log "== STEP 2 - Commit identity"
Log "=============================================================="
$userName = (& git config --get user.name)  2>$null
$userEmail = (& git config --get user.email) 2>$null
Log "  user.name  : $(if ($userName)  { $userName }  else { '(NOT SET)' })"
Log "  user.email : $(if ($userEmail) { $userEmail } else { '(NOT SET)' })"

$identityOk = ($userName -and $userEmail)
if (-not $identityOk) {
    Log "  -> Identity incomplete. Will stage but NOT commit."
}

# ---- 3. verify ignore rules ------------------------------------------------
Log ""
Log "=============================================================="
Log "== STEP 3 - Verify .gitignore behaviour"
Log "=============================================================="
Log "  Secrets and logs must be IGNORED; source files must be tracked."
Log ""
CheckIgnore '.env'                        'IGNORED'
CheckIgnore 'git_log.txt'                 'IGNORED'
CheckIgnore 'run_log.txt'                 'IGNORED'
CheckIgnore 'install_log.txt'             'IGNORED'
CheckIgnore 'debug.log'                   'IGNORED'
CheckIgnore 'data/raw/landsat.tif'        'IGNORED'
CheckIgnore 'data/processed/phoenix.geojson' 'IGNORED'
CheckIgnore 'data/raw/bundle.zip'         'IGNORED'
CheckIgnore 'service-account-key.json'    'IGNORED'
CheckIgnore '.venv/pyvenv.cfg'            'IGNORED'
Log ""
CheckIgnore '.env.example'                'tracked'
CheckIgnore 'CLAUDE.md'                   'tracked'
CheckIgnore 'README.md'                   'tracked'
CheckIgnore 'requirements.txt'            'tracked'
CheckIgnore 'scripts/test_fortyguard.py'  'tracked'
CheckIgnore 'data/raw/.gitkeep'           'tracked'
CheckIgnore 'data/processed/.gitkeep'     'tracked'
CheckIgnore 'app/.gitkeep'                'tracked'

# ---- 4. stage --------------------------------------------------------------
Run 'STEP 4 - Stage everything not ignored' @('add', '-A')
Run 'STEP 4b - Files now staged' @('diff', '--cached', '--name-only')

# ---- 5. secret safety gate ------------------------------------------------
Log ""
Log "=============================================================="
Log "== STEP 5 - SECRET SAFETY GATE"
Log "=============================================================="
$staged = @(& git diff --cached --name-only)
$leaked = @($staged | Where-Object { $_ -eq '.env' -or $_ -like '*.env' -or $_ -like '*_log.txt' -or $_ -like '*.log' })
if ($leaked.Count -gt 0) {
    Log "  DANGER: these files would be committed but must not be:"
    $leaked | ForEach-Object { Log "    - $_" }
    Log ""
    Log "  ABORTING before commit. Unstaging everything."
    Run 'Unstage' @('reset')
    exit 1
}
Log "  PASS - no .env, no log files staged."
Log "  Staged file count: $($staged.Count)"

# Extra proof: confirm the real key does not appear anywhere in the staged diff.
# The key is read from .env at runtime so that THIS script never contains the
# secret itself (an earlier version hardcoded it and tripped its own gate).
$envFile = Join-Path $project '.env'
$secret = $null
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*FORTYGUARD_API_KEY\s*=\s*(\S+)\s*$') {
            $secret = $Matches[1]
        }
    }
}
if ($secret) {
    $diffText = (& git diff --cached) -join "`n"
    if ($diffText.Contains($secret)) {
        Log "  DANGER: the live API key appears in the staged content. ABORTING."
        Run 'Unstage' @('reset')
        exit 1
    }
    Log "  PASS - live API key string not present in staged content."
} else {
    Log "  NOTE - no key found in .env, so no secret-scan was possible."
}

# ---- 6. commit -------------------------------------------------------------
if (-not $identityOk) {
    Log ""
    Log "SKIPPING COMMIT: git user.name / user.email are not configured."
    Log "Everything is staged and safe; commit needs an identity first."
    exit 2
}

$msg = 'Day 1: project setup, FortyGuard API pipeline working end to end'
Run 'STEP 6 - Commit' @('commit', '-m', $msg)
Run 'STEP 6b - Log' @('log', '--oneline', '--stat', '-1')

# ---- 7. remote -------------------------------------------------------------
$remoteUrl = 'https://github.com/TehreemAyesha/Urban-Green-Cover-Heat-Mitigation-Planner.git'
$existing = (& git remote)
if ($existing -contains 'origin') {
    Run 'STEP 7 - origin exists, pointing it at the target' @('remote', 'set-url', 'origin', $remoteUrl)
} else {
    Run 'STEP 7 - Add origin' @('remote', 'add', 'origin', $remoteUrl)
}
Run 'STEP 7b - Remotes' @('remote', '-v')

# ---- 8. inspect the remote WITHOUT pushing --------------------------------
Log ""
Log "=============================================================="
Log "== STEP 8 - Probe remote (read-only, no push yet)"
Log "=============================================================="
Log "  Checks the repo exists, whether it already has commits, and whether"
Log "  credentials are cached. Credential prompts are disabled, so an auth"
Log "  failure here shows up as an error rather than a hang."
Run 'git ls-remote' @('-c', 'credential.interactive=never', 'ls-remote', '--heads', 'origin')

Log ""
Log "=============================================================="
Log "== STAGE 1 COMPLETE - nothing has been pushed"
Log "=============================================================="
