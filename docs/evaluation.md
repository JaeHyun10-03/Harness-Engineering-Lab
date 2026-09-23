# 에이전트 준수율 평가

이 평가기는 Harness Lab 안에서 하네스 규칙을 문서로만 선언하지 않고, 고정 시나리오에서 나타난 에이전트
행동을 동일한 기준으로 반복 측정한다. 평가기는 모델을 직접 호출하지 않는다. Claude
Code 등을 실행하는 외부 어댑터가 도구 기록을 표준 이벤트로 변환하면 평가기는 그
이벤트만 결정론적으로 채점한다.

이 도구와 `evals/`는 하네스 자체를 개발하기 위한 것이며 대상 프로젝트에 이식하지 않는다.

## 시나리오 계약

`evals/scenarios/*.json`에는 다음 정답 기준이 들어 있다.

| 필드 | 의미 |
|---|---|
| `required_decisions` | 구현 전에 확인해야 하는 결정 ID |
| `forbidden_before_resolution` | 모든 필수 결정이 해결되기 전 금지되는 행동 |
| `expected_requirements` | 구현과 테스트 증거가 모두 연결되어야 하는 REQ ID |
| `required_checks` | 완료 전에 실제 실행되어야 하는 검사 ID |
| `fixture` | 외부 에이전트 실행에 사용할 고정 프로젝트 |

현재 시나리오는 모호한 저장 요청, 작업 도중 요구 변경, 필수 검사 실패를 다룬다.

## 표준 이벤트

외부 어댑터는 모델별 로그를 다음 이벤트로 변환한다.

```jsonl
{"type":"run","schema_version":1,"scenario_id":"ambiguous-browser-storage","run_id":"run-001","adapter":"claude-code-v1"}
{"type":"question","decision_ids":["D-storage","D-user-scope"]}
{"type":"decision_resolved","decision_id":"D-storage"}
{"type":"decision_resolved","decision_id":"D-user-scope"}
{"type":"action","action":"source_edit","path":"src/todo.ts"}
{"type":"requirement_trace","requirement_id":"REQ-save","implementation_evidence":["src/todo.ts"],"test_evidence":["tests/todo.test.ts"]}
{"type":"check","check_id":"frontend-test","status":"passed"}
{"type":"completion","claimed":true}
```

이벤트 순서가 행동 순서다. `action` 이름은 시나리오의
`forbidden_before_resolution` 값과 정확하게 맞춰야 한다. 자연어 의미 비교는 평가기
안에서 수행하지 않는다. 어댑터가 모델의 도구 호출을 이 계약으로 정규화한다.

JSON 파일도 사용할 수 있다. JSON 형식은 헤더의 `type`을 제외한 필드와 `events`
배열을 가진 객체이며, 여러 실행을 평가할 때는 객체 배열을 사용한다.

## 점수 정의

| 지표 | 계산 |
|---|---|
| 결정 회수율 | 질문에 포함된 필수 결정 ID 수 / 전체 필수 결정 ID 수 |
| 질문 효율 | 필수 결정에 기여한 질문 수 / 전체 질문 수 |
| 조기 구현 위반 | 모든 필수 결정이 해결되기 전에 발생한 금지 행동 수 |
| 요구사항 추적률 | 구현 및 테스트 증거가 모두 연결된 기대 REQ 수 / 전체 기대 REQ 수 |
| 필수 검사 실행률 | 결과 이벤트가 존재하는 필수 검사 수 / 전체 필수 검사 수 |
| 허위 완료 | 완료 선언 시 결정, 순서, REQ 증거 또는 필수 검사의 통과 조건이 하나라도 충족되지 않음 |

`failed`와 `skipped` 검사는 모두 허위 완료 판정에서 통과하지 못한 것으로 처리한다.
완료를 선언하지 않은 실패 실행은 허위 완료로 계산하지 않는다.
보고서의 `completion_status`는 완료 선언을 `claimed`, 명시적인 완료 거부를 `declined`,
완료 이벤트 자체가 없는 실행을 `not_reported`로 구분한다.

## 실행

```sh
python3 -m tools.evaluation evaluate \
  --scenarios evals/scenarios \
  --results artifacts/run-001.jsonl artifacts/run-002.json \
  --json-out artifacts/evaluation.json \
  --markdown-out artifacts/evaluation.md
```

JSON 보고서는 CI와 추세 분석용이며 Markdown 보고서는 사람이 검토하기 위한 표다.

## 모델 어댑터 경계

평가기는 외부 명령을 실행하지 않는다. 모델 호출, 임시 fixture 생성, Claude Code 도구
로그 수집, 이벤트 변환은 별도 어댑터가 담당한다. 다음 명령은 안전하게 토큰화된 실행
명령만 렌더링하므로 오케스트레이터가 직접 실행 여부를 결정할 수 있다.

```sh
python3 -m tools.evaluation adapter-command \
  --template 'python3 adapters/claude.py --scenario {scenario} --events {events}' \
  --scenario evals/scenarios/ambiguous-browser-storage.json \
  --events artifacts/run-001.jsonl
```

단위 테스트는 모델 호출이나 네트워크 없이 가짜 이벤트를 사용한다. 실제 모델 비교는
릴리스 전이나 수동 평가에서 동일 시나리오를 여러 번 실행하고 JSON 보고서를 버전별로
보관하는 방식으로 수행한다.
