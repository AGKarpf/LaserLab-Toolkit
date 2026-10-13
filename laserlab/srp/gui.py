"""Combined PyQt6 GUI for configured lasers and a shared AWG.
Each panel contains an ordered diode chain and optional APG/EOM widgets.
Deferred SF8 drivers have explicit COM-port selection and serial verification.
AWG gating and chain dependencies control output switches."""

from __future__ import annotations

from typing import Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QGroupBox, QHBoxLayout, QMessageBox, QScrollArea,
    QVBoxLayout, QWidget,
)
from PyQt6.QtWidgets import QLayout, QSizePolicy, QLabel

from maiman_drivers.gui import LaserChainWidget
from apg1780da.gui import ApgDeviceWidget

from eom_controller.gui import EomWidget

from ..laser import Laser
from .awg_widget import AwgPanel
from .connection_widget import ConnectionPanel


class GatedLaserChainWidget(LaserChainWidget):
    """Laser chain with an additional global output-switch gate.
    on_change notifies the main window to synchronize AWG coupling."""

    def __init__(self, chain, on_change=None) -> None:
        self._gate = True
        self.on_change = on_change
        super().__init__(chain)          # calls self.refresh() at the end

    def set_gate(self, enabled: bool) -> None:
        self._gate = enabled
        self.refresh()

    def refresh(self) -> None:
        for i, w in enumerate(self.stage_widgets):
            try:
                if self._gate:
                    w.refresh(self.chain.can_enable(i), self.chain.can_disable(i))
                else:
                    w.refresh(False, False)   # globally locked -> disable button
            except Exception as exc:          # device / communication error
                # Keep the GUI running and mark the failed stage.
                w.button.setEnabled(False)
                w.button.setText("Error")
                w.button.setStyleSheet(
                    "background-color:#b71c1c;color:white;font-weight:bold;")
                w.status.setText(f"⚠ Communication error: {exc}")

    def request_toggle(self, index: int) -> None:
        try:
            super().request_toggle(index)
        except Exception as exc:              # e.g. MaimanError E0001 from is_on
            QMessageBox.warning(self, "Device error",
                                f"Action failed:\n{exc}")
            self.refresh()
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass


class LaserPanel(QGroupBox):
    """Diode chain with optional APG and EOM controls for one laser."""

    def __init__(self, laser: Laser) -> None:
        super().__init__(laser.name)
        self.laser = laser

        layout = QVBoxLayout(self)

        # Top-to-bottom order:
        # 1) APG pulse generator
        self.apg_widget = None
        if laser.apg is not None:
            self.apg_widget = ApgDeviceWidget(
                laser.apg, title="APG1780DA pulse generator")
            layout.addWidget(self.apg_widget)

        # 2) Seed indicator from the assigned EOM channel
        eom_title = "Seed indicator (EOM)"
        if laser.eom is not None:
            eom_title = f"Seed indicator - EOM {laser.eom.index + 1} (channel {laser.eom.index})"
        self.eom_widget = None
        if laser.eom is not None:
            self.eom_widget = EomWidget(laser.eom, title=eom_title)
            layout.addWidget(self.eom_widget)

        # 3) Ordered diode chain with AWG output-switch gating
        self.chain_widget = GatedLaserChainWidget(laser.chain)
        layout.addWidget(self.chain_widget)

        layout.addStretch(1)


class CombinedWindow(QWidget):
    """Main window: laser panels arranged horizontally in a scroll area."""

    def __init__(self, lasers: Sequence[Laser],
                 awg: object = None,
                 title: str = "SRP Controller", connection_settings=None,
                 show_awg: bool | None = None) -> None:
        """show_awg: show the AWG panel; default only when an AWG is given.
        Pass True to show a configured AWG that failed to start (error state)."""
        super().__init__()
        self.setWindowTitle(title)

        outer = QVBoxLayout(self)

        # Global AWG panel (pump/Stokes phase, decay, probe), only with an AWG.
        if show_awg is None:
            show_awg = awg is not None
        self.awg_panel = AwgPanel(awg) if show_awg else None
        if self.awg_panel is not None:
            outer.addWidget(self.awg_panel)

        # Scrollable laser panels below.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, stretch=1)

        content = QWidget()
        row = QHBoxLayout(content)
        row.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        row.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        content.setMinimumWidth(0)
        content.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )

        def add_laser_column(widget):
            # Allow the column and its nested boxes to shrink.
            boxes = [widget, *widget.findChildren(QGroupBox)]

            for box in boxes:
                if box.layout() is not None:
                    box.layout().setSizeConstraint(
                        QLayout.SizeConstraint.SetNoConstraint
                    )

                box.setMinimumWidth(0)
                box.setSizePolicy(
                    QSizePolicy.Policy.Ignored,
                    QSizePolicy.Policy.Preferred,
                )

            # Wrap long status messages instead of widening the column.
            for label in widget.findChildren(QLabel):
                label.setWordWrap(True)

            # Equal weight for every laser column.
            row.addWidget(widget, 1)






        self.panels = [LaserPanel(laser) for laser in lasers]
        for panel in self.panels:
            if panel.laser.apg is None and panel.laser.eom is None:
                add_laser_column(panel.chain_widget)
            else:
                add_laser_column(panel)
        self.connection_panels = []
        if connection_settings is not None:
            definitions, reserved, settings_path = connection_settings
            for definition in definitions:
                panel = ConnectionPanel(definition, reserved, settings_path,
                                        self._add_connected_laser)
                self.connection_panels.append(panel)
                add_laser_column(panel)
        scroll.setWidget(content)

        # --- AWG <-> laser coupling ---------------------------------------
        # AWG OFF -> lasers OFF and output switches locked.
        # Laser ON -> AWG stop locked (AWG must keep running).
        if self.awg_panel is not None:
            self.awg_panel.on_running_changed = self._sync_awg_coupling
        for panel in self.panels:
            panel.chain_widget.on_change = self._sync_awg_coupling
        self._sync_awg_coupling()

        self.resize(950, 800)

    def _add_connected_laser(self, laser):
        panel = LaserPanel(laser)
        panel.chain_widget.on_change = self._sync_awg_coupling
        self.panels.append(panel)
        self._sync_awg_coupling()
        if laser.apg is None and laser.eom is None:
            return panel.chain_widget
        return panel

    def closeEvent(self, event):
        """Stop diode outputs before closing serial ports and the AWG."""
        errors = []
        for panel in self.panels:
            try:
                panel.laser.chain.shutdown()
            except Exception as exc:
                errors.append(f"{panel.laser.name}: {exc}")
        if errors:
            QMessageBox.warning(self, "Shutdown failed", "\n".join(errors))
            event.ignore()
            return
        for panel in self.panels:
            panel.laser.shutdown()
            for stage in panel.laser.chain.stages:
                stage.driver.close()
            if panel.laser.apg is not None:
                panel.laser.apg.close()
        if self.awg_panel is not None and self.awg_panel.controller is not None:
            self.awg_panel.controller.close()
        event.accept()

    def _sync_awg_coupling(self) -> None:
        if self.awg_panel is None or self.awg_panel.controller is None:
            return                                    # no coupling without an AWG
        running = self.awg_panel.is_running()
        for panel in self.panels:
            if not running:
                try:
                    panel.laser.chain.shutdown()      # lasers OFF when AWG is OFF
                except Exception:
                    pass
            panel.chain_widget.set_gate(running)      # lock / unlock output switches
        try:
            any_laser_on = any(stage.is_on for panel in self.panels
                               for stage in panel.laser.chain.stages)
        except Exception:
            any_laser_on = True                       # lock AWG stop if state is uncertain
        # Allow stopping the AWG only when all lasers are OFF.
        self.awg_panel.set_toggle_enabled((not running) or (not any_laser_on))


def run(lasers: Sequence[Laser], awg: object = None,
        title: str = "SRP Controller", connection_settings=None,
        show_awg: bool | None = None) -> int:
    """Start the Qt application with the supplied devices or chains."""
    app = QApplication.instance() or QApplication([])
    from PyQt6.QtGui import QFont

    app.setStyle("Fusion")  # Consistent appearance across Windows themes
    app.setFont(QFont("Segoe UI", 12))  # Font size in points: default 11

    app.setStyleSheet("""
        QWidget {
            background-color: #202833;
            color: #F1F5F9;
        }

        QGroupBox {
            border: 1px solid #475569;
            border-radius: 6px;
            margin-top: 14px;
            padding-top: 10px;
        }

        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
        }

        QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {
            background-color: #303D4D;
            color: #F1F5F9;
            border: 1px solid #64748B;
            border-radius: 4px;
            padding: 4px;
            selection-background-color: #2563EB;
        }

        QPushButton {
            background-color: #36465A;
            color: #F1F5F9;
            border: 1px solid #64748B;
            border-radius: 4px;
            padding: 6px;
        }

        QPushButton:hover {
            background-color: #465B75;
        }

        QWidget:disabled {
            color: #94A3B8;
        }

        QComboBox QAbstractItemView {
            background-color: #303D4D;
            color: #F1F5F9;
            selection-background-color: #2563EB;
        }

        QSpinBox::up-button, QDoubleSpinBox::up-button {
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 20px;
        background-color: #64748B;
        border: 1px solid #94A3B8;
        }

        QSpinBox::down-button, QDoubleSpinBox::down-button {
            subcontrol-origin: border;
            subcontrol-position: bottom right;
            width: 20px;
            background-color: #64748B;
            border: 1px solid #94A3B8;
        }

        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
            width: 10px;
            height: 10px;
        }
    """)


    from pathlib import Path
    import tempfile

    # Create white arrow icons automatically.
    icon_dir = Path(tempfile.gettempdir()) / "srp_gui_icons"
    icon_dir.mkdir(exist_ok=True)

    up_icon = icon_dir / "up.svg"
    down_icon = icon_dir / "down.svg"

    up_icon.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12">'
        '<path d="M2 9 L6 3 L10 9 Z" fill="#FFFFFF"/></svg>',
        encoding="utf-8",
    )
    down_icon.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12">'
        '<path d="M2 3 L6 9 L10 3 Z" fill="#FFFFFF"/></svg>',
        encoding="utf-8",
    )

    arrow_style = """
        QSpinBox, QDoubleSpinBox {
            padding-right: 24px;
        }

        QSpinBox::up-button, QDoubleSpinBox::up-button {
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 22px;
            background-color: #465B75;
            border: 1px solid #64748B;
        }

        QSpinBox::down-button, QDoubleSpinBox::down-button {
            subcontrol-origin: border;
            subcontrol-position: bottom right;
            width: 22px;
            background-color: #465B75;
            border: 1px solid #64748B;
        }

        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
            image: url("UP_ICON");
            width: 12px;
            height: 12px;
        }

        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
            image: url("DOWN_ICON");
            width: 12px;
            height: 12px;
        }
    """

    arrow_style = arrow_style.replace("UP_ICON", up_icon.as_posix())
    arrow_style = arrow_style.replace("DOWN_ICON", down_icon.as_posix())
    app.setStyleSheet(app.styleSheet() + arrow_style)

    window = CombinedWindow(lasers, awg, title, connection_settings, show_awg)
    window.show()
    return app.exec()
