"""Schema validation primitives for harness and profile JSON documents."""

from pathlib import Path, PurePosixPath
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


SCHEMA_VERSION = 1
IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
CHECK_KINDS = {
    "static",
    "lint",
    "unit",
    "integration",
    "e2e",
    "build",
    "security",
    "performance",
    "deployment",
}


class ConfigError(ValueError):
    """Raised with every configuration problem found in one validation pass."""

    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        message = "\n".join("- " + error for error in self.errors)
        super().__init__("하네스 설정이 올바르지 않습니다:\n" + message)


def is_plain_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def reject_unknown(mapping: Mapping[str, Any], allowed: Sequence[str], path: str,
                   errors: List[str]) -> None:
    for key in mapping:
        if key not in allowed:
            errors.append("{}: 알 수 없는 필드 '{}'".format(path, key))


def require_version(document: Mapping[str, Any], path: str, errors: List[str]) -> None:
    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        errors.append(
            "{}.schema_version: {}만 지원합니다 (입력값: {!r})".format(
                path, SCHEMA_VERSION, version
            )
        )


def validate_identifier(value: Any, path: str, errors: List[str]) -> None:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        errors.append("{}: 소문자 영문으로 시작하는 kebab-case 식별자여야 합니다".format(path))


def validate_relative_path(value: Any, path: str, errors: List[str]) -> None:
    if not isinstance(value, str) or not value:
        errors.append("{}: 비어 있지 않은 상대 경로 문자열이어야 합니다".format(path))
        return
    candidate = PurePosixPath(value.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        errors.append("{}: 프로젝트 밖을 가리킬 수 없습니다 ({!r})".format(path, value))


def validate_string_list(value: Any, path: str, errors: List[str],
                         *, allow_empty: bool = True) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        errors.append("{}: 비어 있지 않은 문자열의 배열이어야 합니다".format(path))
    elif not allow_empty and not value:
        errors.append("{}: 항목이 하나 이상 필요합니다".format(path))


def validate_check(check: Any, path: str, errors: List[str],
                   *, allow_templates: bool = False,
                   project_root: Optional[Path] = None) -> None:
    if not is_plain_mapping(check):
        errors.append("{}: 객체여야 합니다".format(path))
        return
    allowed = (
        "id", "kind", "cwd", "argv", "required", "timeout_seconds",
        "description", "artifacts", "when",
    )
    reject_unknown(check, allowed, path, errors)
    validate_identifier(check.get("id"), path + ".id", errors)
    kind = check.get("kind")
    if kind not in CHECK_KINDS:
        errors.append(
            "{}.kind: 다음 값 중 하나여야 합니다: {}".format(
                path, ", ".join(sorted(CHECK_KINDS))
            )
        )
    cwd = check.get("cwd")
    if allow_templates and isinstance(cwd, str) and "${" in cwd:
        pass
    else:
        validate_relative_path(cwd, path + ".cwd", errors)
    argv = check.get("argv")
    validate_string_list(argv, path + ".argv", errors, allow_empty=False)
    required = check.get("required")
    if not isinstance(required, bool):
        errors.append("{}.required: true 또는 false여야 합니다".format(path))
    timeout = check.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
        errors.append("{}.timeout_seconds: 1~3600 사이의 정수여야 합니다".format(path))
    if "description" in check and (
        not isinstance(check["description"], str) or not check["description"].strip()
    ):
        errors.append("{}.description: 비어 있지 않은 문자열이어야 합니다".format(path))
    if "artifacts" in check:
        artifacts = check["artifacts"]
        validate_string_list(artifacts, path + ".artifacts", errors)
        if isinstance(artifacts, list):
            for index, artifact in enumerate(artifacts):
                if allow_templates and isinstance(artifact, str) and "${" in artifact:
                    continue
                validate_relative_path(artifact, "{}.artifacts[{}]".format(path, index), errors)
    if "when" in check:
        validate_condition(check["when"], path + ".when", errors, allow_templates)
    if project_root is not None and isinstance(cwd, str) and "${" not in cwd:
        target = project_root / cwd
        if not target.is_dir():
            errors.append("{}.cwd: 디렉터리가 존재하지 않습니다 ({})".format(path, target))


def validate_condition(value: Any, path: str, errors: List[str],
                       allow_templates: bool) -> None:
    if not is_plain_mapping(value):
        errors.append("{}: 객체여야 합니다".format(path))
        return
    reject_unknown(value, ("files_any", "files_all"), path, errors)
    if not value:
        errors.append("{}: files_any 또는 files_all이 필요합니다".format(path))
    for key in ("files_any", "files_all"):
        if key not in value:
            continue
        patterns = value[key]
        validate_string_list(patterns, path + "." + key, errors, allow_empty=False)
        if isinstance(patterns, list):
            for index, pattern in enumerate(patterns):
                if allow_templates and isinstance(pattern, str) and "${" in pattern:
                    continue
                validate_relative_path(
                    pattern, "{}.{}[{}]".format(path, key, index), errors
                )


def validate_unique_ids(items: Sequence[Any], path: str, errors: List[str]) -> None:
    seen = set()
    for index, item in enumerate(items):
        if not is_plain_mapping(item) or not isinstance(item.get("id"), str):
            continue
        identifier = item["id"]
        if identifier in seen:
            errors.append("{}[{}].id: 중복된 식별자 '{}'".format(path, index, identifier))
        seen.add(identifier)


def raise_if_errors(errors: List[str]) -> None:
    if errors:
        raise ConfigError(errors)


def copied_mapping(value: Mapping[str, Any]) -> Dict[str, Any]:
    """Return JSON-compatible data detached from a caller-owned mapping."""
    import json

    return json.loads(json.dumps(value, ensure_ascii=False))
