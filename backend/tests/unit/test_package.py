import cerebro


def test_package_is_importable() -> None:
    assert cerebro.__doc__ is not None
