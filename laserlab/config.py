"""Read config.toml and build laser chains, APG, shared EOM and AWG controllers.
[general] use_mock selects simulation. Each [[laser]] contains optional apg/eom
blocks and [[laser.stage]] entries of type maiman_tec, aerodiode or maiman_notec.
Deferred lasers are skipped in hardware mode and connected manually in the GUI.
Component types come from the plugin registry, so plugins can add new types."""

from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:      # Python 3.10
    import tomli as tomllib
from pathlib import Path
from typing import List, Optional

from maiman_drivers.laser import LaserChain

from .srp.awg import AwgController
from .builtin import TEC_TEMPERATURE_C, _activate_tec  # noqa: F401 (re-exported)
from eom_controller import EomChannel, SharedEom
from .laser import Laser
from .registry import Registry, UnknownComponentError, default_registry


class ConfigError(Exception):
    """Invalid or unreadable SRP configuration."""


# ---------------------------------------------------------------------------
# Individual components (built through the plugin registry)
# ---------------------------------------------------------------------------
def _registry(registry: Optional[Registry]) -> Registry:
    return registry if registry is not None else default_registry()


def _build_stage(stage: dict, use_mock: bool, registry: Optional[Registry] = None):
    typ = stage.get("type")
    name = stage.get("name", typ or "Stage")
    try:
        return _registry(registry).build("stage", typ, stage, use_mock)
    except UnknownComponentError as exc:
        raise ConfigError(f"{exc} (stage '{name}').") from None


def _build_apg(cfg: Optional[dict], use_mock: bool, registry: Optional[Registry] = None):
    if not cfg:
        return None
    try:
        return _registry(registry).build("pulse_generator", cfg.get("type", "apg1780da"),
                                         cfg, use_mock)
    except UnknownComponentError as exc:
        raise ConfigError(str(exc)) from None
    except Exception as exc:
        print(f"APG on {cfg.get('port')} could not be connected: {exc}")
        return None


def _build_shared_eom(cfg: Optional[dict], use_mock: bool,
                      base_dir: Path, registry: Optional[Registry] = None) -> Optional[SharedEom]:
    """Build the one shared dual EOM controller, or return None."""
    if not cfg:
        return None
    params = {"base_dir": str(base_dir), **cfg}
    try:
        return _registry(registry).build("eom", cfg.get("type", "dual_eom"), params, use_mock)
    except UnknownComponentError as exc:
        raise ConfigError(str(exc)) from None
    except Exception as exc:
        print(f"Dual EOM ({cfg.get('ini')}) could not be loaded: {exc}")
        return None


def _assign_channel(shared: Optional[SharedEom],
                    laser_eom_cfg: Optional[dict],
                    laser_name: str) -> Optional[EomChannel]:
    """Assign one channel of the shared controller to a laser."""
    if shared is None or not laser_eom_cfg:
        return None
    channel = laser_eom_cfg.get("channel")
    if channel is None:
        print(f"'{laser_name}': no EOM channel specified -> no EOM.")
        return None
    if int(channel) not in (0, 1):
        raise ConfigError(f"'{laser_name}': EOM channel must be 0 or 1 "
                          f"(got {channel!r}).")
    return EomChannel(shared, int(channel))


def _read_config(path: str | Path) -> tuple[Path, dict]:
    """Read a TOML file; relative paths resolve against the current folder."""
    cfg_path = Path(path).resolve()
    if not cfg_path.exists():
        raise ConfigError(f"Configuration file not found: {cfg_path}")
    with cfg_path.open("rb") as f:
        return cfg_path, tomllib.load(f)


def _use_mock(data: dict, force_mock: Optional[bool]) -> bool:
    if force_mock is not None:
        return force_mock
    return bool(data.get("general", {}).get("use_mock", True))


# ---------------------------------------------------------------------------
# Complete configuration
# ---------------------------------------------------------------------------
def load_awg(path: str | Path = "config.toml",
             force_mock: Optional[bool] = None,
             registry: Optional[Registry] = None) -> Optional[AwgController]:
    """Read the [awg] block and build the AWG controller, or return None."""
    _, data = _read_config(path)
    use_mock = _use_mock(data, force_mock)
    a = data.get("awg")
    if not a:
        return None
    try:
        return _registry(registry).build("awg", a.get("type", "spectrum_m4i"), a, use_mock)
    except UnknownComponentError as exc:
        raise ConfigError(str(exc)) from None
    except Exception as exc:
        print(f"AWG ({a.get('device', '/dev/spcm0')}) could not be initialized: {exc}")
        return None


def load_lasers(path: str | Path = "config.toml",
                force_mock: Optional[bool] = None,
                registry: Optional[Registry] = None) -> List[Laser]:
    """Read config.toml and initialize configured lasers.
    Relative paths resolve against the current folder. force_mock overrides
    [general] use_mock; True guarantees simulation. Hardware mode skips
    deferred_connect entries until their ports are selected in the GUI.
    Stage/APG/EOM types are looked up in the plugin registry (see registry.py)."""
    cfg_path, data = _read_config(path)
    use_mock = _use_mock(data, force_mock)
    base_dir = cfg_path.parent

    # One shared dual EOM controller for all assigned lasers.
    shared_eom = _build_shared_eom(data.get("eom"), use_mock, base_dir, registry)

    lasers: List[Laser] = []
    for ldef in data.get("laser", []):
        if ldef.get("deferred_connect", False) and not use_mock:
            continue
        name = ldef.get("name", f"Laser {len(lasers) + 1}")
        stage_defs = ldef.get("stage", [])
        if not stage_defs:
            raise ConfigError(f"'{name}' has no stages ([[laser.stage]]).")
        stages = [_build_stage(s, use_mock, registry) for s in stage_defs]
        chain = LaserChain(name, stages)
        laser = Laser(
            name=name,
            chain=chain,
            apg=_build_apg(ldef.get("apg"), use_mock, registry),
            eom=_assign_channel(shared_eom, ldef.get("eom"), name),
        )
        laser.initialize()
        lasers.append(laser)

    if not lasers:
        raise ConfigError("No lasers defined in the configuration ([[laser]]).")
    return lasers
