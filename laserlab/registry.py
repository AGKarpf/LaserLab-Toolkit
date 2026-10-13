"""Plugin registry for building devices and widgets dynamically.

Every component type is registered under a kind and a name:

    kind              names (built in)
    ----------------  ---------------------------------------------
    stage             maiman_tec, maiman_notec, aerodiode
    pulse_generator   apg1780da
    eom               dual_eom
    awg               spectrum_m4i
    chain             laser_chain   (widget only; chains are built from stages)

A ComponentType has a build(params, mock) factory that returns a connected,
ready-to-use object (or a simulation when mock is True) and an optional
widget(obj, **kwargs) factory returning a PyQt6 widget for that object. Qt is
only imported when a widget is requested.

Third-party packages add component types through the entry-point group
"laserlab.plugins"; each entry point is a callable register(registry):

    [project.entry-points."laserlab.plugins"]
    my_laser = "my_package.srp_plugin:register"
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

#: Entry-point group scanned for external plugins.
PLUGIN_GROUP = "laserlab.plugins"

BuildFactory = Callable[[Mapping[str, Any], bool], Any]
WidgetFactory = Callable[..., Any]


class UnknownComponentError(KeyError):
    """No component type is registered under the requested kind/name."""

    def __str__(self) -> str:          # KeyError would quote the message
        return str(self.args[0])


@dataclass(frozen=True)
class ComponentType:
    """Description of one buildable component type."""

    kind: str
    name: str
    build: BuildFactory
    widget: Optional[WidgetFactory] = None
    description: str = ""


class Registry:
    """Collection of component types, keyed by (kind, name)."""

    def __init__(self) -> None:
        self._types: Dict[Tuple[str, str], ComponentType] = {}
        self.loaded_plugins: List[str] = []

    # -- registration ---------------------------------------------------
    def register(self, component: ComponentType, *, replace: bool = False) -> ComponentType:
        key = (component.kind, component.name)
        if key in self._types and not replace:
            raise ValueError(f"Component {component.kind}/{component.name} is already "
                             f"registered (pass replace=True to override).")
        self._types[key] = component
        return component

    def add(self, kind: str, name: str, build: BuildFactory,
            widget: Optional[WidgetFactory] = None, description: str = "",
            *, replace: bool = False) -> ComponentType:
        """Shorthand for register(ComponentType(...))."""
        return self.register(ComponentType(kind, name, build, widget, description),
                             replace=replace)

    def load_entry_points(self, group: str = PLUGIN_GROUP) -> List[str]:
        """Call register(self) for every installed plugin entry point.
        A failing plugin is reported as a warning and does not stop the others."""
        loaded = []
        for ep in entry_points(group=group):
            try:
                ep.load()(self)
            except Exception as exc:
                warnings.warn(f"laserlab plugin '{ep.name}' ({ep.value}) "
                              f"failed to load: {exc}", RuntimeWarning, stacklevel=2)
                continue
            loaded.append(ep.name)
        self.loaded_plugins.extend(loaded)
        return loaded

    # -- lookup ---------------------------------------------------------
    def get(self, kind: str, name: str) -> ComponentType:
        try:
            return self._types[(kind, name)]
        except KeyError:
            available = ", ".join(self.names(kind)) or "none"
            raise UnknownComponentError(
                f"Unknown {kind} type {name!r} (available: {available}).") from None

    def kinds(self) -> List[str]:
        return sorted({kind for kind, _ in self._types})

    def names(self, kind: Optional[str] = None) -> List[str]:
        return sorted(name for k, name in self._types if kind is None or k == kind)

    def types(self, kind: Optional[str] = None) -> List[ComponentType]:
        return [self._types[key] for key in sorted(self._types)
                if kind is None or key[0] == kind]

    # -- construction ---------------------------------------------------
    def build(self, kind: str, name: str,
              params: Optional[Mapping[str, Any]] = None, mock: bool = False) -> Any:
        """Build a component from a parameter mapping (e.g. a config.toml table)."""
        return self.get(kind, name).build(dict(params or {}), mock)

    def create_widget(self, kind: str, name: str, obj: Any, **kwargs) -> Any:
        """Create the PyQt6 widget registered for a component type."""
        component = self.get(kind, name)
        if component.widget is None:
            raise UnknownComponentError(f"{kind}/{name} has no widget.")
        return component.widget(obj, **kwargs)


_default: Optional[Registry] = None


def default_registry() -> Registry:
    """Shared registry with the built-in types and all installed plugins."""
    global _default
    if _default is None:
        from . import builtin
        registry = Registry()
        builtin.register(registry)
        registry.load_entry_points()
        _default = registry
    return _default


def build(kind: str, name: str, params: Optional[Mapping[str, Any]] = None,
          mock: bool = False) -> Any:
    """Build a component with the default registry."""
    return default_registry().build(kind, name, params, mock)


def create_widget(kind: str, name: str, obj: Any, **kwargs) -> Any:
    """Create a widget with the default registry."""
    return default_registry().create_widget(kind, name, obj, **kwargs)


def names(kind: Optional[str] = None) -> List[str]:
    """List registered type names of the default registry."""
    return default_registry().names(kind)
