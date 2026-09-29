"""One tag releases the chart and the package, so they carry one version, and
`netclab --version` says it."""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

import yaml
from typer.testing import CliRunner

from netclab.cli import app

ROOT = Path(__file__).parent.parent


def test_the_package_is_the_charts_version():
    chart = yaml.safe_load((ROOT / "Chart.yaml").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

    assert project["version"] == chart["version"]


def test_version_names_netclab_and_its_version():
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output == f"netclab {version('netclab')}\n"
