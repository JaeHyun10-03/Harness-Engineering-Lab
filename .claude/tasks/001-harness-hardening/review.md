# 검토 결과

## 검토 범위

작업 001-harness-hardening, 완료 기준 C1~C11, 기준 ID SEC-02/03/05/07, E2E-01/04/05/06.
변경 파일: evidence/changes.txt(수정 15개 + assets 27개 이동 + 실험 기록 1개 신규).

## 독립 검증 결과

### 회차 1/3 — 2026-09-22 05:56Z, agentId a13019935dc560a9f

대상 snapshot: 2b887dbc8589aeacb4bdab5a48b8457b9eff6809713273745a2459993904ee32
읽은 자료: testing-policy, security.md, e2e.md, task.md, plan-review.md, progress.md, state.json, evidence 3종, workflow.py, test_workflow.py, settings.json, checks.json, .claude/README.md, getting-started, rules/verification.md, 스킬 3종, recovery.md, verifier.md, decisions.md, 루트 README, build_diagrams.py.
미확인: 테스트 실행 자체(로그만), permissions.deny 상대경로 해석·Edit 규칙의 Write/NotebookEdit 적용, NotebookEdit 매처. Agent 훅은 state.json의 verifier_calls[0](attempt 1, snapshot 일치)로 실제 기록 확인.

판정: 수정 필요 (중요 3, 개선 5)

1. [중요] SEC-02/C5 — start 계획 게이트가 `phase == 'planning'`일 때만 실행. planning→wait→start, planning→block→start 경로에서 우회. README·getting-started 문구 불일치.
2. [중요] C9/계획 F — getting-started 83행 "상세 자료는 evidence/에 저장" 잔존.
3. [중요·경미] SEC-03/C3/C5 — 테스트 누락: `../` 경로 이탈, 활성 작업 없음 hook-edit, 활성 작업 없음 hook-agent no-op.
4. [개선] C11 — progress·실험 기록에 훅 확인 결과·절차 변경 이유 미기록.
5. [개선] C7 — git null 분기 미단언.
6. [개선] SEC-03 — `/evidence\b`는 `cd … && cat evidence/…` 통과(문서화된 한계). rm 정규식 오차단(안전 방향). `git add …/active.json` 정상 명령 거부 → 안내 권장.
7. [개선] settings.json deny 경로 `/`-접두 권장.
8. [개선] changes.txt diff --stat은 unstaged만. 정보 손실 없음.

완료 기준 대조: C1·C2·C3·C4·C6·C8·C10 충족. C5 우회 미커버(1). C7 테스트 약함(5). C9 불일치(1·2). C11 기록 미완(4).

### 회차 2/3 (a) — SendMessage 재개, agentId a13019935dc560a9f — 기록 없음

같은 verifier에게 SendMessage로 재리뷰 요청. 응답: "통과 권고, 지적 1~8 전부 해결, 회귀 없음. 단 2회차 verifier 호출 기록이 없어 review 등록 불가(지적 A). 기존 001 state에 started 부재(지적 B, 별도 수정 불필요)". 이 경로는 Agent Hook을 거치지 않아 state에 기록되지 않음 — 정식 회차로 인정하지 않고 (b)로 대체.

### 회차 2/3 (b) — 2026-09-22 06:10Z, agentId a7ab60c997758eb55 (새 문맥, Agent 도구 호출, verifier_calls[1] attempt 2 기록 확인)

대상 snapshot: e45f51a53034a43bea36d975547a600604875d6512e8af6b0e044e7041696d15
읽은 자료: testing-policy, security.md, e2e.md, task.md, review.md, progress.md, state.json, evidence 3종, workflow.py, test_workflow.py, settings.json, getting-started, .claude/README.md, 실험 기록 001, plan-review.md.
미확인: 테스트 실행 자체, log_hash 실제 값, verify 이후 파일 변경 여부(review pass 시 프로그램이 판정), permissions.deny·NotebookEdit 실제 동작.

판정: 통과 권고 (차단/중요 없음, 개선 2)

이전 지적별: 1 해결(workflow.py 344~350행 `started` 게이트, hook-agent 274행 stage, 테스트 315~320행, 문서 일치. 회귀: test_aborted_review_consumes_budget·test_review_budget_stops 로그 ok). 2 해결(83행). 3 해결(277·291~295행). 4 해결(실험 기록·progress). 5 해결(358~359행). 6 해결(README 182행, getting-started 134행). 7 해결(settings 4~11행). 8 수용.
완료 기준: C1~C7 테스트 7개 로그 ok, C8 24개 OK, C9 assets 없음·링크 정상, C10 unittest -v, C11 verifier_calls[1] attempt 2 snapshot 일치. SEC-05 로그에 비밀 없음. E2E-05 임시 루트 단언. 적용 제외 타당.
새 사항(완료 막지 않음): (가) 001 state에 `started` 없어 2회차 호출이 stage plan으로 오표기 — review는 attempt·snapshot만 보므로 영향 없음, 완료 후 자연 소멸. (나) SendMessage 재개가 호출 기록 게이트를 우회하는 실제 사례 — 문서화 권장.

## 지적별 처리

| 회차·번호 | 심각도 | 처리 | 재테스트·재검증 |
| --- | --- | --- | --- |
| 1-1 | 중요 | `state['started']`, hook-agent `stage`, 테스트·문서 | verify 재실행 24개 통과, 2(b)에서 해결 확인 |
| 1-2 | 중요 | getting-started 83행 수정 | 2(b) 확인 |
| 1-3 | 중요·경미 | 테스트 3건 추가 | 로그 ok, 2(b) 확인 |
| 1-4 | 개선 | 실험 기록 001 작성, progress 갱신 | 2(b) 확인 |
| 1-5 | 개선 | git None 단언 | 로그 ok |
| 1-6 | 개선 | git add 디렉토리 단위 안내 | 2(b) 확인 |
| 1-7 | 개선 | deny 경로 `/`-접두 | 2(b) 확인 |
| 1-8 | 개선 | 변경 없음 | — |
| 2-가 | 개선 | 실험 기록에 한계로 기록. 코드 변경 없음(완료 후 소멸) | — |
| 2-나 | 개선 | getting-started 한계·실험 기록 관찰에 추가 (docs는 지문 제외라 snapshot 유지) | — |

## 완료 기준별 근거

| 기준 | 근거 |
| --- | --- |
| C1 | test_forged_evidence_without_verifier_is_rejected ok |
| C2 | test_bash_gate ok (deny 9, allow 6, 손상 시 동작) |
| C3 | test_edit_gate_deny_by_default ok (`../`, ipynb, 활성 없음, 손상 포함) |
| C4 | test_stop_after_done_and_corrupt_state ok + bash/edit/agent 손상 사례 |
| C5 | test_verifier_call_required ok (wait 경유 거부, 재사용 거부, no-op, review-begin 전 deny) |
| C6 | test_review_report_format ok |
| C7 | test_evidence_metadata ok (git None) |
| C8 | check-1.log `Ran 24 tests … OK`, exit 0 |
| C9 | 2(b) 확인: assets 링크·트리, rules 중복 제거, 스킬 문구, getting-started |
| C10 | checks.json unittest -v 300초, verify가 실제 실행 |
| C11 | verifier_calls attempt 1·2 기록. 재시작 불필요(훅 세션 중 재로드). 실험 기록 001 |

## 성능 테스트 확인

불필요. 훅 스크립트·앱 코드 없음. Stop 훅 해시 비용은 done 통과로 제거.

## 발견한 문제

미해결 차단/중요 없음. 개선 2-가·2-나는 문서로 처리.

## 판정과 이유

회차 2(b) 통과 권고 + 완료 기준 전부 근거 확인 → review pass.
회차 2(a)는 훅 기록이 없어 정식 회차로 쓰지 않았다.

## 확인하지 못한 부분

NotebookEdit 매처·notebook_path, permissions.deny 규칙의 실제 해석은 후속 세션 확인 항목. Bash 패턴 한계는 getting-started에 기록.
