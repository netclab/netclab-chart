"""One tag releases the chart and the package, so they carry one version."""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent


def test_the_package_is_the_charts_version():
    chart = yaml.safe_load((ROOT / "Chart.yaml").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

    assert project["version"] == chart["version"]
