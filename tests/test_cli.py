def test_cli_main_prints_help(capsys) -> None:
    from reflexor.cli.main import main

    exit_code = main([])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Reflexor CLI (stub)" in captured.out
