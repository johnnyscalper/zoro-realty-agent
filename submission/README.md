# 집의 다음 · Track 1 제출 자료

강남구 아파트 전세 상담에서 현장 정보가 바뀌면 어떤 매물을 빼야 하는지, 입주가 늦어지면 청소와 이사를 언제 다시 잡을 수 있는지 확인하는 에이전트입니다. Zoro-1이 다음 도구를 선택하고 앱이 검색, 현장 기록, 일정 계산을 실행합니다.

**[심사용 데모 열기](demo/README.md)** · [5분 발표 원고](../docs/TRACK1_TALK_KO.md) · [프로젝트 실행 안내](../README.md)

## L40S 실행 로그

2026년 11월 19일 17시 20분(KST), Windows 앱의 Check 5를 NVIDIA Brev의 L40S 모델 서버에 연결해 세 시나리오를 검사했습니다. 매물과 업체 일정은 가상 자료이며, 검사 프로그램이 시연용 변경을 승인했습니다.

| 시나리오 | 실제 모델 호출에서 확인한 행동 | 경과 시간 | 로그 |
| --- | --- | ---: | --- |
| 후보 검색 | 후보 검색을 실행한 뒤 추가 질문을 선택했습니다. 최종 대화 상태는 `needs_input`입니다. | 6.44초 | [01-candidate_search.json](logs/01-candidate_search.json) |
| 현장 재검증 | 기록 제안을 승인한 뒤 관찰을 한 번 저장하고, 무계단 접근이 불가능한 L001을 후보에서 제외했습니다. | 4.103초 | [02-inspection_recheck.json](logs/02-inspection_recheck.json) |
| 입주 지연 | 승인 후 입주 시각을 변경하고 충돌을 찾아 대안을 제시했습니다. 기존 청소·이사 시간은 이 단계에서 유지됐습니다. | 10.42초 | [03-entry_delay.json](logs/03-entry_delay.json) |

각 JSON은 참가자가 전달한 통합 결과의 공통 실행 정보와 해당 시나리오의 `result`를 담고 있습니다. `zoro_used`는 턴마다 기록합니다. 현장 재검증의 두 번째 턴에서는 앱이 승인된 행동을 처리했습니다. 소요 시간은 모델 호출과 앱 처리를 포함합니다. [통합 기록](logs/zoro_live_check.json) · [판정 코드](../scripts/check_zoro.py)

개발용 규칙 모드에서는 별도로 20개 시나리오를 실행해 모두 통과했습니다. 같은 제출 코드의 전체 자동 테스트는 1,271개가 통과했습니다.

## 재현

1. 이 브랜치의 코드를 받아 Windows에서 `START_WINDOWS.cmd`를 실행합니다. 개발용 규칙 모드는 시연 자료만으로 실행할 수 있습니다.
2. 실제 모델을 사용할 때는 GPU 서버를 실행하고 로컬 포트 전달을 연 뒤 `CONFIGURE_ZORO_WINDOWS.cmd`에서 연결을 저장합니다. [GPU 연결 안내](../docs/ZORO_GPU_QUICKSTART.md)
3. `CHECK_ZORO_WINDOWS.cmd`가 세 사례를 독립된 임시 상담에서 검사하고 `reports/zoro_live_check.json`을 생성합니다. 국토부의 과거 실거래 조회는 앱에서 별도로 실행할 수 있습니다.
