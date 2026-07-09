#!/usr/bin/env python3
"""
Targeted PBP005 firmware emulator for dynamic analysis.

This is a hybrid emulator, not a full LPC804 board model. The ARM Thumb code
runs under Unicorn, while MCU peripherals are stubbed or instrumented. The first
supported target is the PBP005 service LED/button state machine.
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PYDEPS = ROOT / ".codex_tmp" / "pydeps"
if LOCAL_PYDEPS.exists():
    sys.path.insert(0, str(LOCAL_PYDEPS))

try:
    from unicorn import (
        UC_ARCH_ARM,
        UC_HOOK_CODE,
        UC_HOOK_MEM_INVALID,
        UC_HOOK_MEM_READ,
        UC_HOOK_MEM_WRITE,
        UC_MEM_READ_UNMAPPED,
        UC_MEM_WRITE_UNMAPPED,
        UC_MODE_MCLASS,
        UC_MODE_THUMB,
        Uc,
        UcError,
    )
    from unicorn.arm_const import (
        UC_ARM_REG_LR,
        UC_ARM_REG_PC,
        UC_ARM_REG_R0,
        UC_ARM_REG_R1,
        UC_ARM_REG_R2,
        UC_ARM_REG_R3,
        UC_ARM_REG_R4,
        UC_ARM_REG_R5,
        UC_ARM_REG_R6,
        UC_ARM_REG_R7,
        UC_ARM_REG_SP,
        UC_ARM_REG_XPSR,
    )
except ImportError as exc:  # pragma: no cover - environment dependent
    raise SystemExit(
        "Missing dependency: unicorn. Install it with:\n"
        "  python -m pip install unicorn\n"
        "or for this workspace:\n"
        "  python -m pip install --target .codex_tmp\\pydeps unicorn"
    ) from exc


DEFAULT_FW = ROOT / "firmware" / "pbp005_280109559-02_O_20221209_fixed.hex"

FLASH_BASE = 0x00000000
FLASH_SIZE = 0x00010000
RAM_BASE = 0x10000000
RAM_SIZE = 0x00010000
MMIO_A_BASE = 0xA0000000
MMIO_A_SIZE = 0x00100000
MMIO_4_BASE = 0x40000000
MMIO_4_SIZE = 0x00100000
SCS_BASE = 0xE0000000
SCS_SIZE = 0x00100000
STOP_ADDR = 0x00007FF0

SERVICE_BASE = 0x10000230
IND_SW_W15 = 0xA000103C
GPIO_W20 = 0xA0001050
GPIO_SET0 = 0xA0002200
GPIO_CLR0 = 0xA0002280
GPIO_NOT0 = 0xA0002300

FN_TIMER_START = 0x000001A6
FN_TIMER_EXPIRED = 0x000001E4
FN_DELAY = 0x00000148
FN_BUSY_WAIT_A = 0x00000128
FN_BUSY_WAIT_B = 0x00000134
FN_TICK_WAIT = 0x00003388
FN_UART_CHAR_SINK = 0x000015C6
FN_DEBUG_LOG = 0x0000165C
FN_UART_RUNTIME_SERVICE = 0x000016BA
FN_SET_STATE = 0x00002120
FN_SERVICE_FSM = 0x0000213C
FN_PERIODIC_TICK = 0x00004CD8
FN_WAIT_BUTTON = 0x00004E98
FN_VOLTAGE_CLASS = 0x000004BC
FN_BMS_TO_SERVICE_STATE = 0x000056AE
FN_SMBUS_TRANSFER = 0x000036F8
FN_AFE_READ_WORD = 0x000017AC
FN_AFE_CELL_VOLTAGE = 0x00001A04
FN_AFE_ADOC_STATUS = 0x00001D48
FN_AFE_SET_BALANCE = 0x00001CDC
FN_UART_SERVICE_PUMP = 0x000033CC
FN_NVM_UPDATE_WORDS = 0x00003788
FN_AFE_MEASUREMENT_UPDATE = 0x00000338

I2C_STATUS_REG = 0x40054004
I2C_STATUS_READY_MASK = 0x01000000

SPECIAL_FLAG_ADDR = 0x100003D8
BMS_CTX_BASE = 0x10000150
EVENT_LATCH_BASE = 0x100003C8
HARNESS_FLAG_PTR = 0x10001000
HARNESS_GATE_PTR = 0x10001010
HARNESS_MEAS_PTR = 0x10001020
HARNESS_AFE_OUT = 0x10001040
HARNESS_NVM_SRC = 0x10001100
HARNESS_UART_TEXT = 0x10001200
HARNESS_UART_TX_RING = 0x10001300
HARNESS_UART_RX_RING = 0x10001320
HARNESS_UART_TX_BUF = 0x10001400
HARNESS_UART_RX_BUF = 0x10001500
HARNESS_UART_RING_SIZE = 0x80

LOG_STATE_BASE = 0x100000B4
LOG_ENABLED_ADDR = 0x100000B6

NVM_BASE = 0x00007E00
NVM_END = 0x00007F00
NVM_FAULT_WORD = 0x00007E94

AFE_ADDR = 0x29
AFE_WR_ADDR = (AFE_ADDR << 1) | 0
AFE_RD_ADDR = (AFE_ADDR << 1) | 1

DEFAULT_AFE_REGS = {
    0x02: 0x8082,
    0x03: 0xFC9C,
    0x05: 0x0010,
    0x0E: 0xFFC0,
    0x20: 0xFBBF,
    0x21: 0xFC72,
    0x22: 0xFC72,
    0x23: 0xFC73,
    0x24: 0xFC4E,
    0x25: 0xFC72,
    0x27: 0xF63C,
}

LED_MASKS = {
    0x10000000: "LED1",
    0x08000000: "LED2",
    0x04000000: "LED3",
    0x00100000: "LED4",
}

SERVICE_STATES = {
    0x01: "normal/class 4/all LED bits",
    0x02: "fault/lockout blink all LED bits",
    0x03: "LED/service step",
    0x04: "LED/service step",
    0x05: "LED/service step",
    0x06: "LED/service step",
    0x07: "LED1+LED2",
    0x08: "LED1+LED3",
    0x09: "LED1+LED4",
    0x0A: "LED2+LED3",
    0x0B: "LED2+LED4",
    0x0C: "LED3+LED4",
    0x0D: "LED1+LED2+LED3",
    0x0E: "LED1+LED2+LED4",
    0x0F: "LED1+LED3+LED4",
    0x10: "LED2+LED3+LED4",
    0x80: "standby/default",
    0x81: "cell-voltage band 1",
    0x82: "cell-voltage band 2",
    0x83: "cell-voltage band 3",
    0x87: "special service flag active",
    0x8C: "BMS state 0x03 special pattern",
}


def parse_ihex(path: Path) -> dict[int, int]:
    mem: dict[int, int] = {}
    upper = 0
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise ValueError(f"{path}:{lineno}: not an Intel HEX record")
        count = int(line[1:3], 16)
        off = int(line[3:7], 16)
        rectype = int(line[7:9], 16)
        data = bytes.fromhex(line[9 : 9 + count * 2])
        checksum = int(line[9 + count * 2 : 11 + count * 2], 16)
        total = count + (off >> 8) + (off & 0xFF) + rectype + sum(data) + checksum
        if total & 0xFF:
            raise ValueError(f"{path}:{lineno}: checksum mismatch")
        if rectype == 0:
            base = upper + off
            for i, b in enumerate(data):
                mem[base + i] = b
        elif rectype == 1:
            break
        elif rectype == 4:
            upper = int.from_bytes(data, "big") << 16
        elif rectype == 2:
            upper = int.from_bytes(data, "big") << 4
    return mem


def pack32(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def unpack32(data: bytes) -> int:
    return struct.unpack("<I", data)[0]


def led_names(mask: int) -> str:
    names = [name for bit, name in LED_MASKS.items() if mask & bit]
    return "+".join(names) if names else "-"


def crc8_pec(data: list[int]) -> int:
    crc = 0
    for byte in data:
        crc ^= byte & 0xFF
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


@dataclass
class Event:
    kind: str
    pc: int
    detail: str


@dataclass
class EmulatorConfig:
    firmware: Path
    trace: bool = False
    max_instructions: int = 20_000
    timer_expired: int = 0
    timer_model: bool = False
    expired_timers: set[int] = field(default_factory=set)
    stop_pcs: set[int] = field(default_factory=set)
    afe_regs: dict[int, int] = field(default_factory=lambda: dict(DEFAULT_AFE_REGS))
    afe_fail: bool = False
    afe_status_clear: bool = True
    press_after: int = 1
    release_after: int = 1


@dataclass
class Pbp005Emulator:
    cfg: EmulatorConfig
    uc: Uc = field(init=False)
    events: list[Event] = field(default_factory=list)
    led_state: int = 0
    code_count: int = 0
    button_press_reads: int = 0
    button_release_reads: int = 0
    uart_tx: bytearray = field(default_factory=bytearray)
    uart_rx: bytearray = field(default_factory=bytearray)
    tick: int = 0
    timer_deadlines: dict[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self._map_memory()
        self._install_hooks()

    def _map_memory(self) -> None:
        mem = parse_ihex(self.cfg.firmware)
        flash = bytearray([0xFF]) * FLASH_SIZE
        for addr, value in mem.items():
            if FLASH_BASE <= addr < FLASH_BASE + FLASH_SIZE:
                flash[addr - FLASH_BASE] = value

        self.uc.mem_map(FLASH_BASE, FLASH_SIZE)
        self.uc.mem_write(FLASH_BASE, bytes(flash))
        self.uc.mem_map(RAM_BASE, RAM_SIZE)
        self.uc.mem_write(RAM_BASE, bytes(RAM_SIZE))
        self.uc.mem_map(MMIO_A_BASE, MMIO_A_SIZE)
        self.uc.mem_write(MMIO_A_BASE, bytes(MMIO_A_SIZE))
        self.uc.mem_map(MMIO_4_BASE, MMIO_4_SIZE)
        self.uc.mem_write(MMIO_4_BASE, bytes(MMIO_4_SIZE))
        self.uc.mem_map(SCS_BASE, SCS_SIZE)
        self.uc.mem_write(SCS_BASE, bytes(SCS_SIZE))

        initial_sp = unpack32(bytes(flash[0:4]))
        if not (RAM_BASE <= initial_sp < RAM_BASE + RAM_SIZE):
            initial_sp = RAM_BASE + RAM_SIZE - 0x100
        self.uc.reg_write(UC_ARM_REG_SP, initial_sp)
        self.uc.reg_write(UC_ARM_REG_XPSR, 0x01000000)

        # Default unpressed button. IND_SW is active-low in the analyzed code.
        self.write32(IND_SW_W15, 1)
        self.write32(I2C_STATUS_REG, I2C_STATUS_READY_MASK)

    def _install_hooks(self) -> None:
        self.uc.hook_add(UC_HOOK_CODE, self._hook_code)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._hook_mem_write)
        self.uc.hook_add(UC_HOOK_MEM_READ, self._hook_mem_read)
        self.uc.hook_add(UC_HOOK_MEM_INVALID, self._hook_mem_invalid)

    def read32(self, addr: int) -> int:
        return unpack32(self.uc.mem_read(addr, 4))

    def write32(self, addr: int, value: int) -> None:
        self.uc.mem_write(addr, pack32(value))

    def read8(self, addr: int) -> int:
        return self.uc.mem_read(addr, 1)[0]

    def read16(self, addr: int) -> int:
        return struct.unpack("<H", self.uc.mem_read(addr, 2))[0]

    def read_s16(self, addr: int) -> int:
        return struct.unpack("<h", self.uc.mem_read(addr, 2))[0]

    def read_s32(self, addr: int) -> int:
        return struct.unpack("<i", self.uc.mem_read(addr, 4))[0]

    def write8(self, addr: int, value: int) -> None:
        self.uc.mem_write(addr, bytes([value & 0xFF]))

    def write16(self, addr: int, value: int) -> None:
        self.uc.mem_write(addr, struct.pack("<H", value & 0xFFFF))

    def log(self, kind: str, pc: int, detail: str) -> None:
        self.events.append(Event(kind, pc, detail))

    def _return_from_stub(self, r0: int | None = None) -> None:
        if r0 is not None:
            self.uc.reg_write(UC_ARM_REG_R0, r0 & 0xFFFFFFFF)
        lr = self.uc.reg_read(UC_ARM_REG_LR)
        self.uc.reg_write(UC_ARM_REG_PC, lr)

    def _hook_code(self, uc: Uc, address: int, size: int, user_data: object) -> None:
        self.code_count += 1
        if self.code_count > self.cfg.max_instructions:
            self.log("stop", address, f"max instruction count {self.cfg.max_instructions} reached")
            uc.emu_stop()
            return

        if address == STOP_ADDR:
            self.log("stop", address, "function returned")
            uc.emu_stop()
            return

        if address in self.cfg.stop_pcs:
            regs = (
                f"lr=0x{uc.reg_read(UC_ARM_REG_LR):08X} "
                f"r0=0x{uc.reg_read(UC_ARM_REG_R0):08X} "
                f"r1=0x{uc.reg_read(UC_ARM_REG_R1):08X} "
                f"r2=0x{uc.reg_read(UC_ARM_REG_R2):08X} "
                f"r3=0x{uc.reg_read(UC_ARM_REG_R3):08X} "
                f"r4=0x{uc.reg_read(UC_ARM_REG_R4):08X} "
                f"r5=0x{uc.reg_read(UC_ARM_REG_R5):08X} "
                f"r6=0x{uc.reg_read(UC_ARM_REG_R6):08X} "
                f"r7=0x{uc.reg_read(UC_ARM_REG_R7):08X} "
                f"sp=0x{uc.reg_read(UC_ARM_REG_SP):08X}"
            )
            self.log("stop-pc", address, regs)
            uc.emu_stop()
            return

        if address == FN_TIMER_START:
            timer = uc.reg_read(UC_ARM_REG_R0)
            timeout = uc.reg_read(UC_ARM_REG_R1)
            if self.cfg.timer_model:
                deadline = (self.tick + timeout) & 0xFFFFFFFFFFFFFFFF
                self.timer_deadlines[timer] = deadline
                self.write32(timer, deadline & 0xFFFFFFFF)
                self.write32(timer + 4, (deadline >> 32) & 0xFFFFFFFF)
                self.write8(timer + 7, (self.read8(timer + 7) | 0x80) & ~0x40)
                self.log(
                    "stub",
                    address,
                    f"timer_start(timer=0x{timer:08X}, timeout={timeout}) deadline={deadline} tick={self.tick}",
                )
            else:
                self.log("stub", address, f"timer_start(timer=0x{timer:08X}, timeout={timeout})")
                self.write32(timer, timeout)
            self._return_from_stub(0)
            return

        if address == FN_TIMER_EXPIRED:
            timer = uc.reg_read(UC_ARM_REG_R0)
            if timer in self.cfg.expired_timers:
                result = 1
                detail = "forced"
            elif self.cfg.timer_model:
                deadline = self.timer_deadlines.get(timer)
                if deadline is None:
                    deadline = self.read32(timer) | (self.read32(timer + 4) << 32)
                result = 1 if self.tick >= deadline else 0
                if result:
                    self.write8(timer + 7, self.read8(timer + 7) | 0x40)
                detail = f"tick={self.tick} deadline={deadline}"
            else:
                result = self.cfg.timer_expired
                detail = "fixed"
            self.log("stub", address, f"timer_expired(timer=0x{timer:08X}) -> {result} ({detail})")
            self._return_from_stub(result)
            return

        if address in (FN_DELAY, FN_BUSY_WAIT_A, FN_BUSY_WAIT_B, FN_TICK_WAIT):
            delay = uc.reg_read(UC_ARM_REG_R0)
            if self.cfg.timer_model:
                self.tick += delay
            self.log("stub", address, f"delay/busy_wait({delay})")
            self._return_from_stub(0)
            return

        if address == FN_DEBUG_LOG:
            self._stub_debug_log(address)
            return

        if address == FN_UART_CHAR_SINK:
            self._stub_uart_char_sink(address)
            return

        if address == FN_UART_SERVICE_PUMP:
            self._stub_uart_service_pump(address)
            return

        if address == FN_SMBUS_TRANSFER:
            self._stub_smbus_transfer(address)
            return

        if address == FN_NVM_UPDATE_WORDS:
            self._stub_nvm_update(address)
            return

        if address == 0x4EA4:
            self.button_press_reads += 1
            value = 1 if self.button_press_reads <= self.cfg.press_after else 0
            self.write32(IND_SW_W15, value)
            self.log("button", address, f"press-loop read #{self.button_press_reads}: GPIO_W15={value}")
            return

        if address == 0x4EAA:
            self.button_release_reads += 1
            value = 0 if self.button_release_reads <= self.cfg.release_after else 1
            self.write32(IND_SW_W15, value)
            self.log("button", address, f"release-loop read #{self.button_release_reads}: GPIO_W15={value}")
            return

        if self.cfg.trace:
            self.log("trace", address, f"instruction size={size}")

    def _hook_mem_read(self, uc: Uc, access: int, address: int, size: int, value: int, user_data: object) -> None:
        if address == IND_SW_W15:
            current = self.read32(IND_SW_W15)
            self.log("mmio-read", self.uc.reg_read(UC_ARM_REG_PC), f"GPIO_W15/IND_SW -> {current}")
        elif address == I2C_STATUS_REG and size == 4:
            self.write32(I2C_STATUS_REG, self.read32(I2C_STATUS_REG) | I2C_STATUS_READY_MASK)

    def _hook_mem_write(self, uc: Uc, access: int, address: int, size: int, value: int, user_data: object) -> None:
        pc = self.uc.reg_read(UC_ARM_REG_PC)
        if address == GPIO_SET0 and size == 4:
            self.led_state |= value
            self.log("gpio", pc, f"GPIO_SET0 0x{value:08X} ({led_names(value)}) -> LED_STATE 0x{self.led_state:08X}")
        elif address == GPIO_CLR0 and size == 4:
            self.led_state &= ~value
            self.log("gpio", pc, f"GPIO_CLR0 0x{value:08X} ({led_names(value)}) -> LED_STATE 0x{self.led_state:08X}")
        elif address == GPIO_NOT0 and size == 4:
            self.led_state ^= value
            self.log("gpio", pc, f"GPIO_NOT0 0x{value:08X} ({led_names(value)}) -> LED_STATE 0x{self.led_state:08X}")
        elif address == GPIO_W20 and size in (1, 2, 4):
            if value:
                self.led_state |= 0x00100000
            else:
                self.led_state &= ~0x00100000
            self.log("gpio", pc, f"GPIO_W20/LED4 <- {value} -> LED_STATE 0x{self.led_state:08X}")
        elif address <= BMS_CTX_BASE + 0x08 < address + size:
            old = int.from_bytes(self.uc.mem_read(address, size), "little")
            self.log(
                "bms-write",
                pc,
                f"ctx+0x08 via 0x{address:08X} size={size} 0x{old:0{size * 2}X}->0x{value:0{size * 2}X}",
            )
        elif NVM_BASE <= address < NVM_END:
            old = int.from_bytes(self.uc.mem_read(address, size), "little")
            self.log(
                "nvm-write",
                pc,
                f"direct 0x{address:08X} size={size} 0x{old:0{size * 2}X}->0x{value:0{size * 2}X}",
            )

    def _hook_mem_invalid(
        self,
        uc: Uc,
        access: int,
        address: int,
        size: int,
        value: int,
        user_data: object,
    ) -> bool:
        kind = "read" if access == UC_MEM_READ_UNMAPPED else "write" if access == UC_MEM_WRITE_UNMAPPED else "mem"
        pc = self.uc.reg_read(UC_ARM_REG_PC)
        self.log("invalid", pc, f"{kind} unmapped 0x{address:08X} size={size} value=0x{value:X}")
        return False

    def _stub_uart_char_sink(self, pc: int) -> None:
        out_ptr = self.uc.reg_read(UC_ARM_REG_R0)
        char = self.uc.reg_read(UC_ARM_REG_R1) & 0xFF
        if out_ptr == 0:
            self.uart_tx.append(char)
            printable = chr(char) if 32 <= char < 127 or char in (10, 13, 9) else f"\\x{char:02X}"
            self.log("uart-tx", pc, f"0x{char:02X} {printable!r}")
            self._return_from_stub(0)
            return
        self.write8(out_ptr, char)
        self._return_from_stub(out_ptr + 1)

    def read_c_string(self, addr: int, max_len: int = 512) -> str:
        data = bytearray()
        for offset in range(max_len):
            byte = self.read8(addr + offset)
            if byte == 0:
                break
            data.append(byte)
        return bytes(data).decode("ascii", "replace")

    def _format_c_string(self, fmt: str, args: list[int]) -> str:
        out: list[str] = []
        arg_index = 0
        i = 0
        while i < len(fmt):
            if fmt[i] != "%":
                out.append(fmt[i])
                i += 1
                continue
            i += 1
            if i < len(fmt) and fmt[i] == "%":
                out.append("%")
                i += 1
                continue
            width_s = ""
            zero_pad = False
            if i < len(fmt) and fmt[i] == "0":
                zero_pad = True
                i += 1
            while i < len(fmt) and fmt[i].isdigit():
                width_s += fmt[i]
                i += 1
            if i >= len(fmt):
                out.append("%" + width_s)
                break
            spec = fmt[i]
            i += 1
            value = args[arg_index] if arg_index < len(args) else 0
            arg_index += 1
            width = int(width_s) if width_s else 0
            pad = "0" if zero_pad else " "
            if spec in "u":
                text = str(value & 0xFFFFFFFF)
            elif spec in "d":
                signed = value & 0xFFFFFFFF
                if signed & 0x80000000:
                    signed -= 0x100000000
                text = str(signed)
            elif spec == "X":
                text = f"{value & 0xFFFFFFFF:X}"
            elif spec == "x":
                text = f"{value & 0xFFFFFFFF:x}"
            elif spec == "c":
                text = chr(value & 0xFF)
            elif spec == "s":
                text = self.read_c_string(value)
            else:
                text = "%" + width_s + spec
            if width:
                text = text.rjust(width, pad)
            out.append(text)
        return "".join(out)

    def _stub_debug_log(self, pc: int) -> None:
        fmt_addr = self.uc.reg_read(UC_ARM_REG_R0)
        sp = self.uc.reg_read(UC_ARM_REG_SP)
        args = [
            self.uc.reg_read(UC_ARM_REG_R1),
            self.uc.reg_read(UC_ARM_REG_R2),
            self.uc.reg_read(UC_ARM_REG_R3),
        ]
        for index in range(8):
            args.append(self.read32(sp + index * 4))
        fmt = self.read_c_string(fmt_addr)
        if self.read8(LOG_ENABLED_ADDR) != 1:
            self.log("debug-log", pc, f"disabled fmt={fmt!r}")
            self._return_from_stub(0)
            return
        text = self._format_c_string(fmt, args)
        data = text.encode("ascii", "replace")
        self.uart_tx.extend(data)
        self.log("debug-log", pc, f"{fmt!r} -> {text!r}")
        self._return_from_stub(0)

    def ring_init(self, ring: int, buf: int, size: int = HARNESS_UART_RING_SIZE) -> None:
        self.write32(ring, buf)
        self.write16(ring + 4, 0)
        self.write16(ring + 6, 0)
        self.write16(ring + 8, size)

    def ring_put(self, ring: int, value: int) -> bool:
        buf = self.read32(ring)
        write_index = struct.unpack("<H", self.uc.mem_read(ring + 4, 2))[0]
        read_index = struct.unpack("<H", self.uc.mem_read(ring + 6, 2))[0]
        size = struct.unpack("<H", self.uc.mem_read(ring + 8, 2))[0]
        next_index = write_index + 1
        if next_index >= size:
            next_index = 0
        if next_index == read_index:
            return False
        self.write8(buf + write_index, value)
        self.write16(ring + 4, next_index)
        return True

    def ring_get(self, ring: int) -> int | None:
        buf = self.read32(ring)
        write_index = struct.unpack("<H", self.uc.mem_read(ring + 4, 2))[0]
        read_index = struct.unpack("<H", self.uc.mem_read(ring + 6, 2))[0]
        size = struct.unpack("<H", self.uc.mem_read(ring + 8, 2))[0]
        if read_index == write_index:
            return None
        value = self.read8(buf + read_index)
        read_index += 1
        if read_index >= size:
            read_index = 0
        self.write16(ring + 6, read_index)
        return value

    def ring_dump(self, ring: int, limit: int = 256) -> bytes:
        out = bytearray()
        for _ in range(limit):
            value = self.ring_get(ring)
            if value is None:
                break
            out.append(value)
        return bytes(out)

    def _stub_uart_service_pump(self, pc: int) -> None:
        uart_id = self.uc.reg_read(UC_ARM_REG_R0)
        tx_ring = self.uc.reg_read(UC_ARM_REG_R1)
        rx_ring = self.uc.reg_read(UC_ARM_REG_R2)
        tx_count = 0
        if tx_ring:
            while True:
                value = self.ring_get(tx_ring)
                if value is None:
                    break
                self.uart_tx.append(value)
                tx_count += 1
        rx_count = 0
        rx_dropped = 0
        if rx_ring:
            while self.uart_rx:
                value = self.uart_rx.pop(0)
                if not self.ring_put(rx_ring, value):
                    self.uart_rx.insert(0, value)
                    break
                rx_count += 1
        elif self.uart_rx:
            rx_dropped = len(self.uart_rx)
            self.uart_rx.clear()
        self.log(
            "uart-pump",
            pc,
            f"uart={uart_id} tx_drained={tx_count} rx_inserted={rx_count} rx_dropped={rx_dropped}",
        )
        self._return_from_stub(0)

    def _stub_nvm_update(self, pc: int) -> None:
        src = self.uc.reg_read(UC_ARM_REG_R0)
        dst = self.uc.reg_read(UC_ARM_REG_R1)
        word_count = self.uc.reg_read(UC_ARM_REG_R2)
        status = self.nvm_update_words(src, dst, word_count, pc)
        self._return_from_stub(status)

    def nvm_update_words(self, src: int, dst: int, word_count: int, pc: int = FN_NVM_UPDATE_WORDS) -> int:
        if dst < NVM_BASE or dst + word_count * 4 > NVM_END or dst & 3:
            self.log("nvm", pc, f"reject src=0x{src:08X} dst=0x{dst:08X} words={word_count}")
            return 1
        self.log("nvm", pc, f"update src=0x{src:08X} dst=0x{dst:08X} words={word_count}")
        for index in range(word_count):
            addr = dst + index * 4
            new = self.read32(src + index * 4)
            old = self.read32(addr)
            if old != new:
                self.log("nvm-write", pc, f"0x{addr:08X}: 0x{old:08X}->0x{new:08X}")
            else:
                self.log("nvm-write", pc, f"0x{addr:08X}: unchanged 0x{old:08X}")
            self.write32(addr, new)
        return 0

    def _stub_smbus_transfer(self, pc: int) -> None:
        bus = self.uc.reg_read(UC_ARM_REG_R0)
        slave = self.uc.reg_read(UC_ARM_REG_R1) & 0xFF
        tx_buf = self.uc.reg_read(UC_ARM_REG_R2)
        tx_len = self.uc.reg_read(UC_ARM_REG_R3) & 0xFFFF
        sp = self.uc.reg_read(UC_ARM_REG_SP)
        rx_buf = self.read32(sp)
        rx_len = self.read32(sp + 4) & 0xFFFF

        tx = list(self.uc.mem_read(tx_buf, tx_len)) if tx_len else []
        detail = (
            f"bus={bus} slave=0x{slave:02X} tx={bytes(tx).hex(' ').upper()} "
            f"rx_buf=0x{rx_buf:08X} rx_len={rx_len}"
        )

        if self.cfg.afe_fail or slave != AFE_ADDR or not tx:
            self.log("smbus", pc, detail + " -> fail")
            self._return_from_stub(1)
            return

        command = tx[0]
        if tx_len == 1 and rx_len:
            word = self.cfg.afe_regs.get(command, 0) & 0xFFFF
            hi = (word >> 8) & 0xFF
            lo = word & 0xFF
            pec = crc8_pec([AFE_WR_ADDR, command, AFE_RD_ADDR, hi, lo])
            response = bytes([hi, lo, pec])[:rx_len]
            self.uc.mem_write(rx_buf, response)
            self.log(
                "smbus",
                pc,
                detail + f" -> read cmd=0x{command:02X} word=0x{word:04X} pec=0x{pec:02X}",
            )
            self._return_from_stub(0)
            return

        if tx_len >= 3:
            word = ((tx[1] << 8) | tx[2]) & 0xFFFF
            old = self.cfg.afe_regs.get(command, 0)
            stored = word
            side_effect = ""
            if self.cfg.afe_status_clear and command == 0x02 and word == 0xFFFF:
                stored = old & ~0x1000
                side_effect = f" clear-status=>0x{stored:04X}"
            self.cfg.afe_regs[command] = stored
            pec = tx[3] if tx_len >= 4 else None
            calc = crc8_pec([AFE_WR_ADDR, command, tx[1], tx[2]])
            pec_note = "" if pec is None else f" pec=0x{pec:02X} calc=0x{calc:02X}"
            self.log(
                "smbus",
                pc,
                detail + f" -> write cmd=0x{command:02X} 0x{old:04X}->0x{word:04X}{pec_note}{side_effect}",
            )
            self._return_from_stub(0)
            return

        self.log("smbus", pc, detail + " -> unsupported")
        self._return_from_stub(1)

    def call(
        self,
        addr: int,
        r0: int = 0,
        r1: int = 0,
        r2: int = 0,
        r3: int = 0,
        stack_args: list[int] | None = None,
    ) -> None:
        sp = self.uc.reg_read(UC_ARM_REG_SP)
        for index, value in enumerate(stack_args or []):
            self.write32(sp + index * 4, value)
        self.uc.reg_write(UC_ARM_REG_R0, r0)
        self.uc.reg_write(UC_ARM_REG_R1, r1)
        self.uc.reg_write(UC_ARM_REG_R2, r2)
        self.uc.reg_write(UC_ARM_REG_R3, r3)
        self.uc.reg_write(UC_ARM_REG_LR, STOP_ADDR | 1)
        self.uc.reg_write(UC_ARM_REG_PC, addr | 1)
        try:
            self.uc.emu_start(addr | 1, STOP_ADDR, count=self.cfg.max_instructions)
        except UcError as exc:
            pc = self.uc.reg_read(UC_ARM_REG_PC)
            self.log("error", pc, f"Unicorn error: {exc}")

    def set_service_state(self, state: int, timeout: int) -> None:
        self.call(FN_SET_STATE, state, timeout)

    def run_service_fsm(self) -> None:
        self.call(FN_SERVICE_FSM)

    def run_periodic_tick(self) -> None:
        self.tick += 1
        self.call(FN_PERIODIC_TICK)

    def drain_events(self) -> list[Event]:
        events = self.events
        self.events = []
        return events

    def run_button_wait(self) -> None:
        self.call(FN_WAIT_BUTTON)

    def classify_voltage(self, millivolts: int) -> int:
        self.call(FN_VOLTAGE_CLASS, millivolts)
        return self.uc.reg_read(UC_ARM_REG_R0)

    def map_bms_to_service(
        self,
        bms_state: int,
        cell_mv: int,
        flag_byte: int = 0,
        gate_byte: int = 0,
        special_flag: int = 0,
        extra_arg: int = 0,
    ) -> None:
        self.write8(HARNESS_FLAG_PTR, flag_byte)
        self.write8(HARNESS_GATE_PTR, gate_byte)
        self.write8(SPECIAL_FLAG_ADDR, special_flag)
        self.write16(HARNESS_MEAS_PTR + 0x0A, cell_mv)
        self.call(
            FN_BMS_TO_SERVICE_STATE,
            bms_state,
            HARNESS_FLAG_PTR,
            HARNESS_GATE_PTR,
            HARNESS_MEAS_PTR,
            stack_args=[extra_arg],
        )

    def afe_read_word(self, command: int) -> tuple[int, int]:
        self.write16(HARNESS_AFE_OUT, 0)
        self.call(FN_AFE_READ_WORD, command, HARNESS_AFE_OUT)
        status = self.uc.reg_read(UC_ARM_REG_R0)
        word = struct.unpack("<H", self.uc.mem_read(HARNESS_AFE_OUT, 2))[0]
        return status, word

    def afe_cell_voltage(self, index: int) -> tuple[int, int]:
        self.write16(HARNESS_AFE_OUT, 0)
        self.call(FN_AFE_CELL_VOLTAGE, index, HARNESS_AFE_OUT)
        status = self.uc.reg_read(UC_ARM_REG_R0)
        millivolts = struct.unpack("<H", self.uc.mem_read(HARNESS_AFE_OUT, 2))[0]
        return status, millivolts

    def afe_adoc_status(self) -> int:
        self.call(FN_AFE_ADOC_STATUS)
        return self.uc.reg_read(UC_ARM_REG_R0)

    def afe_set_balance(self, cell_index: int) -> int:
        self.call(FN_AFE_SET_BALANCE, cell_index)
        return self.uc.reg_read(UC_ARM_REG_R0)

    def afe_measurement_update(self, reuse_existing: bool = False) -> int:
        self.uc.mem_write(HARNESS_MEAS_PTR, bytes(0x40))
        self.call(FN_AFE_MEASUREMENT_UPDATE, HARNESS_MEAS_PTR, 1 if reuse_existing else 0)
        return self.uc.reg_read(UC_ARM_REG_R0)

    def nvm_read_words(self, addr: int, word_count: int) -> list[int]:
        return [self.read32(addr + index * 4) for index in range(word_count)]

    def nvm_write_values(self, addr: int, values: list[int]) -> int:
        for index, value in enumerate(values):
            self.write32(HARNESS_NVM_SRC + index * 4, value)
        self.call(FN_NVM_UPDATE_WORDS, HARNESS_NVM_SRC, addr, len(values))
        return self.uc.reg_read(UC_ARM_REG_R0)

    def fault_persist_sequence(self, current_fault: int, old_word: int, force_first: bool = False) -> tuple[int, int]:
        self.write32(NVM_FAULT_WORD, old_word)
        current_fault &= 0xFF
        word = old_word & 0xFFFFFFFF
        first_written = word
        if force_first or (word & 0xFF) != current_fault:
            first_written = (current_fault | ((word << 8) & 0xFFFFFFFF)) & 0xFFFFFFFF
            self.write32(HARNESS_NVM_SRC, first_written)
            self.nvm_update_words(HARNESS_NVM_SRC, NVM_FAULT_WORD, 1, pc=0x4C1C)
            word = first_written
        final_word = (word << 8) & 0xFFFFFFFF
        self.write32(HARNESS_NVM_SRC, final_word)
        self.nvm_update_words(HARNESS_NVM_SRC, NVM_FAULT_WORD, 1, pc=0x4CA8)
        return first_written, final_word

    def run_bms_runtime(self) -> None:
        self.set_log_enabled(True)
        self.call(0x4268)

    def set_log_enabled(self, enabled: bool = True) -> None:
        self.write8(LOG_ENABLED_ADDR, 1 if enabled else 0)

    def write_c_string(self, addr: int, text: str) -> None:
        data = text.encode("ascii", "replace") + b"\x00"
        self.uc.mem_write(addr, data)

    def run_debug_log_text(self, text: str, args: list[int] | None = None) -> bytes:
        values = list(args or [])
        while len(values) < 3:
            values.append(0)
        self.set_log_enabled(True)
        self.write_c_string(HARNESS_UART_TEXT, text)
        self.call(FN_DEBUG_LOG, HARNESS_UART_TEXT, values[0], values[1], values[2])
        return bytes(self.uart_tx)

    def run_uart_pump(self, rx: bytes = b"", tx: bytes = b"") -> bytes:
        self.ring_init(HARNESS_UART_TX_RING, HARNESS_UART_TX_BUF)
        self.ring_init(HARNESS_UART_RX_RING, HARNESS_UART_RX_BUF)
        for byte in tx:
            self.ring_put(HARNESS_UART_TX_RING, byte)
        self.uart_rx.extend(rx)
        self.call(FN_UART_SERVICE_PUMP, 0, HARNESS_UART_TX_RING, HARNESS_UART_RX_RING)
        return self.ring_dump(HARNESS_UART_RX_RING)

    def run_uart_runtime_service(self, rx: bytes = b"") -> None:
        self.set_log_enabled(True)
        self.uart_rx.extend(rx)
        self.call(FN_UART_RUNTIME_SERVICE)

    def service_state(self) -> int:
        return self.read8(SERVICE_BASE)

    def service_prev_state(self) -> int:
        return self.read8(SERVICE_BASE + 1)

    def service_substate(self) -> int:
        return self.read8(SERVICE_BASE + 2)

    def print_events(self) -> None:
        for event in self.events:
            print(f"{event.kind:10s} pc=0x{event.pc:04X} {event.detail}")

    def print_summary(self) -> None:
        state = self.service_state()
        print(f"service_state=0x{state:02X} ({SERVICE_STATES.get(state, 'unknown')})")
        print(f"previous_state=0x{self.service_prev_state():02X}")
        print(f"substate=0x{self.service_substate():02X}")
        print(f"led_state=0x{self.led_state:08X} ({led_names(self.led_state)})")
        if self.uart_tx:
            decoded = bytes(self.uart_tx).decode("ascii", "replace")
            print(f"uart_tx={decoded!r}")
        print(f"pc=0x{self.uc.reg_read(UC_ARM_REG_PC):08X}")
        print(f"lr=0x{self.uc.reg_read(UC_ARM_REG_LR):08X}")
        print(f"instructions={self.code_count}")

    def print_bms_summary(self) -> None:
        fault = self.read8(BMS_CTX_BASE + 0x08)
        state = self.read8(BMS_CTX_BASE + 0x0D)
        previous_fault = self.read8(BMS_CTX_BASE + 0x0F)
        auto_clear = self.read32(BMS_CTX_BASE + 0x18)
        persistent = self.read32(BMS_CTX_BASE + 0x1C)
        latch = bytes(self.uc.mem_read(EVENT_LATCH_BASE, 5))
        print(f"bms_ctx=0x{BMS_CTX_BASE:08X}")
        print(f"bms_fault_event=0x{fault:02X}")
        print(f"bms_state=0x{state:02X}")
        print(f"bms_previous_fault=0x{previous_fault:02X}")
        print(f"bms_auto_clear_counter={auto_clear}")
        print(f"bms_persistent_word=0x{persistent:08X}")
        print(f"event_latch={latch.hex(' ').upper()}")
        print(f"nvm_7e94=0x{self.read32(NVM_FAULT_WORD):08X}")

    def print_measurement_summary(self) -> None:
        base = HARNESS_MEAS_PTR
        cells = [self.read16(base + index * 2) for index in range(5)]
        raw = bytes(self.uc.mem_read(base, 0x30))
        print(f"measurement_ptr=0x{base:08X}")
        print("cells_mv=" + ",".join(str(value) for value in cells))
        print(f"min_cell_mv={self.read16(base + 0x0A)}")
        print(f"max_cell_mv={self.read16(base + 0x0C)}")
        print(f"cell_sum_mv={self.read16(base + 0x0E)}")
        print(f"min_cell_index={self.read8(base + 0x10)}")
        print(f"max_cell_index={self.read8(base + 0x11)}")
        print(f"value_14={self.read_s32(base + 0x14)}")
        print(f"filtered_18={self.read_s32(base + 0x18)}")
        print(f"temperature_1c={self.read_s16(base + 0x1C)}")
        print(f"accumulator_20=0x{self.read32(base + 0x20):08X}")
        print(f"computed_limit_24={self.read_s32(base + 0x24)}")
        print(f"predicted_28={self.read_s32(base + 0x28)}")
        print(f"raw_struct={raw.hex(' ').upper()}")


def parse_int(value: str) -> int:
    return int(value, 0)


def parse_assignment(value: str) -> tuple[int, int]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected ADDR=VALUE")
    addr_s, value_s = value.split("=", 1)
    return int(addr_s, 0), int(value_s, 0)


def parse_bytes(value: str) -> bytes:
    value = value.strip()
    if not value:
        return b""
    if value.startswith("ascii:"):
        return value[6:].encode("ascii", "replace")
    cleaned = value.replace(",", " ").replace(":", " ")
    parts = [part for part in cleaned.split() if part]
    return bytes(int(part, 16) for part in parts)


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--firmware", type=Path, default=DEFAULT_FW)
    parser.add_argument("--max-instructions", type=int, default=20_000)
    parser.add_argument("--timer-expired", type=parse_int, default=0, help="stub return value for timer_expired")
    parser.add_argument("--timer-model", action="store_true", help="use emulator tick deadlines for timer_start/timer_expired")
    parser.add_argument(
        "--expire-timer",
        type=parse_int,
        action="append",
        default=[],
        help="force timer_expired(timer_addr) to return 1 for a specific timer object",
    )
    parser.add_argument("--stop-pc", type=parse_int, action="append", default=[], help="stop execution when PC reaches address")
    parser.add_argument("--afe-reg", type=parse_assignment, action="append", default=[], help="override emulated AFE word register: CMD=VALUE")
    parser.add_argument("--afe-fail", action="store_true", help="force emulated SMBus/AFE transfers to fail")
    parser.add_argument("--no-afe-status-clear", action="store_true", help="store AFE 0x02=0xFFFF literally instead of clear/ack side-effect")
    parser.add_argument("--trace", action="store_true")


def make_emulator(args: argparse.Namespace) -> Pbp005Emulator:
    afe_regs = dict(DEFAULT_AFE_REGS)
    for command, value in getattr(args, "afe_reg", []) or []:
        afe_regs[command & 0xFF] = value & 0xFFFF
    return Pbp005Emulator(
        EmulatorConfig(
            firmware=args.firmware,
            trace=args.trace,
            max_instructions=args.max_instructions,
            timer_expired=args.timer_expired,
            timer_model=getattr(args, "timer_model", False),
            expired_timers=set(getattr(args, "expire_timer", []) or []),
            stop_pcs=set(getattr(args, "stop_pc", []) or []),
            afe_regs=afe_regs,
            afe_fail=getattr(args, "afe_fail", False),
            afe_status_clear=not getattr(args, "no_afe_status_clear", False),
            press_after=getattr(args, "press_after", 1),
            release_after=getattr(args, "release_after", 1),
        )
    )


def cmd_led_state(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    emu.set_service_state(args.state, args.timeout)
    for _ in range(args.steps):
        emu.run_service_fsm()
    emu.print_events()
    emu.print_summary()
    return 0


def cmd_button_wait(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    emu.run_button_wait()
    emu.print_events()
    emu.print_summary()
    return 0


def apply_memory_writes(emu: Pbp005Emulator, args: argparse.Namespace) -> None:
    for addr, value in getattr(args, "mem8", []) or []:
        emu.write8(addr, value)
        emu.log("poke", 0, f"u8 [0x{addr:08X}] = 0x{value & 0xFF:02X}")
    for addr, value in getattr(args, "mem16", []) or []:
        emu.write16(addr, value)
        emu.log("poke", 0, f"u16 [0x{addr:08X}] = 0x{value & 0xFFFF:04X}")
    for addr, value in getattr(args, "mem32", []) or []:
        emu.write32(addr, value)
        emu.log("poke", 0, f"u32 [0x{addr:08X}] = 0x{value & 0xFFFFFFFF:08X}")


def cmd_run_func(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    apply_memory_writes(emu, args)
    emu.call(args.addr, args.r0, args.r1, args.r2, args.r3, stack_args=args.stack)
    emu.print_events()
    emu.print_summary()
    print(f"r0=0x{emu.uc.reg_read(UC_ARM_REG_R0):08X}")
    return 0


def cmd_voltage_class(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    result = emu.classify_voltage(args.mv)
    emu.print_events()
    print(f"cell_mv={args.mv}")
    print(f"voltage_class={result}")
    return 0


def cmd_bms_map(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    emu.map_bms_to_service(
        bms_state=args.bms_state,
        cell_mv=args.cell_mv,
        flag_byte=args.flag_byte,
        gate_byte=args.gate_byte,
        special_flag=args.special_flag,
        extra_arg=args.extra_arg,
    )
    for _ in range(args.led_steps):
        emu.run_service_fsm()
    emu.print_events()
    emu.print_summary()
    print(f"r0=0x{emu.uc.reg_read(UC_ARM_REG_R0):08X}")
    return 0


def cmd_afe_read(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    status, word = emu.afe_read_word(args.cmd)
    emu.print_events()
    print(f"cmd=0x{args.cmd & 0xFF:02X}")
    print(f"status={status}")
    print(f"word=0x{word:04X}")
    return 0


def cmd_afe_cell(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    status, millivolts = emu.afe_cell_voltage(args.index)
    emu.print_events()
    print(f"cell_index={args.index}")
    print(f"status={status}")
    print(f"millivolts={millivolts}")
    return 0


def cmd_afe_adoc(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    result = emu.afe_adoc_status()
    emu.print_events()
    print(f"adoc_status={result}")
    return 0


def cmd_afe_balance(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    status = emu.afe_set_balance(args.cell)
    emu.print_events()
    print(f"cell={args.cell}")
    print(f"status={status}")
    print(f"afe_reg_0e=0x{emu.cfg.afe_regs.get(0x0E, 0):04X}")
    return 0


def cmd_afe_scan(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    cells: list[int] = []
    statuses: list[int] = []
    for index in range(1, 6):
        status, millivolts = emu.afe_cell_voltage(index)
        statuses.append(status)
        cells.append(millivolts)
    raw20_status, raw20 = emu.afe_read_word(0x20)
    raw27_status, raw27 = emu.afe_read_word(0x27)
    adoc = emu.afe_adoc_status()
    emu.print_events()
    print("cells_mv=" + ",".join(str(value) for value in cells))
    print(f"cell_min_mv={min(cells)}")
    print(f"cell_max_mv={max(cells)}")
    print(f"cell_spread_mv={max(cells) - min(cells)}")
    print(f"cell_statuses={','.join(str(status) for status in statuses)}")
    print(f"raw_0x20_status={raw20_status}")
    print(f"raw_0x20=0x{raw20:04X}")
    print(f"raw_0x27_status={raw27_status}")
    print(f"raw_0x27=0x{raw27:04X}")
    print(f"adoc_status={adoc}")
    return 0


def cmd_afe_measure_update(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    status = emu.afe_measurement_update(reuse_existing=args.reuse_existing)
    emu.print_events()
    print(f"status={status}")
    emu.print_measurement_summary()
    return 0


def cmd_nvm_read(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    values = emu.nvm_read_words(args.addr, args.words)
    for index, value in enumerate(values):
        print(f"0x{args.addr + index * 4:08X}: 0x{value:08X}")
    return 0


def cmd_nvm_update(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    status = emu.nvm_write_values(args.addr, args.value)
    emu.print_events()
    print(f"status={status}")
    for index, value in enumerate(emu.nvm_read_words(args.addr, len(args.value))):
        print(f"0x{args.addr + index * 4:08X}: 0x{value:08X}")
    return 0


def cmd_fault_persist(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    first, final = emu.fault_persist_sequence(args.current_fault, args.old_word, args.force_first)
    emu.print_events()
    print(f"current_fault=0x{args.current_fault & 0xFF:02X}")
    print(f"old_word=0x{args.old_word & 0xFFFFFFFF:08X}")
    print(f"first_word=0x{first:08X}")
    print(f"final_word=0x{final:08X}")
    print(f"nvm_7e94=0x{emu.read32(NVM_FAULT_WORD):08X}")
    return 0


def cmd_uart_send(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    for byte in args.text.encode("ascii", "replace"):
        emu.call(FN_UART_CHAR_SINK, 0, byte)
    emu.print_events()
    emu.print_summary()
    return 0


def cmd_log_text(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    emu.run_debug_log_text(args.text, args.arg)
    emu.print_events()
    emu.print_summary()
    return 0


def cmd_uart_pump(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    rx_inserted = emu.run_uart_pump(rx=args.rx, tx=args.tx)
    emu.print_events()
    emu.print_summary()
    print(f"rx_ring={rx_inserted.hex(' ').upper()}")
    return 0


def cmd_uart_runtime_pump(args: argparse.Namespace) -> int:
    emu = make_emulator(args)
    emu.run_uart_runtime_service(rx=args.rx)
    emu.print_events()
    emu.print_summary()
    print(f"uart_rx_remaining={bytes(emu.uart_rx).hex(' ').upper()}")
    return 0


def cmd_bms_runtime(args: argparse.Namespace) -> int:
    args.timer_model = True
    emu = make_emulator(args)
    emu.run_bms_runtime()
    emu.print_events()
    emu.print_summary()
    emu.print_bms_summary()
    return 0


def ticker_event_visible(event: Event, show_timers: bool) -> bool:
    if show_timers or event.kind != "stub":
        return True
    if event.detail.startswith("timer_start"):
        return True
    if event.detail.startswith("timer_expired") and "-> 1" in event.detail:
        return True
    if event.detail.startswith("delay"):
        return True
    return False


def cmd_ticker(args: argparse.Namespace) -> int:
    args.timer_model = True
    emu = make_emulator(args)
    if args.state is not None:
        emu.set_service_state(args.state, args.timeout)
    for event in emu.drain_events():
        if ticker_event_visible(event, args.show_timers):
            print(f"[tick {emu.tick:06d}] {event.kind:10s} pc=0x{event.pc:04X} {event.detail}")

    for _ in range(args.ticks):
        emu.run_periodic_tick()
        events = [event for event in emu.drain_events() if ticker_event_visible(event, args.show_timers)]
        if args.print_each or events:
            if args.print_each:
                print(
                    f"[tick {emu.tick:06d}] state=0x{emu.service_state():02X} "
                    f"sub=0x{emu.service_substate():02X} led=0x{emu.led_state:08X} ({led_names(emu.led_state)})"
                )
            for event in events:
                print(f"[tick {emu.tick:06d}] {event.kind:10s} pc=0x{event.pc:04X} {event.detail}")
        if args.realtime_ms:
            time.sleep(args.realtime_ms / 1000.0)

    emu.print_summary()
    print(f"emu_tick={emu.tick}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    led = sub.add_parser("led-state", help="set service state and run the LED/GPIO FSM")
    add_common_args(led)
    led.add_argument("--state", type=parse_int, required=True)
    led.add_argument("--timeout", type=parse_int, default=0x7D0)
    led.add_argument("--steps", type=int, default=1)
    led.set_defaults(func=cmd_led_state)

    btn = sub.add_parser("button-wait", help="run the IND_SW press/release wait helper at 0x4E98")
    add_common_args(btn)
    btn.add_argument("--press-after", type=int, default=1)
    btn.add_argument("--release-after", type=int, default=1)
    btn.set_defaults(func=cmd_button_wait)

    run = sub.add_parser("run-func", help="call an arbitrary firmware function")
    add_common_args(run)
    run.add_argument("addr", type=parse_int)
    run.add_argument("--r0", type=parse_int, default=0)
    run.add_argument("--r1", type=parse_int, default=0)
    run.add_argument("--r2", type=parse_int, default=0)
    run.add_argument("--r3", type=parse_int, default=0)
    run.add_argument("--stack", type=parse_int, action="append", default=[], help="append a 32-bit stack argument")
    run.add_argument("--mem8", type=parse_assignment, action="append", default=[], help="write u8 before call: ADDR=VALUE")
    run.add_argument("--mem16", type=parse_assignment, action="append", default=[], help="write u16 before call: ADDR=VALUE")
    run.add_argument("--mem32", type=parse_assignment, action="append", default=[], help="write u32 before call: ADDR=VALUE")
    run.set_defaults(func=cmd_run_func)

    vc = sub.add_parser("voltage-class", help="run PBP005 cell-voltage classifier at 0x4BC")
    add_common_args(vc)
    vc.add_argument("--mv", type=parse_int, required=True, help="cell voltage value passed in r0")
    vc.set_defaults(func=cmd_voltage_class)

    bms = sub.add_parser("bms-map", help="run 0x56AE BMS-state to service/LED-state mapper")
    add_common_args(bms)
    bms.add_argument("--bms-state", type=parse_int, required=True)
    bms.add_argument("--cell-mv", type=parse_int, default=3700)
    bms.add_argument("--flag-byte", type=parse_int, default=0, help="byte at r1 pointer; bit 3 gates special service state")
    bms.add_argument("--gate-byte", type=parse_int, default=0, help="byte at r2 pointer; bit 6 gates BMS state 0x03 branch")
    bms.add_argument("--special-flag", type=parse_int, default=0, help="byte read by helper 0x0C90 at 0x100003D8")
    bms.add_argument("--extra-arg", type=parse_int, default=0, help="first stack argument consumed by 0x56AE")
    bms.add_argument("--led-steps", type=int, default=0, help="run service FSM after mapping")
    bms.set_defaults(func=cmd_bms_map)

    ar = sub.add_parser("afe-read", help="run AFE3705_ReadRegister16_PEC at 0x17AC")
    add_common_args(ar)
    ar.add_argument("--cmd", type=parse_int, required=True)
    ar.set_defaults(func=cmd_afe_read)

    ac = sub.add_parser("afe-cell", help="run AFE cell-voltage read at 0x1A04")
    add_common_args(ac)
    ac.add_argument("--index", type=parse_int, required=True, help="cell index passed to firmware; command is 0x20+index")
    ac.set_defaults(func=cmd_afe_cell)

    aa = sub.add_parser("afe-adoc", help="run AFE ADOC status check at 0x1D48")
    add_common_args(aa)
    aa.set_defaults(func=cmd_afe_adoc)

    ab = sub.add_parser("afe-balance", help="run AFE balance control writer at 0x1CDC")
    add_common_args(ab)
    ab.add_argument("--cell", type=parse_int, required=True, help="0 disables; 1..5 selects balance bit")
    ab.set_defaults(func=cmd_afe_balance)

    asc = sub.add_parser("afe-scan", help="run a firmware-level AFE measurement snapshot")
    add_common_args(asc)
    asc.set_defaults(func=cmd_afe_scan)

    amu = sub.add_parser("afe-measure-update", help="run PBP005 AFE measurement update at 0x338")
    add_common_args(amu)
    amu.add_argument("--reuse-existing", action="store_true", help="skip the fresh cell scan path, matching r1 != 0")
    amu.set_defaults(func=cmd_afe_measure_update)

    nr = sub.add_parser("nvm-read", help="read emulated NVM words")
    add_common_args(nr)
    nr.add_argument("--addr", type=parse_int, default=NVM_FAULT_WORD)
    nr.add_argument("--words", type=int, default=1)
    nr.set_defaults(func=cmd_nvm_read)

    nu = sub.add_parser("nvm-update", help="call/stub PBP005 NVM_UpdateWords at 0x3788")
    add_common_args(nu)
    nu.add_argument("--addr", type=parse_int, required=True)
    nu.add_argument("--value", type=parse_int, action="append", required=True, help="32-bit word value; repeat for multiple words")
    nu.set_defaults(func=cmd_nvm_update)

    fp = sub.add_parser("fault-persist", help="simulate PBP005 state 0xFF fault-history writes to 0x7E94")
    add_common_args(fp)
    fp.add_argument("--current-fault", type=parse_int, required=True)
    fp.add_argument("--old-word", type=parse_int, default=0)
    fp.add_argument("--force-first", action="store_true", help="force the first current_fault | old<<8 write")
    fp.set_defaults(func=cmd_fault_persist)

    us = sub.add_parser("uart-send", help="send ASCII text through firmware char sink 0x15C6")
    add_common_args(us)
    us.add_argument("--text", required=True)
    us.set_defaults(func=cmd_uart_send)

    lt = sub.add_parser("log-text", help="call PBP005 debug logger 0x165C with a RAM format string")
    add_common_args(lt)
    lt.add_argument("--text", required=True)
    lt.add_argument("--arg", type=parse_int, action="append", default=[], help="up to three formatter args in r1-r3")
    lt.set_defaults(func=cmd_log_text)

    up = sub.add_parser("uart-pump", help="run/stub UART_ServicePump_RxTx 0x33CC with emulator ring buffers")
    add_common_args(up)
    up.add_argument("--rx", type=parse_bytes, default=b"", help="incoming UART bytes, e.g. '46 01' or 'ascii:HELLO'")
    up.add_argument("--tx", type=parse_bytes, default=b"", help="bytes preloaded in TX ring")
    up.set_defaults(func=cmd_uart_pump)

    urp = sub.add_parser("uart-runtime-pump", help="run firmware UART service path 0x16BA with incoming RX bytes")
    add_common_args(urp)
    urp.add_argument("--rx", type=parse_bytes, default=b"", help="incoming UART bytes for firmware UART service")
    urp.set_defaults(func=cmd_uart_runtime_pump)

    br = sub.add_parser("bms-runtime", help="run PBP005 full BMS runtime body at 0x4268 with peripheral stubs")
    add_common_args(br)
    br.add_argument("--press-after", type=int, default=1)
    br.add_argument("--release-after", type=int, default=1)
    br.set_defaults(func=cmd_bms_runtime)

    tick = sub.add_parser("ticker", help="run firmware periodic tick 0x4CD8 with emulator time")
    add_common_args(tick)
    tick.add_argument("--ticks", type=int, default=20, help="number of periodic ticks to execute")
    tick.add_argument("--state", type=parse_int, default=None, help="optional service/LED state to set before ticking")
    tick.add_argument("--timeout", type=parse_int, default=0x7D0)
    tick.add_argument("--print-each", action="store_true", help="print state summary for every tick")
    tick.add_argument("--show-timers", action="store_true", help="include non-expired timer polls in ticker output")
    tick.add_argument("--realtime-ms", type=int, default=0, help="sleep this many milliseconds after each tick")
    tick.set_defaults(func=cmd_ticker)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
