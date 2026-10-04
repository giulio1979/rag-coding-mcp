from rag_coding_mcp import __version__


def test_version_is_a_string() -> None:
    assert isinstance(__version__, str)
    assert __version__.count(".") >= 1
