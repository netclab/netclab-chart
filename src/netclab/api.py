"""What netadopt imports from netclab: a change here is a breaking change.

`netadopt avd lab up` brings a lab up with `up`, Crossplane and the AVD packages
included, and takes it down with `down`. Each name is pinned by tests/test_api.py.
"""

from __future__ import annotations

from netclab.lab import Crossplane, LabError, down, up

__all__ = ["Crossplane", "LabError", "down", "up"]
