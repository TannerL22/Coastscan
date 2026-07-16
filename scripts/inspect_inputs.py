"""Compatibility wrapper for input inspection."""

import sys

from coastscan.cli import app

if __name__ == "__main__":
    app(prog_name="inspect_inputs.py", args=["inspect-inputs", *sys.argv[1:]])
