"""Hardware-free regression tests for the additional SF8 laser."""

import copy
import os
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest
import laserlab
from laserlab.srp import connections
from laserlab.config import load_lasers, load_awg
from maiman_drivers.driver import MaimanDriver, SerialNumberMismatch
from maiman_drivers.mock import FakeMaimanSerial, MockSF8150T
from maiman_drivers.laser import ChainDependencyError, TecNotRunningError, AdjustableTecLaserStage


@pytest.fixture(autouse=True)
def fast_mock_transport():
    """Suppress only mock driver delays; never connect to physical hardware."""
    with patch("maiman_drivers.driver.time.sleep"), patch("laserlab.builtin.SF8150T", side_effect=AssertionError("Real hardware forbidden")):
        yield


@pytest.fixture
def definition():
    with (ROOT / "config.toml").open("rb") as stream:
        return tomllib.load(stream)["laser"][2]


@pytest.fixture
def simulated_boards(monkeypatch):
    """Use the real connection/serial-validation path with a fake transport."""
    boards = {}
    instances = []

    class RecordingSerial(FakeMaimanSerial):
        def __init__(self, serial_number):
            super().__init__(serial_number, True, 0.0001, 0.0001)
            self.writes = []
        def write(self, data):
            self.writes.append(data)
            return super().write(data)

    class TestDriver(MockSF8150T):
        def __init__(self, port, **kwargs):
            instances.append(self)
            super().__init__(port, serial_number=boards[port], **kwargs)
        def _open_transport(self):
            self.transport = RecordingSerial(self._mock_serial_number)
            return self.transport

    monkeypatch.setattr(connections, "SF8150T", TestDriver)
    boards.update({"COM21": 4985, "COM22": 65535})
    return boards, instances


def test_config_adds_third_laser_without_altering_existing_settings():
    with (ROOT / "config.toml").open("rb") as stream:
        current = tomllib.load(stream)
    assert current["laser"][0]["stage"][0]["port"] == "COM5"
    assert current["laser"][0]["stage"][0]["diode_current_a"] == 1.5
    assert current["laser"][1]["stage"][0]["max_current_a"] == 0.0
    lasers = load_lasers(force_mock=True)
    assert len(lasers) == 3
    seed, pump = lasers[2].chain.stages
    for stage, serial, limit in [(seed,4985,0.5),(pump,65535,1.5)]:
        assert isinstance(stage, AdjustableTecLaserStage)
        assert stage.driver.serial_number == serial
        assert stage.current_max_a == limit
        assert stage.get_current_setpoint() == 0
        assert stage.driver.get_tec_temperature_setpoint() == 25
        assert stage.tec_running()
        assert not stage.is_on
    assert lasers[2].apg is None and lasers[2].eom is None


@pytest.mark.parametrize("current", [-0.001, 0.5001, float("inf"), float("nan")])
def test_seed_rejects_invalid_currents(current):
    seed = load_lasers(force_mock=True)[2].chain.stages[0]
    with pytest.raises(ValueError):
        seed.set_current(current)
    assert seed.get_current_setpoint() == 0


def test_current_bounds_ordering_and_tec_guard():
    laser = load_lasers(force_mock=True)[2]
    seed, pump = laser.chain.stages
    seed.set_current(0.5)
    pump.set_current(1.5)
    assert seed.get_current_setpoint() == pytest.approx(0.5)
    assert pump.get_current_setpoint() == pytest.approx(1.5)
    with pytest.raises(ValueError):
        pump.set_current(1.5001)
    with pytest.raises(ChainDependencyError):
        laser.chain.enable(1)
    seed.driver.tec_stop()
    with pytest.raises(TecNotRunningError):
        laser.chain.enable(0)
    seed.driver.tec_start()
    laser.chain.enable(0)
    pump.driver.tec_stop()
    with pytest.raises(TecNotRunningError):
        laser.chain.enable(1)
    pump.driver.tec_start()
    laser.chain.enable(1)
    with pytest.raises(ChainDependencyError):
        laser.chain.disable(0)
    laser.chain.shutdown()
    assert laser.chain.state() == [False, False]


@pytest.mark.parametrize("ports,reserved", [(["", "COM22"],set()),(["COM21","COM21"],set()),(["com21","COM21"],set()),(["COM21","COM22"],{"com22"})])
def test_invalid_port_choices_never_open_drivers(definition, ports, reserved):
    with patch.object(connections, "SF8150T") as constructor:
        with pytest.raises(ValueError):
            connections.connect_laser(definition, ports, reserved)
        constructor.assert_not_called()


def test_both_identities_checked_before_control_writes(definition, simulated_boards):
    boards, instances = simulated_boards
    boards["COM22"] = 1234
    with pytest.warns(UserWarning), pytest.raises(SerialNumberMismatch):
        connections.connect_laser(definition,["COM21","COM22"])
    assert len(instances)==2
    for driver in instances:
        assert not driver.is_open
        assert not any(data.startswith(b"P") for data in driver.transport.writes)


def test_successful_connection_verifies_zero_and_25c(definition, simulated_boards):
    laser=connections.connect_laser(definition,["COM21","COM22"])
    for stage in laser.chain.stages:
        assert stage.get_current_setpoint()==0
        assert stage.driver.get_tec_temperature_setpoint()==25
        assert stage.tec_running()
        assert not stage.is_on
    laser.chain.enable_all()
    laser.chain.shutdown()
    assert laser.chain.state()==[False,False]


def test_tec_verification_failure_stops_and_closes(definition, simulated_boards, monkeypatch):
    boards, instances = simulated_boards
    driver_class=connections.SF8150T
    monkeypatch.setattr(driver_class,"get_tec_temperature_setpoint",lambda self: 30.0)
    with pytest.raises(RuntimeError,match="TEC setpoint"):
        connections.connect_laser(definition,["COM21","COM22"])
    assert all(not d.is_open and not d.is_running for d in instances)


def test_output_stop_verification_failure_closes(definition, simulated_boards, monkeypatch):
    boards, instances = simulated_boards
    monkeypatch.setattr(connections.SF8150T,"read_state",lambda self: {"started":True})
    with pytest.raises(RuntimeError,match="output did not stop"):
        connections.connect_laser(definition,["COM21","COM22"])
    assert all(not d.is_open for d in instances)


def test_hardware_connection_settings_reserve_existing_ports():
    definitions,reserved,path=connections.read_connection_settings(ROOT/"config.toml")
    assert len(definitions)==1 and definitions[0]["deferred_connect"]
    assert reserved=={"COM5","COM3","COM10","COM11","COM12"}
    assert path.name=="connections.json"


def test_saved_ports_are_preferences_not_autoconnection(tmp_path):
    path=tmp_path/"connections.json"
    connections.save_ports(path,"Additional laser (SF8)",["COM21","COM22"])
    assert connections.read_saved_ports(path,"Additional laser (SF8)")==["COM21","COM22"]
    path.write_text("corrupted",encoding="utf-8")
    assert connections.read_saved_ports(path,"Additional laser (SF8)")==[]


@pytest.fixture
def app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_gui_controls_and_awg_dependencies(app, tmp_path):
    from laserlab.srp.gui import CombinedWindow
    from maiman_drivers.gui import AdjustableTecStageWidget
    lasers=load_lasers(force_mock=True)
    awg=load_awg(force_mock=True)
    window=CombinedWindow(lasers,awg)
    window.resize(1450,1050)
    window.show()
    app.processEvents()
    seed,pump=window.panels[2].chain_widget.stage_widgets
    assert isinstance(seed,AdjustableTecStageWidget)
    assert seed.spin.maximum()==0.5 and pump.spin.maximum()==1.5
    assert not seed.button.isEnabled() and not pump.button.isEnabled()
    window.awg_panel.run_btn.click()
    assert seed.button.isEnabled() and not pump.button.isEnabled()
    seed.spin.setValue(0.2)
    pump.spin.setValue(0.7)
    assert lasers[2].chain.stages[0].get_current_setpoint()==pytest.approx(0.2)
    seed.button.click()
    assert pump.button.isEnabled() and not window.awg_panel.run_btn.isEnabled()
    pump.button.click()
    assert not seed.button.isEnabled()
    window.grab().save(str(tmp_path/"gui.png"))
    pump.button.click()
    seed.button.click()
    assert window.awg_panel.run_btn.isEnabled()
    window.close()
    assert all(not s.is_on for laser in lasers for s in laser.chain.stages)
    assert not awg.running


def test_manual_gui_connection_joins_awg_gate(app, definition, simulated_boards, tmp_path, monkeypatch):
    from laserlab.srp.gui import CombinedWindow
    import laserlab.srp.connection_widget as widget
    monkeypatch.setattr(widget,"available_ports",lambda: [("COM21","COM21 — seed USB"),("COM22","COM22 — pump USB")])
    window=CombinedWindow([],load_awg(force_mock=True),connection_settings=([definition],set(),tmp_path/"connections.json"))
    panel=window.connection_panels[0]
    panel.combos[0].setCurrentIndex(1)
    panel.combos[1].setCurrentIndex(2)
    panel.confirm.setChecked(True)
    panel.connect_btn.click()
    assert len(window.panels)==1
    assert panel.laser is not None
    assert panel.combos[0].isEnabled() is False
    seed,pump=window.panels[0].chain_widget.stage_widgets
    assert not seed.button.isEnabled()
    window.awg_panel.run_btn.click()
    assert seed.button.isEnabled()
    seed.button.click()
    pump.button.click()
    assert not window.awg_panel.run_btn.isEnabled()
    window.close()
    assert panel.laser.chain.state()==[False,False]
