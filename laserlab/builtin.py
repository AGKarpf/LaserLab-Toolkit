"""Built-in component types: Maiman/Aerodiode stages, APG1780DA, dual EOM,
Spectrum AWG and laser chains. Parameter names match the config.toml tables.
Drivers that need optional native libraries (Spectrum, NI DLL) and all Qt
widgets are imported lazily, only when such a component is actually built."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

# Hardware drivers
from maiman_drivers import SF8150T, SF6015
from aerodiode_driver import Mmd
from apg1780da import APG1780DA

# Mock drivers
from maiman_drivers.mock import MockSF8150T, MockSF6015
from aerodiode_driver.mock import MockMmd
from apg1780da.mock import MockAPG1780DA

# Chain framework and EOM
from maiman_drivers.laser import LaserChain, TecLaserStage, DcLaserStage, AdjustableTecLaserStage
from eom_controller import DualEOMController, EomChannel, MockDualEOMController, SharedEom

from .srp.awg import AwgController, AwgSettings

#: Default TEC setpoint: 25 °C (Maiman stages: tec_temperature_c overrides it).
TEC_TEMPERATURE_C = 25.0
#: Maiman SF8 TEC setpoint validation (15-40 °C), bound before any patching/port.
_check_maiman_tec = SF8150T.check_tec_temperature


def _activate_tec(drv, name: str, is_maiman: bool, use_mock: bool,
                  temperature_c: float = TEC_TEMPERATURE_C) -> None:
    """Set the TEC setpoint immediately after connection and start it.
    Maiman also requires internal temperature and enable sources. Each command
    is attempted independently with a 30 ms hardware delay; verify started state
    and retry once. Failures are reported without preventing later commands."""
    delay = 0.0 if use_mock else 0.03

    def cmd(fn, what: str) -> None:
        try:
            fn()
            time.sleep(delay)
        except Exception as exc:
            print(f"{name}: TEC command '{what}' failed ({exc}).")

    cmd(lambda: drv.set_tec_temperature(temperature_c), "set_tec_temperature")
    if is_maiman:
        cmd(lambda: drv.tec_set_source(internal=True), "tec_set_source")
        cmd(lambda: drv.tec_set_enable_source(internal=True), "tec_set_enable_source")
    cmd(lambda: drv.tec_start(), "tec_start")

    # Verify and retry once if the TEC is not running.
    try:
        if not drv.read_tec_state().get("started"):
            cmd(lambda: drv.tec_start(), "tec_start (retry)")
            if not drv.read_tec_state().get("started"):
                print(f"{name}: WARNING: TEC still reports 'not "
                      f"started' (check connection/timing).")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Stages (driver + laser-chain stage)
# ---------------------------------------------------------------------------
def build_maiman_tec(stage: Mapping[str, Any], use_mock: bool):
    """SF8150-T with TEC. Keys: name, port, serial, current_max_a,
    diode_current_a | initial_current_a, adjustable_current,
    tec_temperature_c (TEC setpoint, 15-40 °C, default 25 °C)."""
    name = stage.get("name", "maiman_tec")
    port = stage.get("port") or f"MOCK-{name}"
    current = float(stage.get("diode_current_a", stage.get("initial_current_a", 0.0)))
    # Validate before any port is opened.
    try:
        tec_c = _check_maiman_tec(stage.get("tec_temperature_c", TEC_TEMPERATURE_C))
    except ValueError as exc:
        raise ValueError(f"Stage '{name}': {exc}") from None
    if use_mock:
        drv = MockSF8150T(port, serial_number=stage.get("serial", 0x0002),
                          current_max_a=stage.get("current_max_a"),
                          expected_serial=stage.get("serial"))
    else:
        drv = SF8150T(port, current_max_a=stage.get("current_max_a"),
                      expected_serial=stage.get("serial"))
    # Activate TEC at connection with the configured setpoint, before initialize().
    _activate_tec(drv, name, is_maiman=True, use_mock=use_mock, temperature_c=tec_c)
    stage_class = AdjustableTecLaserStage if stage.get("adjustable_current") else TecLaserStage
    return stage_class(name, drv, current, tec_temperature_c=tec_c)


def build_aerodiode(stage: Mapping[str, Any], use_mock: bool):
    """Aerodiode MMD with adjustable current. Keys: name, port, address,
    max_current_a, initial_current_a | diode_current_a."""
    name = stage.get("name", "aerodiode")
    port = stage.get("port") or f"MOCK-{name}"
    # Adjustable current with a maximum from the configuration, like the no-TEC Maiman.
    max_a = float(stage.get("max_current_a", 10.0))
    # Initial current: prefer initial_current_a, then diode_current_a, then 0.
    initial = float(stage.get("initial_current_a", stage.get("diode_current_a", 0.0)))
    if use_mock:
        drv = MockMmd(port, address=int(stage.get("address", 1)))
    else:
        drv = Mmd(port, expected_address=stage.get("address"))
    drv.set_max_current(max_a)      # apply configured maximum current
    # Activate TEC at connection with a 25 °C setpoint.
    _activate_tec(drv, name, is_maiman=False, use_mock=use_mock)
    return DcLaserStage(name, drv, initial)


def build_maiman_notec(stage: Mapping[str, Any], use_mock: bool):
    """SF6015 without TEC. Keys: name, port, serial, max_current_a, initial_current_a."""
    name = stage.get("name", "maiman_notec")
    port = stage.get("port") or f"MOCK-{name}"
    initial = float(stage.get("initial_current_a", 0.0))
    if use_mock:
        drv = MockSF6015(port, serial_number=stage.get("serial", 0x0001))
    else:
        drv = SF6015(port, expected_serial=stage.get("serial"))
    if "max_current_a" in stage:   # optional upper bound of the current input
        drv.CURRENT_MAX_A = float(stage["max_current_a"])
    return DcLaserStage(name, drv, initial)


# ---------------------------------------------------------------------------
# Pulse generator, EOM, AWG
# ---------------------------------------------------------------------------
def build_apg1780da(cfg: Mapping[str, Any], use_mock: bool):
    """Keys: port, mode (default "usb")."""
    port = cfg.get("port")
    return MockAPG1780DA(port) if use_mock else APG1780DA(port, mode=cfg.get("mode", "usb"))


def build_dual_eom(cfg: Mapping[str, Any], use_mock: bool) -> SharedEom:
    """One shared dual EOM controller. Keys: board_idx (mock), ini (hardware;
    relative to base_dir, default: current folder)."""
    if use_mock:
        return SharedEom(MockDualEOMController(board_idx=int(cfg.get("board_idx", 1))))
    ini = cfg.get("ini")
    if not ini:
        raise ValueError("EOM: no 'ini' path in the configuration.")
    ini_path = Path(ini)
    if not ini_path.is_absolute():
        ini_path = Path(cfg.get("base_dir", ".")) / ini_path
    return SharedEom(DualEOMController.from_config_path(str(ini_path)))


def awg_settings(a: Mapping[str, Any]) -> AwgSettings:
    """Translate an [awg] table into AwgSettings."""
    shift = a.get("probe_pump_shift_samples")   # None -> half pulse period
    return AwgSettings(
        device=a.get("device", "/dev/spcm0"),
        samplerate_hz=int(a.get("samplerate_hz", 819_200_000)),
        reference_clock_hz=int(a.get("reference_clock_hz", 12_800_000)),
        external_clock=bool(a.get("external_clock", True)),
        external_trigger=bool(a.get("external_trigger", True)),
        trigger_level_mv=int(a.get("trigger_level_mv", 1500)),
        pulse_frequency_hz=int(a.get("pulse_frequency_hz", 51_200_000)),
        num_pulses=int(a.get("num_pulses", 10)),
        amplitude_mv=int(a.get("amplitude_mv", 1000)),
        analog_channel=int(a.get("analog_channel", 0)),
        pump_line=int(a.get("pump_line", 0)),
        stokes_line=int(a.get("stokes_line", 1)),
        trigger_line=int(a.get("trigger_line", 2)),
        pump_phase_samples=int(a.get("pump_phase_samples", 0)),
        stokes_phase_samples=int(a.get("stokes_phase_samples", 0)),
        decay_time_s=float(a.get("decay_time_us", 20.0)) * 1e-6,
        probe_enabled=bool(a.get("probe_enabled", True)),
        probe_pump_shift_samples=(int(shift) if shift is not None else None),
    )


def build_spectrum_awg(cfg: Mapping[str, Any], use_mock: bool) -> AwgController:
    """SRP sequence on a Spectrum M4i.6631-x8. Keys: see awg_settings()."""
    return AwgController(awg_settings(cfg), use_mock=use_mock)


# ---------------------------------------------------------------------------
# Widgets (PyQt6 imported on demand)
# ---------------------------------------------------------------------------
def apg_widget(device, **kwargs):
    from apg1780da.gui import ApgDeviceWidget
    return ApgDeviceWidget(device, **kwargs)


def eom_widget(obj, **kwargs):
    """EomChannel -> seed indicator; SharedEom -> window with both channels."""
    from eom_controller.gui import DualEomWindow, EomWidget
    if isinstance(obj, SharedEom):
        return DualEomWindow(obj, **kwargs)
    return EomWidget(obj, **kwargs)


def awg_widget(controller, **kwargs):
    from .srp.awg_widget import AwgPanel
    return AwgPanel(controller, **kwargs)


def chain_widget(chain, **kwargs):
    from maiman_drivers.gui import LaserChainWidget
    return LaserChainWidget(chain, **kwargs)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register(registry) -> None:
    """Register all built-in component types in a Registry."""
    registry.add("stage", "maiman_tec", build_maiman_tec,
                 description="Maiman SF8150-T diode driver with TEC")
    registry.add("stage", "maiman_notec", build_maiman_notec,
                 description="Maiman SF6015 diode driver without TEC")
    registry.add("stage", "aerodiode", build_aerodiode,
                 description="Aerodiode MMD diode driver (adjustable current)")
    registry.add("pulse_generator", "apg1780da", build_apg1780da, apg_widget,
                 description="Sigtrona APG1780DA picosecond pulse generator")
    registry.add("eom", "dual_eom", build_dual_eom, eom_widget,
                 description="Dual EOM bias regulator (NI USB-6001, EOM_Controller_NI.dll)")
    registry.add("awg", "spectrum_m4i", build_spectrum_awg, awg_widget,
                 description="Spectrum M4i.6631-x8 AWG running the SRP sequence")

    def build_chain(cfg: Mapping[str, Any], use_mock: bool) -> LaserChain:
        """Keys: name, stage = [ {type = ..., ...}, ... ] (ordered)."""
        name = cfg.get("name", "Laser chain")
        stages = [registry.build("stage", s.get("type"), s, use_mock)
                  for s in cfg.get("stage", [])]
        return LaserChain(name, stages)

    registry.add("chain", "laser_chain", build_chain, chain_widget,
                 description="Ordered diode chain with switch-on dependencies")


__all__ = [
    "TEC_TEMPERATURE_C", "register", "awg_settings",
    "build_maiman_tec", "build_maiman_notec", "build_aerodiode",
    "build_apg1780da", "build_dual_eom", "build_spectrum_awg",
]
