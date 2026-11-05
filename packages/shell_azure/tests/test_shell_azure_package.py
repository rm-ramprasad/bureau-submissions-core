import shell_azure


def test_version() -> None:
    assert shell_azure.__version__ == "0.1.0"
