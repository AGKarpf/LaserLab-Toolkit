"""The AWG panel is optional: hidden without [awg], shown (with gating) otherwise."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from laserlab.config import load_awg, load_lasers

CONFIG_WITHOUT_AWG = """
[general]
use_mock = true

[[laser]]
name = "Test laser"
  [[laser.stage]]
  type = "maiman_notec"
  name = "Stage 1"
  max_current_a = 1.0
"""


@pytest.fixture
def app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_no_awg_section_hides_panel(app, tmp_path):
    from laserlab.srp.gui import CombinedWindow
    config = tmp_path / "config.toml"
    config.write_text(CONFIG_WITHOUT_AWG, encoding="utf-8")
    assert load_awg(config) is None
    lasers = load_lasers(config)
    window = CombinedWindow(lasers, None)
    assert window.awg_panel is None
    (stage_widget,) = window.panels[0].chain_widget.stage_widgets
    assert stage_widget.button.isEnabled()            # no AWG gate
    stage_widget.button.click()
    assert lasers[0].chain.stages[0].is_on
    window.close()
    assert not lasers[0].chain.stages[0].is_on        # shutdown on close


def test_configured_but_failed_awg_still_shown(app):
    from laserlab.srp.gui import CombinedWindow
    window = CombinedWindow([], None, show_awg=True)
    assert window.awg_panel is not None and window.awg_panel.controller is None
    window.close()


def test_mock_awg_panel_and_gate(app, tmp_path):
    from laserlab.srp.gui import CombinedWindow
    config = tmp_path / "config.toml"
    config.write_text(CONFIG_WITHOUT_AWG + "\n[awg]\nnum_pulses = 4\n", encoding="utf-8")
    lasers = load_lasers(config)
    window = CombinedWindow(lasers, load_awg(config))
    (stage_widget,) = window.panels[0].chain_widget.stage_widgets
    assert window.awg_panel is not None
    assert not stage_widget.button.isEnabled()        # locked until AWG runs
    window.awg_panel.run_btn.click()
    assert stage_widget.button.isEnabled()
    window.close()
