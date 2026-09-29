"""How `up` and `down` show their steps.

In a terminal, a step runs under a spinner with its time, and leaves one line when it
ends: `✓` and the time it took, or `✗` when it failed. Without a terminal, as in CI or a
log, a step is the line `>> step`, printed as it starts.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

console = Console(highlight=False)

# Steps are padded to this width, so their times line up.
WIDTH = 48


def elapsed(seconds: float) -> str:
    """`h:mm:ss`, as the spinner shows it."""
    whole = int(seconds)
    return f"{whole // 3600}:{whole // 60 % 60:02d}:{whole % 60:02d}"


def say(text: str) -> None:
    """A line that is no step: what was found, and left as it is."""
    if console.is_terminal:
        console.print(f"  {text}", markup=False, highlight=False)
    else:
        print(f">> {text}", flush=True)


@contextmanager
def step(text: str) -> Iterator[None]:
    if not console.is_terminal:
        print(f">> {text}", flush=True)
        yield
        return
    start = time.monotonic()
    progress = Progress(
        SpinnerColumn(),
        TextColumn("{task.description}", markup=False),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    try:
        with progress:
            progress.add_task(text.ljust(WIDTH), total=None)
            yield
    except BaseException:
        console.print(
            f"✗ {text.ljust(WIDTH)} {elapsed(time.monotonic() - start)}",
            markup=False,
            highlight=False,
        )
        raise
    console.print(
        f"✓ {text.ljust(WIDTH)} {elapsed(time.monotonic() - start)}", markup=False, highlight=False
    )
