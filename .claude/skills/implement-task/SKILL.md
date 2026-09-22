---
name: implement-task
description: 합의한 개발 작업을 구현하고 실제 검사를 실행해 결과를 남긴다. 기존 작업 재개에도 사용한다.
---

1. `python3 .claude/hooks/workflow.py status`와 해당 작업의 `task.md`, `progress.md`를 읽는다.
2. 실제 파일 상태를 대조하고 관련 소스·테스트를 읽는다. 사용자의 변경을 덮어쓰지 않는다.
3. 미결정 사항이 해결되고 명세가 확정됐으면 `python3 .claude/hooks/workflow.py start`로 구현 단계를 시작한다. 구현 중 요구사항이나 테스트 기준이 바뀌면 임의로 범위를 정하지 말고 질문·결정을 task.md에 반영한 뒤 verifier 계획 검증과 start를 다시 수행한다. 변경된 명세의 검증 없이 verify를 재사용하지 않는다.
4. 요청한 범위만 구현한다. 의미 있는 단계마다 `progress.md`에 다음 행동을 남긴다.
5. task.md의 성능 테스트 필요 여부를 확인한다. `.claude/checks.json`에는 일반 검사 다음에 필요한 성능 검사 명령을 순서대로 등록한다. `verify`는 첫 실패나 시간 초과에 멈추고 나머지를 미실행으로 기록한다. 각 명령은 기준 미달 시 0이 아닌 종료 코드를 반환해야 한다.
6. `python3 .claude/hooks/workflow.py verify`를 실행한다. 필요한 성능 결과는 조건·측정값·변경 전후 비교를 포함해 검사 로그에 출력한다. 큰 상세 자료는 `docs/experiments/<id>/`에 둔다(evidence/는 프로그램만 쓴다). 필수 성능 테스트를 실행할 수 없으면 block으로 전환하고 미완료로 보고한다.
7. 실패하면 최신 `evidence/verify-NNN/check-*.log`를 Read 도구로 읽고(Bash로는 열 수 없다) 수정한 뒤 다시 검사한다. 이전 실행 기록은 덮어쓰지 않는다. 통과하면 `review-task`로 이어간다.

환경 실패·기록 불일치·중단 후 복구 때만 [복구 절차](references/recovery.md)를 추가로 읽는다.
