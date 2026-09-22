# 하네스 강화 — 지침에서 프로그램 강제로

날짜: 2026-09-22

## 시작 전

- 실험 이름: 001-harness-hardening
- 확인하고 싶은 점: 평가에서 실증된 우회 5건(증거 위조, .claude 무방비, Bash 무통제, done 이후 Stop 반복, 상태 손상 교착)을 프로그램으로 막을 수 있는가. Claude Code의 Agent·Bash Hook과 permissions.deny가 실제 세션에서 기대대로 동작하는가.
- 사용할 도구: Claude Code(Opus), workflow.py, unittest
- AI에게 맡길 작업: 계획·구현·테스트·문서 갱신. 계획·결과 리뷰는 verifier.
- 이번에 하지 않을 작업: 암호학적 위조 방지, CI 연결, 앱 기술 선택
- 완료로 인정할 조건: task.md C1~C11
- 이전 방식과 달라지는 규칙: 편집 허용 목록, Bash 패턴 거부, verifier 호출 기록 강제, Stop done 통과

## 실행 후

- 실제 변경 내용: workflow.py(+hook-bash, hook-agent, edit_decision, 조건 강화, 메타데이터), settings.json(deny 16개, 매처 3개), 테스트 17→24개, 문서 9개, assets 이동
- 실행한 검사와 결과: `python3 -m unittest discover -s tests -v` 24개 통과 (verify 경유, evidence/check-1.log)
- 실행하지 못한 검사와 이유: NotebookEdit 매처·notebook_path — 이 세션에서 노트북 편집이 없어 미확인
- 걸린 시간: 약 1시간 (계획 리뷰 3회 + 결과 리뷰 2회 포함)
- 확인 가능한 사용 비용: 계획 verifier 약 68k 토큰, 결과 verifier 1회차 약 106k 토큰
- 사람이 수정하거나 개입한 내용: 핵심 파일 통제 수준(단계 게이트)과 Bash 차단 범위(파괴적 git+삭제) 결정
- 남은 문제: Bash 문자열 패턴은 `cd … && cat evidence/…` 통과, `rm -f x && grep -r` 오차단. 같은 OS 사용자 위조는 못 막음.
- 다음에 이어서 할 일: 앱 기술 선택 후 실제 검사 명령 등록. NotebookEdit 확인.

## 관찰

- **훅은 세션 중 재로드된다.** settings.json 저장 직후 같은 세션에서 Bash Hook이 동작해 heredoc에 경로 문자열이 든 명령을 거부했다. Agent Hook도 재시작 없이 verifier 호출을 기록했다(state.json의 verifier_calls, attempt 1, snapshot 일치). 계획 리뷰 C·N의 전제("시작 시 훅 스냅샷")는 이 환경(Claude Code 2.1.278, 데스크톱 앱)에서 성립하지 않았다. C11의 재시작 절차는 불필요했고, 대신 review-begin → verifier → status 확인만으로 검증했다.
- Bash 훅이 메인 자신의 문서 편집도 막았다(문서 본문에 `active.json`이 있으면 heredoc 명령이 거부됨). Edit·Write 도구로 우회하는 것이 정상 경로이며, 이는 "Bash로 상태 파일을 건드리지 않는다"는 의도와 일치한다.
- Agent 훅 payload의 `tool_input.subagent_type` 필드명은 예상과 같았다.
- **SendMessage 재개는 훅에 안 잡힌다.** 1회차 verifier에게 SendMessage로 2회차를 요청하니 "통과 권고"는 왔지만 verifier_calls에 attempt 2가 없었다(verifier 자신이 지적). Agent 도구로 새로 호출해 기록을 남긴 뒤 등록했다. 회차마다 새 Agent 호출이 정상 경로이며 getting-started 한계에 추가했다.
- 이 작업의 state는 `started` 플래그 도입 전에 start했으므로 플래그가 없다. 그 결과 2회차 호출이 `stage: plan`으로 기록됐다(review는 attempt·snapshot만 보므로 영향 없음). 완료 후 자연 소멸. 새 작업부터는 정확히 기록된다.

## 판단

- 유지할 규칙: 편집 허용 목록, evidence·state 이중 거부, verifier 호출 기록 요구, Stop done 통과
- 바꾸거나 제거할 규칙: 없음. Bash 패턴은 앱 실험에서 오차단 빈도를 보고 조정.
- 판단 근거: 24개 테스트와 실제 세션 훅 동작 관찰
