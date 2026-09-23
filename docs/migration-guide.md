# 수동 이식 안내

이 하네스는 대상 프로젝트에 별도 런타임이나 관리 폴더를 설치하지 않는다. 공통 작업 흐름을 복사한 뒤 프로젝트의 기존 설정과 사람이 대조·병합한다.

## 이식할 항목

```text
.claude/
├── CLAUDE.md
├── agents/
├── hooks/
├── rules/
├── skills/
├── settings.json
├── checks.json
├── snapshot.json
└── tasks/

docs/
├── testing-policy.md
└── testing/

tests/test_workflow.py
```

`docs/experiments/`와 기존 `.claude/tasks/<작업 ID>/`는 이식하지 않는다. 대상 프로젝트에는 빈 `.claude/tasks/index.md`만 만들고 `active.json`은 복사하지 않는다.

## 순서

1. 대상 프로젝트에서 별도 브랜치를 만든다.
2. 위 항목을 임시 디렉터리에 복사하고 기존 `.claude`·문서·CI와 차이를 확인한다.
3. 기존 지침과 Hook을 보존하면서 충돌을 수동 병합한다.
4. `.claude/checks.json`을 대상 프로젝트의 실제 명령으로 바꾼다.
5. 프로젝트 기술·도메인 규칙은 기존 문서 또는 별도 `.claude/rules/` 파일에 추가한다.
6. 회귀 테스트와 작은 샘플 작업으로 실패 차단·재검사·리뷰·재개를 확인한다.

## 기본 검사 예시

Next.js 프로젝트는 lint, TypeScript, 테스트, production build를 연결한다. Spring Boot 프로젝트는 Gradle 또는 Maven Wrapper의 테스트와 패키징을 연결한다. MySQL·PostgreSQL 검증은 Spring 통합 테스트와 Testcontainers에 포함한다. GitHub Actions·EC2 배포는 워크플로 문법, 배포 전 검사, 배포 후 헬스 체크와 롤백 절차를 프로젝트 CI에서 관리한다.

```json
{
  "checks": [
    {"argv": ["npm", "--prefix", "frontend", "run", "lint"], "timeout_seconds": 180},
    {"argv": ["npm", "--prefix", "frontend", "run", "typecheck"], "timeout_seconds": 180},
    {"argv": ["npm", "--prefix", "frontend", "test", "--", "--run"], "timeout_seconds": 300},
    {"argv": ["npm", "--prefix", "frontend", "run", "build"], "timeout_seconds": 600},
    {"argv": ["backend/gradlew", "test"], "timeout_seconds": 900}
  ]
}
```

프론트엔드와 백엔드가 하위 디렉터리에 있다면 해당 디렉터리에서 실행하는 작은 래퍼 스크립트를 프로젝트가 제공하거나, 프로젝트 루트에서 동작하는 Gradle·npm 명령으로 조정한다. 검사 명령은 셸을 거치지 않으므로 `cd`, `&&`, 파이프를 직접 넣지 않는다.

## 이식 완료 조건

- Claude Code에서 SessionStart, PreToolUse, Stop Hook이 보인다.
- `python3 -m unittest discover -s tests -v`가 통과한다.
- `.claude/checks.json`의 모든 명령이 대상 프로젝트에서 실제로 실행된다.
- 명세 확정 전 코드 수정, 실패한 검사 뒤 완료, 검사 후 변경 뒤 완료가 차단된다.
- 프로젝트의 기존 CI·배포·보안 규칙이 사라지지 않았다.

여러 프로젝트에서 동일한 수동 작업이 반복되고 실제 유지 비용이 확인될 때만 설치·업데이트 자동화를 다시 도입한다.
