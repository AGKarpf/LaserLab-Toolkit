"""Build your own GUI from srp_controller components (simulated devices).
Run: py examples/custom_gui.py   (drop mock=True / use real ports for hardware)

Every device type and its widget is looked up by name in the plugin registry,
so new types from installed plugins work the same way."""

import sys

import srp_controller            # import before PyQt6 (EOM DLL / Qt runtime order)
from srp_controller import registry

from PyQt6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget

MOCK = True


def main() -> int:
    print("Available component types:")
    for t in registry.default_registry().types():
        print(f"  {t.kind:16s} {t.name:14s} {t.description}")

    # Devices, built from the same parameter tables as config.toml.
    chain = registry.build("chain", "laser_chain", {
        "name": "My laser",
        "stage": [
            {"type": "maiman_tec", "name": "Seed", "port": "COM5",
             "current_max_a": 0.5, "initial_current_a": 0.0, "adjustable_current": True},
            {"type": "aerodiode", "name": "Amplifier", "port": "COM3", "max_current_a": 5.5},
        ],
    }, mock=MOCK)
    chain.initialize()
    apg = registry.build("pulse_generator", "apg1780da", {"port": "COM10"}, mock=MOCK)
    eom = registry.build("eom", "dual_eom", {"ini": "eom.ini"}, mock=MOCK)

    app = QApplication.instance() or QApplication(sys.argv)
    window = QWidget()
    window.setWindowTitle("Custom laser GUI")
    row = QHBoxLayout(window)
    left = QVBoxLayout()
    left.addWidget(registry.create_widget("pulse_generator", "apg1780da", apg,
                                          title="Pulse generator"))
    from eom_controller import EomChannel
    left.addWidget(registry.create_widget("eom", "dual_eom", EomChannel(eom, 0),
                                          title="Seed indicator (EOM 1)"))
    row.addLayout(left)
    row.addWidget(registry.create_widget("chain", "laser_chain", chain))
    window.show()
    code = app.exec()
    chain.shutdown()
    eom.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
