# 하네스 설치·진단 CLI

## 왜 필요한가

수동 이식은 `.claude/`와 검증 문서를 복사하고, 프로젝트 설정과 Hook을 직접 합친 뒤
실행 가능 여부를 사람이 확인해야 합니다. 프로젝트가 여러 개가 되면 복사된 코어의
버전이 달라지고, 수정된 파일을 업데이트가 덮어쓸 위험이 생깁니다.

`harness.py`는 이 과정을 다음 원칙으로 자동화합니다.

- manifest에 등록된 공통 코어만 관리합니다.
- 기존 파일과 로컬 수정 파일을 자동으로 덮어쓰지 않습니다.
- `harness.json`, `.claude/snapshot.json`, 작업 기록은 프로젝트 소유로 취급합니다.
- 설치 당시 SHA-256을 `.harness/installation.json`에 기록해 안전한 업데이트 여부를 판단합니다.
- Python 3.9 표준 라이브러리만 사용합니다.

## 사용법

하네스 저장소의 `harness.py`를 대상 프로젝트에서 실행합니다. `--project`를 생략하면
현재 디렉터리가 대상입니다.

```sh
python3 /path/to/harness-lab/harness.py init --dry-run
python3 /path/to/harness-lab/harness.py init
python3 /path/to/harness-lab/harness.py validate
python3 /path/to/harness-lab/harness.py doctor
```

다른 위치를 대상으로 지정할 때 전역 옵션은 명령 앞에 둡니다.

```sh
python3 harness.py --project /path/to/project init --dry-run
python3 harness.py --project /path/to/project --json doctor
```

### `init`

manifest의 관리 파일과 초기 프로젝트 설정을 설치합니다. 먼저 전체 계획을 계산하고,
충돌이 하나라도 있으면 어떤 파일도 쓰지 않습니다. 같은 릴리스와 같은 내용으로 다시
실행하면 모두 `SKIP`되어 멱등성을 유지합니다.

초기 설정 파일이 이미 있으면 내용을 비교하거나 덮어쓰지 않고 그대로 보존합니다.
새 프로젝트에서는 `package.json`, Gradle/Maven 파일, GitHub Actions 워크플로를 조사해
Next.js, Spring Boot, MySQL/PostgreSQL, GitHub Actions·EC2 프로필을 보수적으로 활성화한
`harness.json`을 생성합니다. 탐지 결과와 실제 구조가 다르면 경로와 활성 프로필을
프로젝트에 맞게 조정합니다. 스택을 확인할 수 없으면 빈 프로필로 생성되어 doctor가
검사 명령 부재를 알려줍니다.

### `validate`

`harness.json`과 선택한 profile을 읽어 스키마, 경로, 중복 ID, check 정의를 한 번에
검사합니다. 발견한 설정 오류를 가능한 한 모두 출력하며 실패가 있으면 종료 코드 1을
반환합니다.

### `doctor`

다음 실행 환경을 진단합니다.

- Python 3.9 이상
- Git 설치와 Git 작업 트리
- Claude Code 설치
- `SessionStart`, `PreToolUse`, `Stop` Hook과 `workflow.py` 연결
- `harness.json`과 profile 유효성
- 각 check의 실행 디렉터리와 실행 파일 존재 여부
- 설치 기록의 존재와 형식

필수 check의 명령이 없으면 `FAIL`, 선택 check의 명령이 없으면 `WARN`입니다. doctor는
check를 직접 실행하지 않으므로 애플리케이션 상태를 바꾸지 않습니다.

### `upgrade`

새 하네스 버전의 저장소에서 대상 프로젝트를 지정해 실행합니다.

```sh
python3 /path/to/new-harness/harness.py --project /path/to/project upgrade --dry-run
python3 /path/to/new-harness/harness.py --project /path/to/project upgrade
```

대상 파일이 설치 당시 내용과 같을 때만 새 버전으로 교체합니다. 설치 후 수정된 관리
파일이 하나라도 있으면 전체 업데이트를 중단하고 충돌 경로를 보여줍니다. 새 manifest에서
빠진 과거 파일은 자동 삭제하지 않고 경고만 출력합니다. 프로젝트 설정과 작업 기록은
upgrade 대상이 아닙니다.

## 출력과 자동화

`--json`을 명령 앞에 지정하면 CI나 별도 도구가 읽을 수 있는 JSON을 출력합니다.
`init`과 `upgrade`는 `--dry-run`으로 파일을 쓰지 않고 동일한 충돌 검사를 수행합니다.

종료 코드는 성공 0, 충돌·설정 오류·필수 진단 실패 1, 잘못된 CLI 사용 2입니다.

## 현재 범위

설정 병합이나 로컬 수정 파일의 자동 병합은 수행하지 않습니다. 충돌 파일은 사용자가
차이를 검토한 뒤 프로젝트 변경을 별도 파일로 옮기거나 새 코어 변경을 직접 반영해야
합니다. 실제 check 실행과 완료 판정은 workflow가 담당하고, doctor는 실행 전 준비 상태만
검사합니다.
