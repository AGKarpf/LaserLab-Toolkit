"""Manual USB port selection and connection for an additional SF8 laser."""

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout,
)

from .connections import available_ports, connect_laser, read_saved_ports, save_ports


class ConnectionPanel(QGroupBox):
    """Keep a new laser disconnected until its ports are explicitly selected."""

    def __init__(self, definition, reserved_ports, settings_path, on_connected):
        super().__init__(definition["name"])
        self.definition = definition
        self.reserved_ports = reserved_ports
        self.settings_path = settings_path
        self.on_connected = on_connected
        self.laser = None
        self.body = QVBoxLayout(self)
        text = QLabel(
            "Select the USB COM port for each SF8 driver. Outputs start OFF at 0 A.\n"
            "Serial 65535 also appears in the existing setup: identify the pump's USB cable."
        )
        text.setWordWrap(True)
        self.body.addWidget(text)
        self.combos = []
        for stage in definition["stage"]:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{stage['name']} — serial {stage['serial']}"))
            combo = QComboBox()
            combo.setMinimumWidth(230)
            row.addWidget(combo)
            self.combos.append(combo)
            self.body.addLayout(row)
        self.refresh_btn = QPushButton("Refresh COM ports")
        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.body.addWidget(self.refresh_btn)
        self.confirm = QCheckBox("I have identified the seed and pump USB connections")
        self.body.addWidget(self.confirm)
        self.connect_btn = QPushButton("Connect seed and pump")
        self.connect_btn.clicked.connect(self.connect_devices)
        self.body.addWidget(self.connect_btn)
        self.status = QLabel("Disconnected — select ports to connect")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)
        self.refresh_ports(read_saved_ports(settings_path, definition["name"]))

    def refresh_ports(self, saved=None):
        """Refresh OS port metadata while retaining available selections."""
        if not isinstance(saved, list):
            saved = [c.currentData() for c in self.combos]
        try:
            ports = available_ports()
        except Exception as exc:
            self.status.setText(f"Cannot list COM ports: {exc}")
            return
        for i, combo in enumerate(self.combos):
            combo.clear()
            combo.addItem("Select COM port…", "")
            for port, description in ports:
                if port.upper() not in self.reserved_ports:
                    combo.addItem(description, port)
            if i < len(saved):
                idx = combo.findData(saved[i])
                if idx >= 0:
                    combo.setCurrentIndex(idx)
        self.confirm.setChecked(False)

    def connect_devices(self):
        """Connect verified boards; keep the existing panels usable on failure."""
        if not self.confirm.isChecked():
            self.status.setText("Identify both USB connections and tick the checkbox first.")
            return
        ports = [c.currentData() for c in self.combos]
        self.connect_btn.setEnabled(False)
        self.status.setText("Checking serial numbers and initializing…")
        try:
            laser = connect_laser(self.definition, ports, self.reserved_ports)
        except Exception as exc:
            self.status.setText(f"Connection failed: {exc}")
            QMessageBox.warning(self, "Connection failed", str(exc))
            self.connect_btn.setEnabled(True)
            return
        self.laser = laser
        self.reserved_ports.update(p.upper() for p in ports)
        for control in [*self.combos, self.refresh_btn, self.confirm]:
            control.setEnabled(False)
        self.connect_btn.setText("Connected")
        targets = ", ".join(f"{s.tec_temperature_c:g}" for s in laser.chain.stages)
        self.status.setText(f"Connected — outputs OFF, currents 0 A, TEC targets {targets} °C")
        try:
            save_ports(self.settings_path, self.definition["name"], ports)
        except OSError as exc:
            self.status.setText(f"Connected; could not save port selections: {exc}")
        self.body.addWidget(self.on_connected(laser))
