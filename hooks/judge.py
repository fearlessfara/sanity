#!/usr/bin/env python3
"""Agent hook shim -> sanity hook stop/judge (NL rules feedback loop)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sanity.cli import main  # noqa: E402

sys.exit(main(["hook", "stop"] + sys.argv[1:]))
