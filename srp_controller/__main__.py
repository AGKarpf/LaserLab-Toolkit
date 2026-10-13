"""Start the combined SRP GUI: py -m srp_controller [--config config.toml] [--mock].
Hardware mode follows [general] use_mock; deferred drivers connect from the GUI.
The EOM DLL is preloaded before Qt is imported (see eom_controller.preload_dll)."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path


def _preload_eom_dll(config_path: Path) -> None:
    """Load the EOM DLL named in the configured eom.ini before Qt starts."""
    from eom_controller import DualEOMConfig, preload_dll
    try:
        with config_path.open("rb") as stream:
            ini = tomllib.load(stream).get("eom", {}).get("ini")
        if ini:
            ini_path = Path(ini)
            if not ini_path.is_absolute():
                ini_path = config_path.parent / ini_path
            preload_dll(DualEOMConfig.from_config_file(ini_path).dll_path)
    except Exception:
        pass        # the controller reports real errors when it is created


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="srp_controller", description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.toml"),
                        help="configuration file (default: ./config.toml)")
    parser.add_argument("--mock", action="store_true",
                        help="simulate all devices, no hardware connections")
    args = parser.parse_args(argv)
    config = args.config.resolve()

    if not args.mock:
        _preload_eom_dll(config)

    from .config import load_awg, load_lasers
    from .connections import read_connection_settings

    force_mock = True if args.mock else None
    lasers = load_lasers(config, force_mock=force_mock)
    awg = load_awg(config, force_mock=force_mock)
    connection_settings = None if args.mock else read_connection_settings(config)

    from .gui import run
    title = "SRP Controller (Demo / simulated)" if args.mock else "SRP Controller"
    return run(lasers, awg, title=title, connection_settings=connection_settings)


if __name__ == "__main__":
    sys.exit(main())
