from __future__ import annotations

import importlib
from pathlib import Path

from scripts.check_project_skeleton import collect_project_skeleton_errors

ROOT = Path(__file__).resolve().parents[1]


def test_app_package_can_be_imported() -> None:
    assert importlib.import_module("app") is not None


def test_required_project_skeleton_directories_exist() -> None:
    required_directories = [
        "app",
        "app/alerting",
        "app/core",
        "app/exchange",
        "app/exchange/binance",
        "app/storage/mysql",
        "app/storage/redis",
        "app/market_data",
        "app/scheduler",
        "app/monitoring",
        "configs",
        "migrations",
        "scripts",
        "tests",
        "logs",
        "docs/implementation",
    ]

    missing = [path for path in required_directories if not (ROOT / path).is_dir()]

    assert missing == []


def test_required_project_skeleton_files_exist() -> None:
    required_files = [
        "pyproject.toml",
        ".env.example",
        ".gitignore",
        "README.md",
        "AGENTS.md",
        "alembic.ini",
        "scripts/check_project_skeleton.py",
        "tests/test_project_skeleton.py",
    ]

    missing = [path for path in required_files if not (ROOT / path).is_file()]

    assert missing == []


def test_project_skeleton_check_script_can_be_imported() -> None:
    assert importlib.import_module("scripts.check_project_skeleton") is not None


def test_project_skeleton_check_passes_without_external_services() -> None:
    assert collect_project_skeleton_errors() == []

