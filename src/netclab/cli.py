"""The command line."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from netclab import lab

app = typer.Typer(no_args_is_help=True, add_completion=False)

CLUSTER = "netclab"


@app.callback()
def netclab() -> None:
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
) -> None:
    """Bring a lab up: the registry, the cluster, the CNI plugins, Multus, and the chart."""
    try:
        lab.up(cluster, namespace, values, chart)
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
