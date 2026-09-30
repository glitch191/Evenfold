# Builds dist\Evenfold\Evenfold.exe, then verifies the bundle with a console
# twin (EvenfoldCheck.exe) that masters the test album end to end.
# Uses the project's .venv when there is one (local), otherwise the current
# Python (GitHub Actions).
#
#   powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

function Run([string] $what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)." }
}

& $python -m pip install -q pyinstaller; Run "Installing PyInstaller"
& $python -m tools.make_icon; Run "Making the icon"
& $python -m tests.make_test_album | Out-Null; Run "Making the test album"

$env:EVENFOLD_CHECK = ""
& $python -m PyInstaller tools\evenfold.spec --noconfirm --log-level WARN --distpath dist --workpath build\app
Run "Building Evenfold.exe"

$env:EVENFOLD_CHECK = "1"
& $python -m PyInstaller tools\evenfold.spec --noconfirm --log-level WARN --distpath build\check-dist --workpath build\check-work
Run "Building EvenfoldCheck.exe"
$env:EVENFOLD_CHECK = ""
& build\check-dist\EvenfoldCheck\EvenfoldCheck.exe tests\album
Run "The frozen build check"

Remove-Item -Recurse -Force build\check-dist, build\check-work
Write-Host "Built dist\Evenfold\Evenfold.exe"
