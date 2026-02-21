def test_import_package() -> None:
    import reflexor  # noqa: F401


def test_import_version() -> None:
    import reflexor
    from reflexor.version import __version__

    assert isinstance(__version__, str)
    assert reflexor.__version__ == __version__
    assert reflexor.get_version() == __version__
