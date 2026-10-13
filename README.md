# LaserLab-Toolkit

Python toolkit (`import laserlab`) for the AG Karpf laser setups: Maiman and
Aerodiode diode-driver chains, APG1780DA pulse generators, the shared dual EOM
controller and the Spectrum M4i AWG. Every device type and its PyQt6 widget is
available through a plugin registry for building your own GUIs.

Included application: the **SRP controller** (`laserlab.srp`), which controls
the 1064 nm and 1550 nm lasers, the shared dual EOM, the APG pulse generators,
the Spectrum AWG and an additional seed/pump laser with Maiman SF8 TEC drivers.

## Installation

Python 3.10 or newer. All drivers are separate repositories and are installed
automatically as dependencies:

| Package (import) | Repository |
|---|---|
| `maiman_drivers` | [Maiman-Treiber-Software](https://github.com/AGKarpf/Maiman-Treiber-Software) |
| `aerodiode_driver` | [Aerodiode-Treiber-Software](https://github.com/AGKarpf/Aerodiode-Treiber-Software) |
| `apg1780da` | [apg1780da](https://github.com/AGKarpf/apg1780da) |
| `eom_controller` | [EOM-Controller](https://github.com/AGKarpf/EOM-Controller) (C++ DLL + Python, DLL bundled) |
| `spectrum_awg` | [spectrum_m4i_awg](https://github.com/AGKarpf/spectrum_m4i_awg) |

```powershell
py -m pip install https://github.com/AGKarpf/LaserLab-Toolkit/archive/refs/heads/main.zip
```

Only Python (3.10 or newer) and internet access are needed: no Git and no
GitHub account. All drivers, PyQt6 and the EOM DLL come along automatically.
With the Spectrum card binding: `py -m pip install spcm_core` in addition.

Update to the latest version:

```powershell
py -m pip install --upgrade --force-reinstall https://github.com/AGKarpf/LaserLab-Toolkit/archive/refs/heads/main.zip
```

The Spectrum binding needs the native SPCM drivers. Development checkout:
`py -m pip install -e .[test]` (Git needed only for developing).

## Start

```powershell
laserlab --mock                # simulated devices, all laser panels (no console)
py -m laserlab --mock          # same, with console output
py -m laserlab --init          # copy config.toml + eom.ini here to adapt ports/settings
py -m laserlab                 # uses ./config.toml, else the packaged default
py -m laserlab --config D:/lab/config.toml
```

In a development checkout, `py demo.py` / `py main.py` use the `config.toml`
next to them. The packaged default lives in `laserlab/srp/defaults/`. Hardware
mode requires the NI-DAQmx runtime (EOM) and the Spectrum drivers (AWG).

## Using the components in your own GUI (plugin registry)

Every device type and its PyQt6 widget is registered by kind and name and can
be built from the same parameter tables as `config.toml`:

```python
import laserlab                           # import before PyQt6 (EOM DLL / Qt runtime order)
from laserlab import registry

chain = registry.build("chain", "laser_chain", {"name": "My laser", "stage": [
    {"type": "maiman_tec", "port": "COM5", "current_max_a": 0.5, "adjustable_current": True},
    {"type": "aerodiode", "port": "COM3", "max_current_a": 5.5},
]}, mock=False)
apg = registry.build("pulse_generator", "apg1780da", {"port": "COM10"})
eom = registry.build("eom", "dual_eom", {"ini": "eom.ini"})

widget = registry.create_widget("chain", "laser_chain", chain)   # any QWidget layout
```

| kind | built-in names | widget |
|---|---|---|
| `stage` | `maiman_tec`, `maiman_notec`, `aerodiode` | (inside a chain) |
| `chain` | `laser_chain` | `LaserChainWidget` |
| `pulse_generator` | `apg1780da` | `ApgDeviceWidget` |
| `eom` | `dual_eom` | `EomWidget` (for an `EomChannel`) / `DualEomWindow` |
| `awg` | `spectrum_m4i` | `AwgPanel` |

`registry.default_registry().types()` lists everything installed. A complete
example is [`examples/custom_gui.py`](examples/custom_gui.py).

**Adding your own device types:** another package registers a function in the
entry-point group `laserlab.plugins`; it is called with the registry on
first use, and its types are then also usable as `type = "..."` in `config.toml`:

```toml
# pyproject.toml of your plugin package
[project.entry-points."laserlab.plugins"]
my_driver = "my_package.srp_plugin:register"
```

```python
# my_package/srp_plugin.py
def register(registry):
    registry.add("stage", "my_driver", build=lambda params, mock: ...,
                 description="My diode driver")
```

## Additional SF8 laser

| Control | Driver serial (decimal) | Software current limit | Initial current | TEC target |
|---|---:|---:|---:|---:|
| Seed diode | 4985 (`0x1379`) | 0.5 A (500 mA) | 0 A | 25 °C |
| Pump diode | 65535 (`0xFFFF`) | 1.5 A | 0 A | 25 °C |

In hardware mode, the additional panel starts disconnected. The existing setup
can start before the new ports are known:

1. Open Windows Device Manager → Ports (COM & LPT). Identify the seed and pump
   USB adapters by observing the port list as each adapter is connected, with
   diode outputs disabled.
2. Click **Refresh COM ports**. Select different seed and pump ports using the
   USB descriptions where available.
3. Tick the checkbox confirming that you identified both USB connections, then
   click **Connect seed and pump**.
4. Both serial numbers are checked before control commands. Outputs are stopped,
   currents initialized to zero, TECs started at 25 °C, and setpoints verified.
   A mismatch or setup failure reports the error and keeps controls disconnected.
5. Start the AWG as required by the existing gating logic. Set currents and enable
   the seed before the pump. Switch off the pump before the seed.

The original configuration assigns serial `0xFFFF` to the 1064 nm driver on
`COM5`. **Serial 65535 alone cannot uniquely identify the new pump.** Ports
reserved by existing devices are excluded and rejected. Enumeration reads OS
metadata without sending commands to unrelated instruments.

Successful selections are saved in `connections.json` beside `config.toml`.
They preselect ports on later launches; they never reconnect automatically.
Close and reopen the application to change ports after connecting.

Current controls accept direct entry with 0.1 mA display resolution and 1 mA
arrow increments. Software rejects values outside the diode limits. These
limits constrain this application's commands; **they do not program a hardware
current-limit/interlock register** or constrain other applications or external
analog sources. The TEC-running check does not imply temperature stability;
measured temperature is displayed for the operator.

The additional laser has no APG or EOM assigned: none was specified, and the
shared dual EOM controller's two channels are already in use.

## Existing setup and waveform controls

The original 1064 nm chain has a fixed-current TEC Maiman stage and an adjustable
Aerodiode stage. The 1550 nm chain has one no-TEC Maiman stage. Their settings,
including the original 1550 nm stage's zero current limit, remain in `config.toml`.

The AWG panel controls pulse rate, pump/Stokes phases, decay duration and probe.
Waveform changes use two buffers while running. With an assigned AWG stopped,
diode switches are locked; enabled diodes lock the AWG stop switch. If no AWG
controller is available, the inherited behavior applies no AWG gate.

Closing the GUI stops diodes in reverse chain order before closing ports and
stopping the AWG. A diode shutdown failure prevents the window from closing.
TECs remain enabled, as in the original controller.

## Files

- `config.toml`: devices, ports and AWG settings; `eom.ini`: dual EOM parameters.
- `laserlab/registry.py`: plugin registry (kinds, build/widget factories, entry points).
- `laserlab/builtin.py`: built-in component types and TEC activation.
- `laserlab/config.py`: builds lasers/AWG from `config.toml` via the registry.
- `laserlab/srp/connections.py`, `connection_widget.py`: manual port selection and identity checks.
- `laserlab/srp/gui.py`: combined panels and AWG gating; `awg.py`/`awg_widget.py`: SRP waveform.
- `laserlab/srp/__main__.py`: command-line start (`laserlab`, `py -m laserlab`, `py -m laserlab.srp`).
- `test_srp.py`, `tests/`: hardware-free regression tests (`tests/stubs`: Spectrum driver stubs).

## Test without hardware

```powershell
py -m pip install -e .[test]
py test_srp.py
py -m pytest
```

The tests use simulated serial drivers, the Spectrum driver stubs and an
offscreen Qt GUI. No laboratory hardware is connected. Confirm SF8 model/scaling
and actual USB connections on the setup before operation.
