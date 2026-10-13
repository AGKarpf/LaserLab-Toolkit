"""py -m laserlab: start the SRP setup GUI (see laserlab.srp.__main__)."""

import sys

from .srp.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
