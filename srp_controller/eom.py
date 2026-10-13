"""One shared dual EOM controller, with a view for each laser.
Moved to the eom_controller package; re-exported here for compatibility."""

from eom_controller import EomChannel, SharedEom

__all__ = ["SharedEom", "EomChannel"]
