"""LaserLab toolkit: laser diode chains (Maiman/Aerodiode), APG pulse generators,
shared dual EOM and Spectrum AWG, built from config tables through a plugin
registry and usable in your own PyQt6 GUIs. The SRP setup GUI is laserlab.srp.

Building blocks for your own GUIs come from the plugin registry:

    from laserlab import registry
    stage = registry.build("stage", "maiman_tec", {"port": "COM5"}, mock=True)
    widget = registry.create_widget("pulse_generator", "apg1780da", apg)

Qt is only imported by widget factories and laserlab.srp.gui."""

from .config import ConfigError, load_awg, load_lasers
from .laser import Laser
from .registry import (
    ComponentType, PLUGIN_GROUP, Registry, UnknownComponentError, default_registry,
)
from . import registry

__all__ = [
    "Laser", "load_lasers", "load_awg", "ConfigError",
    "registry", "Registry", "ComponentType", "UnknownComponentError",
    "default_registry", "PLUGIN_GROUP",
]
__version__ = "2.0.0"
