"""Global PyQt6 panel for the SRP AWG sequence.
Adjust pump and Stokes sample offsets, pulse rate, decay duration and probe enable.
Edits regenerate the preview and update a running card without restarting it."""

from __future__ import annotations

from typing import Optional

import numpy as np

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QVBoxLayout,
)

from .awg import PULSE_RATE_OPTIONS_HZ

_RUN_ON_STYLE = "background-color:#2e7d32;color:white;font-weight:bold;"
_RUN_OFF_STYLE = "background-color:#b71c1c;color:white;font-weight:bold;"

# Optional matplotlib preview; disabled if matplotlib is unavailable.
try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    _HAVE_MPL = True
except Exception:                                     # pragma: no cover
    _HAVE_MPL = False

_OK_STYLE = "color:#86efac;font-weight:bold;"
_ERR_STYLE = "color:#fca5a5;font-weight:bold;"

# Distinct channel colors
_TRACES = [
    ("pump", "Pump", "#1f77b4"),
    ("stokes", "Stokes", "#d62728"),
    ("probe", "Probe", "#2ca02c"),
    ("trigger", "Trigger", "#7f7f7f"),
]


if _HAVE_MPL:
    class WaveformPreview(FigureCanvasQTAgg):
        """Preview the SRP sequence around the train-to-decay transition."""

        def __init__(self) -> None:
            self._fig = Figure(figsize=(5.2, 2.6), layout="constrained")
            super().__init__(self._fig)
            self.ax = self._fig.add_subplot(111)
            self.setMinimumHeight(200)

        def update_plot(self, data: dict, info: dict) -> None:
            ax = self.ax
            ax.clear()
            length = int(data["pump"].size)
            spp = max(1, int(info["sample_per_pulse"]))
            train_end = int(info["train_samples"])

            # Window around the transition: a few pulses before and after.
            start = max(0, train_end - 4 * spp)
            end = min(length, train_end + 6 * spp)
            if end - start < 2:
                start, end = 0, length
            x = np.arange(start, end)

            for k, (key, label, color) in enumerate(_TRACES):
                sig = np.asarray(data[key])[start:end].astype(float)
                offset = (len(_TRACES) - 1 - k) * 1.4
                ax.plot(x, sig * 0.85 + offset, drawstyle="steps-post",
                        color=color, linewidth=1.3)

            if start <= train_end <= end:
                ax.axvline(train_end, color="black", linestyle="--", linewidth=0.9)
                ax.annotate(" Decay start", xy=(train_end, 0.02),
                            xycoords=("data", "axes fraction"),
                            fontsize=7, va="bottom", ha="left")

            ax.set_yticks([(len(_TRACES) - 1 - k) * 1.4 + 0.4
                           for k in range(len(_TRACES))])
            ax.set_yticklabels([label for _key, label, _c in _TRACES], fontsize=8)
            ax.set_xlabel("Sample", fontsize=8)
            ax.tick_params(labelsize=7)
            ax.set_title(f"SRP sequence (train-to-decay transition), total "
                         f"{info['total_samples']} Samples", fontsize=8)
            ax.margins(x=0)
            self.draw_idle()


class AwgPanel(QGroupBox):
    """Controls for the SRP AWG controller."""

    def __init__(self, controller: Optional[object],
                 title: str = "AWG - SRP sequence") -> None:
        super().__init__(title)
        self.controller = controller
        #: Callback notifying the GUI of start/stop (assigned by the window).
        self.on_running_changed = None

        layout = QVBoxLayout(self)

        # -- Start/stop button -----------------------------------------
        self.run_btn = QPushButton("Start AWG")
        self.run_btn.setMinimumHeight(40)
        layout.addWidget(self.run_btn)

        # -- Pulse rate (pump/Stokes) [dropdown] ------------------------
        rate_row = QHBoxLayout()
        rate_row.addWidget(QLabel("Pulse rate:"))
        self.rate_combo = QComboBox()
        for hz in PULSE_RATE_OPTIONS_HZ:
            self.rate_combo.addItem(f"{hz / 1e6:g} MHz", hz)
        rate_row.addWidget(self.rate_combo)
        rate_row.addStretch(1)
        layout.addLayout(rate_row)

        # -- Pump phase [samples] -------------------------------------
        layout.addLayout(self._spin_row(
            "Pump phase:", "pump_spin", QSpinBox, -1_000_000, 1_000_000,
            suffix=" Samples"))
        # -- Stokes phase [samples] -----------------------------------
        layout.addLayout(self._spin_row(
            "Stokes phase:", "stokes_spin", QSpinBox, -1_000_000, 1_000_000,
            suffix=" Samples"))
        # -- Decay time [us] ------------------------------------------
        layout.addLayout(self._spin_row(
            "Decay time:", "decay_spin", QDoubleSpinBox, 0.0, 1_000_000.0,
            suffix=" us", decimals=2, step=1.0))

        # -- Probe ON/OFF ---------------------------------------------
        self.probe_check = QCheckBox("Probe laser enabled (analog output)")
        layout.addWidget(self.probe_check)

        # -- Status ---------------------------------------------------
        self.status = QLabel("-")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        # -- Waveform preview ----------------------------------------
        self.preview = WaveformPreview() if _HAVE_MPL else None
        if self.preview is not None:
            layout.addWidget(self.preview)

        if self.controller is None:
            self._set_disconnected()
            return

        # Load initial settings before connecting signals.
        s = self.controller.settings
        idx = self.rate_combo.findData(int(s.pulse_frequency_hz))
        self.rate_combo.setCurrentIndex(idx if idx >= 0 else
                                        self.rate_combo.count() - 1)
        self.pump_spin.setValue(int(s.pump_phase_samples))
        self.stokes_spin.setValue(int(s.stokes_phase_samples))
        self.decay_spin.setValue(s.decay_time_s * 1e6)   # s -> us
        self.probe_check.setChecked(bool(s.probe_enabled))

        # Connect signals; compute the preview without starting output.
        self.run_btn.clicked.connect(self._toggle_run)
        self.rate_combo.currentIndexChanged.connect(self._apply)
        self.pump_spin.valueChanged.connect(self._apply)
        self.stokes_spin.valueChanged.connect(self._apply)
        self.decay_spin.valueChanged.connect(self._apply)
        self.probe_check.stateChanged.connect(self._apply)
        self._apply()
        self._refresh_run_button()

    # ------------------------------------------------------------------ #
    # Start/Stop
    # ------------------------------------------------------------------ #
    def is_running(self) -> bool:
        return bool(self.controller is not None and self.controller.running)

    def set_toggle_enabled(self, enabled: bool) -> None:
        """Enable or lock the AWG switch; stopping is locked while a laser is ON."""
        if self.controller is not None:
            self.run_btn.setEnabled(enabled)

    def _toggle_run(self) -> None:
        try:
            if self.controller.running:
                self.controller.stop()
            else:
                self.controller.start()
        except Exception as exc:                      # pragma: no cover
            self.status.setText(f"⚠ AWG error: {exc}")
            self.status.setStyleSheet(_ERR_STYLE)
            return
        self._refresh_run_button()
        self._apply()                                 # update status and preview
        if self.on_running_changed is not None:
            self.on_running_changed()

    def _refresh_run_button(self) -> None:
        on = self.is_running()
        self.run_btn.setText("Stop AWG" if on else "Start AWG")
        self.run_btn.setStyleSheet(_RUN_ON_STYLE if on else _RUN_OFF_STYLE)

    # ------------------------------------------------------------------ #
    def _spin_row(self, label, attr, cls, lo, hi, suffix="",
                  decimals=None, step=None):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        spin = cls()
        spin.setRange(lo, hi)
        if decimals is not None:
            spin.setDecimals(decimals)
        if step is not None:
            spin.setSingleStep(step)
        if suffix:
            spin.setSuffix(suffix)
        spin.setKeyboardTracking(False)
        setattr(self, attr, spin)
        row.addWidget(spin)
        row.addStretch(1)
        return row

    def _set_disconnected(self) -> None:
        for w in (self.run_btn, self.rate_combo, self.pump_spin,
                  self.stokes_spin, self.decay_spin, self.probe_check):
            w.setEnabled(False)
        self.status.setText("⚠ AWG disconnected")
        self.status.setStyleSheet(_ERR_STYLE)
        self.setStyleSheet("QGroupBox { border:1px solid #b71c1c; }")

    def _apply(self, *args) -> None:
        c = self.controller
        c.settings.pulse_frequency_hz = int(self.rate_combo.currentData())
        c.set_pump_phase(self.pump_spin.value())
        c.set_stokes_phase(self.stokes_spin.value())
        c.set_decay_time(self.decay_spin.value() * 1e-6)   # us -> s
        c.set_probe_enabled(self.probe_check.isChecked())
        try:
            data = c.apply()
        except Exception as exc:                      # pragma: no cover
            self.status.setText(f"⚠ AWG error: {exc}")
            self.status.setStyleSheet(_ERR_STYLE)
            return
        info = c.info()
        mode = "simulated" if c.use_mock else "card"
        rate_mhz = c.settings.pulse_frequency_hz / 1e6
        state = "running" if c.running else "stopped (preview)"
        mark = "●" if c.running else "○"
        self.status.setText(
            f"{mark} {state} ({mode}) | Pulse rate {rate_mhz:g} MHz | "
            f"Pulse {info['sample_per_pulse']} S | Pump-Shift {info['pump_shift']} S | "
            f"Train {info['train_samples']} S | Decay {info['decay_samples']} S | "
            f"total {info['total_samples']} S")
        self.status.setStyleSheet(_OK_STYLE if c.running else "")
        if self.preview is not None:
            self.preview.update_plot(data, info)
