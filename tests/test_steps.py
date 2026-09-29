"""How a step shows: a line without a terminal, and in one a line with its time."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from netclab import steps


@pytest.fixture
def terminal(monkeypatch) -> io.StringIO:
    out = io.StringIO()
    monkeypatch.setattr(steps, "console", Console(file=out, force_terminal=True, width=100))
    return out


def test_without_a_terminal_a_step_is_its_line(capsys):
    with steps.step("Multus v4.3.1"):
        pass

    assert capsys.readouterr().out == ">> Multus v4.3.1\n"


def test_a_step_that_ends_leaves_a_tick_and_its_time(terminal):
    with steps.step("Multus v4.3.1"):
        pass

    assert f"✓ {'Multus v4.3.1'.ljust(steps.WIDTH)} 0:00:00" in terminal.getvalue()


def test_a_step_that_fails_leaves_a_cross_and_the_error_goes_on(terminal):
    with pytest.raises(RuntimeError), steps.step("Multus v4.3.1"):
        raise RuntimeError("no Multus")

    assert "✗ Multus v4.3.1" in terminal.getvalue()
    assert "✓" not in terminal.getvalue()


def test_in_a_terminal_what_is_said_is_no_step(terminal):
    steps.say("no lab l3ls")

    assert terminal.getvalue() == "  no lab l3ls\n"


@pytest.mark.parametrize(
    ("seconds", "shown"), [(0.4, "0:00:00"), (75, "0:01:15"), (3661, "1:01:01")]
)
def test_a_time_is_shown_as_the_spinner_shows_it(seconds, shown):
    assert steps.elapsed(seconds) == shown
