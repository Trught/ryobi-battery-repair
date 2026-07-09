#!/usr/bin/env python3
"""
Minimal Ryobi/LPC804 diagnostic UART client.

Static analysis notes this script is based on:
PBP004 D-tech frame transport:
- UART frame start byte: 0x46 ("F")
- Header after start: len, packed nibbles, flag, opcode_hi, opcode_lo,
  header_crc_low
- Payload length: header len byte
- Trailer: CRC16 high, CRC16 low
- CRC peripheral is used with seed 0xFFFF and mode 0; this matches a
  CRC-16/CCITT-FALSE candidate.

PBP005:
- No matching PBP004 fixture key/auth parser was found statically.
- The useful public UART path currently appears to be the ASCII debug log
  plus a service/GPIO state machine at firmware address 0x213C.
PBP002:
- No matching PBP004 fixture key/auth strings were found statically.
- It has the same family of ASCII debug logs and a service/GPIO state
  machine at firmware address 0x21A0.

This is intended for read-only/diagnostic probing first. Avoid raw write
requests until the target command is understood.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
import time
from dataclasses import dataclass

try:
    import serial
except ImportError:  # pragma: no cover - user environment dependent
    serial = None


START = 0x46
DEFAULT_BAUD = 115200
DEFAULT_FIXTURE_KEY = bytes.fromhex("c2 c7 60 7a b5 8f 44 d2 4e 7a")


class ProtocolError(RuntimeError):
    pass


@dataclass
class Frame:
    opcode: int
    payload: bytes
    upper: int = 0
    lower: int = 1
    flag: int = 0


@dataclass(frozen=True)
class ServiceState:
    handler: int | None
    meaning: str


@dataclass(frozen=True)
class DtechItem:
    risk: str
    meaning: str


DTECH_TRANSPORT_OPCODES = {
    0x0001: DtechItem("auth", "create/send slave challenge response; allowed before authentication"),
    0x0002: DtechItem("reserved", "recognized by pre-auth allow-list, exact role not yet resolved"),
    0x0003: DtechItem("auth", "final response; sets authenticated flag when challenge response matches"),
    0x0004: DtechItem("status", "data/error/status handling; allowed before authentication"),
    0x0005: DtechItem("fixture", "fixture request dispatcher; requires type1/type3 authentication first"),
    0x0006: DtechItem("state-changing", "baud-rate request; can change UART baud"),
    0x0007: DtechItem("state-changing", "tool barcode request/update candidate"),
    0x0008: DtechItem("read", "revisions request candidate"),
    0x0009: DtechItem("state-changing", "power-off request"),
    0x0101: DtechItem("state-changing", "OCP/current threshold related request"),
    0x0102: DtechItem("state-changing", "OCP/current threshold related request"),
    0x0104: DtechItem("state-changing", "OCP/OTP/UVP/pack-status command family starts here"),
}


PBP004_FIXTURE_REQUESTS = {
    0x01: DtechItem("auth", "fixture auth payload: 0x01 + 10-byte fixture key"),
    0x03: DtechItem("state-changing", "32-bit threshold/current request; requires length 5 and value >= 0x54a48e01; calls 0x494e"),
    0x04: DtechItem("write", "length 0x29 request; calls 0x3878/0x4040/0x3f64/0x3918; barcode/config update candidate"),
    0x05: DtechItem("ack", "simple one-byte response/error path"),
    0x06: DtechItem("ack", "simple one-byte response/error path"),
    0x07: DtechItem("ack", "simple one-byte response/error path"),
    0x08: DtechItem("ack", "simple one-byte response/error path"),
    0x09: DtechItem("state-changing", "ack, delay 0x7d0, calls 0x4998(0x40) and 0x4a1e"),
    0x0A: DtechItem("read", "copies three 0x2a-byte blocks into payload, sends length 0x7f"),
    0x0B: DtechItem("danger", "sends service patterns then enters infinite loop; likely reset/shutdown/fixture mode"),
}


PBP005_BMS_STATES = {
    0x00: "init/idle decision; persistent fault -> 0xff, sleep/event -> 0xfe, otherwise -> 0x01",
    0x01: "normal/active; fault -> 0xff, load event can enter 0x03",
    0x02: "charge-management; uses substate at base+0x14 and helpers 0x630/0x54cc/0x5744",
    0x03: "discharge/load-management; handles EOV/EUV/EOT/ADOC and sleep/load checks",
    0xFE: "sleep/standby; fault -> 0xff, wake/runtime bits can return to 0x01/0x02",
    0xFF: "fault persistence/lockout; shifts and writes fault history to NVM 0x7e94",
}


PBP002_BMS_STATES = {
    0x00: "init/idle decision; persistent fault -> 0xff, sleep/event -> 0xfe, otherwise -> 0x01",
    0x01: "normal/active; fault -> 0xff, load event can enter 0x03",
    0x02: "charge-management; uses ctx+0x44 substate and helpers 0x900/0x5300/0x5580",
    0x03: "discharge/load-management; handles FOV/FUV/FOT/ADOC/DOC and sleep/load checks",
    0xFE: "sleep/standby; fault -> 0xff, wake/runtime bits can return to 0x01/0x02",
    0xFF: "fault persistence/lockout; shifts and writes fault history to NVM 0x7e94",
}


PBP005_SERVICE_STATES = {
    0x00: ServiceState(None, "idle/reset service output"),
    0x01: ServiceState(0x22A4, "service voltage class 4 / normal-active class from 0x56ae"),
    0x02: ServiceState(0x22AC, "fault/lockout service state from BMS state 0xff"),
    0x03: ServiceState(0x22D2, "sleep-entry/service pattern step"),
    0x04: ServiceState(0x22DA, "sleep-entry/service pattern step"),
    0x05: ServiceState(0x22E2, "sleep-entry/service pattern step"),
    0x06: ServiceState(0x22EA, "sleep-entry/service pattern step"),
    0x07: ServiceState(0x22F0, "GPIO/peripheral pattern output"),
    0x08: ServiceState(0x22F8, "GPIO/peripheral pattern output"),
    0x09: ServiceState(0x2300, "GPIO/peripheral pattern output"),
    0x0A: ServiceState(0x2306, "GPIO/peripheral pattern output"),
    0x0B: ServiceState(0x230E, "auto-clear/fault-history service pulse candidate"),
    0x0C: ServiceState(0x2316, "GPIO/peripheral pattern output"),
    0x0D: ServiceState(0x231E, "GPIO/peripheral pattern output"),
    0x0E: ServiceState(0x2326, "GPIO/peripheral pattern output"),
    0x0F: ServiceState(0x232C, "GPIO/peripheral pattern output"),
    0x10: ServiceState(0x2332, "GPIO/peripheral pattern output"),
    0x11: ServiceState(0x22A4, "alias of state 0x01"),
    0x80: ServiceState(0x233A, "standby/default service state from 0x56ae"),
    0x81: ServiceState(0x2360, "cell-voltage band 1 service state from 0x56ae"),
    0x82: ServiceState(0x237E, "cell-voltage band 2 service state from 0x56ae"),
    0x83: ServiceState(0x239E, "cell-voltage band 3 service state from 0x56ae"),
    0x84: ServiceState(0x23D0, "timed service pattern"),
    0x85: ServiceState(0x23F8, "timed service pattern"),
    0x86: ServiceState(0x2426, "timed service pattern"),
    0x87: ServiceState(0x2454, "special service flag active; emitted when 0x56ae sees flag bit 3 and 0x0c90()"),
    0x88: ServiceState(0x22A4, "alias of state 0x01"),
    0x89: ServiceState(0x2488, "configuration/service pulse sequence"),
    0x8A: ServiceState(0x24C2, "configuration/service pulse sequence"),
    0x8B: ServiceState(0x2530, "configuration/service pulse sequence"),
    0x8C: ServiceState(0x25A2, "BMS state 0x03 special load/sleep pulse from 0x56ae"),
}


PBP002_SERVICE_STATES = {
    0x00: ServiceState(None, "idle/reset service output"),
    0x01: ServiceState(0x2308, "service voltage class 4 / normal-active class from 0x54ea"),
    0x02: ServiceState(0x2312, "fault/lockout service state from BMS state 0xff"),
    0x03: ServiceState(0x233C, "sleep-entry/service pattern step"),
    0x04: ServiceState(0x2344, "sleep-entry/service pattern step"),
    0x05: ServiceState(0x234C, "sleep-entry/service pattern step"),
    0x06: ServiceState(0x2354, "sleep-entry/service pattern step"),
    0x07: ServiceState(0x235A, "GPIO/peripheral pattern output"),
    0x08: ServiceState(0x2362, "GPIO/peripheral pattern output"),
    0x09: ServiceState(0x236A, "GPIO/peripheral pattern output"),
    0x0A: ServiceState(0x2370, "GPIO/peripheral pattern output"),
    0x0B: ServiceState(0x2378, "auto-clear/fault-history service pulse candidate"),
    0x0C: ServiceState(0x2380, "GPIO/peripheral pattern output"),
    0x0D: ServiceState(0x2388, "GPIO/peripheral pattern output"),
    0x0E: ServiceState(0x2390, "GPIO/peripheral pattern output"),
    0x0F: ServiceState(0x2396, "GPIO/peripheral pattern output"),
    0x10: ServiceState(0x239C, "GPIO/peripheral pattern output"),
    0x11: ServiceState(0x2308, "alias of state 0x01"),
    0x80: ServiceState(0x23A4, "standby/default service state from 0x54ea"),
    0x81: ServiceState(0x23CC, "cell-voltage band 1 service state from 0x54ea"),
    0x82: ServiceState(0x23EC, "cell-voltage band 2 service state from 0x54ea"),
    0x83: ServiceState(0x240E, "cell-voltage band 3 service state from 0x54ea"),
    0x84: ServiceState(0x2444, "timed service pattern"),
    0x85: ServiceState(0x246C, "timed service pattern"),
    0x86: ServiceState(0x249A, "timed service pattern"),
    0x87: ServiceState(0x24C8, "special service flag active; emitted when 0x54ea sees flag bit 3 and 0x0f30()"),
    0x88: ServiceState(0x2308, "alias of state 0x01"),
    0x89: ServiceState(0x24FC, "configuration/service pulse sequence"),
    0x8A: ServiceState(0x2536, "configuration/service pulse sequence"),
    0x8B: ServiceState(0x25B6, "configuration/service pulse sequence"),
    0x8C: ServiceState(0x263A, "BMS state 0x03 special load/sleep pulse from 0x54ea"),
}


PBP005_LOG_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bADOC\b"), "ADOC event: fault bit 0x40 source, confirmed PBP005 lockout path"),
    (re.compile(r"\bADOC_E\b"), "ADOC recovery/clear path"),
    (re.compile(r"\bAFERc\b"), "AFE recovery/clear path"),
    (re.compile(r"\bDOCRc\b"), "discharge over-current recovery/clear path candidate"),
    (re.compile(r"WFLR:x([0-9a-fA-F]+)"), "fault/lockout related word log before flash write"),
    (re.compile(r"F2Flsh:x([0-9a-fA-F]+)"), "fault word written to NVM 0x7e94"),
    (re.compile(r"\bSlp\s+(\d+)"), "sleep entry path"),
    (re.compile(r"\b!Slp\s+(\d+)"), "sleep aborted or wake path"),
    (re.compile(r"\bDPDWAKE\b"), "deep-power-down wake event"),
    (re.compile(r"\bAFEPNR\b"), "AFE power-not-ready candidate"),
    (re.compile(r"\bAFENR\b"), "AFE not-ready candidate"),
    (re.compile(r"\bAINT:0x([0-9a-fA-F]+)"), "AFE interrupt/status log"),
    (re.compile(r"\bChgWkI\b"), "charger/wake interrupt candidate"),
    (re.compile(r"\bEOVs\b"), "over-voltage set/event candidate"),
    (re.compile(r"\bEUVs\b"), "under-voltage set/event candidate"),
    (re.compile(r"\bEOTs\b"), "over-temperature set/event candidate"),
    (re.compile(r"Fail:x([0-9a-fA-F]+)"), "failure bitmask/status log"),
    (re.compile(r"\bPS:(\d+)\s+(\d+)"), "power/service state log"),
    (re.compile(r"\bHB\s+(\d+)"), "heartbeat/status log"),
    (re.compile(r"\bSCc\b"), "service/charger check pulse from 0x5744"),
]


PBP002_LOG_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bFOV\b"), "over-voltage fault set candidate; fault bit 0x04 path"),
    (re.compile(r"\bFUV\b"), "under-voltage fault set candidate; fault bit 0x08 path"),
    (re.compile(r"\bFOT\b"), "over-temperature fault set candidate; fault bit 0x02 path"),
    (re.compile(r"\bEUB Time St\b"), "PBP002 cell-unbalance timer started before fault bit 0x20"),
    (re.compile(r"\bEUB Flag\b"), "PBP002 cell-unbalance timed fault set; fault bit 0x20 path"),
    (re.compile(r"\bEUV Time St\b"), "PBP002 extreme under-voltage timer started before fault bit 0x40"),
    (re.compile(r"\bEUV Flag\b"), "PBP002 extreme under-voltage timed fault set; fault bit 0x40 path"),
    (re.compile(r"\bADOC\b"), "PBP002 ADOC runtime event/log path; static writes seen on ctx+0x34, not direct ctx+0x3b"),
    *PBP005_LOG_PATTERNS,
]


PBP004_LOG_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bFOV\b"), "over-voltage fault set candidate; fault bit 0x04 path"),
    (re.compile(r"\bFUV\b"), "under-voltage fault set candidate; fault bit 0x08 path"),
    (re.compile(r"\bFOT\b"), "over-temperature fault set candidate; fault bit 0x02 path"),
    (re.compile(r"D-tech Authentication successful"), "D-tech transport authentication succeeded"),
    (re.compile(r"D-tech Authentication failed"), "D-tech transport authentication failed"),
    (re.compile(r"DTk Fixture Auth Success"), "fixture auth key accepted"),
    (re.compile(r"Unauthorized D-tech Fixture Request"), "fixture request rejected before fixture auth"),
    (re.compile(r"D-tech Bad Header CRC Received"), "D-tech header CRC failure"),
    (re.compile(r"D-tech received CRC error message"), "D-tech final CRC failure"),
    *PBP005_LOG_PATTERNS,
]


def crc16_ccitt(data: bytes, seed: int = 0xFFFF) -> int:
    crc = seed & 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def dtech_transform(value: int) -> int:
    """Implements firmware function 0x4854."""
    value &= 0xFFFF
    inv_hi = (~(value >> 8)) & 0xFF
    return (((value & 0xFF) ^ inv_hi) << 8) | inv_hi


def build_frame(frame: Frame) -> bytes:
    if len(frame.payload) > 0xFF:
        raise ValueError("payload is too long for one D-tech frame")
    if not 0 <= frame.opcode <= 0xFFFF:
        raise ValueError("opcode must be 0..0xffff")

    packed = ((frame.upper & 0x0F) << 4) | (frame.lower & 0x0F)
    header_without_start = bytes(
        [
            len(frame.payload),
            packed,
            frame.flag & 0xFF,
            (frame.opcode >> 8) & 0xFF,
            frame.opcode & 0xFF,
        ]
    )
    header_crc = crc16_ccitt(header_without_start) & 0xFF
    crc_input = header_without_start + bytes([header_crc]) + frame.payload
    trailer = crc16_ccitt(crc_input).to_bytes(2, "big")
    return bytes([START]) + crc_input + trailer


def parse_frame(raw: bytes) -> Frame:
    if len(raw) < 9:
        raise ProtocolError("short frame")
    if raw[0] != START:
        raise ProtocolError(f"bad start byte 0x{raw[0]:02x}")

    length = raw[1]
    expected_len = 1 + 6 + length + 2
    if len(raw) != expected_len:
        raise ProtocolError(f"bad frame length: got {len(raw)}, expected {expected_len}")

    header = raw[1:6]
    header_crc = raw[6]
    if (crc16_ccitt(header) & 0xFF) != header_crc:
        raise ProtocolError("bad header CRC")

    payload = raw[7 : 7 + length]
    final_crc_input = raw[1 : 7 + length] + raw[7 + length : 9 + length]
    if crc16_ccitt(final_crc_input) != 0:
        raise ProtocolError("bad frame CRC")

    packed = raw[2]
    opcode = (raw[4] << 8) | raw[5]
    return Frame(
        opcode=opcode,
        payload=payload,
        upper=(packed >> 4) & 0x0F,
        lower=packed & 0x0F,
        flag=raw[3],
    )


def read_exact(port, count: int, timeout_s: float) -> bytes:
    deadline = time.monotonic() + timeout_s
    out = bytearray()
    while len(out) < count:
        if time.monotonic() > deadline:
            raise TimeoutError(f"timeout while reading {count} bytes")
        chunk = port.read(count - len(out))
        if chunk:
            out.extend(chunk)
    return bytes(out)


def read_frame(port, timeout_s: float = 2.0) -> tuple[Frame, bytes]:
    deadline = time.monotonic() + timeout_s
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("timeout waiting for frame start")
        b = port.read(1)
        if b and b[0] == START:
            break

    header = read_exact(port, 6, timeout_s)
    length = header[0]
    tail = read_exact(port, length + 2, timeout_s)
    raw = bytes([START]) + header + tail
    return parse_frame(raw), raw


def write_frame(port, frame: Frame, verbose: bool = False) -> bytes:
    raw = build_frame(frame)
    if verbose:
        print("TX", raw.hex(" "))
    port.write(raw)
    port.flush()
    return raw


def transact(port, frame: Frame, timeout_s: float, verbose: bool) -> Frame:
    write_frame(port, frame, verbose=verbose)
    reply, raw = read_frame(port, timeout_s=timeout_s)
    if verbose:
        print("RX", raw.hex(" "))
        print(
            f"   opcode=0x{reply.opcode:04x} len={len(reply.payload)} "
            f"upper={reply.upper} lower={reply.lower} flag=0x{reply.flag:02x} "
            f"payload={reply.payload.hex(' ')}"
        )
    return reply


def require_pbp004(profile: str, command: str) -> None:
    if profile != "pbp004":
        raise ProtocolError(
            f"{command} is only implemented for PBP004-style D-tech frames. "
            f"{profile.upper()} has no statically confirmed matching auth parser/key; use listen-log or a state map."
        )


def run_auth(port, fixture_key: bytes, timeout_s: float, verbose: bool) -> None:
    if len(fixture_key) != 10:
        raise ValueError("fixture key must be exactly 10 bytes")

    host_challenge = secrets.randbits(16)
    challenge_payload = bytes([1]) + host_challenge.to_bytes(2, "big")

    stage1 = transact(
        port,
        Frame(opcode=0x0001, payload=challenge_payload, lower=1),
        timeout_s,
        verbose,
    )
    if len(stage1.payload) < 5:
        raise ProtocolError("short stage1 response")
    if stage1.payload[0] != 1:
        raise ProtocolError(f"unexpected stage1 response id {stage1.payload[0]}")

    expected_slave_response = dtech_transform(host_challenge)
    slave_response = int.from_bytes(stage1.payload[1:3], "big")
    if slave_response != expected_slave_response:
        raise ProtocolError(
            "slave challenge response mismatch: "
            f"got 0x{slave_response:04x}, expected 0x{expected_slave_response:04x}"
        )

    slave_challenge_raw = int.from_bytes(stage1.payload[3:5], "big")
    final_response = dtech_transform(slave_challenge_raw)

    transact(
        port,
        Frame(opcode=0x0003, payload=final_response.to_bytes(2, "big"), lower=1),
        timeout_s,
        verbose,
    )

    dtech_fixture_auth = bytes([1]) + fixture_key
    transact(
        port,
        Frame(opcode=0x0005, payload=dtech_fixture_auth, lower=1),
        timeout_s,
        verbose,
    )


def parse_pbp005_log_line(line: str) -> str | None:
    return parse_log_line("pbp005", line)


def parse_log_line(profile: str, line: str) -> str | None:
    compact = line.strip()
    if not compact:
        return None
    if profile == "pbp002":
        patterns = PBP002_LOG_PATTERNS
    elif profile == "pbp004":
        patterns = PBP004_LOG_PATTERNS
    else:
        patterns = PBP005_LOG_PATTERNS
    for pattern, meaning in patterns:
        match = pattern.search(compact)
        if match:
            if pattern.pattern.startswith("F2Flsh") and match.groups():
                return f"{meaning}: 0x{int(match.group(1), 16):08x}"
            if pattern.pattern.startswith("WFLR") and match.groups():
                return f"{meaning}: 0x{int(match.group(1), 16):08x}"
            if pattern.pattern.startswith("AINT") and match.groups():
                return f"{meaning}: 0x{int(match.group(1), 16):x}"
            if pattern.pattern.startswith("Fail") and match.groups():
                return f"{meaning}: 0x{int(match.group(1), 16):x}"
            if match.groups():
                return f"{meaning}: {' '.join(match.groups())}"
            return meaning
    return None


def read_log_line(port, timeout_s: float = 2.0) -> bytes:
    deadline = time.monotonic() + timeout_s
    out = bytearray()
    while True:
        if time.monotonic() > deadline:
            if out:
                return bytes(out)
            raise TimeoutError("timeout waiting for log data")
        chunk = port.read(1)
        if not chunk:
            continue
        out.extend(chunk)
        if chunk in (b"\n", b"\r"):
            while out and out[-1:] in (b"\n", b"\r"):
                out.pop()
            return bytes(out)


def print_pbp005_map(state: int | None = None) -> None:
    print_state_map("pbp005", state)


def print_state_map(profile: str, state: int | None = None) -> None:
    if profile == "pbp002":
        bms_states = PBP002_BMS_STATES
        service_states = PBP002_SERVICE_STATES
        fsm_addr = 0x21A0
        model = "PBP002"
    else:
        bms_states = PBP005_BMS_STATES
        service_states = PBP005_SERVICE_STATES
        fsm_addr = 0x213C
        model = "PBP005"

    print(f"{model} BMS states")
    for value, meaning in bms_states.items():
        if state is None or state == value:
            print(f"  0x{value:02x}: {meaning}")

    print()
    print(f"{model} service states from firmware FSM 0x{fsm_addr:04x}")
    items = sorted(service_states.items())
    for value, service_state in items:
        if state is not None and state != value:
            continue
        handler = "default" if service_state.handler is None else f"0x{service_state.handler:04x}"
        print(f"  0x{value:02x}: handler {handler}: {service_state.meaning}")

    if state is None:
        print("  0x12..0x7f: default handler 0x25e4")
        print("  >0x8c: default handler 0x25e4")


def print_pbp004_requests() -> None:
    print("PBP004 D-tech transport opcodes")
    for opcode, item in sorted(DTECH_TRANSPORT_OPCODES.items()):
        print(f"  0x{opcode:04x}: {item.risk}: {item.meaning}")

    print()
    print("PBP004 fixture request IDs inside opcode/type 0x0005")
    print(f"  fixture key: {DEFAULT_FIXTURE_KEY.hex(' ')}")
    for request_id, item in sorted(PBP004_FIXTURE_REQUESTS.items()):
        print(f"  0x{request_id:02x}: {item.risk}: {item.meaning}")


def parse_hex_bytes(text: str) -> bytes:
    cleaned = text.replace("0x", "").replace(",", " ").replace(":", " ")
    parts = cleaned.split()
    if len(parts) == 1 and len(parts[0]) % 2 == 0:
        return bytes.fromhex(parts[0])
    return bytes(int(part, 16) & 0xFF for part in parts)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", choices=("pbp002", "pbp004", "pbp005"), default="pbp004")
    ap.add_argument("--port", help="Serial port, for example COM5")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--verbose", "-v", action="store_true")

    sub = ap.add_subparsers(dest="cmd", required=True)
    auth = sub.add_parser("auth", help="Run type1/type3 challenge and type5 fixture auth")
    auth.add_argument(
        "--key",
        type=parse_hex_bytes,
        default=DEFAULT_FIXTURE_KEY,
        help="10-byte D-tech fixture key as hex; default is the PBP004 key",
    )

    raw = sub.add_parser("raw", help="Send one raw D-tech frame")
    raw.add_argument("--opcode", type=lambda s: int(s, 0), required=True)
    raw.add_argument("--payload", type=parse_hex_bytes, default=b"")
    raw.add_argument("--upper", type=int, default=0)
    raw.add_argument("--lower", type=int, default=1)
    raw.add_argument("--flag", type=lambda s: int(s, 0), default=0)

    listen = sub.add_parser("listen", help="Print incoming D-tech frames")
    listen.add_argument("--count", type=int, default=0, help="0 means forever")

    listen_log = sub.add_parser("listen-log", help="Print ASCII diagnostic log lines, useful for PBP002/PBP005")
    listen_log.add_argument("--count", type=int, default=0, help="0 means forever")
    listen_log.add_argument("--hex", action="store_true", help="also print raw bytes as hex")

    decode_log = sub.add_parser("decode-log", help="Decode one PBP005 log line without opening a serial port")
    decode_log.add_argument("line", help="log line text")

    pbp005_map = sub.add_parser("pbp005-map", help="Print PBP005 BMS/service state map from static analysis")
    pbp005_map.add_argument("--state", type=lambda s: int(s, 0), help="show only one state value")

    pbp002_map = sub.add_parser("pbp002-map", help="Print PBP002 BMS/service state map from static analysis")
    pbp002_map.add_argument("--state", type=lambda s: int(s, 0), help="show only one state value")

    sub.add_parser("pbp004-requests", help="Print PBP004 D-tech opcode/request map from static analysis")

    args = ap.parse_args(argv)

    if args.cmd == "decode-log":
        decoded = parse_log_line(args.profile, args.line)
        print(decoded if decoded else f"no known {args.profile.upper()} log pattern matched")
        return 0

    if args.cmd == "pbp005-map":
        print_state_map("pbp005", args.state)
        return 0

    if args.cmd == "pbp002-map":
        print_state_map("pbp002", args.state)
        return 0

    if args.cmd == "pbp004-requests":
        print_pbp004_requests()
        return 0

    try:
        if args.cmd in {"auth", "raw", "listen"}:
            require_pbp004(args.profile, args.cmd)
    except ProtocolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not args.port:
        print(f"error: {args.cmd} requires --port", file=sys.stderr)
        return 2

    if serial is None:
        print("Missing dependency: install pyserial first, e.g. python -m pip install pyserial", file=sys.stderr)
        return 2

    with serial.Serial(args.port, args.baud, timeout=0.05) as port:
        if args.cmd == "auth":
            run_auth(port, args.key, args.timeout, args.verbose)
            print("auth sequence completed")
        elif args.cmd == "raw":
            reply = transact(
                port,
                Frame(
                    opcode=args.opcode,
                    payload=args.payload,
                    upper=args.upper,
                    lower=args.lower,
                    flag=args.flag,
                ),
                args.timeout,
                True,
            )
            print(f"reply opcode=0x{reply.opcode:04x} payload={reply.payload.hex(' ')}")
        elif args.cmd == "listen":
            seen = 0
            while args.count == 0 or seen < args.count:
                frame, raw_frame = read_frame(port, timeout_s=args.timeout)
                print(
                    f"RX {raw_frame.hex(' ')}  opcode=0x{frame.opcode:04x} "
                    f"payload={frame.payload.hex(' ')}"
                )
                seen += 1
        elif args.cmd == "listen-log":
            seen = 0
            while args.count == 0 or seen < args.count:
                raw_line = read_log_line(port, timeout_s=args.timeout)
                if not raw_line:
                    continue
                text = raw_line.decode("ascii", errors="replace")
                decoded = parse_log_line(args.profile, text)
                prefix = f"RX {raw_line.hex(' ')}  " if args.hex else ""
                if decoded:
                    print(f"{prefix}{text}  # {decoded}")
                else:
                    print(f"{prefix}{text}")
                seen += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
