def test_import_api_app() -> None:
    import reflexor.api.app  # noqa: F401


def test_api_container_shim_points_to_bootstrap() -> None:
    from reflexor.api.container import AppContainer as ApiAppContainer
    from reflexor.bootstrap.container import AppContainer as BootstrapAppContainer

    assert ApiAppContainer is BootstrapAppContainer
