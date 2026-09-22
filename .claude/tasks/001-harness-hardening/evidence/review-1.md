# 검토 결과

## 검토 범위

작업 001-harness-hardening, 완료 기준 C1~C11, 기준 ID SEC-02/03/05/07, E2E-01/04/05/06.
변경 파일은 evidence/changes.txt(15개 수정 + assets 27개 이동).

## 독립 검증 결과

### 회차 1/3 — 2026-09-22 05:56Z, agentId a13019935dc560a9f

대상 snapshot: 2b887dbc8589aeacb4bdab5a48b8457b9eff6809713273745a2459993904ee32
읽은 자료: testing-policy, security.md, e2e.md, task.md, plan-review.md, progress.md, state.json, evidence 3종, workflow.py, test_workflow.py, settings.json, checks.json, .claude/README.md, getting-started, rules/verification.md, 스킬 3종, recovery.md, verifier.md, decisions.md, 루트 README, build_diagrams.py.
미확인: 테스트 실행 자체(로그만), permissions.deny 상대경로 해석·Edit 규칙의 Write/NotebookEdit 적용, NotebookEdit 매처. Agent 훅은 state.json의 verifier_calls[0](reviewing, attempt 1, snapshot 일치)로 실제 기록 확인 — C11 핵심 달성.

판정: 수정 필요 (중요 3, 개선 5)

1. [중요] SEC-02/C5 — `start`의 계획 게이트(plan-review·planning verifier 기록)가 `phase == 'planning'`일 때만 실행. planning→wait→start, planning→block→start 경로에서 우회. README 164행·getting-started 113행이 무조건 거부로 설명해 문서 불일치. 수정: 최초 start 여부를 state에 남기고 그 조건으로 검사, 미시작 상태의 hook-agent 기록을 단계 무관하게 인정. 테스트 추가.
2. [중요] C9/계획 F — getting-started 83행 "상세 자료는 evidence/에 저장" 잔존. evidence/는 전 단계 거부이므로 거부되는 행동을 지시. `docs/experiments/<id>/`로 수정.
3. [중요·경미] SEC-03/C3/C5 — 테스트 누락 3건: `../` 경로 이탈, 활성 작업 없음에서 hook-edit deny-by-default, 활성 작업 없음에서 hook-agent no-op.
4. [개선] C11 — progress.md·docs/experiments에 훅 확인 결과와 절차 변경 이유(세션 중 재로드 관찰) 미기록.
5. [개선] C7 — test_evidence_metadata가 git null 분기(head None, dirty None) 미단언.
6. [개선] SEC-03 — `/evidence\b`는 `cd … && cat evidence/…` 통과(문서화된 한계). rm 정규식 `.*`가 `rm -f x && grep -r` 오차단(안전 방향). `git add .claude/tasks/active.json` 같은 정상 명령 거부 → README 안내 권장.
7. [개선] settings.json deny 경로: 선행 슬래시 없는 패턴은 현재 디렉토리 기준일 수 있음. `/`-접두가 견고. 미확인.
8. [개선] changes.txt의 diff --stat은 unstaged만. porcelain에 있어 정보 손실 없음.

완료 기준 대조: C1·C2·C3·C4·C6·C8·C10 충족. C5 우회 경로 미커버(1). C7 테스트 약함(5). C9 불일치(1·2). C11 기록 미완(4). SEC-05·E2E-05 충족. 적용 제외 타당. 계획 지적 A~R 중 F·C/N·I·J 부분 해결.

재리뷰 확인 항목: 지적 1 수정으로 인한 test_verifier_call_required·test_aborted_review_consumes_budget·test_review_budget_stops 회귀. verify 재실행·새 snapshot·review-begin 2회차 필요.

## 지적별 처리

(회차 1 판정 후 수정 예정. 아래 회차 2에서 갱신.)

## 완료 기준별 근거

(회차 2에서 갱신)

## 성능 테스트 확인

불필요. 이유: 훅 스크립트, 앱 코드 없음. Stop 훅 해시 비용은 done 통과로 제거(task.md).

## 발견한 문제

회차 1 지적 1~8.

## 판정과 이유

회차 1: review fail. 중요 지적 1·2·3 미해결.

## 확인하지 못한 부분

NotebookEdit 매처·permissions.deny 규칙 해석은 실제 세션 확인 항목.
