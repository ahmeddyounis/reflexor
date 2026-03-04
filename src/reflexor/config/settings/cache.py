from __future__ import annotations

from functools import lru_cache

from reflexor.config.settings.model import ReflexorSettings


@lru_cache
def get_settings() -> ReflexorSettings:
    return ReflexorSettings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
