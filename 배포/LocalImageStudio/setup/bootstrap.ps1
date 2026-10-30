# 로컬 이미지 생성기 - 설치 1단계: uv + Python 3.13을 이 폴더 안에 준비하고 setup.py 실행
# 시스템 Python, PATH, AppData는 변경하지 않습니다. 다시 실행하면 끝난 단계는 건너뜁니다.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$UvVersion = '0.12.18'
$PyVersion = '3.13.15'
$UvDir = Join-Path $Root 'tools\uv'
$Uv = Join-Path $UvDir 'uv.exe'

function Step($msg) { Write-Host ""; Write-Host "==== $msg" -ForegroundColor Cyan }
trap {
    Write-Host ""
    Write-Host "[설치가 끝나지 않았습니다] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "위 메시지를 확인한 뒤 '설치.bat'을 다시 실행하세요. 끝난 단계는 건너뜁니다." -ForegroundColor Red
    exit 1
}

Write-Host "============================================================"
Write-Host " 로컬 이미지 생성기 설치"
Write-Host " - Python, PyTorch, ComfyUI, 모델을 이 폴더 안에 내려받습니다."
Write-Host " - 필요 공간: 프로그램 약 8GB + 고른 모델"
Write-Host " - 중간에 끊겨도 '설치.bat'을 다시 실행하면 이어서 진행합니다."
Write-Host "============================================================"

if (-not [Environment]::Is64BitOperatingSystem) { throw '64비트 Windows가 필요합니다.' }

# uv / Python 캐시와 설치 위치를 모두 이 폴더 안으로 (다른 앱과 충돌 방지)
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Root 'python'
$env:UV_CACHE_DIR = Join-Path $Root '.cache\uv'
$env:UV_LINK_MODE = 'copy'
$env:UV_PYTHON_PREFERENCE = 'only-managed'
$env:PYTHONUTF8 = '1'

Step "[1/3] uv $UvVersion 준비"
if (-not (Test-Path $Uv)) {
    New-Item -ItemType Directory -Force $UvDir | Out-Null
    $zip = Join-Path $UvDir 'uv.zip'
    $url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
    Write-Host "다운로드: $url"
    & curl.exe -L --fail --retry 5 -S --progress-bar -o $zip $url
    if ($LASTEXITCODE -ne 0) { throw 'uv 다운로드에 실패했습니다. 인터넷 연결을 확인하세요.' }
    Expand-Archive -Path $zip -DestinationPath $UvDir -Force
    Remove-Item $zip
}
& $Uv --version

Step "[2/3] Python $PyVersion 준비 (이 폴더의 python\ 안에 설치)"
# --no-bin / --no-registry: PATH용 실행 파일과 Windows 레지스트리 등록을 하지 않음(이 폴더 밖을 건드리지 않음)
& $Uv python install $PyVersion --no-bin --no-registry
if ($LASTEXITCODE -ne 0) { throw 'Python 설치에 실패했습니다.' }
$PyExe = Get-ChildItem (Join-Path $Root 'python') -Directory -Filter "cpython-$PyVersion*" |
    ForEach-Object { Join-Path $_.FullName 'python.exe' } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $PyExe) { throw "python.exe를 찾지 못했습니다: $Root\python" }

$Venv = Join-Path $Root 'venv'
$VenvPy = Join-Path $Venv 'Scripts\python.exe'
$VenvOk = $false
if (Test-Path $VenvPy) {
    # 폴더를 옮긴 경우 가상환경의 기반 Python 경로를 현재 위치로 고침
    $cfg = Join-Path $Venv 'pyvenv.cfg'
    $lines = (Get-Content $cfg -Encoding UTF8) -replace '^home = .*', ('home = ' + (Split-Path -Parent $PyExe))
    [IO.File]::WriteAllLines($cfg, $lines)
    # single-token code: Start-Process does not quote arguments that contain spaces
    $probe = Start-Process -FilePath $VenvPy -ArgumentList '-c', 'pass' -Wait -PassThru -NoNewWindow
    $VenvOk = ($probe.ExitCode -eq 0)
}
if (-not $VenvOk) {
    & $Uv venv $Venv --python $PyExe --allow-existing
    if ($LASTEXITCODE -ne 0) { throw '가상환경 생성에 실패했습니다.' }
}

Step "[3/3] 설치 프로그램 실행"
$env:LIS_UV = $Uv
& $VenvPy -X utf8 (Join-Path $Root 'setup\setup.py') @args
exit $LASTEXITCODE
