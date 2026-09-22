# 작업 명세

## 목표

하네스 평가에서 실증된 우회 경로와 결함을 프로그램 수준에서 막는다. 지침으로만 요구하던 항목(verifier 호출, 계획 완성도, 증거 메타데이터)을 workflow.py와 settings.json이 강제하도록 바꾼다.

## 범위

`.claude/hooks/workflow.py`, `.claude/settings.json`, `tests/test_workflow.py`, `.claude/agents/verifier.md`, `.claude/skills/*`, `.claude/rules/verification.md`, `.claude/README.md`, `docs/getting-started.md`, `docs/decisions.md`, `.claude/assets/` 이동.

우선순위 순 변경 항목:

1. `settings.json`에 `permissions.deny` 추가: evidence·state.json·active.json·index.md Edit/Write 거부, 파괴적 git(push, reset --hard, clean, checkout --, commit --no-verify)과 `rm -rf`/`rm -r` Bash 거부.
2. Bash PreToolUse 훅(`hook-bash`): 상태 파일을 읽지 않는다(손상 시에도 동작). 정규식으로 거부: `active\.json|state\.json|/evidence\b`(읽기 포함 — 로그는 Read 도구로 읽는다), `workflow\.py\s+["']?hook-`, `git\s+push`, `reset\s+--hard`, `git\s+clean`, `git\s+restore\b`, `git\s+checkout\s+--`, `--no-verify`, `\brm\b.*(\s-[a-zA-Z]*[rR]|--recursive)`.
3. verifier 호출 기록: Agent PreToolUse 훅(`hook-agent`, 매처 `Agent|Task`)이 `tool_input.subagent_type == verifier` 호출을 state의 `verifier_calls`에 `{at, phase, attempt, snapshot}`으로 기록. 활성 작업 없음·verifier 아님은 no-op. reviewing인데 `review_pending` 없으면 deny("review-begin 먼저"). `review pass|fail` 둘 다 `attempt == review_attempts` 이고 `snapshot == review_pending`인 기록을 요구. `start`는 planning에서 phase=planning 기록을 요구. 기록은 호출 시도만 증명한다(한계로 문서화).
4. Stop 훅: phase `done`이면 통과(complete에서 이미 검증, done 이후 변경은 감시하지 않음). `current()` 예외는 모든 훅에서 잡는다: Stop은 1회 block("상태 파일 손상. 사용자에게 보고"), hook-edit는 상태 없음으로 취급(deny-by-default만 적용, 사유에 손상 명시), hook-session은 안내 문구에 손상 표시, hook-agent는 no-op(exit 0).
5. 편집 게이트 deny-by-default: implementing이 아닐 때 허용 경로는 `docs/**`, 루트 `*.md`, `.claude/tasks/<활성 id>/{task,progress,plan-review,review}.md`만. 프로젝트 밖 경로는 현재처럼 허용. `.claude/hooks|settings.json|checks.json|rules|agents|skills`는 app 코드와 같은 게이트. `.claude/tasks/**/evidence/**`, `state.json`, `active.json`, `tasks/index.md`는 모든 단계에서 Edit/Write 거부. 매처를 `Edit|Write|NotebookEdit`로 하고 `file_path or notebook_path`를 읽는다. docs/** 허용으로 이동한 `docs/assets/*.py`는 계획 단계에서도 편집 가능(수용).
6. `start` 조건 강화: `plan-review.md` 존재·비어있지 않음·`판정` 포함, task.md "적용 영역과 상세 기준"·"일반 테스트 방법" 표에 데이터 행 1개 이상. `review pass` 조건 강화: review.md에 `## 독립 검증 결과` 아래 내용과 대상 snapshot 문자열 포함.
7. `evidence/checks.json`에 `started_at`, `finished_at`, 검사별 `duration_seconds`, `python`, `git.head`, `git.dirty` 기록. git 부재·저장소 아님·timeout(10초)이면 null, 예외 없이 진행. `evidence/changes.txt`에 `git status --porcelain` + `git diff --stat` 저장(저장소 아니면 사유 한 줄). dirty는 verify가 state를 먼저 쓰므로 실제 프로젝트에서 항상 true — 정보용.
8. 문서 중복 정리(`.claude/README.md`의 `assets/` 링크 전부 `../docs/assets/`로, `build_diagrams.py` 출력 경로 확인): `rules/verification.md`의 리뷰 반복 제한 절을 링크 한 줄로 축소. `.claude/assets/`를 `docs/assets/`로 이동하고 README 링크 갱신. getting-started "자동으로 확인하는 범위" 갱신.

## 완료 기준

- C1. 테스트가 evidence/checks.json(올바른 snapshot·log_hash)과 active.json phase=reviewing을 직접 써서 위조하면 `review-begin` 후 `review pass`가 verifier 기록 없음으로 exit 1, `complete`도 exit 1이다. 기록까지 위조하면 프로그램은 구분하지 못하며 그 경로는 C2/C3 게이트가 담당한다(문서에 명시).
- C2. `hook-bash`가 `git push`, `rm -rf`, `rm -fr`, `rm -f -r x`, `git restore x`, `cat .claude/tasks/t/evidence/check-1.log`, `python3  ".claude/hooks/workflow.py" hook-stop`를 deny하고 `git commit -m x`, `git status`, `python3 -m unittest discover -s tests`, `python3 .claude/hooks/workflow.py status`를 허용한다. active.json이 손상돼도 동작한다. 테스트 있음.
- C3. `hook-edit`가 planning 단계에서 `src/x.py`, `.claude/hooks/workflow.py`, `.claude/checks.json`, `notebook_path=app/a.ipynb`를 deny하고 `docs/a.md`, `README.md`, 활성 작업 `task.md`, 프로젝트 밖 `/tmp/x`를 허용한다. implementing에서도 `evidence/checks.json`, `active.json`, `state.json`, `tasks/index.md`는 deny. 활성 작업 없음·손상 state에서도 deny-by-default가 적용된다. 테스트 있음.
- C4. `done` 상태에서 루트 파일 변경 후 `hook-stop`이 빈 출력(허용)이다. 손상된 active.json에서 `hook-stop`은 exit 0·block JSON 1회(stop_hook_active면 빈 출력), `hook-edit`는 exit 0·deny, `hook-bash`는 exit 0·허용 명령 통과, `hook-session`은 exit 0·손상 안내, `hook-agent`는 exit 0·빈 출력이다. 테스트 있음.
- C5. `review pass`와 `review fail`은 현재 회차·snapshot과 일치하는 `hook-agent`(verifier) 기록이 없으면 실패한다. 1회차 기록은 2회차에 재사용되지 않는다(fail→verify→review-begin→호출 없이 pass→exit 1). `subagent_type`이 다르거나 활성 작업이 없으면 hook-agent는 no-op이고, reviewing에서 review-begin 전 호출은 deny다. `start`는 planning에서 plan-review.md 없거나 planning 단계 verifier 기록 없으면 실패한다. 테스트 있음.
- C6. `review pass`는 review.md에 `## 독립 검증 결과`와 snapshot 문자열이 없으면 실패한다. 테스트 있음.
- C7. verify 후 `evidence/checks.json`에 started_at·finished_at·python·git(head·dirty, 저장소 아니면 null)·검사별 duration_seconds가 있고 `evidence/changes.txt`가 존재한다. 테스트 있음(git 저장소 아닌 임시 루트).
- C8. `python3 -m unittest discover -s tests` 전체 통과. 기존 17개 갱신 대상: setUp에 두 표 데이터 행·plan-review.md·planning 단계 hook-agent 호출 추가(start 호출 17개 전부), `pass_review()`에 hook-agent 호출과 review.md 형식 추가(7개), fail 호출 3개(test_review_failure_requires_new_checks, test_review_budget_stops_and_explicit_extension_preserves_history, test_third_review_can_pass_and_begin_is_required)에 기록 추가, test_missing_plan_and_invalid_id에 stderr 메시지 단언 추가. 충돌 없음 3개는 유지.
- C9. `.claude/README.md`, `docs/getting-started.md`가 새 훅·명령·게이트 경로를 설명한다. `rules/verification.md`에 리뷰 반복 제한 본문 중복이 없다. `.claude/assets/`가 없고 README 그림 링크가 `../docs/assets/`를 가리킨다.
- C10. `.claude/checks.json`에 `python3 -m unittest discover -s tests -v`(timeout_seconds 300)가 등록되어 verify가 이 테스트를 실행한다.
- C11. 이 작업의 결과 리뷰는 세션 재시작 후 진행한다: 구현·verify 후 `wait "세션 재시작 필요"` → 사용자가 세션 재시작 → `start` → `verify` → `review-begin` → verifier 호출 → `status`에서 `verifier_calls`에 현재 attempt 기록 확인 → 없으면(훅 미로드 또는 payload 필드명 불일치) `block`으로 기록하고 미완료 보고. 회차 1이 소모되어도 수용한다. 확인 결과를 progress.md와 실험 기록에 남긴다.

## 하지 않을 일

- 같은 OS 사용자에 대한 암호학적 위조 방지. CI 재실행 연결.
- 할 일 웹앱 기술 선택·구현.
- snapshot 계산 방식 변경(mtime 캐시, git 기반). Stop 훅이 done을 통과하면 해시 비용 문제가 사라지므로 이번 범위에서 제외.
- verifier `model:` 고정.

## 적용 영역과 상세 기준

- 프론트: 해당 없음 (화면 없음).
- 백엔드·데이터: 해당 없음 (API·DB 없음).
- AI: 해당 없음 (제품 LLM 기능 아님. verifier는 개발 도구).
- DevOps: 해당 없음 (CI·배포 미연결. 향후 적용으로 기록).
- 보안: 적용. 권한 경계(훅·deny 규칙)를 바꾸는 변경.
- 성능: 해당 없음 (아래 성능 테스트 항목 참조).
- 전체 흐름: 적용. 하네스 자체의 사용자 흐름(계획→구현→검사→리뷰→완료)과 회귀.

| 기준 ID | 완료 기준·시나리오 | 기대 결과·수치 목표 | 실행 명령 | 시점 | 증거 위치 |
| --- | --- | --- | --- | --- | --- |
| SEC-02 | C3·C5: 단계별 편집 권한, verifier 기록 없는 리뷰 등록 | 허용 단계 외 deny, 우회 0건 | `python3 -m unittest discover -s tests -v` | 작업 완료 전 | evidence/check-1.log |
| SEC-03 | C2·C3: 경로 이탈(`../`), 절대경로, 명령 문자열 패턴 | 프로젝트 밖 경로는 무시, 패턴 deny | 동일 | 동일 | 동일 |
| SEC-05 | 훅 로그·evidence에 비밀 출력 없음 | 검사 명령이 환경변수를 출력하지 않음 (현 검사는 unittest만) | 검토 시 로그 확인 | 리뷰 | evidence/check-1.log |
| SEC-07 | C1·C4: 증거 위조·상태 손상 시 권한 | 위조 complete 0건, 손상 시 교착 없음 | 동일 | 동일 | 동일 |
| SEC-01/04/06/08 | 해당 없음 | 인증·브라우저·의존성·트래픽 없음 | — | — | — |
| E2E-01 | C8: 정상 흐름 new→start→verify→review-begin→hook-agent→review pass→complete | done 도달 | 동일 | 동일 | 동일 |
| E2E-04 | C1·C2·C4·C6: 실패 경로가 성공으로 표시되지 않음 | exit 1 또는 deny JSON | 동일 | 동일 | 동일 |
| E2E-05 | 테스트는 임시 디렉토리 격리 | 테스트가 쓰는 script·tasks 경로가 임시 루트 아래임을 단언, 실제 001 state.json은 verify의 phase 전이 외 동일 | 동일 | 동일 | 동일 |
| E2E-06 | C8: 기존 17개 테스트 회귀 | 전체 통과 | 동일 | 동일 | 동일 |
| E2E-02/03 | 해당 없음 | 프론트·DB 연동 없음 | — | — | — |

## 일반 테스트 방법

| 완료 기준 | 시나리오·예상 결과 | 테스트 명령·근거 위치 | 실행 시점 |
| --- | --- | --- | --- |
| C1 | 위조 evidence → review pass exit 1, complete exit 1 | tests/test_workflow.py::test_forged_evidence_without_verifier_is_rejected | 완료 전 |
| C2 | hook-bash deny/allow 목록 | tests/test_workflow.py::test_bash_gate | 완료 전 |
| C3 | hook-edit 단계·경로 매트릭스 | tests/test_workflow.py::test_edit_gate_deny_by_default | 완료 전 |
| C4 | done 후 편집 허용, 손상 state 1회 block | tests/test_workflow.py::test_stop_after_done_and_corrupt_state | 완료 전 |
| C5 | verifier 기록 유무에 따른 start·review | tests/test_workflow.py::test_verifier_call_required | 완료 전 |
| C6 | review.md 형식 검사 | tests/test_workflow.py::test_review_report_format | 완료 전 |
| C7 | 증거 메타데이터·changes.txt | tests/test_workflow.py::test_evidence_metadata | 완료 전 |
| C8 | 전체 통과 | `python3 -m unittest discover -s tests -v` (checks.json 경유 verify) | 완료 전 |
| C11 | 세션 재시작 후 verifier_calls 생성 | `workflow.py status` 출력 확인, progress.md 기록 | 리뷰 전 |
| C9·C10 | 문서·설정 대조 | 리뷰에서 파일 직접 확인 | 리뷰 |

제외: 동시 요청(단일 세션 설계), 성능(아래 참조). 실제 Claude Code 세션에서 훅 매처가 `Agent`·`NotebookEdit`를 잡는지는 단위 테스트로 증명 못 함 → C11에서 Agent는 완료 전 확인, NotebookEdit는 실험 기록의 미확인 항목.

## 성능 테스트

- 필요 여부와 이유: 불필요. 훅 스크립트는 파일 몇 개 읽고 JSON 출력. Stop 훅 해시 비용은 done 통과로 제거. 앱 코드 없음.

## 사용자 결정과 남은 질문

- 2026-09-22 결정: 하네스 핵심 파일은 완전 잠금 대신 단계 게이트(implementing에서만 수정).
- 2026-09-22 결정: Bash 차단은 파괴적 git + 삭제 명령. 일반 커밋 허용.
- 2026-09-22 결정(계획 리뷰 F): implement-task SKILL의 "상세 자료는 evidence/에 보관"을 제거. 검사 명령이 표준 출력에 남기고, 큰 자료는 `docs/experiments/<id>/`에 둔다.
- 2026-09-22 결정(계획 리뷰 G): `review fail`에도 verifier 기록을 요구한다. 호출 불가는 `block`이 정해진 경로.
- 한계 명시: 같은 OS 사용자인 Claude가 Bash로 `.claude` 아래를 덮어쓰는 것을 hook-bash 패턴이 전부 막지는 못함. state를 쓸 수 있으면 verifier_calls도 쓸 수 있음. 이번 범위는 실수·편법 차단. hook-agent 기록은 호출 시도만 증명.
