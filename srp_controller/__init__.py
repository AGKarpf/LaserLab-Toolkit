"""SRP controller: laser diode chains, APG pulse generators, shared dual EOM
and Spectrum AWG, configurable through config.toml and usable as a toolkit.

Building blocks for your own GUIs come from the plugin registry:

    from srp_controller import registry
    stage = registry.build("stage", "maiman_tec", {"port": "COM5"}, mock=True)
    widget = registry.create_widget("pulse_generator", "apg1780da", apg)

Qt is only imported by widget factories and srp_controller.gui."""

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
