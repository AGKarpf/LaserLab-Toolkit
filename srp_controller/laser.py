"""Laser container and legacy two-laser mock fixture.
A laser combines an ordered diode chain with optional APG and EOM components.
The integrated configuration may contain additional diode-only lasers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from maiman_drivers.laser import LaserChain, TecLaserStage, DcLaserStage
from maiman_drivers.mock import MockSF8150T, MockSF6015
from aerodiode_driver.mock import MockMmd
from apg1780da.mock import MockAPG1780DA

from eom_controller import EomChannel, MockDualEOMController, SharedEom


@dataclass
class Laser:
    """Diode chain with optional APG pulse generator and EOM channel."""

    name: str
    chain: LaserChain
    apg: Optional[object]          # APG1780DA / MockAPG1780DA / None
    eom: Optional[object]          # DualEOMController / MockDualEOMController / None

    def initialize(self) -> None:
        """Initialize the diode chain with TECs running and outputs disabled."""
        self.chain.initialize()

    def shutdown(self) -> None:
        """Stop the diode chain and the shared EOM control loop."""
        try:
            self.chain.shutdown()
        except Exception:
            pass
        if self.eom is not None:
            try:
                self.eom.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Legacy two-laser mock fixture (hardware-free)
# ---------------------------------------------------------------------------
def build_lasers_mock() -> List[Laser]:
    """Build the legacy two-laser fixture without hardware."""

    # One shared dual EOM controller: laser 1 = channel 0, laser 2 = channel 1.
    shared_eom = SharedEom(MockDualEOMController(board_idx=1))

    # -- Laser 1: TEC Maiman seed -> Aerodiode amplifier ---------------
    # Like the no-TEC Maiman, the Aerodiode provides
    # adjustable current (DcLaserStage) with a configured maximum.
    aero = MockMmd("MOCK-L1-AERO", address=1)
    aero.set_max_current(10.0)
    aero.tec_start()
    laser1 = Laser(
        name="Laser 1",
        chain=LaserChain("Laser 1 - Chain", [
            TecLaserStage(
                "Seed (Maiman TEC)",
                MockSF8150T("MOCK-L1-MAIMAN", serial_number=0x1001),
                diode_current_a=0.3),
            DcLaserStage(
                "Amplifier (Aerodiode)",
                aero, initial_current_a=0.0),
        ]),
        apg=MockAPG1780DA("MOCK-L1-APG"),
        eom=EomChannel(shared_eom, 0),
    )

    # -- Laser 2: one no-TEC Maiman -----------------------------------------
    laser2 = Laser(
        name="Laser 2",
        chain=LaserChain("Laser 2 - Chain", [
            DcLaserStage("Stage 1 (Maiman No-TEC)",
                         MockSF6015("MOCK-L2-D", serial_number=0x2004),
                         initial_current_a=0.0),
        ]),
        apg=MockAPG1780DA("MOCK-L2-APG"),
        eom=EomChannel(shared_eom, 1),
    )

    lasers = [laser1, laser2]
    for laser in lasers:
        laser.initialize()
    return lasers
