"""Connect deferred laser panels only to explicitly selected serial ports.

Port enumeration reads USB/OS metadata; it never probes unrelated instruments.
Maiman serial numbers are queried on the two selected ports before any control
command is sent. Serial 65535 can be shared by multiple boards, so port selection
and physical identification remain necessary."""

from __future__ import annotations

import json
import math
import tomllib
from pathlib import Path

from serial.tools import list_ports
from maiman_drivers import SF8150T
from maiman_drivers.laser import AdjustableTecLaserStage, LaserChain
from ..laser import Laser


def read_connection_settings(config_path="config.toml"):
    """Return deferred laser definitions, reserved ports and saved selections."""
    config_path = Path(config_path).resolve()
    with config_path.open("rb") as stream:
        data = tomllib.load(stream)
    if data.get("general", {}).get("use_mock", True):
        return [], set(), config_path.with_name("connections.json")
    deferred, reserved = [], set()
    for definition in data.get("laser", []):
        if definition.get("deferred_connect", False):
            deferred.append(definition)
        else:
            for stage in definition.get("stage", []):
                if stage.get("port"):
                    reserved.add(stage["port"].upper())
            port = definition.get("apg", {}).get("port")
            if port:
                reserved.add(port.upper())
    return deferred, reserved, config_path.with_name("connections.json")


def available_ports():
    """List port names plus USB descriptions without opening any port."""
    return [(p.device, f"{p.device} — {p.description} [{p.hwid}]")
            for p in sorted(list_ports.comports(), key=lambda p: p.device)]


def read_saved_ports(path: Path, laser_name: str):
    """Read optional selections; an invalid file does not prevent startup."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ports = data.get(laser_name, [])
        return ports if isinstance(ports, list) else []
    except (OSError, ValueError, AttributeError):
        return []


def save_ports(path: Path, laser_name: str, ports):
    """Save selections atomically. Reconnection always requires a button click."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[laser_name] = list(ports)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def connect_laser(definition, ports, reserved_ports=()):
    """Verify both boards, stop outputs and initialize adjustable TEC stages.
    
    Validation and identity checks precede control writes. A failed setup attempts
    to stop verified outputs and closes every opened connection.
        """
    stages = definition["stage"]
    if len(ports) != len(stages) or any(not p for p in ports):
        raise ValueError("Select a COM port for each diode.")
    normalized = [p.upper() for p in ports]
    if len(set(normalized)) != len(normalized):
        raise ValueError("Seed and pump must use different COM ports.")
    if set(normalized) & {p.upper() for p in reserved_ports}:
        raise ValueError("A selected port is already reserved by another device.")
    for stage in stages:
        limit = float(stage["current_max_a"])
        if not math.isfinite(limit) or not 0 < limit <= 1.5:
            raise ValueError("SF8150-T current limits must be greater than 0 and at most 1.5 A.")
        if stage.get("type") != "maiman_tec" or not stage.get("adjustable_current"):
            raise ValueError("Deferred connections require adjustable SF8 TEC stages.")
    drivers = []
    configuring = False
    try:
        for stage, port in zip(stages, ports):
            drivers.append(SF8150T(port, current_max_a=float(stage["current_max_a"]),
                                   expected_serial=stage["serial"]))
        configuring = True
        for driver in drivers:
            driver.stop()
        chain = LaserChain(definition['name'], [
            AdjustableTecLaserStage(stage["name"], driver, 0.0)
            for stage, driver in zip(stages, drivers)
        ])
        laser = Laser(definition["name"], chain, apg=None, eom=None)
        laser.initialize()
        for driver in drivers:
            if driver.read_state()["started"]:
                raise RuntimeError("Diode output did not stop.")
            if abs(driver.get_tec_temperature_setpoint() - 25.0) > 0.02:
                raise RuntimeError("TEC setpoint verification failed (expected 25 °C).")
            if not driver.read_tec_state()["started"]:
                raise RuntimeError("TEC did not start.")
            if abs(driver.get_current_setpoint()) > driver.CURRENT_SET_STEP_A:
                raise RuntimeError("Initial zero-current verification failed.")
        return laser
    except Exception:
        for driver in drivers:
            if configuring:
                try:
                    driver.stop()
                except Exception:
                    pass
            driver.close()
        raise
