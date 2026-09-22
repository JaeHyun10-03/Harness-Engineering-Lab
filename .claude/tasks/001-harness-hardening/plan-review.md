# 계획 검증 기록

## 회차 1/3 — 2026-09-22, agentId a2b23e4dbcafddeaf

판정: 수정 필요. 지적 A~G(중요), H~M(개선).

### verifier 원문

- 모드: 계획 리뷰 (회차 1/3) / 작업 ID: 001-harness-hardening / 대상: task.md (phase=planning, 2026-09-22T05:35Z)
- 읽음: testing-policy, security.md, e2e.md, task.md, workflow.py, test_workflow.py(17개), settings.json, checks.json, 스킬 3종, 템플릿, verifier.md, README/getting-started 관련 줄.
- 확인 못 함: Agent 훅 payload 필드명(subagent_type), permissions.deny Edit/Write 규칙 의미, 세션 중 settings.json 훅 변경 반영 여부.

**A. [중요] SEC-07/C4** active.json 손상 시 `hook()`가 모든 이벤트에서 `current()` 호출 → hook-edit·hook-bash도 exit 2 → Edit/Write/Bash 전부 차단, hook-bash가 `active.json` 문자열을 거부하면 복구 명령도 불가. 수정: hook-bash는 상태를 읽지 않음, hook-edit는 예외를 "상태 없음"으로 처리, Stop 메시지는 "사용자에게 보고"로.
**B. [중요] SEC-02/C5** verifier 기록을 snapshot으로만 대조하면 재리뷰 때 1회차 기록 재사용 가능(snapshot이 tasks·docs 제외). 수정: 기록에 `attempt` 포함, review가 attempt·snapshot 둘 다 요구. 테스트: fail→verify→review-begin→호출 없이 pass→exit 1.
**C. [중요] E2E-01** 이 작업 자체가 새 게이트를 통과하려면 Agent 훅이 세션에 로드되어야 하나 Claude Code는 시작 시 훅을 스냅샷함. 수정: 구현→세션 재시작→status로 verifier_calls 확인→review-begin→verifier 절차를 계획에 명시. 필드명 다르면 block.
**D. [중요] SEC-07/C1** review pass는 reviewing 단계 필요 → 위조는 반드시 active.json 쓰기 동반. state를 쓸 수 있으면 verifier_calls도 쓸 수 있어 기록 요구는 위조 자체를 못 막음. 실질 방어는 항목 1·2·5. C1 문구를 "기록 없는 위조 exit 1 + 기록까지 위조하면 C2/C3 게이트 담당"으로 조정.
**E. [중요] SEC-02/C3** NotebookEdit는 `notebook_path` 사용, `file_path`만 읽으면 항상 허용. 수정: 둘 다 처리, `.ipynb` 사례 추가.
**F. [중요]** implement-task SKILL 6단계 "상세 자료는 evidence/에 보관"이 evidence 전면 deny와 충돌. 선택: SKILL 문구 변경(권장) 또는 evidence/attachments 허용.
**G. [중요]** review fail에도 기록 요구 여부 불명확. 충돌 테스트 목록: start 호출 16개(표 데이터 행·plan-review.md·planning 기록 필요), pass_review 6개, fail 호출 3개(test_review_failure_requires_new_checks, test_review_budget_stops…, test_third_review_can_pass…). 충돌 없음: test_edit_gate_and_session_resume, test_questions_waiting_and_loop_escape, test_happy_path.
**H. [개선] SEC-03/C2** `workflow.py hook-` 리터럴은 공백·따옴표로 우회 → 정규식. `rm -rf`만 → `rm\s+-[a-zA-Z]*[rR]`. `git restore` 누락. evidence·active.json 문자열 거부는 읽기 명령도 막음 → README에 Read 도구 사용 명시 또는 쓰기 형태만 차단.
**I. [개선]** hook-agent 경계: 활성 작업 없음·verifier 아님은 no-op, review-begin 전 호출은 deny 선택. PreToolUse 기록은 "호출 시도"만 증명 → README 한계에 기록.
**J. [개선] C7** 임시 디렉토리는 git 아님 → head/dirty null 허용, timeout, git 부재 처리. 실제 프로젝트에서 dirty는 항상 true(정보용).
**K. [개선] C10** timeout_seconds 값 미정 (예: 300).
**L. [개선] E2E-05** "active.json 변화 없음"은 verify가 바꾸므로 성립 안 함 → 임시 루트 격리 단언으로.
**M. [개선]** README assets 링크 14곳, build_diagrams.py 출력 경로, docs/** 허용으로 .py 편집 가능해짐 언급, "done 이후 변경 미감시" 한계 추가.

적용 제외 타당성: 모두 타당. 성능 불필요 타당(K만 결정).

### 메인 처리

- A~G 전부 반영. F는 SKILL 문구 변경(evidence 직접 쓰기 제거, 큰 자료는 docs/experiments/<id>/).
- G: review fail에도 기록 요구.
- H: 정규식 채택, `git restore` 추가. evidence·state 언급 Bash는 읽기 포함 전부 거부하고 Read 도구 사용을 문서화.
- I: no-op 2건 테스트. review-begin 전 호출 deny는 채택(비용 낮음).
- J·K(300초)·L·M 반영.

## 회차 2/3 — 2026-09-22, agentId a2b23e4dbcafddeaf (재개)

판정: 수정 필요 (중요 1건 N, 개선 O·P·Q). 이전 지적 A~M: C는 부분 해결(N으로 이어짐), 나머지 전부 해결.

### verifier 원문

**N. [중요] E2E-01/C11** 재시작 절차가 충돌: `wait` 뒤 phase=waiting이라 `review-begin`(reviewing 필요) 불가 — `start`·`verify` 단계 누락. 또 "확인용 verifier 호출"은 review-begin 전이라 항목 3의 deny에 걸림. 수정(a): verify→wait→재시작→start→verify→review-begin→verifier→status 확인→없으면 block. 회차 1 소모 수용. deny 응답 자체가 훅 로드 증거.
**O. [개선] SEC-03** `git\s+(checkout|restore)\s+.*--`는 `git restore file` 통과·`git restore --staged` 오차단 → `git\s+restore\b` 전체 차단 + `checkout\s+--`. `rm\s+-[a-zA-Z]*[rR]`는 `rm -f -r`, `--recursive` 통과 → 확장, C2에 `rm -f -r x` 추가.
**P. [개선]** hook-agent의 손상 state 동작 누락 → no-op(exit 0), C4 추가.
**Q. [개선] C8** start 호출 17개 전부, pass_review 사용 7개.
적용 제외·성능 판단: 유지.

### 메인 처리

N(a) 채택·C11 재작성. O 정규식 교체·C2 사례 추가. P 항목 4·C4 추가. Q 숫자 수정.

## 회차 3/3 — 2026-09-22, agentId a2b23e4dbcafddeaf (재개, 마지막)

판정: 수정 필요(경미 1건 R). "R 적용 시 통과 권고에 해당, 재리뷰 불필요". N·P·Q 해결, O 부분 해결→R.

### verifier 원문

**R. [중요·경미] SEC-03/항목 2·C2** rm 정규식 `[rR]\b`가 `rm -rf`(r 뒤 f) 불일치. 2회차 verifier 제시 패턴의 오류. 수정: `\b` 제거 → `\brm\b.*(\s-[a-zA-Z]*[rR]|--recursive)`. 검산: `rm -rf`, `rm -fr`, `rm -f -r x`, `rm -Rf` 일치; `rm -f x`, `rm --force x` 불일치. settings.json deny가 2차 방어이고 C2 테스트가 검출함.
선택지: (1) 정규식 수정 후 start(권장, 재리뷰 불필요) (2) 구현 중 수정(비권장).

### 메인 처리

(1) 채택. 정규식 수정 완료. 계획 리뷰 한도 소진(3/3). 결과 리뷰에서 C2 테스트로 확인.
