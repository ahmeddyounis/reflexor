def test_import_package() -> None:
    import reflexor  # noqa: F401


def test_import_version() -> None:
    from reflexor.version import __version__

    assert isinstance(__version__, str)
