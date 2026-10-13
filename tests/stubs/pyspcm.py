"""Test-Stub des SPCM-Treibers (KEINE echte Hardware).

Zeichnet alle Registerschreibzugriffe und Kommandos in einer globalen Liste auf,
sodass die Offline-Tests den erzeugten Registerstrom pruefen koennen. GetParam
liefert plausible Default-Werte einer M4i.6631-x8.
"""

import ctypes

# --- Aufzeichnung ---------------------------------------------------------- #
# Jeder Eintrag: (art, register/kommando, wert)  art in {"set32","set64","cmd"}
writes: list[tuple] = []
transfers: list[tuple] = []   # (buftype, direction, offset_bytes, length_bytes)


def reset_recording() -> None:
    writes.clear()
    transfers.clear()


# --- Default-Rueckgabewerte fuer GetParam (Karteninfo) --------------------- #
import regs as _r  # type: ignore

_GET_DEFAULTS = {
    _r.SPC_PCITYP: 0x6631,          # Kartentyp M4i.6631
    _r.SPC_PCISERIALNO: 12345,
    _r.SPC_MIINST_MODULES: 1,
    _r.SPC_MIINST_CHPERMODULE: 2,   # 2 Analogkanaele (M4i.6631-x8)
    _r.SPC_MIINST_MAXADCVALUE: 32767,
    _r.SPC_PCISAMPLERATE: 1_250_000_000,
    _r.SPC_PCIMEMSIZE: 2 * 1024 * 1024 * 1024,
}


# --- Treiberfunktionen (Signaturen wie im echten pyspcm) ------------------- #
def spcm_hOpen(device_buffer):
    return 1  # truthy Handle


def spcm_vClose(handle):
    return None


def spcm_dwSetParam_i32(handle, register, value):
    if register == _r.SPC_M2CMD:
        writes.append(("cmd", register, int(value)))
    else:
        writes.append(("set32", int(register), int(value)))
    return _r.ERR_OK


def spcm_dwSetParam_i64(handle, register, value):
    writes.append(("set64", int(register), int(value)))
    return _r.ERR_OK


def spcm_dwGetParam_i32(handle, register, ptr):
    ptr[0] = int(_GET_DEFAULTS.get(int(register), 0))
    return _r.ERR_OK


def spcm_dwGetParam_i64(handle, register, ptr):
    ptr[0] = int(_GET_DEFAULTS.get(int(register), 0))
    return _r.ERR_OK


def spcm_dwDefTransfer_i64(handle, buftype, direction, notify, buffer, offset, length):
    transfers.append((int(buftype), int(direction), int(offset), int(length)))
    return _r.ERR_OK


def spcm_dwGetErrorInfo_i32(handle, reg_ptr, val_ptr, text_buffer):
    text_buffer.value = b"mock: no error"
    return _r.ERR_OK
