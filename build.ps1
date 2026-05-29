# build.ps1 — Build Block Search Tool into a standalone Windows executable
# Usage: .\build.ps1 [-Zip] [-Clean]
#   -Zip    Also create a portable ZIP archive in dist/
#   -Clean  Delete previous build/dist folders before building
#
# Requirements:
#   - .venv with PyInstaller, PySide6, ezdxf, rapidfuzz installed
#   - ODAFileConverter.exe already placed (or will be downloaded at first run)

param(
    [switch]$Zip,
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root       = $PSScriptRoot
$Venv       = Join-Path $Root ".venv\Scripts"
$Python     = Join-Path $Venv "python.exe"
$PyInstaller= Join-Path $Venv "pyinstaller.exe"
$DistDir    = Join-Path $Root "dist\BlockSearchTool"
$ZipOut     = Join-Path $Root "dist\BlockSearchTool-portable.zip"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Block Search Tool — Build Script" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ── Sanity checks ─────────────────────────────────────────────────────────
if (-not (Test-Path $Python)) {
    Write-Error "Python venv not found at $Python`nRun: python -m venv .venv && .venv\Scripts\pip install -r requirements.txt"
}
if (-not (Test-Path $PyInstaller)) {
    Write-Error "PyInstaller not found. Run: .venv\Scripts\pip install pyinstaller"
}

# ── Clean ─────────────────────────────────────────────────────────────────
if ($Clean) {
    Write-Host "[1/5] Cleaning previous build..." -ForegroundColor Yellow
    foreach ($dir in @("build", "dist")) {
        $p = Join-Path $Root $dir
        if (Test-Path $p) { Remove-Item $p -Recurse -Force; Write-Host "  Removed $p" }
    }
} else {
    Write-Host "[1/5] Skipping clean (use -Clean to remove previous build)" -ForegroundColor DarkGray
}

# ── Generate ICO ──────────────────────────────────────────────────────────
Write-Host "[2/5] Generating icon.ico from icon.svg..." -ForegroundColor Yellow
$IcoPath = Join-Path $Root "resources\icon.ico"
& $Python (Join-Path $Root "make_ico.py")
if ($LASTEXITCODE -ne 0) {
    Write-Warning "ICO generation failed — building without custom icon"
} else {
    Write-Host "  icon.ico ready" -ForegroundColor Green
}

# ── PyInstaller ───────────────────────────────────────────────────────────
Write-Host "[3/5] Running PyInstaller..." -ForegroundColor Yellow
Push-Location $Root
try {
    & $PyInstaller build.spec --noconfirm
    if ($LASTEXITCODE -ne 0) {
        Write-Error "PyInstaller failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
Write-Host "  Build complete: $DistDir" -ForegroundColor Green

# ── Verify output ─────────────────────────────────────────────────────────
Write-Host "[4/5] Verifying output..." -ForegroundColor Yellow
$Exe = Join-Path $DistDir "BlockSearchTool.exe"
if (-not (Test-Path $Exe)) {
    Write-Error "Expected executable not found: $Exe"
}
$SizeMB = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
Write-Host "  BlockSearchTool.exe  ($SizeMB MB)" -ForegroundColor Green
$TotalMB = [math]::Round(
    (Get-ChildItem $DistDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 0)
Write-Host "  Total dist size: ~$TotalMB MB" -ForegroundColor Green

# ── Portable ZIP ──────────────────────────────────────────────────────────
if ($Zip) {
    Write-Host "[5/5] Creating portable ZIP..." -ForegroundColor Yellow
    if (Test-Path $ZipOut) { Remove-Item $ZipOut -Force }
    Compress-Archive -Path $DistDir -DestinationPath $ZipOut
    $ZipMB = [math]::Round((Get-Item $ZipOut).Length / 1MB, 0)
    Write-Host "  $ZipOut  ($ZipMB MB)" -ForegroundColor Green
} else {
    Write-Host "[5/5] Skipping ZIP (use -Zip to create a portable archive)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Done!  Output: dist\BlockSearchTool\" -ForegroundColor Green
Write-Host "  Run:   dist\BlockSearchTool\BlockSearchTool.exe" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
