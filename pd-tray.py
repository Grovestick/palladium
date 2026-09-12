#!/usr/bin/env python3
"""Palladium in the system tray, run from source. The tray itself is pd_tray; built,
the server shows the icon and there is no separate program."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pd_tray import main  # noqa: E402

if __name__ == "__main__":
    main()
