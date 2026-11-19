# 집의 다음 · Track 1 제출 자료

**[Google Slides 제출본 · 09_T1_조니realtorweb3](https://docs.google.com/presentation/d/1ge06XS_cBO2crv8nEHQoEx0n1otNqFqMfKS69D6gEyQ/edit?slide=id.p1#slide=id.p1) · [실제 앱 녹화 · 1분 2초](https://drive.google.com/file/d/1glDUGIpctpFEOYttOO61-2cBdNl9vSQZ/view) · [앱 실행 및 프로젝트 소개](../README.md)**

행사 GPU 연결은 종료했습니다. 발표 8장의 녹화에서는 강남구 전세 조건으로 후보를 찾고 현장에서 무계단 출입이 불가능하다고 확인한 L001을 재검증합니다. 중개사가 변경을 승인한 뒤 화면 오른쪽에서 **확인 필요 3개 → 2개, 제외 11개 → 12개**로 바뀝니다. 마지막에는 앱의 **Zoro 서버 응답 사용** 기록이 보입니다. [시연에 사용한 앱 코드](../app) · [GPU 없는 Codespaces 체험](https://codespaces.new/johnnyscalper/zoro-realty-agent)

## L40S에서 실행한 세 가지 상담

2026년 11월 19일 17시 20분(한국 시간), 앱 v0.4.3의 Check 5를 Brev L40S 모델 서버에 연결해 검사했습니다. 가상 매물과 업체 시간표를 사용했고, 검사 프로그램이 변경 승인을 입력했습니다. 시간은 모델 호출과 앱 처리를 합친 값입니다.

| 시나리오 | 확인한 행동과 상태 | 시간 |
| --- | --- | ---: |
| [후보 검색](logs/01-candidate_search.json) | 후보를 검색한 뒤 추가 질문을 선택해 `needs_input` 상태에서 멈춥니다. | 6.44초 |
| [현장 재검증](logs/02-inspection_recheck.json) | 현장 기록을 제안하고 승인 후 한 번 저장합니다. 무계단 접근이 불가능한 L001이 후보에서 제외됩니다. | 4.103초 |
| [입주 지연](logs/03-entry_delay.json) | 승인된 입주 시각을 반영하고 일정 충돌과 대안을 제시합니다. 고객이 대안을 고르기 전까지 청소·이사 시간은 유지됩니다. | 10.42초 |

세 사례 모두 통과했습니다. 각 턴의 `zoro_used`와 도구 목록은 JSON에 있습니다. 현장 재검증의 두 번째 턴은 앞서 승인받은 동작을 앱이 처리했습니다. 이 실행에서는 국토부 실거래 API와 운영 DB를 사용하지 않았습니다. [원본 통합 기록](logs/zoro_live_check.json) · [판정 코드](../scripts/check_zoro.py)

## 심사 중 직접 확인하기

- 실제 앱 화면과 Zoro 동작은 위 1분 2초 녹화에서 확인할 수 있습니다. 32초짜리 별도 영상은 세 시나리오의 **저장된 로그를 카드로 재구성한 요약본**입니다. [로그 카드와 재생 영상](demo/README.md)
- 앱은 GPU 없이 개발용 규칙 모드로 작동합니다. Windows에서 `START_WINDOWS.cmd`를 열고 **키 없이 예시 자료로 먼저 시작**을 고르세요. 독립적으로 실행한 개발용 HTTP 상담 시나리오 20개가 모두 통과했습니다. [시나리오와 실행 방법](../docs/AGENT_EVALUATION.md)
- GPU에서 세 검사를 다시 수행하려면 모델 서버를 준비한 뒤 연결을 설정하고 `CHECK_ZORO_WINDOWS.cmd`를 실행합니다. 검사 결과는 `reports/zoro_live_check.json`에 저장됩니다. [GPU 연결 안내](../docs/ZORO_GPU_QUICKSTART.md)
