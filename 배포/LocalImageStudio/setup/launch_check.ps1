# 실행 전 점검 (이미지생성기_실행.bat이 호출). 한글 메시지는 BAT 대신 여기서 출력합니다.
# - 설치 여부 확인
# - 폴더를 옮긴 경우 가상환경(venv\pyvenv.cfg)의 Python 경로를 현재 위치로 고침
# 종료 코드: 0 = 실행 가능, 1 = 설치가 필요함
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root 'venv'

if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) {
    Write-Host "[안내] 아직 설치되지 않았습니다. 먼저 이 폴더의 '설치.bat'을 실행하세요." -ForegroundColor Yellow
    exit 1
}
if (-not (Test-Path (Join-Path $Root 'ComfyUI\main.py'))) {
    Write-Host "[안내] 설치가 끝나지 않았습니다. '설치.bat'을 다시 실행하세요." -ForegroundColor Yellow
    exit 1
}
# 새 버전 파일을 기존 폴더에 덮어쓰고 '설치.bat'을 다시 실행하지 않은 경우: 이전 ComfyUI로는 새 모델이 동작하지 않음
try {
    $need = [regex]::Match((Get-Content (Join-Path $PSScriptRoot 'setup.py') -Raw -Encoding UTF8), 'COMFY_TAG = "v?(\d+\.\d+\.\d+)"').Groups[1].Value
    $have = [regex]::Match((Get-Content (Join-Path $Root 'ComfyUI\comfyui_version.py') -Raw -Encoding UTF8), '__version__\s*=\s*"v?(\d+\.\d+\.\d+)').Groups[1].Value
    if ($need -and $have -and ([version]$have -lt [version]$need)) {
        Write-Host "[안내] 설치된 ComfyUI($have)가 이 버전에 필요한 ComfyUI($need)보다 오래되었습니다." -ForegroundColor Yellow
        Write-Host "       '설치.bat'을 다시 실행하면 ComfyUI를 업데이트합니다(모델·생성 기록·설정은 그대로 유지)." -ForegroundColor Yellow
        exit 1
    }
} catch { }
# uv가 만드는 'cpython-3.13-...' 정션(절대 경로)이 아니라 실제 버전 폴더를 사용
$py = Get-ChildItem (Join-Path $Root 'python') -Directory -Filter 'cpython-3.13.*' -ErrorAction SilentlyContinue |
    Where-Object { Test-Path (Join-Path $_.FullName 'python.exe') } | Select-Object -First 1
if (-not $py) {
    Write-Host "[안내] Python 폴더를 찾지 못했습니다. '설치.bat'을 다시 실행하세요." -ForegroundColor Yellow
    exit 1
}
$cfg = Join-Path $Venv 'pyvenv.cfg'
$lines = Get-Content $cfg -Encoding UTF8
$fixed = $lines -replace '^home = .*', ('home = ' + $py.FullName)
if (($lines -join '|') -ne ($fixed -join '|')) {
    [IO.File]::WriteAllLines($cfg, $fixed)   # UTF-8, BOM 없음
    Write-Host "폴더 위치가 바뀌어 Python 경로를 다시 맞췄습니다."
}
exit 0
