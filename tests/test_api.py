"""What netadopt imports from netclab."""

from __future__ import annotations

from netclab import api, lab


def test_the_api_is_these_names():
    assert sorted(api.__all__) == ["Crossplane", "LabError", "down", "up"]


def test_each_name_is_the_one_in_the_lab():
    for name in api.__all__:
        assert getattr(api, name) is getattr(lab, name)
