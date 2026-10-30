# LocalImageStudio — 로컬 이미지 생성기

Anima · Krea 2 · Qwen-Image 2.1 · MOSS-Image · Stable Diffusion(SDXL/SD1.5/SD 3.5)을 **내 PC에서만** 실행하는 한국어 GUI 이미지 생성 프로그램입니다.
프롬프트와 이미지는 인터넷으로 전송되지 않습니다(접속 주소 `127.0.0.1`, 이 PC 전용). 설치할 때만 프로그램과 모델 파일을 내려받습니다.

- 버전: 1.3.0 ([VERSION.txt](../배포/LocalImageStudio/VERSION.txt))
- 생성 엔진: ComfyUI v0.43.0 · Python 3.13.15 · PyTorch 2.14.1+cu130

## 주요 기능

- **텍스트 → 이미지**, **이미지 변환**(denoise), **지시문 편집**(Qwen, "우산을 파란색으로 바꿔 줘"), **참조 이미지 생성**(Krea 2 화풍 참조 · Qwen 다중 참조), **부분 수정(인페인팅)**(SD 계열)
- 모든 모델에서 **LoRA** 최대 3개 사용. 파일 내용으로 계열을 판별해 맞는 모델에만 표시
- 직접 받은 SDXL·SD1.5 체크포인트(Illustrious·Pony 포함) 자동 인식
- 한국어→영어 프롬프트 번역·보강(로컬 실행), Danbooru **태그 자동완성**(영어·한국어 별칭)
- 쇼츠 9:16(1080×1920) 프리셋, 연속 생성 대기열, 생성 기록, 엔진 상태·VRAM 표시

## 필요한 환경

| 항목 | 요구 사항 |
|---|---|
| OS | Windows 10/11 64비트 |
| GPU | NVIDIA, VRAM 8GB 이상 권장, CUDA 13.0 이상을 지원하는 최신 드라이버 (AMD·Intel·Mac 미지원) |
| RAM | 16GB 이상 (Krea 2는 32GB 권장) |
| 디스크 | 프로그램 약 8GB + 고른 모델 (SSD 권장) |

모델별 용량: Anima 약 9.8GB · Krea 2 약 19.5GB · Qwen-Image 2.1 약 17.3GB · Qwen Base(선택) 약 7.3GB · SDXL 약 7.0GB · SD 3.5 Medium 약 11.6GB · SD 3.5 Large 약 14.9GB · MOSS-Image 약 13.0GB

## 설치

1. 이 저장소의 [`배포/LocalImageStudio`](../배포/LocalImageStudio) 폴더를 받습니다(Releases의 zip 또는 저장소 다운로드). 공간이 넉넉한 드라이브에 둡니다(예: `D:\LocalImageStudio`).
2. 폴더 안의 **`설치.bat`** 을 실행합니다. "Windows의 PC 보호" 창이 뜨면 `추가 정보` → `실행`.
3. 설치할 모델 번호를 고르고(엔터 = 1,2,3 / `all` = 전부) 라이선스 안내를 확인한 뒤 `y`를 입력합니다.
4. 다운로드는 30분~수 시간 걸립니다. 중간에 끊겨도 `설치.bat`을 다시 실행하면 이어받습니다.
5. 끝나면 바탕화면에 **`로컬 이미지 생성기`** 바로가기가 생깁니다.

Python·가상환경·ComfyUI·모델은 모두 이 폴더 안에 설치되며, 시스템 Python이나 다른 AI 프로그램에는 영향을 주지 않습니다.
이미 받은 모델은 SHA256만 확인하고 건너뛰며, PC 안의 다른 ComfyUI 설치·Hugging Face 캐시에 같은 파일이 있으면 찾아서 재사용합니다.

### 이전 버전에서 업데이트

1. 바탕화면 `로컬 이미지 생성기 - 종료·복구`로 프로그램을 끕니다.
2. 새 파일을 기존 설치 폴더에 덮어씁니다.
3. `설치.bat`을 다시 실행합니다. ComfyUI가 오래되었으면 자동으로 교체하며, 모델(`models\`)·생성 이미지(`outputs\`)·기록·설정(`app\data\`)은 그대로 둡니다.

## 실행

- 바탕화면 `로컬 이미지 생성기` (또는 `이미지생성기_실행.bat`)
- 약 20초 뒤 브라우저에 `http://127.0.0.1:7860/` 화면이 열립니다. 콘솔 창을 닫으면 종료됩니다.
- 멈췄을 때: 바탕화면 `로컬 이미지 생성기 - 종료·복구` 실행 후 다시 실행

## 문서

| 문서 | 내용 |
|---|---|
| [처음_읽어주세요.txt](../배포/LocalImageStudio/처음_읽어주세요.txt) | 설치·업데이트·라이선스 요약 |
| [docs/사용설명서.md](../배포/LocalImageStudio/docs/사용설명서.md) | 화면 사용법, 저장 위치, 권장 사용법, 문제 해결 |
| [docs/모델_구성.md](../배포/LocalImageStudio/docs/모델_구성.md) | 모델 버전·정밀도·워크플로·메모리 설정·라이선스 |
| [docs/workflows/](../배포/LocalImageStudio/docs/workflows) | 모델·모드별 ComfyUI API 워크플로 |

## 폴더 구성

```
LocalImageStudio/
├─ 설치.bat                  설치·모델 추가·업데이트
├─ 이미지생성기_실행.bat      실행
├─ 이미지생성기_복구.bat      종료·복구
├─ app/                      프로그램 소스 (서버·GUI·태그 데이터)
├─ setup/                    설치 프로그램 (bootstrap.ps1, setup.py, 패키지 버전 고정)
└─ docs/                     설명서·워크플로
```

설치하면 `python\`, `venv\`, `ComfyUI\`, `models\`, `outputs\`, `logs\` 폴더가 함께 생깁니다(저장소에는 포함되지 않음).

## 라이선스

- **이 프로그램 코드**: [MIT License](../LICENSE)
- **ComfyUI**: GPL-3.0 (설치 때 공식 GitHub에서 내려받음)
- **태그 데이터** (`app/data/tags/`): 한국어 현지화 부분 CC BY-SA 4.0, 원본 MIT — [LICENSE.txt](../배포/LocalImageStudio/app/data/tags/LICENSE.txt)
- **모델** (설치 때 각 공식 배포처에서 내려받음, 저장소에는 포함되지 않음):

| 모델 | 라이선스 | 요약 |
|---|---|---|
| Anima | CircleStone Labs Non-Commercial License | 모델은 비상업 용도만 |
| Krea 2 | Krea 2 Community License | 연매출 100만 달러 미만 상업 사용 가능. 부적절한 결과물을 거르는 조치(직접 검토 등) 필요 |
| Qwen-Image 2.1 | Qwen Research License | 연구·비상업 용도만 |
| MOSS-Image | Apache-2.0 | 상업 사용 가능 |
| Stable Diffusion XL | CreativeML Open RAIL++-M | 상업 사용 가능, 이용 제한 조항 준수 |
| Stable Diffusion 3.5 | Stability AI Community License | 연매출 100만 달러 미만 상업 사용 가능 |

직접 넣은 체크포인트·LoRA는 각 배포처의 라이선스를 따릅니다. 생성한 이미지의 사용 책임은 사용자에게 있습니다. (법률 자문 아님)
