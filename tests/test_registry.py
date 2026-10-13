"""Plugin registry: built-in types, custom registrations, widgets, config integration."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from srp_controller import registry
from srp_controller.config import ConfigError, _build_stage
from srp_controller.registry import Registry, UnknownComponentError


def test_builtin_types_registered():
    reg = registry.default_registry()
    assert reg.names("stage") == ["aerodiode", "maiman_notec", "maiman_tec"]
    assert reg.names("pulse_generator") == ["apg1780da"]
    assert reg.names("eom") == ["dual_eom"]
    assert reg.names("awg") == ["spectrum_m4i"]
    assert reg.names("chain") == ["laser_chain"]


def test_build_mock_components():
    chain = registry.build("chain", "laser_chain", {"name": "L", "stage": [
        {"type": "maiman_tec", "name": "Seed", "diode_current_a": 0.3},
        {"type": "maiman_notec", "name": "Amp"},
    ]}, mock=True)
    chain.initialize()
    assert [s.name for s in chain.stages] == ["Seed", "Amp"]
    eom = registry.build("eom", "dual_eom", {}, mock=True)
    eom.start()
    assert eom.running
    awg = registry.build("awg", "spectrum_m4i", {"num_pulses": 4}, mock=True)
    assert awg.settings.num_pulses == 4


def test_unknown_type_lists_available():
    with pytest.raises(UnknownComponentError, match="available: aerodiode"):
        registry.build("stage", "nope", {}, mock=True)
    with pytest.raises(ConfigError, match="stage 'X'"):
        _build_stage({"type": "nope", "name": "X"}, use_mock=True)


def test_custom_registry_and_duplicates():
    reg = Registry()
    reg.add("stage", "dummy", lambda params, mock: ("built", params["x"], mock))
    assert reg.build("stage", "dummy", {"x": 1}, mock=True) == ("built", 1, True)
    with pytest.raises(ValueError):
        reg.add("stage", "dummy", lambda p, m: None)
    reg.add("stage", "dummy", lambda p, m: "new", replace=True)
    assert reg.build("stage", "dummy") == "new"
    with pytest.raises(UnknownComponentError):
        reg.create_widget("stage", "dummy", None)


def test_plugin_types_usable_in_config(monkeypatch):
    reg = Registry()
    from srp_controller import builtin
    builtin.register(reg)
    seen = []
    reg.add("stage", "plugin_stage",
            lambda params, mock: seen.append(params["name"]) or
            reg.build("stage", "maiman_notec", params, mock))
    stage = _build_stage({"type": "plugin_stage", "name": "P"}, True, registry=reg)
    assert seen == ["P"] and stage.name == "P"


def test_entry_point_plugins_loaded(monkeypatch):
    class FakeEntryPoint:
        name, value = "fake", "fake:register"
        def load(self):
            return lambda reg: reg.add("stage", "from_plugin", lambda p, m: "ok")

    class BrokenEntryPoint(FakeEntryPoint):
        name = "broken"
        def load(self):
            raise ImportError("missing dependency")

    monkeypatch.setattr(registry, "entry_points",
                        lambda group: [FakeEntryPoint(), BrokenEntryPoint()])
    reg = Registry()
    with pytest.warns(RuntimeWarning, match="broken"):
        assert reg.load_entry_points() == ["fake"]
    assert reg.build("stage", "from_plugin") == "ok"


def test_widgets():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication, QWidget
    from eom_controller import EomChannel
    app = QApplication.instance() or QApplication([])
    apg = registry.build("pulse_generator", "apg1780da", {"port": "COM1"}, mock=True)
    eom = registry.build("eom", "dual_eom", {}, mock=True)
    chain = registry.build("chain", "laser_chain", {"stage": [{"type": "maiman_notec"}]}, mock=True)
    awg = registry.build("awg", "spectrum_m4i", {}, mock=True)
    widgets = [
        registry.create_widget("pulse_generator", "apg1780da", apg, title="APG"),
        registry.create_widget("eom", "dual_eom", EomChannel(eom, 1)),
        registry.create_widget("eom", "dual_eom", eom),
        registry.create_widget("chain", "laser_chain", chain),
        registry.create_widget("awg", "spectrum_m4i", awg),
    ]
    assert all(isinstance(w, QWidget) for w in widgets)
    for w in widgets:
        w.close()
    app.processEvents()
