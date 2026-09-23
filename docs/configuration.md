# 범용 하네스 설정

## 계층

설정은 다음 세 계층으로 나뉩니다.

1. `harness/`는 JSON을 검증하고 프로필을 해석하는 공통 코드입니다. 특정 기술 명령을 알지 못합니다.
2. `profiles/`는 기술별 기본 검사와 검토 규칙을 선언합니다.
3. 루트 `harness.json`은 프로젝트가 사용할 프로필, 경로, 추가 검사를 선택합니다.

따라서 새로운 기술 스택을 지원할 때 공통 Python 코드를 수정하지 않고 프로필 JSON을 추가할 수 있습니다.

## 제공 프로필

| 프로필 ID | 기본 대상 | 검사 |
| --- | --- | --- |
| `frontend-nextjs` | React, TypeScript, Next.js | lint, typecheck, test, production build |
| `backend-spring` | Gradle 또는 Maven 기반 Spring Boot | test, package/build |
| `database-mysql` | MySQL | Testcontainers, 마이그레이션, 인덱스·잠금 검토 규칙 |
| `database-postgresql` | PostgreSQL | Testcontainers, 마이그레이션, 실행 계획·잠금 검토 규칙 |
| `devops-github-actions-ec2` | GitHub Actions, AWS EC2 | 선택적 actionlint와 배포·롤백 규칙 |

데이터베이스 프로필은 MySQL과 PostgreSQL 중 실제 운영 엔진 하나를 선택합니다. 두 프로필은 프로젝트의 테스트 명령을 추측하지 않고 검토 규칙을 제공합니다. 실제 Testcontainers·Flyway 검사는 Spring 프로젝트의 통합 테스트에 구현한 뒤 `backend-test`로 실행합니다.

## `harness.json`

`profile_defaults`는 설치 도구가 자동 감지할 수 없을 때 제안할 기본값입니다. `profiles`는 실제 활성화할 프로필입니다. 이 저장소는 하네스 자체를 개발하므로 애플리케이션 프로필을 `enabled: false`로 보관합니다.

```json
{
  "schema_version": 1,
  "project": {
    "name": "sample-service",
    "type": "fullstack",
    "source_paths": ["frontend", "backend"]
  },
  "profile_defaults": {
    "frontend": "frontend-nextjs",
    "backend": "backend-spring",
    "database": "database-postgresql",
    "devops": "devops-github-actions-ec2"
  },
  "profiles": [
    {
      "id": "frontend-nextjs",
      "enabled": true,
      "options": {"path": "frontend", "package_manager": "npm"}
    },
    {
      "id": "backend-spring",
      "enabled": true,
      "options": {
        "path": "backend",
        "build_executable": "./gradlew",
        "test_task": "test",
        "package_task": "build"
      }
    },
    {"id": "database-postgresql", "enabled": true, "options": {}},
    {"id": "devops-github-actions-ec2", "enabled": true, "options": {}}
  ],
  "checks": []
}
```

`checks`에는 프로필에 없는 프로젝트 전용 검사를 추가합니다.

```json
{
  "id": "api-contract",
  "kind": "integration",
  "cwd": "backend",
  "argv": ["./gradlew", "contractTest"],
  "required": true,
  "timeout_seconds": 600,
  "description": "외부 API 계약 검사"
}
```

- `id`: 프로젝트 전체에서 중복되지 않는 kebab-case 식별자
- `kind`: `static`, `lint`, `unit`, `integration`, `e2e`, `build`, `security`, `performance`, `deployment` 중 하나
- `cwd`: 프로젝트 루트 기준 상대 경로
- `argv`: 셸을 거치지 않고 실행할 명령과 인수 배열
- `required`: 완료에 필수인 검사 여부
- `timeout_seconds`: 1~3600초
- `artifacts`: 선택적 결과 파일 상대 경로 배열
- `when`: 선택적 `files_any` 또는 `files_all` 조건

경로에는 절대 경로와 `..`를 사용할 수 없습니다. 같은 검사 ID를 프로필과 프로젝트 설정에 동시에 선언할 수도 없습니다. 명령에 토큰이나 암호를 기록하지 않습니다. 프로필과 프로젝트 검사를 합친 결과에는 `required: true`인 검사가 하나 이상 있어야 합니다. 선택 검사만 있는 프로젝트는 완료를 판정할 수 없습니다.

## Python API

Python 3.9 표준 라이브러리만 사용합니다.

```python
from pathlib import Path
from harness.config import ConfigError, load_config, validate_config

try:
    resolved = load_config(Path.cwd())
    for check in resolved["checks"]:
        print(check["id"], check["argv"])
except ConfigError as error:
    for problem in error.errors:
        print(problem)
```

`validate_config(data)`는 JSON 구조만 검사합니다. `validate_config(data, project_root=Path.cwd())`는 각 검사의 `cwd`가 실제 디렉터리인지도 검사합니다. 성공하면 호출자가 준 객체와 분리된 정규화 사본을 반환하고, 실패하면 발견한 문제를 `ConfigError.errors`에 모아서 반환합니다.

`load_config(project_root)`는 다음 순서로 처리합니다.

1. `harness.json`의 구조와 버전을 검사합니다.
2. 활성 프로필 JSON을 읽고 옵션 기본값을 채웁니다.
3. `${option}`을 안전한 문자열 치환으로 해석합니다.
4. 프로필 검사와 프로젝트 검사를 합칩니다.
5. 중복 ID, 경로 이탈, 존재하지 않는 실행 디렉터리를 거부합니다.

`workflow.py verify`는 `harness.json`이 있으면 이 최종 검사 목록을 실행합니다. 각 검사는 선언한 `cwd`에서 실행되며 `when.files_any`·`when.files_all`이 맞지 않으면 실행하지 않고 사유를 증거에 남깁니다. `required: false`인 검사는 도구가 설치되지 않았으면 건너뛰며, 실행 가능한 경우 결과를 기록하되 실패가 전체 필수 검사 통과를 막지는 않습니다. 필수 검사가 실패하면 뒤 검사를 실행하지 않고 건너뛴 사유를 기록합니다.

아직 `harness.json`을 설치하지 않은 기존 프로젝트에서는 `.claude/checks.json`을 읽습니다. 이 호환 경로의 모든 검사는 프로젝트 루트에서 실행하는 필수 검사로 처리됩니다.

검사는 `argv` 배열로만 표현하므로 파이프, 리다이렉션, `&&` 같은 셸 문법을 자동 해석하지 않습니다. 운영체제별로 다른 명령이 필요하면 기존 프로필을 억지로 확장하지 않고 별도 프로필 또는 프로젝트 전용 검사를 선언합니다.
