"""Milestone 0 smoke tests."""

from importlib import import_module

from sqlalchemy import text

from radar.config import Settings
from radar.db import create_db_engine, init_database


def test_python_imports_succeed() -> None:
    modules = (
        "radar",
        "radar.config",
        "radar.db",
        "radar.models",
        "radar.schemas",
        "radar.vector_store",
        "radar.skills",
        "radar.skills.writer",
        "radar.skills.reviewer",
        "radar.skills.auditor",
        "radar.skills.experiment",
        "radar.skills.rebuttal",
        "app",
    )

    for module_name in modules:
        assert import_module(module_name) is not None


def test_settings_have_local_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.database_url == "sqlite:///data/research_radar.db"
    assert settings.data_dir.as_posix() == "data"


def test_database_initializes(tmp_path) -> None:
    database_path = tmp_path / "research_radar_test.db"
    test_engine = create_db_engine(f"sqlite:///{database_path}")

    try:
        init_database(test_engine)
        with test_engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        test_engine.dispose()

    assert database_path.exists()

