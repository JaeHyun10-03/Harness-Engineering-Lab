# 첫 실험 사용법

## 준비 상태

Python 3.9 이상과 Claude Code가 필요합니다. 추가 Python 패키지는 필요 없습니다.
앱은 아직 만들지 않았고 서버·DB·언어도 미정입니다. 먼저 이 선택을 한 뒤 실제 앱 검사 명령을 등록합니다.

프로젝트 루트에서 Claude Code를 실행합니다. 프로젝트 설정 신뢰 여부를 묻는 화면은 사용자가 확인합니다.
`/memory` 또는 `/context`에서 `.claude/CLAUDE.md`의 로딩을 확인하고 `/hooks`에서
SessionStart, PreToolUse, Stop 등록을 확인합니다. CLI 버전에 따라 화면이 다를 수 있습니다.

## 사용자가 할 일

Claude에 다음처럼 요청합니다.

> 할 일 웹앱 첫 작업을 계획해줘. 아직 서버와 저장 방식은 정하지 않았으니 선택지를 설명하고 내 선택을 기다려줘.

선택이 끝난 뒤 구현을 요청하면 아래 절차를 Claude가 진행합니다.

## Claude가 사용하는 명령

모든 명령은 프로젝트 루트에서 실행합니다.

```sh
python3 .claude/hooks/workflow.py status
python3 .claude/hooks/workflow.py new 001-add-todo
```

생성된 task.md의 목표·범위·완료 기준과 나머지 필요한 항목을 작성합니다.
사용자 결정이 끝나고 구현이 허용됐으면 다음 명령으로 시작합니다.

```sh
python3 .claude/hooks/workflow.py start
```

`.claude/checks.json`의 checks 배열에 실제 프로젝트 검사 명령을 등록합니다.
각 항목은 `argv`(명령과 인수의 문자열 배열), `timeout_seconds`(1~3600)를 가집니다.
예를 들어 npm 기반 앱을 app 폴더에 선택했다면 다음 형태입니다. 이는 형식 예시이며 기술 선택이 아닙니다.

```json
{
  "checks": [
    {"argv": ["npm", "--prefix", "app", "test", "--", "--run"], "timeout_seconds": 120}
  ]
}
```

실제 앱의 테스트 도구가 지원하는 명령으로 조정해야 합니다. 빈 검사 목록은 통과할 수 없습니다.
검사 명령은 프로젝트 루트에서 실행되며 셸 문법을 자동 해석하지 않습니다.

```sh
python3 .claude/hooks/workflow.py verify
```

통과하면 메인이 verifier를 결과 리뷰 모드로 호출합니다. task.md·기준 ID·변경 파일·최신 검사 증거를 전달하고 실제 응답과 지적별 처리 내역을 review.md에 보존합니다. 필수 근거 부족·미해결 중요 지적이 있으면 수정·테스트·재검증합니다.

```sh
python3 .claude/hooks/workflow.py review-begin
# 이 명령 성공 후 verifier 호출·보고서 저장
python3 .claude/hooks/workflow.py review pass
python3 .claude/hooks/workflow.py complete
```

문제가 있으면 `review fail`, 수정 단계로 돌아가려면 `start`를 사용합니다.
수정한 뒤에는 verify → review → complete를 다시 수행합니다.

```sh
python3 .claude/hooks/workflow.py wait "사용자의 저장 방식 선택 대기"
python3 .claude/hooks/workflow.py block "DB 실행 실패. DB가 실행되면 재개"
```

대기·중단 이유와 다음 행동은 progress.md에도 기록합니다. 재개는 start로 시작하므로 재검사가 필요합니다.

## 필요한 작업의 성능 테스트

계획할 때 task.md에 성능 테스트 필요 여부와 이유를 기록합니다. API·쿼리·동시 처리 변경 등을 검토하되, 도구와 목표 수치는 앱과 측정 대상에 맞춰 사용자와 정합니다.
필요한 경우 데이터 규모·동시 요청 수·실행 시간·반복 횟수·실행 환경과 통과 기준을 함께 기록합니다. 기존 기능 개선은 구현 전에 기준 성능을 측정합니다.

checks.json에는 **일반 테스트 → 통과한 경우에만 성능 테스트**를 실행하는 통합 명령을 등록합니다.
현재 verify는 checks 배열의 모든 명령을 실행하므로, 두 명령을 단순히 나열하면 실패 시 성능 테스트를 건너뛴다는 보장이 없습니다.
통합 명령은 일반 테스트 실패나 성능 기준 미달 시 0이 아닌 종료 코드를 반환해야 합니다. 실제 앱이 정해지면 그 도구에 맞춰 작성합니다.

측정 조건·결과·목표 충족 여부·변경 전후 비교를 표준 출력으로 남기면 verify가 검사 로그와 함께 보관합니다. 상세 자료는 해당 작업의 evidence/에 저장하고 로그에서 위치를 안내합니다.
프로그램은 종료 코드와 로그의 변경 여부를 확인하며, 측정 조건이나 목표의 적절성은 리뷰에서 확인합니다.
필수 성능 테스트를 실행하지 못하면 block으로 기록하고 완료 처리하지 않습니다. 성능 테스트가 불필요한 작업은 이유를 남기고 일반 테스트만 실행합니다.

## 검증 서브에이전트 사용

프로젝트의 .claude/agents/verifier.md가 읽기 전용 검증 담당입니다. 도구는 Read·Grep·Glob으로 제한하며 모델은 별도로 고정하지 않습니다.
새 Claude Code 세션에서 이 프로젝트를 열고, 계획 확정 전과 결과 리뷰 시 verifier를 호출하도록 지침을 두었습니다.
메인은 모드·작업 ID·작업 문서·기준 ID·변경 파일·증거 경로를 전달합니다. verifier는 보고만 하고 실행·파일 수정·상태 변경을 하지 않습니다.
계획 응답은 plan-review.md, 결과 응답은 review.md에 메인이 저장합니다. 결과 리뷰에는 checks.json의 snapshot을 기록합니다.

호출이 안 되면 자체 리뷰로 대체하지 말고 원인과 재개 조건을 기록합니다. 실제 호출·도구 제한·보고 전달은 Claude Code 세션에서 별도로 확인해야 합니다.
기준은 [테스트 기준](testing-policy.md)과 해당 영역의 상세 문서를 사용합니다.

## 역할과 자동 실행

| 담당 | 하는 일 |
| --- | --- |
| 클로드 코드 | settings.json에 등록된 시점에 Hook 실행 |
| 메인 클로드 | 계획·구현·테스트 실행·상태 변경·보고 |
| verifier | 계획과 코드·테스트·증거를 읽기 전용 검증 |
| workflow.py | 호출된 명령에 따라 단계 변경·테스트 실행·증거 확인 |

Hook 연결은 .claude/settings.json, 처리 내용은 .claude/hooks/workflow.py에 있습니다.
Hook이 전체 개발을 진행시키지는 않습니다. 메인이 verify·complete 등 필요한 명령을 호출합니다.
현재는 메인 한 세션과 읽기 전용 verifier로 작업 하나를 진행합니다.
CI 병합·배포 차단과 정기 실행은 별도 연결이 필요합니다. 실제 Claude 대화에서의 준수 여부는 앱 실험에서 확인해야 합니다.

## 자동으로 확인하는 범위

- 목표·범위·완료 기준 항목이 비어 있으면 구현 시작 거부
- app, scripts, tests 파일의 Edit·Write 호출은 구현 단계에서만 허용
- 필수 검사 명령을 실제로 실행하고 종료 코드와 로그 저장
- 실패·시간 초과·검사 도중 파일 변경은 검사 실패 처리
- 검사 후 코드·설정·task.md가 바뀌면 검토·완료 거부
- 검토 통과 등록과 보고서가 없거나 보고서가 바뀌면 완료 거부
- 미완료 상태에서 응답을 끝내면 한 번 안내하고, 반복 차단 루프는 피함
- 단순 질문에 작업이 없거나 계획·대기·중단 상태이면 정상 응답 종료 허용

## 이 방식이 보장하지 않는 것

- 파일을 읽고 제대로 이해했는지, 요구사항과 테스트가 충분한지는 AI와 사람이 판단합니다.
- verifier의 독립 검증을 지침으로 요구합니다. Python은 호출 진위·검증 품질을 자동 증명하지 않습니다.
- Bash·다른 도구·외부 편집기의 모든 파일 변경을 사전에 막지는 않습니다. 검사 후 변경은 완료 시 지문으로 감지합니다.
- 상태·검사 설정·Hook 자체를 수정할 수 있는 주체에 대한 보안 경계가 아닙니다.
- Stop은 완료 선언의 자연어 의미를 판별하지 않습니다. 반복 방지로 응답 종료를 허용해도 작업은 완료 처리되지 않습니다.
- 한 프로젝트에서 한 개발 세션·한 작업씩 사용합니다. 동시 쓰기·다중 작업·자동 에이전트 실행은 지원하지 않습니다.
- 지문은 전체 프로젝트에서 작업 기록, docs, experiments, .git, 의존성·빌드 산출물과 로그를 제외합니다.
  테스트가 제외 경로의 파일을 실제 입력으로 사용한다면 이 제외 정책을 먼저 조정해야 합니다.
- 심볼릭 링크를 통한 검사 입력은 현재 지원하지 않습니다. 환경·외부 서비스의 변화 전체를 지문으로 잡지는 못합니다.
- 실제 Claude 세션에서 Hook 호출 여부와 지침 준수율은 별도 첫 실험으로 확인해야 합니다.

## 틀 자체의 검사

```sh
python3 -m unittest discover -s tests -v
```

테스트는 임시 프로젝트를 만들어 실행하므로 실제 작업 상태를 바꾸지 않습니다.
이 결과는 할 일 웹앱의 검사 통과 증거로 사용하지 않습니다.

공식 참고: [Hooks](https://code.claude.com/docs/en/hooks),
[지침](https://code.claude.com/docs/en/memory), [Skills](https://code.claude.com/docs/en/skills).

## 리뷰 한도와 사용자 결정 후 재개

결과 리뷰는 최초 1회 + 재리뷰 2회입니다. verifier 호출 직전에 review-begin을 실행합니다.
세 번째 review fail은 실패 종료 코드를 반환하고 작업을 waiting으로 바꿉니다. 회차별 보고서는 evidence/review-N.md에 보존합니다.
review-begin 후 호출이 실패해도 회차는 소비합니다. block으로 기록하고 호출 결과를 위조하지 않습니다.
사용자가 추가 리뷰를 명시적으로 허용하면 메인이 다음 명령에 실제 결정 내용을 기록합니다.

```sh
python3 .claude/hooks/workflow.py review-extend "사용자의 추가 리뷰 1회 허용 내용"
python3 .claude/hooks/workflow.py start
```

이는 1회만 추가하고 기존 회차를 초기화하지 않습니다. 다시 구현·verify·review-begin·verifier·review 순서로 진행합니다.
계획 리뷰와 리뷰만 요청한 작업의 한도는 지침으로 적용합니다. 실제 에이전트 호출·사용자 허락의 진위는 Python이 확인하지 않습니다.
