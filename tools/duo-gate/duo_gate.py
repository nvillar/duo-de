#!/usr/bin/env python3
"""duo-gate entry point: uv run --no-project python tools/duo-gate/duo_gate.py --help"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from duogate.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
