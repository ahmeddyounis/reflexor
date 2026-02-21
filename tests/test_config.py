import importlib.util

from reflexor.config import ReflexorSettings, load_env_file


def test_config_import_and_defaults() -> None:
    settings = ReflexorSettings()
    assert settings.environment == "dev"
    assert settings.log_level == "INFO"
    assert settings.dry_run is True


def test_load_env_file_is_optional(tmp_path, monkeypatch) -> None:
    has_dotenv = importlib.util.find_spec("dotenv") is not None

    env_path = tmp_path / ".env"
    env_path.write_text("REFLEXOR_ENVIRONMENT=prod\n", encoding="utf-8")

    monkeypatch.delenv("REFLEXOR_ENVIRONMENT", raising=False)

    loaded = load_env_file(env_path)
    if not has_dotenv:
        assert loaded is False
        assert ReflexorSettings().environment == "dev"
        return

    assert loaded is True
    assert ReflexorSettings().environment == "prod"
