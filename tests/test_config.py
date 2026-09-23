"""Tests for portable harness configuration and profile expansion."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from harness.config import ConfigError, load_config, validate_config
from harness.profiles import load_profile, validate_profile


ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="harness-config-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        shutil.copytree(ROOT / "profiles", self.project / "profiles")

    def write_config(self, value):
        (self.project / "harness.json").write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )

    def base_config(self):
        return {
            "schema_version": 1,
            "project": {
                "name": "sample-project",
                "type": "fullstack",
                "source_paths": ["frontend", "backend"],
            },
            "profile_defaults": {
                "frontend": "frontend-nextjs",
                "backend": "backend-spring",
                "database": "database-postgresql",
                "devops": "devops-github-actions-ec2",
            },
            "profiles": [],
            "checks": [],
        }

    def test_repository_config_loads_without_enabling_app_profiles(self):
        config = load_config(ROOT)
        self.assertEqual(config["project"]["name"], "harness-lab")
        self.assertEqual([check["id"] for check in config["checks"]], ["harness-unit"])
        self.assertEqual(config["profiles"], [])

    def test_profile_catalog_is_valid(self):
        expected = {
            "frontend-nextjs",
            "backend-spring",
            "database-mysql",
            "database-postgresql",
            "devops-github-actions-ec2",
        }
        loaded = {load_profile(path)["id"] for path in (ROOT / "profiles").glob("*.json")}
        self.assertEqual(loaded, expected)

    def test_enabled_profile_expands_options_and_checks(self):
        (self.project / "web").mkdir()
        config = self.base_config()
        config["profiles"] = [{
            "id": "frontend-nextjs",
            "enabled": True,
            "options": {"path": "web", "package_manager": "pnpm"},
        }]
        self.write_config(config)

        resolved = load_config(self.project)

        self.assertEqual(resolved["profiles"][0]["options"]["path"], "web")
        self.assertEqual(len(resolved["checks"]), 4)
        self.assertEqual(resolved["checks"][0]["cwd"], "web")
        self.assertEqual(resolved["checks"][0]["argv"], ["pnpm", "run", "lint"])

    def test_disabled_profile_does_not_require_source_directory(self):
        config = self.base_config()
        config["profiles"] = [{
            "id": "backend-spring",
            "enabled": False,
            "options": {"path": "missing-backend"},
        }]
        config["checks"] = [{
            "id": "project-test", "kind": "unit", "cwd": ".",
            "argv": ["python3", "-V"], "required": True, "timeout_seconds": 10,
        }]
        self.write_config(config)

        resolved = load_config(self.project)

        self.assertEqual(resolved["profiles"], [])
        self.assertEqual([check["id"] for check in resolved["checks"]], ["project-test"])

    def test_at_least_one_required_check_is_needed(self):
        config = self.base_config()
        config["checks"] = [{
            "id": "optional-only", "kind": "lint", "cwd": ".",
            "argv": ["optional-tool"], "required": False, "timeout_seconds": 10,
        }]
        self.write_config(config)

        with self.assertRaises(ConfigError) as raised:
            load_config(self.project)

        self.assertIn("필수 검사", str(raised.exception))

    def test_validation_collects_clear_errors(self):
        invalid = {
            "schema_version": 9,
            "project": {
                "name": "Bad Name",
                "type": "fullstack",
                "source_paths": ["../outside"],
            },
            "profiles": [{"id": "frontend-nextjs", "enabled": "yes"}],
            "checks": [{
                "id": "Bad Check",
                "kind": "unknown",
                "cwd": "/tmp",
                "argv": [],
                "required": "yes",
                "timeout_seconds": 0,
            }],
            "typo": True,
        }

        with self.assertRaises(ConfigError) as raised:
            validate_config(invalid)

        message = str(raised.exception)
        self.assertGreaterEqual(len(raised.exception.errors), 8)
        self.assertIn("알 수 없는 필드 'typo'", message)
        self.assertIn("schema_version", message)
        self.assertIn("프로젝트 밖", message)
        self.assertIn("timeout_seconds", message)

    def test_unknown_profile_reports_file_path(self):
        config = self.base_config()
        config["profiles"] = [{"id": "frontend-unknown", "enabled": True}]
        self.write_config(config)

        with self.assertRaises(ConfigError) as raised:
            load_config(self.project)

        self.assertIn("frontend-unknown.json", str(raised.exception))

    def test_unknown_default_profile_is_rejected_even_when_not_enabled(self):
        config = self.base_config()
        config["profile_defaults"]["database"] = "database-oracle"
        self.write_config(config)

        with self.assertRaises(ConfigError) as raised:
            load_config(self.project)

        self.assertIn("database-oracle.json", str(raised.exception))

    def test_duplicate_check_id_across_profile_and_project_is_rejected(self):
        (self.project / "frontend").mkdir()
        config = self.base_config()
        config["profiles"] = [{"id": "frontend-nextjs", "enabled": True}]
        config["checks"] = [{
            "id": "frontend-lint",
            "kind": "lint",
            "cwd": ".",
            "argv": ["python3", "-V"],
            "required": True,
            "timeout_seconds": 10,
        }]
        self.write_config(config)

        with self.assertRaises(ConfigError) as raised:
            load_config(self.project)

        self.assertIn("중복된 식별자 'frontend-lint'", str(raised.exception))

    def test_only_one_profile_per_category_can_be_active(self):
        config = self.base_config()
        config["profiles"] = [
            {"id": "database-mysql", "enabled": True, "options": {}},
            {"id": "database-postgresql", "enabled": True, "options": {}},
        ]
        self.write_config(config)

        with self.assertRaises(ConfigError) as raised:
            load_config(self.project)

        self.assertIn("동시에 활성화할 수 없습니다", str(raised.exception))

    def test_invalid_profile_token_is_rejected(self):
        profile = {
            "schema_version": 1,
            "id": "bad-profile",
            "category": "frontend",
            "description": "잘못된 토큰 시험",
            "options": {},
            "checks": [{
                "id": "bad-check",
                "kind": "unit",
                "cwd": "${unknown}",
                "argv": ["python3", "-V"],
                "required": True,
                "timeout_seconds": 10,
            }],
            "rules": [],
        }

        with self.assertRaises(ConfigError) as raised:
            validate_profile(profile)

        self.assertIn("정의되지 않은 옵션 '${unknown}'", str(raised.exception))

    def test_local_validation_rejects_missing_check_directory(self):
        config = self.base_config()
        config["checks"] = [{
            "id": "missing-cwd",
            "kind": "unit",
            "cwd": "missing",
            "argv": ["python3", "-V"],
            "required": True,
            "timeout_seconds": 10,
        }]

        with self.assertRaises(ConfigError) as raised:
            validate_config(config, project_root=self.project)

        self.assertIn("디렉터리가 존재하지 않습니다", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
