"""Safe, manifest-based installation and upgrade operations.

The installer deliberately treats project configuration and task records as
project-owned data.  Only files listed by ``harness/manifest.json`` are managed
and an upgrade replaces a managed file only when it still matches the digest
recorded at installation time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


STATE_PATH = Path(".harness/installation.json")
MANIFEST_PATH = Path("harness/manifest.json")


class InstallError(RuntimeError):
    """Raised when an installation cannot be performed safely."""


@dataclass(frozen=True)
class FileAction:
    action: str
    path: str
    reason: str


@dataclass
class InstallResult:
    command: str
    dry_run: bool
    release: str
    actions: List[FileAction] = field(default_factory=list)
    conflicts: List[FileAction] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any(item.action in {"create", "update"} for item in self.actions)

    @property
    def ok(self) -> bool:
        return not self.conflicts


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise InstallError("필요한 파일이 없습니다: {}".format(path)) from error
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError("JSON을 읽을 수 없습니다: {} ({})".format(path, error)) from error


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(path.parent), delete=False
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(str(temporary), str(path))


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise InstallError("manifest에 안전하지 않은 경로가 있습니다: {}".format(value))
    return path


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve())
        return True
    except ValueError:
        return False


def load_manifest(source_root: Path) -> Tuple[str, List[Path]]:
    """Load and expand the source manifest without following symlinks."""
    source_root = source_root.resolve()
    raw = _read_json(source_root / MANIFEST_PATH)
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise InstallError("지원하지 않는 설치 manifest 형식입니다.")
    release = raw.get("release")
    includes = raw.get("include")
    if not isinstance(release, str) or not release.strip():
        raise InstallError("manifest.release는 비어 있지 않은 문자열이어야 합니다.")
    if not isinstance(includes, list) or not includes:
        raise InstallError("manifest.include는 비어 있지 않은 배열이어야 합니다.")

    files: Dict[str, Path] = {}
    for entry in includes:
        if isinstance(entry, str):
            pattern, required = entry, True
        elif isinstance(entry, dict):
            pattern, required = entry.get("pattern"), entry.get("required", True)
        else:
            raise InstallError("manifest.include 항목 형식이 잘못됐습니다.")
        if not isinstance(pattern, str) or not isinstance(required, bool):
            raise InstallError("manifest pattern/required 형식이 잘못됐습니다.")
        _safe_relative(pattern)
        matches = sorted(source_root.glob(pattern))
        regular = []
        for path in matches:
            if not _inside(source_root, path):
                raise InstallError("관리 파일이 배포 원본 밖을 가리킵니다: {}".format(path))
            if path.is_symlink():
                raise InstallError("관리 파일은 심볼릭 링크일 수 없습니다: {}".format(path))
            if path.is_file():
                rel = path.relative_to(source_root)
                files[rel.as_posix()] = rel
                regular.append(path)
        if required and not regular:
            raise InstallError("manifest pattern과 일치하는 파일이 없습니다: {}".format(pattern))
    return release, [files[key] for key in sorted(files)]


def _load_state(project_root: Path, required: bool = False) -> Optional[Dict[str, Any]]:
    path = project_root / STATE_PATH
    if not path.exists():
        if required:
            raise InstallError("설치 기록이 없습니다. 먼저 init을 실행하세요: {}".format(path))
        return None
    raw = _read_json(path)
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise InstallError("지원하지 않는 설치 기록 형식입니다: {}".format(path))
    if not isinstance(raw.get("files"), dict):
        raise InstallError("설치 기록의 files가 올바르지 않습니다: {}".format(path))
    return raw


def _target(project_root: Path, rel: Path) -> Path:
    target = project_root / rel
    if not _inside(project_root, target):
        raise InstallError("대상 프로젝트 밖의 경로는 쓸 수 없습니다: {}".format(rel))
    return target


def _copy_atomic(source: Path, target: Path) -> None:
    if target.exists() and target.is_symlink():
        raise InstallError("심볼릭 링크 대상은 덮어쓸 수 없습니다: {}".format(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=str(target.parent), delete=False) as handle:
        temporary = Path(handle.name)
        with source.open("rb") as source_handle:
            shutil.copyfileobj(source_handle, handle)
    shutil.copymode(str(source), str(temporary))
    os.replace(str(temporary), str(target))


def _project_config(project_root: Path) -> bytes:
    """Build a conservative config from stack markers; users may edit it later."""
    name = re.sub(r"[^a-z0-9]+", "-", project_root.name.lower()).strip("-")
    if not name or not name[0].isalpha():
        name = "project-" + (name or "local")
    name = name[:64].rstrip("-")
    profiles = []
    source_paths: List[str] = []

    package_files = [project_root / "package.json", project_root / "frontend/package.json"]
    for package_file in package_files:
        if not package_file.is_file():
            continue
        try:
            package = json.loads(package_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        dependencies = {}
        for field_name in ("dependencies", "devDependencies"):
            value = package.get(field_name, {})
            if isinstance(value, dict):
                dependencies.update(value)
        if "next" in dependencies and "typescript" in dependencies:
            rel = package_file.parent.relative_to(project_root).as_posix() or "."
            manager = "pnpm" if (package_file.parent / "pnpm-lock.yaml").exists() else (
                "yarn" if (package_file.parent / "yarn.lock").exists() else "npm"
            )
            profiles.append({
                "id": "frontend-nextjs", "enabled": True,
                "options": {"path": rel, "package_manager": manager},
            })
            source_paths.append(rel)
            break

    for directory in (project_root, project_root / "backend"):
        gradle_markers = [directory / "build.gradle", directory / "build.gradle.kts"]
        maven_marker = directory / "pom.xml"
        markers = gradle_markers + [maven_marker]
        text = "\n".join(
            marker.read_text(encoding="utf-8", errors="ignore")
            for marker in markers if marker.is_file()
        )
        if "spring" in text.lower():
            uses_gradle = any(marker.is_file() for marker in gradle_markers)
            if uses_gradle:
                build_executable = "./gradlew" if (directory / "gradlew").is_file() else "gradle"
                test_task, package_task = "test", "build"
            else:
                build_executable = "./mvnw" if (directory / "mvnw").is_file() else "mvn"
                test_task, package_task = "test", "package"
            profiles.append({
                "id": "backend-spring", "enabled": True,
                "options": {
                    "path": directory.relative_to(project_root).as_posix() or ".",
                    "build_executable": build_executable,
                    "test_task": test_task,
                    "package_task": package_task,
                },
            })
            source_paths.append(directory.relative_to(project_root).as_posix() or ".")
            lowered = text.lower()
            database = "database-postgresql" if "postgresql" in lowered else (
                "database-mysql" if "mysql" in lowered else None
            )
            if database:
                profiles.append({"id": database, "enabled": True, "options": {}})
            break

    workflows = project_root / ".github/workflows"
    if workflows.is_dir():
        workflow_text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for pattern in ("*.yml", "*.yaml") for path in workflows.glob(pattern)
        ).lower()
        if any(token in workflow_text for token in ("ec2", "amazon-ec2", "ssh-action", "aws-actions")):
            profiles.append({
                "id": "devops-github-actions-ec2", "enabled": True,
                "options": {"workflow_path": ".github/workflows"},
            })

    if not source_paths:
        source_paths = ["."]
    project_type = "fullstack" if len({item["id"].split("-")[0] for item in profiles}) > 1 else (
        "application" if profiles else "tooling"
    )
    value = {
        "schema_version": 1,
        "project": {"name": name, "type": project_type, "source_paths": sorted(set(source_paths))},
        "profile_defaults": {
            "frontend": "frontend-nextjs",
            "backend": "backend-spring",
            "database": "database-postgresql",
            "devops": "devops-github-actions-ec2",
        },
        "profiles": profiles,
        "checks": [],
    }
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _seed_candidates(source_root: Path, project_root: Path) -> List[Tuple[Path, Optional[bytes]]]:
    """Return project-owned files that init may create but never upgrades."""
    candidates: List[Tuple[Path, Optional[bytes]]] = []
    candidates.append((Path("harness.json"), _project_config(project_root)))
    for rel in (Path(".claude/snapshot.json"),):
        source = source_root / rel
        if source.is_file() and not source.is_symlink():
            candidates.append((rel, None))
    candidates.append(
        (
            Path(".claude/tasks/index.md"),
            ("# 작업 목록\n\n상태는 workflow.py 명령으로 갱신합니다.\n").encode("utf-8"),
        )
    )
    return candidates


def _write_seed(source_root: Path, project_root: Path, rel: Path, content: Optional[bytes]) -> None:
    target = _target(project_root, rel)
    if content is None:
        _copy_atomic(source_root / rel, target)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=str(target.parent), delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    os.replace(str(temporary), str(target))


def _state_value(release: str, files: Mapping[str, str]) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "release": release,
        "files": dict(sorted(files.items())),
    }


def init_project(
    project_root: Path, source_root: Path, dry_run: bool = False
) -> InstallResult:
    """Install into an existing project without overwriting any existing file."""
    project_root = project_root.resolve()
    source_root = source_root.resolve()
    if not project_root.is_dir():
        raise InstallError("프로젝트 디렉터리가 없습니다: {}".format(project_root))
    release, managed = load_manifest(source_root)
    previous = _load_state(project_root)
    result = InstallResult(command="init", dry_run=dry_run, release=release)
    if previous and previous.get("release") != release:
        result.conflicts.append(
            FileAction("conflict", STATE_PATH.as_posix(), "다른 릴리스가 설치됨; upgrade 사용")
        )
        return result

    digests: Dict[str, str] = {}
    pending: List[Tuple[Path, Path]] = []
    for rel in managed:
        source = source_root / rel
        source_hash = sha256_file(source)
        digests[rel.as_posix()] = source_hash
        target = _target(project_root, rel)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            result.conflicts.append(FileAction("conflict", rel.as_posix(), "일반 파일이 아님"))
        elif not target.exists():
            result.actions.append(FileAction("create", rel.as_posix(), "관리 파일 설치"))
            pending.append((source, target))
        elif sha256_file(target) == source_hash:
            result.actions.append(FileAction("skip", rel.as_posix(), "이미 같은 내용"))
        else:
            result.conflicts.append(FileAction("conflict", rel.as_posix(), "기존 파일 보존"))

    seed_pending: List[Tuple[Path, Optional[bytes]]] = []
    for rel, content in _seed_candidates(source_root, project_root):
        target = _target(project_root, rel)
        if target.exists() or target.is_symlink():
            result.actions.append(FileAction("skip", rel.as_posix(), "프로젝트 소유 파일 보존"))
        else:
            result.actions.append(FileAction("create", rel.as_posix(), "프로젝트 초기 파일"))
            seed_pending.append((rel, content))

    if result.conflicts or dry_run:
        return result
    for source, target in pending:
        _copy_atomic(source, target)
    for rel, content in seed_pending:
        _write_seed(source_root, project_root, rel, content)
    _atomic_json(project_root / STATE_PATH, _state_value(release, digests))
    return result


def upgrade_project(
    project_root: Path, source_root: Path, dry_run: bool = False
) -> InstallResult:
    """Upgrade unchanged managed files and leave every local edit untouched."""
    project_root = project_root.resolve()
    source_root = source_root.resolve()
    release, managed = load_manifest(source_root)
    previous = _load_state(project_root, required=True)
    assert previous is not None
    old_files = previous["files"]
    result = InstallResult(command="upgrade", dry_run=dry_run, release=release)
    new_digests: Dict[str, str] = {}
    pending: List[Tuple[Path, Path]] = []

    current_paths = {rel.as_posix() for rel in managed}
    for old_path in sorted(set(old_files) - current_paths):
        result.warnings.append("이전 관리 파일을 자동 삭제하지 않음: {}".format(old_path))

    for rel in managed:
        key = rel.as_posix()
        source = source_root / rel
        source_hash = sha256_file(source)
        new_digests[key] = source_hash
        target = _target(project_root, rel)
        old_hash = old_files.get(key)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            result.conflicts.append(FileAction("conflict", key, "일반 파일이 아님"))
            continue
        if not target.exists():
            result.actions.append(FileAction("create", key, "새 관리 파일 또는 삭제된 파일 복구"))
            pending.append((source, target))
            continue
        target_hash = sha256_file(target)
        if target_hash == source_hash:
            result.actions.append(FileAction("skip", key, "이미 최신 내용"))
        elif old_hash is not None and target_hash == old_hash:
            result.actions.append(FileAction("update", key, "로컬 수정 없음"))
            pending.append((source, target))
        else:
            result.conflicts.append(FileAction("conflict", key, "설치 후 변경된 파일 보존"))

    if result.conflicts or dry_run:
        return result
    for source, target in pending:
        _copy_atomic(source, target)
    _atomic_json(project_root / STATE_PATH, _state_value(release, new_digests))
    return result


def read_installation(project_root: Path) -> Optional[Mapping[str, Any]]:
    """Public read-only access used by doctor."""
    return _load_state(project_root.resolve())
