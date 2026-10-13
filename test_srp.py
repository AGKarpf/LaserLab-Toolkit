"""Hardware-free SRP regression checks. Run: py test_srp.py.
Verify the legacy two-laser fixture, configuration loading, chain dependencies,
TEC protection, APG controls, shared EOM behavior and AWG waveform updates.
Additional SF8 connection/GUI cases are covered in tests/test_sf8.py."""

import laserlab  # noqa: F401

from maiman_drivers.laser import (
    TecLaserStage, DcLaserStage, ChainDependencyError, TecNotRunningError,
)
from laserlab.laser import build_lasers_mock


_passed = 0


def check(label, cond):
    global _passed
    if not cond:
        raise AssertionError(f"FAILED: {label}")
    _passed += 1
    print(f"  [ok] {label}")


def expect_raises(label, exc_type, func):
    try:
        func()
    except exc_type:
        check(label, True)
    else:
        raise AssertionError(f"FAILED (no exception): {label}")


def test_structure(lasers):
    print("\n== Build the two-laser fixture ==")
    check("two lasers built", len(lasers) == 2)
    l1, l2 = lasers

    # Laser 1: TEC Maiman plus Aerodiode (adjustable current)
    check("Laser 1 has 2 stages", len(l1.chain) == 2)
    check("Laser 1 Stage 0 = TEC", isinstance(l1.chain.stages[0], TecLaserStage))
    check("Laser 1 Stage 1 = Aerodiode (DcLaserStage, adjustable current)",
          isinstance(l1.chain.stages[1], DcLaserStage))
    aero = l1.chain.stages[1]
    check("Aerodiode Maximum current = 10.0 A (from configuration)",
          abs(aero.current_max_a - 10.0) < 1e-6)
    aero.set_current(4.2)
    check("Aerodiode current adjustable (4.2 A)",
          abs(aero.get_current_setpoint() - 4.2) < 1e-2)

    # Laser 2: one no-TEC driver
    check("Laser 2 has 1 stage", len(l2.chain) == 1)
    check("Laser 2: only Stage = No-TEC (DcLaserStage)",
          isinstance(l2.chain.stages[0], DcLaserStage))

    # Both fixture lasers have APG and EOM
    for laser in lasers:
        check(f"{laser.name}: APG present", laser.apg is not None)
        check(f"{laser.name}: Dual-EOM present", laser.eom is not None)


def test_initialization(lasers):
    print("\n== Initialization ==")
    for laser in lasers:
        for stage in laser.chain.stages:
            check(f"{laser.name}/{stage.name}: Output OFF",
                  stage.is_on is False)
            if isinstance(stage, TecLaserStage):
                check(f"{laser.name}/{stage.name}: TEC running",
                      stage.tec_running() is True)


def test_chain_logic(lasers):
    # Verify ordering dependencies with an independent synthetic
    # four-stage chain, separate from the configured laser structure.
    print("\n== Ordering dependency (synthetic four-stage chain) ==")
    from maiman_drivers.laser import LaserChain
    from maiman_drivers.mock import MockSF8150T
    chain = LaserChain("Test chain", [
        TecLaserStage(f"S{i}", MockSF8150T(f"MOCK-T{i}", serial_number=0x3000 + i), 0.4)
        for i in range(4)
    ])
    chain.initialize()
    n = len(chain)
    for i in range(1, n):
        expect_raises(f"enable({i}) from initial state -> Error",
                      ChainDependencyError, lambda i=i: chain.enable(i))
    for i in range(n):
        chain.enable(i)
        check(f"Stage {i} ON", chain.stages[i].is_on)
    for i in range(n - 1):
        expect_raises(f"disable({i}) with successor enabled -> Error",
                      ChainDependencyError, lambda i=i: chain.disable(i))
    chain.shutdown()
    check("Chain fully OFF", not any(chain.state()))


def test_tec_protection(lasers):
    print("\n== TEC protection (Laser 1) ==")
    seed = lasers[0].chain.stages[0]
    seed.driver.tec_stop()
    expect_raises("Diode ON with TEC stopped -> TecNotRunningError",
                  TecNotRunningError, lambda: lasers[0].chain.enable(0))
    seed.driver.tec_start()
    lasers[0].chain.enable(0)
    check("after TEC restart: Diode ON", seed.is_on)
    lasers[0].chain.disable(0)


def test_apg(lasers):
    print("\n== APG-pulse generator ==")
    apg = lasers[0].apg
    check("APG in USB mode", apg.get_mode() == "usb")
    apg.set_pulse_len(20); check("Pulse length set", apg.get_pulse_len() == 20)
    apg.set_trig_level(1500); check("Trigger level set",
                                    apg.get_trig_level() == 1500)


def test_eom(lasers):
    print("\n== Shared dual EOM controller (one controller, two channels) ==")
    ch1 = lasers[0].eom
    ch2 = lasers[1].eom
    # One shared controller for both assigned lasers
    check("both lasers share one controller", ch1.shared is ch2.shared)
    check("Laser 1 = Channel 0 (EOM 1)", ch1.index == 0)
    check("Laser 2 = Channel 1 (EOM 2)", ch2.index == 1)

    # Starting one channel starts the shared loop for both channels
    ch1.start()
    check("after start (via laser 1): Laser 1 running", ch1.is_running is True)
    check("after start (via laser 1): Laser 2 also running",
          ch2.is_running is True)
    d1, b1, l1 = ch1.value()
    d2, b2, l2 = ch2.value()
    check("Channel 0 Seed-Flag = present", l1 is True)
    check("Channel 1 Seed-Flag is boolean (presence indicator)", isinstance(l2, bool))
    check("Channels return their own measurements", isinstance(d1, float) and isinstance(d2, float))

    # Stopping either channel stops both
    ch2.stop()
    check("after stop (via laser 2): Laser 1 OFF", ch1.is_running is False)
    check("after stop (via laser 2): Laser 2 OFF", ch2.is_running is False)


def test_chaining_reported():
    """Verify that the first stage of each existing laser can be enabled."""
    print("\n== Chain: enable the first stage of each laser ==")
    from laserlab.config import load_lasers
    from maiman_drivers.laser import TecLaserStage, DcLaserStage
    l1, l2 = load_lasers("config.toml", force_mock=True)[:2]

    # 1064 nm: first TEC stage must be ready after initialization
    check("1064: Stage 0 is TEC stage", isinstance(l1.chain.stages[0], TecLaserStage))
    check("1064: TEC running after initialization", l1.chain.stages[0].tec_running() is True)
    check("1064: can_enable(0) True", l1.chain.can_enable(0) is True)
    l1.chain.enable(0)
    check("1064: Stage 0 can be enabled", l1.chain.stages[0].is_on is True)

    # 1550 nm: the only no-TEC stage must report state and enable correctly
    check("1550: Stage 0 is No-TEC stage", isinstance(l2.chain.stages[0], DcLaserStage))
    check("1550: is_on readable without an error", l2.chain.stages[0].is_on is False)
    l2.chain.enable(0)
    check("1550: Stage 0 can be enabled", l2.chain.stages[0].is_on is True)


def test_tec_25c():
    """Verify TEC startup at connection and a 25 °C setpoint."""
    print("\n== TEC active at connection, 25 °C ==")
    from laserlab.config import load_lasers
    l1, _ = load_lasers("config.toml", force_mock=True)[:2]

    # 1064 nm stage 0: Maiman TEC
    tec = l1.chain.stages[0]
    check("1064/TEC: running after connection", tec.tec_running() is True)
    sp = tec.driver.get_tec_temperature_setpoint()
    check(f"1064/TEC: Temperature setpoint 25 C (got {sp:.2f})", abs(sp - 25.0) < 0.1)

    # 1064 nm stage 1: Aerodiode, also with TEC
    aero = l1.chain.stages[1]
    check("1064/Aerodiode: TEC running", aero.driver.read_tec_state()["started"] is True)
    t = aero.driver.get_tec_temperature()
    check(f"1064/Aerodiode: TEC at 25 °C (got {t:.2f})", abs(t - 25.0) < 0.5)


def test_awg():
    print("\n== AWG (SRP sequence, simulated) ==")
    import numpy as np
    from laserlab.config import load_awg
    awg = load_awg("config.toml", force_mock=True)
    check("AWG loaded from configuration", awg is not None and awg.use_mock)

    from laserlab.srp.awg import _pulse_train

    # Pump phase 0, Stokes phase 4 (constant offset across the buffer).
    awg.set_pump_phase(0)
    awg.set_stokes_phase(4)
    awg.set_probe_enabled(True)
    awg.settings.probe_pump_shift_samples = 1     # deterministic for this test
    data = awg.apply()
    info = awg.info()
    train_end = info["train_samples"]
    spp = info["sample_per_pulse"]
    length = data["pump"].size
    pump, stokes, probe = data["pump"], data["stokes"], data["probe"]
    base = _pulse_train(length, spp)

    check("Stokes continuously shifted by phase 4",
          np.array_equal(stokes, np.roll(base, 4)))
    check("Pump unshifted in main train (Phase 0)",
          np.array_equal(pump[:train_end], base[:train_end]))
    check("Pump shifted right 1 sample from probe onset",
          np.array_equal(pump[train_end:], np.roll(base, 1)[train_end:]))
    check("Probe active only during decay",
          probe[:train_end].sum() == 0 and probe[train_end:].sum() > 0)
    check("packed word int16, multiple of 32",
          data["fused"].dtype == np.int16 and data["fused"].size % 32 == 0)

    # Probe OFF -> no probe onset -> no additional pump shift.
    awg.set_probe_enabled(False)
    d2 = awg.apply()
    check("Probe OFF -> no probe activation", d2["probe"].sum() == 0)
    check("Probe OFF -> Pump continuously unshifted",
          np.array_equal(d2["pump"], base))

    test_awg_double_buffer()


def test_awg_double_buffer():
    """Verify waveform updates use inactive segments without restarting the card."""
    # Use the bundled register/function stubs, never a physical Spectrum card.
    import os
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent / "tests" / "stubs"))
    os.environ["SPECTRUM_AWG_BACKEND"] = "pyspcm"
    print("\n== AWG: Live update without restart (double buffer) ==")
    from laserlab.srp.awg import AwgController, AwgSettings

    class FakeCard:
        def __init__(self):
            self.log = []
        def load_sequence(self, segs, steps):
            self.log.append(("load_sequence", segs[0].index, steps[0].segment))
        def start(self, enable_trigger=True):
            self.log.append(("start", enable_trigger))
        def force_trigger(self):
            self.log.append(("force_trigger",))
        def write_segment(self, index, data):
            self.log.append(("write_segment", index))
        def write_step(self, step):
            self.log.append(("write_step", step.segment))
        def stop(self):
            self.log.append(("stop",))

    ctrl = AwgController(AwgSettings(), use_mock=True)   # without a physical card
    fake = FakeCard()
    ctrl.card = fake                                     # inject fake card

    ctrl.start()                                         # load and start
    ctrl.apply()                                         # Update -> Segment 1
    ctrl.apply()                                         # Update -> Segment 0

    kinds = [e[0] for e in fake.log]
    check("start: load_sequence then start",
          kinds[0] == "load_sequence" and kinds[1] == "start")
    check("apply() does not restart the card", kinds.count("start") == 1)
    check("no stop during updates", "stop" not in kinds)
    # After the first apply, only write_segment/write_step are used
    seg_writes = [e for e in fake.log if e[0] == "write_segment"]
    check("2 live segment writes", len(seg_writes) == 2)
    check("loaded into inactive segment (1, then 0)",
          [e[1] for e in seg_writes] == [1, 0])
    step_switches = [e[1] for e in fake.log if e[0] == "write_step"]
    check("Step switched seamlessly to new segment (1, then 0)",
          step_switches == [1, 0])


def main():
    print("Build two combined lasers (mocks, no hardware) ...")
    lasers = build_lasers_mock()
    test_structure(lasers)
    test_initialization(lasers)
    test_chain_logic(lasers)
    test_tec_protection(lasers)
    test_apg(lasers)
    test_eom(lasers)
    test_chaining_reported()
    test_tec_25c()
    test_awg()
    print(f"\nAll {_passed} checks passed.")


if __name__ == "__main__":
    main()
