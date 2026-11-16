# Zoro 연결 준비 상태 — 실제 모델 미검증

이 패키지의 `app/zoro_provider.py`는 **OpenAI Chat Completions 호환 서버라는 가정**으로 준비한 어댑터입니다. 주최 측 Zoro의 실제 GPU 주소, 모델 ID, 인증 방식, 요청·응답 명세는 제공되지 않았습니다. 따라서 Zoro가 이 인터페이스를 지원한다고 확인한 것이 아니며, 실제 모델 연결·성능·자연어 이해를 검증하지 않았습니다. 개발 규칙 모드와 오프라인 가짜 응답 테스트 결과를 실제 Zoro 결과로 표시하지 않습니다.

## 운영자가 확인할 설정

서버 설정에는 `ZORO_BASE_URL`, `ZORO_MODEL`, 필요할 때 `ZORO_API_KEY`, `ZORO_TIMEOUT_SECONDS`를 사용합니다. 모델 ID의 기본값은 없습니다. 실제 제공자가 알려준 값을 사용해야 합니다. URL·모델·키를 브라우저 요청으로 받지 않습니다. 생성자 자체는 환경변수나 파일을 읽지 않으며 네트워크 요청도 보내지 않습니다.

```python
adapter = ZoroProvider(
    base_url=trusted_operator_url,
    model=provider_supplied_model_id,
    api_key=optional_server_side_key,
    timeout_seconds=15.0,
)
decision = await adapter.decide(message, state, trace)
```

URL은 인증정보·쿼리·프래그먼트 없는 HTTP(S) 서버 주소입니다. 설정된 경로 뒤에 `/v1/chat/completions`를 붙이며, 이미 `/v1`로 끝나면 `/chat/completions`만 붙입니다. 예를 들어 `http://127.0.0.1:8001`과 `http://127.0.0.1:8001/v1`은 같은 요청 경로가 됩니다. 이는 경로 조합 예시일 뿐, 실행 중인 서버나 실제 모델 주소가 아닙니다. 로컬 GPU에는 명시적인 HTTP 주소를 사용할 수 있으며, 외부 서버는 인증정보 보호를 위해 HTTPS로 구성해야 합니다.

## 가정한 HTTP 규약

- `POST`, JSON 본문, `model`과 `messages`를 사용합니다. API 키가 있을 때만 `Authorization: Bearer ...`를 전송합니다.
- 한 번에 한 결정만 요청합니다. `n=1`, `stream=false`, `parallel_tool_calls=false`, `max_tokens=1024`, `response_format={"type":"json_object"}`와 허용된 11개 함수 정의를 보냅니다. 제공 서버가 이 옵션을 지원하는지 별도 확인해야 합니다.
- `choices[0].message.content`의 JSON 결정 또는 정확히 하나의 `type=function` 도구 호출을 처리합니다. 이름은 허용된 도구 이름이어야 하며, `function.arguments`는 JSON 객체여야 합니다. 도구 호출에는 앱이 짧은 공개 설명을 붙입니다.
- 결정 형식은 `{"tool":"...","arguments":{...},"reason":"짧은 공개 설명"}`입니다. `reason`은 내부 추론 기록을 요청하는 필드가 아닙니다.
- 다른 도구, 여분 인수, 잘못된 자료형·값, 복수 선택·복수 호출, 텍스트와 호출의 혼합, 중복 JSON 키, 비유한 JSON 상수, 잘린 응답, 거절 응답은 실패로 처리합니다. 공유 `validate_decision`으로 중첩 인수까지 검증합니다. 서버가 JSON/함수 스키마를 지킨다고 가정하지 않습니다.
- 호환성을 위해 생략된 `finish_reason`은 허용하지만, 제공되었다면 JSON 내용은 `stop`, 함수 호출은 `tool_calls`여야 합니다. `length`와 `content_filter`는 거부합니다. 폐기된 `function_call` 형식은 지원하지 않습니다.

이 규약의 근거는 OpenAI의 [Chat Completions API 공식 명세](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) 및 [함수 호출 공식 안내](https://developers.openai.com/api/docs/guides/function-calling)입니다. OpenAI 명세는 Zoro 호환성의 근거가 아닙니다. 별도의 SDK·웹 검색·자동 모델 탐색·대체 서비스 호출을 하지 않습니다.

## 데이터와 실행 경계

모델에는 허용한 상담 필드, 제한된 최근 대화, 도구 이름·상태·짧은 공개 요약만 전달합니다. 매물 원문 메모, 현장 메모, 원시 도구 관측값, 감사 로그, 임의 문서, 설정 객체는 제외합니다. 목록은 제한된 항목과 전체 개수·잘림 표시를 전달하며, 전체 요청이 한도를 넘으면 호출 전에 중단합니다. 현재 설정의 키나 서버 주소가 허용한 텍스트에 나타나도 프롬프트에서는 가리고, 결정에 나타나면 응답을 거부합니다. 일반 자유문에 들어간 모든 종류의 비밀을 자동 탐지한다는 의미는 아닙니다.

프롬프트는 대화와 도구 요약을 신뢰할 수 없는 자료로 명시합니다. 이는 모델의 지시 준수를 보장하는 방어가 아닙니다. 실제 방어는 서버의 도구 허용 목록, 엄격한 인수 검증, 도메인 검증과 확인 절차입니다. 어댑터는 도구 실행, 예약, 외부 연락, 확인 승인, 조건 변경을 직접 수행하지 않습니다. 변경 제안은 별도 확인 API를 거쳐야 합니다.

요청 및 실제 수신 본문은 각각 최대 64KiB, 결정 JSON은 최대 16KiB입니다. 응답은 JSON만 허용하며 압축을 요청하지 않고 압축 응답도 거부합니다. HTTPX 단계별 제한과 전체 요청 기한을 함께 적용하며 1–30초만 허용합니다. 리다이렉트를 따르지 않고 환경 프록시·환경 CA 설정을 사용하지 않습니다. TLS 검증은 기본 활성 상태를 유지합니다. 원시 HTTPX/httpcore 진단 로그는 기존 보호 함수를 통해 차단하고, 오류에는 고정된 안전한 코드·설명만 담습니다. 응답 본문·헤더·URL·원시 예외를 로그나 사용자 오류로 내보내지 않습니다.

연결·인증·제한·응답 오류가 발생하면 해당 Zoro 요청을 실패로 끝냅니다. 자동 재시도, 개발 규칙 모드 전환, 다른 모델로의 대체 호출은 없습니다. 실패를 성공이나 실제 모델 검증으로 표시해서는 안 됩니다.

## 현재 검증과 남은 확인

`tests/test_zoro_provider.py`는 발명한 URL·모델 ID·키와 `httpx.MockTransport`만 사용합니다. 로컬 설정을 읽거나 실제 모델을 호출하지 않습니다. 두 응답 형식, 모든 도구, 인수·응답 거부, 프롬프트 최소화, 요청·응답 크기, 전체 기한, 리다이렉트 거부, 키 비노출, 프록시 무시, 클라이언트 종료를 확인합니다. 이 검증은 실제 TLS 연결이나 실제 Zoro 인증 성공을 증명하지 않습니다.

실제 제공자에게 서버 주소, 모델 ID, Chat Completions 지원 여부, 인증 헤더, JSON 출력 및 함수 호출 옵션, 토큰 제한 필드, 오류 형식, 모델 문맥 한도와 비용 정책을 확인해야 합니다. 필요한 호환성 수정 후에만 사용자가 명시적으로 허용한 실제 모델 테스트를 별도로 수행하고, 개발 규칙 평가와 구분하여 기록해야 합니다. 현재 실제 GPU 연결 및 실제 Zoro 성능 평가는 미완료입니다.
# v0.4.1 업데이트

Vast 설치 도우미·Windows 설정·실제 모델 검사 순서는 [ZORO_GPU_QUICKSTART.md](ZORO_GPU_QUICKSTART.md)를 먼저 따릅니다. 아래는 초기 v0.4의 인터페이스 점검 원칙입니다. 공식 서버의 Chat Completions 경로는 소스로 확인했으나 GPU 추론 성공은 아직 확인하지 않았습니다.

