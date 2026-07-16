"""Compatibility wrapper for the primary package CLI."""

import sys

from coastscan.cli import app

if __name__ == "__main__":
    app(prog_name="build_region.py", args=["build-region", *sys.argv[1:]])
