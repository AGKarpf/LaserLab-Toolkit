"""Configurable TEC setpoint (tec_temperature_c) for Maiman SF8150-T stages."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from laserlab.config import ConfigError, load_lasers
from laserlab.srp import connections
from maiman_drivers.mock import MockSF8150T


@pytest.fixture(autouse=True)
def no_serial_delays():
    """Skip the simulated serial delays of the mock drivers."""
    from unittest.mock import patch
    with patch("maiman_drivers.driver.time.sleep"):
        yield

CONFIG = """
[general]
use_mock = true

[[laser]]
name = "TEC test"
  [[laser.stage]]
  type = "maiman_tec"
  name = "Fixed"
  diode_current_a = 0.1
  {fixed}

  [[laser.stage]]
  type = "maiman_tec"
  name = "Adjustable"
  current_max_a = 0.5
  adjustable_current = true
  {adjustable}
"""


def lasers_for(tmp_path, fixed="", adjustable=""):
    config = tmp_path / "config.toml"
    config.write_text(CONFIG.format(fixed=fixed, adjustable=adjustable), encoding="utf-8")
    return load_lasers(config)


def setpoints(laser):
    return [round(s.driver.get_tec_temperature_setpoint(), 2) for s in laser.chain.stages]


def test_default_is_25(tmp_path):
    (laser,) = lasers_for(tmp_path)
    assert setpoints(laser) == [25.0, 25.0]
    assert [s.tec_temperature_c for s in laser.chain.stages] == [25.0, 25.0]


def test_configured_17(tmp_path):
    (laser,) = lasers_for(tmp_path, "tec_temperature_c = 17", "tec_temperature_c = 17.5")
    assert setpoints(laser) == [17.0, 17.5]
    assert all(s.tec_running() for s in laser.chain.stages)


@pytest.mark.parametrize("value", ["14.9", "40.5", '"warm"'])
def test_out_of_range_rejected(tmp_path, value):
    with pytest.raises(ConfigError, match="Fixed.*15 and 40"):
        lasers_for(tmp_path, f"tec_temperature_c = {value}")


def test_gui_shows_configured_target(tmp_path):
    from PyQt6.QtWidgets import QApplication, QLabel
    from laserlab import registry
    app = QApplication.instance() or QApplication([])
    (laser,) = lasers_for(tmp_path, "tec_temperature_c = 17", "tec_temperature_c = 18")
    widget = registry.create_widget("chain", "laser_chain", laser.chain)
    texts = " ".join(label.text() for label in widget.findChildren(QLabel))
    assert "TEC target: 17 °C" in texts and "TEC target: 18 °C" in texts
    widget.close()
    app.processEvents()


def deferred(temperatures):
    stages = []
    for i, celsius in enumerate(temperatures):
        stage = {"type": "maiman_tec", "name": f"S{i}", "serial": 0x1000 + i,
                 "current_max_a": 0.5, "adjustable_current": True}
        if celsius is not None:
            stage["tec_temperature_c"] = celsius
        stages.append(stage)
    return {"name": "Deferred", "stage": stages}


def test_deferred_connection_uses_and_verifies_setpoint(monkeypatch):
    monkeypatch.setattr(connections, "SF8150T", lambda port, current_max_a, expected_serial:
                        MockSF8150T(port, serial_number=expected_serial,
                                    current_max_a=current_max_a, expected_serial=expected_serial))
    laser = connections.connect_laser(deferred([17.0, None]), ["COM21", "COM22"])
    assert setpoints(laser) == [17.0, 25.0]


def test_deferred_connection_rejects_range_before_opening(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("port must not be opened")
    monkeypatch.setattr(connections, "SF8150T", forbidden)
    with pytest.raises(ValueError, match="15 and 40"):
        connections.connect_laser(deferred([12.0, 25.0]), ["COM21", "COM22"])
