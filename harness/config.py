"""Read, validate and resolve the portable harness configuration."""

from pathlib import Path
import json
from typing import Any, Dict, List, Mapping, Optional

from .profiles import load_profile, load_selected_profiles
from .schema import (
    ConfigError,
    copied_mapping,
    is_plain_mapping,
    raise_if_errors,
    reject_unknown,
    require_version,
    validate_check,
    validate_identifier,
    validate_relative_path,
    validate_unique_ids,
)


TOP_LEVEL_FIELDS = (
    "schema_version",
    "project",
    "profile_defaults",
    "profiles",
    "checks",
)
PROFILE_CATEGORIES = ("frontend", "backend", "database", "devops")


def validate_config(data: Any, project_root: Optional[Path] = None) -> Dict[str, Any]:
    """Validate raw config and return a detached normalized mapping.

    ``project_root`` enables local cwd existence checks. Omit it when validating a
    template before its source directories have been created.
    """
    errors: List[str] = []
    if not is_plain_mapping(data):
        raise ConfigError(("harness.json: 최상위 값은 객체여야 합니다",))
    reject_unknown(data, TOP_LEVEL_FIELDS, "harness.json", errors)
    require_version(data, "harness.json", errors)
    _validate_project(data.get("project"), errors)
    _validate_profile_defaults(data.get("profile_defaults"), errors)
    _validate_profile_selections(data.get("profiles"), errors)
    checks = data.get("checks")
    if not isinstance(checks, list):
        errors.append("harness.json.checks: 배열이어야 합니다")
        checks = []
    else:
        for index, check in enumerate(checks):
            validate_check(check, "harness.json.checks[{}]".format(index), errors,
                           project_root=project_root)
        validate_unique_ids(checks, "harness.json.checks", errors)
    raise_if_errors(errors)
    normalized = copied_mapping(data)
    normalized.setdefault("profile_defaults", {})
    normalized.setdefault("profiles", [])
    normalized.setdefault("checks", [])
    return normalized


def load_config(project_root: Path, config_path: Optional[Path] = None,
                profiles_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Load harness.json and return profile-expanded configuration."""
    project_root = Path(project_root).resolve()
    config_path = Path(config_path) if config_path else project_root / "harness.json"
    profiles_dir = Path(profiles_dir) if profiles_dir else project_root / "profiles"
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(("설정 파일을 찾을 수 없습니다: {}".format(config_path),)) from error
    except json.JSONDecodeError as error:
        raise ConfigError((
            "{}:{}:{}: JSON 문법 오류: {}".format(
                config_path, error.lineno, error.colno, error.msg
            ),
        )) from error
    config = validate_config(raw)
    _validate_default_profiles(config["profile_defaults"], profiles_dir)
    selections = config["profiles"]
    resolved_profiles = load_selected_profiles(selections, profiles_dir)
    merged_checks = []
    for profile in resolved_profiles:
        merged_checks.extend(profile["checks"])
    merged_checks.extend(config["checks"])
    errors: List[str] = []
    validate_unique_ids(merged_checks, "resolved.checks", errors)
    for index, check in enumerate(merged_checks):
        validate_check(check, "resolved.checks[{}]".format(index), errors,
                       project_root=project_root)
    if not any(check.get("required") is True for check in merged_checks):
        errors.append("resolved.checks: 완료를 판정할 필수 검사(required: true)가 하나 이상 필요합니다")
    raise_if_errors(errors)
    resolved = copied_mapping(config)
    resolved["profiles"] = resolved_profiles
    resolved["checks"] = merged_checks
    return resolved


def _validate_default_profiles(defaults: Mapping[str, str], profiles_dir: Path) -> None:
    errors = []
    for category, identifier in defaults.items():
        try:
            profile = load_profile(profiles_dir / (identifier + ".json"))
        except ConfigError as error:
            errors.extend(error.errors)
            continue
        if profile["id"] != identifier:
            errors.append(
                "profile_defaults.{}: 파일의 id '{}'가 '{}'와 다릅니다".format(
                    category, profile["id"], identifier
                )
            )
        if profile["category"] != category:
            errors.append(
                "profile_defaults.{}: '{}'는 {} 프로필입니다".format(
                    category, identifier, profile["category"]
                )
            )
    raise_if_errors(errors)


def _validate_project(value: Any, errors: List[str]) -> None:
    if not is_plain_mapping(value):
        errors.append("harness.json.project: 객체여야 합니다")
        return
    reject_unknown(value, ("name", "type", "source_paths"), "harness.json.project", errors)
    validate_identifier(value.get("name"), "harness.json.project.name", errors)
    validate_identifier(value.get("type"), "harness.json.project.type", errors)
    paths = value.get("source_paths")
    if not isinstance(paths, list) or not paths:
        errors.append("harness.json.project.source_paths: 상대 경로가 하나 이상 필요합니다")
    else:
        for index, path in enumerate(paths):
            validate_relative_path(path, "harness.json.project.source_paths[{}]".format(index), errors)


def _validate_profile_defaults(value: Any, errors: List[str]) -> None:
    if value is None:
        return
    if not is_plain_mapping(value):
        errors.append("harness.json.profile_defaults: 객체여야 합니다")
        return
    reject_unknown(value, PROFILE_CATEGORIES, "harness.json.profile_defaults", errors)
    for category, identifier in value.items():
        validate_identifier(identifier, "harness.json.profile_defaults." + category, errors)


def _validate_profile_selections(value: Any, errors: List[str]) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        errors.append("harness.json.profiles: 배열이어야 합니다")
        return
    validate_unique_ids(value, "harness.json.profiles", errors)
    for index, selection in enumerate(value):
        path = "harness.json.profiles[{}]".format(index)
        if not is_plain_mapping(selection):
            errors.append(path + ": 객체여야 합니다")
            continue
        reject_unknown(selection, ("id", "enabled", "options"), path, errors)
        validate_identifier(selection.get("id"), path + ".id", errors)
        if "enabled" in selection and not isinstance(selection["enabled"], bool):
            errors.append(path + ".enabled: true 또는 false여야 합니다")
        if "options" in selection and not is_plain_mapping(selection["options"]):
            errors.append(path + ".options: 객체여야 합니다")
