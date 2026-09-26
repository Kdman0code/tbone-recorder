# Installer for tbone-recorder on Windows.
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/Kdman0code/tbone-recorder/main/install.ps1 | iex"
$ErrorActionPreference = 'Stop'

$repo   = if ($env:TBONE_REPO)   { $env:TBONE_REPO }   else { 'https://github.com/Kdman0code/tbone-recorder' }
$branch = if ($env:TBONE_BRANCH) { $env:TBONE_BRANCH } else { 'main' }

function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }

Say 'Installing tbone-recorder'

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Say 'Installing uv (fetches its own Python, so you do not need one)'
  Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
  $uvBin = Join-Path $env:USERPROFILE '.local\bin'
  if (Test-Path $uvBin) { $env:Path = "$uvBin;$env:Path" }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw 'uv is installed but not on PATH. Open a new PowerShell window and re-run.'
}

Say 'Installing the app'
if (Get-Command git -ErrorAction SilentlyContinue) {
  uv tool install --force "git+$repo@$branch"
} else {
  # No git on this machine: install from the source archive instead.
  uv tool install --force "$repo/archive/refs/heads/$branch.zip"
}

uv tool update-shell 2>$null | Out-Null

Write-Host ''
Say 'Done. Start it with:'
Write-Host '    tbone-rec'
Write-Host ''
if (-not (Get-Command tbone-rec -ErrorAction SilentlyContinue)) {
  Warn 'tbone-rec is not on this session PATH yet - open a new terminal first.'
}
Write-Host 'Windows: allow microphone access for desktop apps under'
Write-Host 'Settings > Privacy & security > Microphone if the meter stays flat.'
