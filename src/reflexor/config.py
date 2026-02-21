from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import cast

from pydantic_settings import BaseSettings, SettingsConfigDict


class ReflexorSettings(BaseSettings):
    """Runtime configuration for Reflexor.

    Settings are loaded from environment variables by default, using the `REFLEXOR_` prefix.
    """

    model_config = SettingsConfigDict(env_prefix="REFLEXOR_", extra="ignore")

    environment: str = "dev"
    log_level: str = "INFO"
    dry_run: bool = True


@lru_cache
def get_settings() -> ReflexorSettings:
    return ReflexorSettings()


def load_env_file(path: str | Path = ".env", *, override: bool = False) -> bool:
    """Load a dotenv file if `python-dotenv` is installed.

    Returns `True` if the dotenv loader ran successfully, otherwise `False`.
    """

    if importlib.util.find_spec("dotenv") is None:
        return False

    module = importlib.import_module("dotenv")
    loader = getattr(module, "load_dotenv", None)
    if loader is None:
        return False

    load_dotenv = cast(Callable[..., object], loader)
    dotenv_path = str(Path(path))
    return bool(load_dotenv(dotenv_path=dotenv_path, override=override))
