#!/usr/bin/env python3
"""Agent hook shim -> sanity hook pr (no installation required)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sanity.cli import main  # noqa: E402

sys.exit(main(["hook", "pr"] + sys.argv[1:]))
