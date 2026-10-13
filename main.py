"""Launch the SRP GUI using config.toml next to this file. Run: py main.py.
Hardware mode follows [general] use_mock; deferred drivers connect from the GUI."""

import sys
from pathlib import Path

from laserlab.srp.__main__ import main

if __name__ == "__main__":
    sys.exit(main(["--config", str(Path(__file__).with_name("config.toml"))]))
