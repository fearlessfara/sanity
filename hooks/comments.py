#!/usr/bin/env python3
"""Agent hook shim -> sanity hook comments (no installation required)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sanity.cli import main  # noqa: E402

sys.exit(main(["hook", "comments"] + sys.argv[1:]))
