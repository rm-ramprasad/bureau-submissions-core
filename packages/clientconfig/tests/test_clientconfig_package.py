import clientconfig


def test_version() -> None:
    assert clientconfig.__version__ == "0.1.0"
