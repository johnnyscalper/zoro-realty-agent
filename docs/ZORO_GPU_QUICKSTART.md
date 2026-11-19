# Brev 또는 Vast GPU에서 Zoro 연결하기

기존 앱은 노트북에서, Zoro만 GPU 서버에서 실행합니다. 행사 NVIDIA L40S에서 실행한 세 시나리오가 Check 5를 통과했습니다. [실행 로그와 데모](../submission/README.md) 도우미는 임대·시작·정지·결제를 대신하지 않습니다.

## 행사에서 제공한 NVIDIA Brev L40S

팀 조직의 **Compute**에서 배정된 L40S를 엽니다. 직접 만드는 경우 **Create a cloud instance**에서 **L40S × 1**과 시간당 요금을 확인합니다. 주최 측이 미리 모델 서버를 실행해 주었다면 제공된 접속 정보로 5단계의 앱 검사부터 진행합니다.

새 인스턴스에서는 JupyterLab의 Terminal에서 `nvidia-smi`를 실행합니다. 아래 명령은 이 버전의 `gpu_zoro.py`를 JupyterLab에 업로드한 폴더에서 실행합니다. Python 3.11 또는 3.12를 사용합니다. 홈 디렉터리에 35GiB 이상의 사용 가능한 공간이 필요합니다. 다른 디스크를 쓴다면 모든 명령에 같은 경로를 지정합니다.

```bash
python3 gpu_zoro.py preflight --runtime-dir ~/zoro-brev-runtime
python3 gpu_zoro.py setup --runtime-dir ~/zoro-brev-runtime
python3 gpu_zoro.py login --runtime-dir ~/zoro-brev-runtime
python3 gpu_zoro.py start --runtime-dir ~/zoro-brev-runtime
```

서버가 준비되면 다른 GPU 터미널에서 `python3 gpu_zoro.py status`로 모델 목록을 확인합니다. 노트북의 Brev CLI에서 로그인하고 행사 조직과 인스턴스를 선택한 뒤 `brev port-forward <인스턴스이름> --port 30001:30001`로 연결합니다. [NVIDIA 콘솔 안내](https://docs.nvidia.com/datascience/deployment/latest/cloud/nvidia/brev/) · [Brev 포트 연결](https://docs.nvidia.com/brev/cli/connectivity).

## 1. GPU를 켜기 전에

1. https://huggingface.co/trillionlabs/Zoro-1 에 로그인하고 모델 접근 조건에 동의합니다. 접근 승인이 필요한 경우 완료될 때까지 기다립니다.
2. 모델을 읽을 수 있는 Hugging Face 토큰을 준비합니다. 토큰은 GPU의 로그인 프롬프트에만 입력합니다. 국토부 인증키와는 다른 값입니다.
3. Vast의 **가동 중 요금, 저장공간, 다운로드 요금**을 확인합니다. 사용자에게 처음 표시된 32GB 중 16GB 사용 상태로 설치를 바로 시작하지 마세요. 총 80GB 정도와 설치 전 35GiB 이상의 실제 여유 공간을 권장합니다. 설치 이미지·캐시에 따라 달라지는 여유 권장량입니다.
4. 기존 인스턴스의 데이터가 필요하면 먼저 백업합니다. 용량이 부족해 새 인스턴스를 만들 경우에도 기존 것을 먼저 삭제하지 않습니다.
5. NVIDIA GPU가 보이는 CUDA 개발/PyTorch Linux 이미지, Python 3.12를 권장합니다. 이 도우미는 Python 3.11~3.12를 검사합니다. 실제 드라이버·SGLang 호환성은 GPU에서 점검합니다.

## 2. 노트북에서 전체본 실행

전체본 ZIP을 새 폴더에 풀고 `START_WINDOWS.cmd`를 실행합니다. 국토부 키는 처음 시작 창에서 입력하거나 기존 폴더에서 가져옵니다. 기존 파일 덮어쓰기는 필요 없습니다. [처음 시작 안내](../START_HERE_KO.md)를 참고하세요.

새 파일:

- `CONFIGURE_ZORO_WINDOWS.cmd`: Zoro 설정 창. 서버가 제공하는 모델 ID를 자동으로 읽고 기존 국토부 키를 보존합니다.
- `CHECK_ZORO_WINDOWS.cmd`: 실제 모델을 사용한 세 가지 작은 통합 검사. GPU 시간은 사용하며 기존 상담 DB는 건드리지 않습니다.
- `scripts/gpu_zoro.py`: **GPU 서버 안에서** 실행하는 설치·시작 도우미입니다.

## 3. GPU 서버에 설치

Vast에서 인스턴스를 시작하고 Jupyter 또는 SSH 터미널을 엽니다. **이 ZIP 자체**를 GPU에 업로드합니다. 노트북의 `.env`, API 키, 실제 상담 DB를 올리지 않습니다. 업로드한 위치에서:

```bash
unzip Zoro_Realty_AllInOne_v0.4.3.zip -d zoro-realty-prep
cd zoro-realty-prep
python3 scripts/gpu_zoro.py preflight
python3 scripts/gpu_zoro.py setup
python3 scripts/gpu_zoro.py login
python3 scripts/gpu_zoro.py start
```

`login`에서 Hugging Face 토큰을 입력합니다. 화면에 입력 문자가 보이지 않을 수 있습니다. Git credential 저장 여부를 물으면 저장하지 않아도 됩니다.

첫 `start`는 모델을 다운로드하고 GPU에 올립니다. 컴파일/초기 로딩은 오래 걸릴 수 있습니다. 서버 시작 메시지가 나온 뒤 다른 GPU 터미널에서 다음을 실행합니다.

```bash
python3 scripts/gpu_zoro.py status
```

정상 실행 중에는 `start` 터미널을 유지합니다. 재시작할 때는 같은 위치에서 `start`만 실행하면 됩니다. 프로그램 종료만으로 Vast 과금이 중지되지는 않으므로 사용 후 Vast의 Stop 상태를 확인합니다. 정지 중에도 저장공간 요금이 남습니다.

### 무엇을 설치하는가

- 공식 저장소: https://github.com/trillion-labs/trida-stack
- 확인한 소스 커밋: `0ddcf516e458a82892d7fe0d0dabeb113d3cf424`. 배포 모델의 `decision_head.pt`와 이전 `jev_head.pt`를 모두 읽습니다.
- 백엔드: 공식 문서의 stock SGLang 경로, `sglang==0.5.22`
- 서버: `inference/zoro-1/serve_decide.py`, `127.0.0.1:30001`
- 공식 모델: `trillionlabs/Zoro-1`. 첫 다운로드의 정확한 리비전을 기록하고 이후 같은 리비전을 재사용합니다.
- 모델 캐시/실행 환경: `/workspace/zoro-realty-runtime`. 다른 경로는 각 명령에 같은 `--runtime-dir`을 지정합니다.
- 설치된 라이브러리 버전은 `installed-packages.txt`, 모델 리비전은 `model-snapshot.json`에 남습니다. 다른 GPU로 옮길 때는 이 정보도 보관합니다.

현재 앱은 이 서버의 `/v1/chat/completions`로 다음 행동을 선택합니다. 선택형 `/v1/decide` 기능도 서버에서 제공하지만 이번 앱 경로에서 사용하지 않습니다. self-speculative decoding의 속도 개선을 구현·측정했다는 주장은 하지 않습니다.

## 4. 노트북에서 GPU로 연결 통로 열기

Vast의 SSH 접속은 SSH 키가 필요합니다. 개인키를 채팅에 붙여넣지 않습니다. 새 키를 계정에 등록한 것만으로 기존 인스턴스에 반영되지 않을 수 있으므로 해당 인스턴스 SSH 설정도 확인합니다.

Vast의 SSH/Connect 화면에 나온 **실제 SSH 호스트·포트·사용자**를 사용합니다. 아래는 형식 예시이고 그대로 실행하는 명령이 아닙니다.

```text
ssh -p 실제SSH포트 -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:30001:127.0.0.1:30001 실제사용자@실제SSH호스트
```

필요하면 Vast에서 안내한 `-i 개인키파일경로`를 유지합니다. GPU 카드에 표시된 IP만으로 SSH 포트를 추측하지 마세요. 첫 접속의 호스트 키는 Vast의 접속 정보와 대조한 뒤 신뢰합니다.

이 창은 연결 통로를 유지하는 동안 비어 보일 수 있습니다. 오류가 없으면 열어 둡니다. 모델 서버는 GPU 내부에서만 열어 두므로 공개 모델 포트나 새 방화벽 개방은 필요 없습니다.

## 5. 앱 설정과 실제 검사

1. 노트북 기존 폴더에서 `CONFIGURE_ZORO_WINDOWS.cmd`를 실행합니다.
2. 기본 주소 `http://127.0.0.1:30001/v1`를 유지하고 Zoro 서버 인증키 칸은 비워 둡니다. **국토부 키나 HF 토큰을 이 칸에 넣지 않습니다.**
3. ‘연결 확인 후 설정 저장’을 누릅니다. 모델 ID는 `/v1/models`에서 읽습니다. GPU를 새로 설치하면 ID가 바뀔 수 있으므로 다시 설정합니다.
4. `CHECK_ZORO_WINDOWS.cmd`를 실행합니다. 후보 검색, 현장 확인 결과 반영, 입주 지연 후 대안 생성을 실제 모델 선택으로 검사합니다.
5. 결과는 `reports/zoro_live_check.json`에 저장됩니다. 연결 실패, 일부 실패는 통과로 바꾸지 않습니다. 서버의 자기보고 ID만으로 실제 가중치가 Zoro라는 사실을 독립 검증할 수는 없습니다. GPU의 공식 다운로드 기록과 함께 확인합니다.
6. `START_WINDOWS.cmd`로 앱을 다시 시작하고 브라우저에서 Ctrl+F5를 누릅니다. 상담의 실행 모드를 **Zoro 서버 모드**로 바꾸고 기존 네 개 예시를 시연합니다.

자동 검사는 새 임시 DB와 가상 자료만 사용합니다. 정확히 지정한 가상 변경만 검사 코드가 승인합니다. 실제 상담에서는 기존 승인 버튼을 유지합니다. 국토부 실조회와 현재 20개 전체 시나리오는 별도로 확인해야 합니다.

## 6. 막혔을 때

| 상황 | 우선 확인 |
| --- | --- |
| 디스크 여유 부족 | Vast 할당 용량과 실제 여유 공간. 환경/모델 설치 전에 정리 또는 다른 인스턴스 준비 |
| 모델 401/403, 다운로드 실패 | HF 계정의 모델 접근 권한, 읽기 권한 토큰, `login` |
| CUDA/커널/설치 오류 | GPU 드라이버, CUDA 개발 이미지, Python 3.12, 설치 출력. 아직 성공으로 간주하지 않음 |
| 노트북 설정 창 연결 실패 | GPU의 `status` 성공 여부, SSH 연결 창 유지, 로컬 30001 포트 |
| 모델은 응답하지만 통합 검사 실패 | 앱의 행동 기록, 승인 전후 상태, 반환 형식. 개발 모드 결과로 대체하지 않음 |
| 시연 중 모델 응답 시간 초과 | 최초 예열 여부와 모델 로그. 현재 요청당 최대 30초, 한 턴 전체 45초이며 실제 GPU 측정 후 조정 |

## 목요일 제출 계획

공식 행사는 2026-11-19(목) 13:00~11-20(금) 12:00입니다. 공개 안내에서 목요일 조기 제출·시연, 금요일 불참 시 심사·수상 자격은 확인되지 않았습니다. 주최 측에 해당 가능 여부와 영상/코드 대체 제출 방식을 확인한 뒤 맞춥니다. 그 전에도 모델 연결 테스트와 재현 가능한 코드 준비는 진행할 수 있습니다.

공식 근거:

- https://github.com/trillion-labs/trida-stack/blob/0ddcf516e458a82892d7fe0d0dabeb113d3cf424/inference/zoro-1/README.md
- https://huggingface.co/trillionlabs/Zoro-1
- https://docs.vast.ai/guides/instances/connect/ssh
- https://docs.vast.ai/guides/reference/billing
- https://hackathon.trillionlabs.co/
