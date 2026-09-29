"""The command line."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

from netclab import lab

app = typer.Typer(no_args_is_help=True, add_completion=False)

CLUSTER = "netclab"


def _version(value: bool) -> None:
    if value:
        typer.echo(f"netclab {version('netclab')}")
        raise typer.Exit(0)


@app.callback()
def netclab(
    _: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="print the version"),
    ] = False,
) -> None:
    """A network lab on kind."""


@app.command()
def up(
    namespace: Annotated[str, typer.Option(help="the namespace the lab is installed into")],
    values: Annotated[
        Path, typer.Option(exists=True, dir_okay=False, help="the chart's values: the topology")
    ],
    cluster: Annotated[str, typer.Option(help="the kind cluster")] = CLUSTER,
    chart: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            file_okay=False,
            help="a chart directory to install instead of the released chart",
        ),
    ] = None,
    crossplane: Annotated[
        str | None, typer.Option(help="the Crossplane release to install, such as v2.4.2")
    ] = None,
    configuration: Annotated[
        str | None,
        typer.Option(help="a Configuration package to install, until it is healthy"),
    ] = None,
    manifest: Annotated[
        list[Path] | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            help="a file applied once the cluster serves its kinds; repeatable",
        ),
    ] = None,
) -> None:
    """Bring a lab up: the registry, the cluster, the CNI plugins, Multus, and the chart.

    With --crossplane, also Crossplane, a Configuration and manifests.
    """
    if crossplane is None and (configuration or manifest):
        raise typer.BadParameter("--configuration and --manifest need --crossplane")
    wanted = (
        lab.Crossplane(crossplane, configuration, tuple(manifest or ())) if crossplane else None
    )
    try:
        lab.up(cluster, namespace, values, chart, wanted)
    except lab.LabError as err:
        typer.echo(f"error: {err}", err=True)
        raise typer.Exit(1) from err


@app.command()
def down(
    namespace: Annotated[
        str | None,
        typer.Option(help="the lab to remove; without it, the whole cluster goes"),
    ] = None,
    cluster: Annotated[str, typer.Option(help="the kind cluster")] = CLUSTER,
) -> None:
    """Take a lab down, or the whole cluster. The registry and its images stay."""
    try:
        lab.down(cluster, namespace)
    except lab.LabError as err:
        typer.echo(f"error: {err}", err=True)
        raise typer.Exit(1) from err


def main() -> None:
    app()
