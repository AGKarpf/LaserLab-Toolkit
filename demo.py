"""Launch the SRP GUI with simulated drivers only. Run: py demo.py.
Read config.toml next to this file but force mock mode; no hardware connections."""

import sys
from pathlib import Path

from laserlab.srp.__main__ import main

if __name__ == "__main__":
    sys.exit(main(["--mock", "--config", str(Path(__file__).with_name("config.toml"))]))
