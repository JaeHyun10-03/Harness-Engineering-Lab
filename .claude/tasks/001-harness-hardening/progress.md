# 진행 기록

## 2026-09-22 계획

- 하네스 평가 결과를 바탕으로 8개 우선순위 항목을 한 작업으로 계획.
- 사용자 결정: 핵심 파일은 단계 게이트, Bash는 파괴적 git+삭제 차단.
- 계획 리뷰 3회(agentId a2b23e4dbcafddeaf). A~R 반영. 3회차 결론: R 수정 시 통과 권고. plan-review.md 참조.
- 다음 행동: `start` → 구현 순서 1→8 → checks.json 등록 → verify → `wait "세션 재시작 필요"` (C11).

## 2026-09-22 구현

- 항목 1~8 전부 구현. workflow.py: PROTECTED·BASH_DENY 상수, edit_decision, hook-bash, hook-agent, Stop done 통과·손상 처리, start·review 조건 강화, 증거 메타데이터·changes.txt.
- settings.json: permissions.deny 16개, PreToolUse 매처 Edit|Write|NotebookEdit / Bash / Agent|Task.
- checks.json: unittest 300초. 테스트 24개(기존 17 갱신 + 신규 7) 로컬 통과.
- 문서: .claude/README, getting-started, decisions, rules/verification, 스킬 3종, recovery, verifier. assets → docs/assets.
- 관찰: settings.json 저장 직후 이 세션에서 Bash Hook이 즉시 동작함(heredoc에 경로 문자열이 있어 거부됨 → Write 도구 사용). 훅이 세션 중 재로드되는 것으로 보임. C11 절차는 그대로 따르되 재시작 없이 verifier_calls가 생기면 그 사실을 기록.
- 다음 행동: verify → review-begin → verifier → status에서 verifier_calls 확인.

## 2026-09-22 결과 리뷰 1회차 → 수정

- verify 통과 (snapshot 2b887dbc…). review-begin 1/3 → verifier 호출 → **verifier_calls에 attempt 1·snapshot 일치 기록 확인** (C11 달성, 세션 재시작 불필요. 관찰은 docs/experiments/001-harness-hardening.md).
- verifier 판정: 수정 필요(중요 3: start 게이트가 wait/block 경유 시 우회, getting-started evidence/ 문구 잔존, 테스트 사례 3건 누락). review fail 등록.
- 수정: `state['started']` 플래그로 최초 start에만 계획 게이트 적용, hook-agent 기록에 `stage` 추가. 문서 2곳 수정. 테스트 사례 추가(wait 경유, `../`, 활성 작업 없음, git null). settings.json deny 경로 `/` 접두. README·getting-started git add 안내. 실험 기록 001 작성.
- 로컬 24개 통과. 다음 행동: verify → review-begin 2/3 → verifier 재리뷰.

## 2026-09-22 결과 리뷰 2회차 → 완료

- verify 재실행 통과 (snapshot e45f51a5…). review-begin 2/3.
- SendMessage 재개 리뷰는 훅 기록이 없어 무효 → Agent 도구로 새 verifier 호출(a7ab60c997758eb55, attempt 2 기록 확인). 판정: 통과 권고, 개선 2건은 문서 처리.
- review pass 등록. complete 실행.
