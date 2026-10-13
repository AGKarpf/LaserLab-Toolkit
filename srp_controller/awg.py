"""Generate SRP waveforms for the Spectrum M4i.6631-x8 via spectrum_awg.
Pump and Stokes triggers run continuously with independent sample offsets.
A main train of num_pulses is followed by a decay window containing the analog
probe output. At probe start, pump receives an additional configurable offset,
defaulting to sample_per_pulse // 2. Pack analog in the low bits and digital
outputs in BIT15/BIT14/BIT13, routed to X0/X1/X2. Mock mode computes only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

MAX_DAC = 32767


# ---------------------------------------------------------------------------
# Waveform generation (ported from SRP_AWG/generate_waveform.py)
# ---------------------------------------------------------------------------
def _pulse_train(length: int, sample_per_pulse: int) -> np.ndarray:
    """Square pulse train with 50% duty across the full buffer."""
    data = np.zeros(length, dtype=np.int16)
    half = max(1, sample_per_pulse // 2)
    for k in range(half):
        data[k::sample_per_pulse] = 1
    return data


def _pulse_train_offset(length: int, sample_per_pulse: int,
                        offset_samples: int) -> np.ndarray:
    """Pulse train with a constant sample offset across the full buffer."""
    base = _pulse_train(length, sample_per_pulse)
    if offset_samples:
        base = np.roll(base, int(offset_samples))
    return base


def generate_srp_sequence(samplerate_hz: int, pulse_frequency_hz: int,
                          num_pulses: int, decay_time_s: float,
                          pump_phase_samples: int = 0,
                          stokes_phase_samples: int = 0,
                          probe_enabled: bool = True,
                          probe_pump_shift_samples: Optional[int] = None):
    """Return (probe, pump, stokes, trigger) as int16 arrays.
    Pump and Stokes have continuous sample offsets. Probe is active only during
    decay. Starting at probe onset, pump is shifted right by the configured offset,
    defaulting to sample_per_pulse // 2 when probe_pump_shift_samples is None."""
    sample_per_pulse = max(1, int(samplerate_hz) // int(pulse_frequency_hz))
    decay_sample = max(0, int(samplerate_hz * decay_time_s))
    train_end = sample_per_pulse * int(num_pulses)
    length = train_end + decay_sample

    # Constant phases across the entire buffer.
    pump = _pulse_train_offset(length, sample_per_pulse, pump_phase_samples)
    stokes = _pulse_train_offset(length, sample_per_pulse, stokes_phase_samples)

    probe = np.zeros(length, dtype=np.int16)
    if probe_enabled and decay_sample > 0:
        probe[train_end:] = 1                      # Probe laser during decay
        # At probe onset, shift pump by half the pulse period or the explicit offset.
        shift = (sample_per_pulse // 2 if probe_pump_shift_samples is None
                 else int(probe_pump_shift_samples))
        pump_shifted = _pulse_train_offset(
            length, sample_per_pulse, pump_phase_samples + shift)
        pump[train_end:] = pump_shifted[train_end:]

    trigger = np.zeros(length, dtype=np.int16)
    trigger[:max(1, length // 2)] = 1
    return probe, pump, stokes, trigger


def fuse_analog_digital(analog16: np.ndarray,
                        digitals: List[np.ndarray]) -> np.ndarray:
    """Pack analog and 1–3 digital channels into int16 words.
    Analog uses the low bits; digital channels use BIT15/BIT14/BIT13 in input
    order. Pad the buffer length to a multiple of 32."""
    n = len(digitals)
    if not 1 <= n <= 3:
        raise ValueError("Supply 1 to 3 digital channels.")
    a = np.asarray(analog16).astype(np.int64)
    low_bits = 16 - n
    analog_part = (a >> n) & ((1 << low_bits) - 1)

    digital_part = np.zeros(a.shape[0], dtype=np.int64)
    for k, dch in enumerate(digitals):
        dch = np.asarray(dch)
        if dch.shape[0] != a.shape[0]:
            raise ValueError(f"Digital channel {k} has an incorrect length.")
        digital_part |= (dch.astype(bool).astype(np.int64) << (15 - k))

    word = analog_part | digital_part
    pad = (-word.size) % 32                          # pad to a multiple of 32
    if pad:
        word = np.concatenate((word, np.zeros(pad, dtype=word.dtype)))
    return word.astype(np.uint16).astype(np.int16)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
#: Pulse rates (Hz) offered by the GUI (pump/Stokes triggers).
PULSE_RATE_OPTIONS_HZ = (6_400_000, 12_800_000, 25_600_000, 51_200_000)
#: Default pulse rate.
DEFAULT_PULSE_RATE_HZ = 51_200_000


@dataclass
class AwgSettings:
    """Fixed card configuration plus editable waveform parameters."""

    # Card / fixed parameters
    device: str = "/dev/spcm0"
    samplerate_hz: int = 819_200_000        # derived from a 12.8 MHz reference clock
    reference_clock_hz: int = 12_800_000    # external reference clock
    external_clock: bool = True             # external reference clock instead of internal PLL
    external_trigger: bool = True           # external trigger instead of software trigger
    trigger_level_mv: int = 1500
    num_pulses: int = 10
    amplitude_mv: int = 1000
    analog_channel: int = 0          # Probe (analog)
    pump_line: int = 0               # X0 -> BIT15
    stokes_line: int = 1             # X1 -> BIT14
    trigger_line: int = 2            # X2 -> BIT13

    # Editable in the GUI
    pulse_frequency_hz: int = DEFAULT_PULSE_RATE_HZ   # Dropdown (Pump/Stokes)
    pump_phase_samples: int = 0
    stokes_phase_samples: int = 0
    decay_time_s: float = 20e-6
    probe_enabled: bool = True
    #: Pump offset from probe onset; None -> half pulse period (sample_per_pulse//2).
    probe_pump_shift_samples: Optional[int] = None

    @property
    def sample_per_pulse(self) -> int:
        return max(1, int(self.samplerate_hz) // int(self.pulse_frequency_hz))


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
class AwgController:
    """Compute SRP waveforms and optionally send them to a Spectrum card.
    Mock mode computes only; hardware mode opens and configures spectrum_awg."""

    def __init__(self, settings: AwgSettings, use_mock: bool = True) -> None:
        self.settings = settings
        self.use_mock = use_mock
        self.card = None
        self.running = False
        self.last: Optional[Dict[str, np.ndarray]] = None
        #: Active segment (double buffer 0/1); None = not started yet.
        self._active_segment: Optional[int] = None
        self._step_index = 0
        if not use_mock:
            self._open_card()

    # -- Hardware ----------------------------------------------------- #
    def _open_card(self) -> None:
        from spectrum_awg import (
            SpectrumM4i6631, CardConfig, ClockConfig, TriggerConfig,
            ChannelConfig, DigitalOutput, ReplayMode, TriggerSource,
            DigitalBitSource, ClockMode, TriggerEdge,
        )
        s = self.settings
        clock = ClockConfig(
            mode=ClockMode.EXTERNAL_REFERENCE if s.external_clock
            else ClockMode.INTERNAL_PLL,
            sample_rate_hz=int(s.samplerate_hz),
            reference_clock_hz=int(s.reference_clock_hz) if s.external_clock else None,
        )
        trigger = TriggerConfig(
            source=TriggerSource.EXTERNAL0 if s.external_trigger
            else TriggerSource.SOFTWARE,
            edge=TriggerEdge.RISING,
            level_mv=int(s.trigger_level_mv),
        )
        cfg = CardConfig(
            replay_mode=ReplayMode.SEQUENCE,
            clock=clock,
            trigger=trigger,
            channels=[ChannelConfig(index=s.analog_channel,
                                    amplitude_mv=int(s.amplitude_mv))],
            digital_outputs=[
                DigitalOutput(x_line=s.pump_line, source_bit=DigitalBitSource.BIT15,
                              source_channel=s.analog_channel),
                DigitalOutput(x_line=s.stokes_line, source_bit=DigitalBitSource.BIT14,
                              source_channel=s.analog_channel),
                DigitalOutput(x_line=s.trigger_line, source_bit=DigitalBitSource.BIT13,
                              source_channel=s.analog_channel),
            ],
            max_segments=2,
        )
        self.card = SpectrumM4i6631(s.device, cfg)
        self.card.open()
        self.card.apply_config()

    # -- Waveform ----------------------------------------------------- #
    def generate(self) -> Dict[str, np.ndarray]:
        """Generate all channels and the packed int16 buffer without output."""
        s = self.settings
        probe, pump, stokes, trigger = generate_srp_sequence(
            s.samplerate_hz, s.pulse_frequency_hz, s.num_pulses, s.decay_time_s,
            s.pump_phase_samples, s.stokes_phase_samples, s.probe_enabled,
            s.probe_pump_shift_samples)
        analog = (probe.astype(np.int64) * MAX_DAC).astype(np.int16)
        fused = fuse_analog_digital(analog, [pump, stokes, trigger])
        self.last = {"probe": probe, "pump": pump, "stokes": stokes,
                     "trigger": trigger, "analog": analog, "fused": fused}
        return self.last

    def _seq_step(self, segment: int):
        """Repeat one segment indefinitely; segment changes take effect at a loop boundary."""
        from spectrum_awg import SequenceStep, StepEndCondition
        return SequenceStep(self._step_index, segment=segment, loops=1,
                            next_step=self._step_index,
                            end_condition=StepEndCondition.ALWAYS)

    def start(self) -> Dict[str, np.ndarray]:
        """Load the current waveform and start output."""
        data = self.generate()
        if self.card is not None:
            from spectrum_awg import Segment
            self.card.load_sequence([Segment(0, data["fused"])], [self._seq_step(0)])
            self.card.start(enable_trigger=True)
            # With an external trigger, the card waits for
            # the external signal. Issue force_trigger only in software trigger mode.
            if not self.settings.external_trigger:
                self.card.force_trigger()
            self._active_segment = 0
        self.running = True
        return data

    def apply(self) -> Dict[str, np.ndarray]:
        """Regenerate the preview and update a running card using two buffers.
        Write the inactive segment, then switch the sequence step without restarting.
        When stopped, compute only; no waveform is sent to the card."""
        data = self.generate()
        if self.running and self.card is not None and self._active_segment is not None:
            idle = 1 - self._active_segment
            self.card.write_segment(idle, data["fused"])
            self.card.write_step(self._seq_step(idle))
            self._active_segment = idle
        return data

    def stop(self) -> None:
        """Stop output."""
        if self.card is not None:
            self.card.stop()
        self._active_segment = None      # the next start() initializes again
        self.running = False

    def close(self) -> None:
        try:
            self.stop()
        finally:
            if self.card is not None:
                self.card.close()
                self.card = None

    # -- Setter (GUI) ------------------------------------------------- #
    def set_pump_phase(self, samples: int) -> None:
        self.settings.pump_phase_samples = int(samples)

    def set_stokes_phase(self, samples: int) -> None:
        self.settings.stokes_phase_samples = int(samples)

    def set_decay_time(self, seconds: float) -> None:
        self.settings.decay_time_s = float(seconds)

    def set_probe_enabled(self, enabled: bool) -> None:
        self.settings.probe_enabled = bool(enabled)

    # -- Info --------------------------------------------------------- #
    def info(self) -> Dict[str, int]:
        s = self.settings
        spp = s.sample_per_pulse
        decay_sample = max(0, int(s.samplerate_hz * s.decay_time_s))
        shift = (spp // 2 if s.probe_pump_shift_samples is None
                 else int(s.probe_pump_shift_samples))
        return {
            "sample_per_pulse": spp,
            "train_samples": spp * s.num_pulses,
            "decay_samples": decay_sample,
            "total_samples": spp * s.num_pulses + decay_sample,
            "pump_shift": shift,
        }
