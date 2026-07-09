#!/usr/bin/env python3
"""Decode Digilent WaveForms I2C event CSV for the Ryobi AFE 0x29 trace."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path


AFE_ADDR_7BIT = 0x29
AFE_WR_ADDR = (AFE_ADDR_7BIT << 1) | 0
AFE_RD_ADDR = (AFE_ADDR_7BIT << 1) | 1


def crc8_pec(data: list[int]) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def load_events(path: Path) -> list[str]:
    events: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line == "Event":
            continue
        events.append(line)
    return events


def is_hex_token(token: str) -> bool:
    return len(token) >= 2 and token[0] == "h"


def hex_token_value(token: str) -> int:
    return int(token[1:], 16)


def parse_transactions(events: list[str]) -> list[dict[str, object]]:
    transactions: list[dict[str, object]] = []
    i = 0

    while i < len(events):
        if events[i] != "Start":
            i += 1
            continue

        i += 1
        if i >= len(events) or events[i] != "h29 WR":
            while i < len(events) and events[i] != "Stop":
                i += 1
            i += int(i < len(events))
            continue

        i += 1
        if i < len(events) and events[i] == "ACK":
            i += 1

        wr: list[int] = []
        rd: list[int] = []
        combined_read = False

        while i < len(events):
            token = events[i]

            if token == "Stop":
                i += 1
                break

            if token == "Re-Start":
                combined_read = True
                i += 1
                if i >= len(events) or events[i] != "h29 RD":
                    raise ValueError(f"Unexpected token after Re-Start at event {i}: {events[i]!r}")
                i += 1
                if i < len(events) and events[i] == "ACK":
                    i += 1

                while i < len(events):
                    token = events[i]
                    if token == "Stop":
                        i += 1
                        break
                    if is_hex_token(token):
                        rd.append(hex_token_value(token))
                    i += 1
                break

            if is_hex_token(token):
                wr.append(hex_token_value(token))
            i += 1

        transactions.append({"kind": "read" if combined_read else "write", "wr": wr, "rd": rd})

    return transactions


def raw12(word: int) -> int:
    return word & 0x0FFF


def cell_mv(word: int) -> float:
    return raw12(word) * 5 / 4


def decode_balance_word(word: int) -> str:
    if (word & 0xFFC0) != 0xFFC0:
        return "unknown 0x0E upper-mask pattern"
    enabled = [index for index in range(1, 6) if word & (1 << index)]
    if not enabled:
        return "balance off"
    return "balance cell bit(s): " + ",".join(str(index) for index in enabled)


def decode(path: Path) -> str:
    events = load_events(path)
    transactions = parse_transactions(events)

    command_counts: Counter[tuple[str, int]] = Counter()
    values: defaultdict[int, list[tuple[str, int]]] = defaultdict(list)
    extra_read_bytes: Counter[tuple[int, ...]] = Counter()
    pec_errors: list[str] = []

    for index, transaction in enumerate(transactions):
        wr = transaction["wr"]
        rd = transaction["rd"]
        if not isinstance(wr, list) or not wr:
            continue
        if not isinstance(rd, list):
            continue

        kind = str(transaction["kind"])
        command = wr[0]
        command_counts[(kind, command)] += 1

        if kind == "write":
            if len(wr) < 4:
                pec_errors.append(f"#{index}: short write command 0x{command:02X}: {wr!r}")
                continue
            data = wr[1:-1]
            pec = wr[-1]
            calc = crc8_pec([AFE_WR_ADDR, command, *data])
            if calc != pec:
                pec_errors.append(
                    f"#{index}: write 0x{command:02X} PEC got 0x{pec:02X}, calc 0x{calc:02X}"
                )
            if len(data) == 2:
                values[command].append((kind, (data[0] << 8) | data[1]))
            continue

        if len(rd) < 3:
            pec_errors.append(f"#{index}: short read command 0x{command:02X}: {rd!r}")
            continue

        word = (rd[0] << 8) | rd[1]
        pec = rd[2]
        calc = crc8_pec([AFE_WR_ADDR, command, AFE_RD_ADDR, rd[0], rd[1]])
        if calc != pec:
            pec_errors.append(
                f"#{index}: read 0x{command:02X} PEC got 0x{pec:02X}, calc 0x{calc:02X}"
            )
        if len(rd) > 3:
            extra_read_bytes[tuple(rd[3:])] += 1
        values[command].append((kind, word))

    cycles: list[dict[int, int]] = []
    current_cycle: dict[int, int] = {}
    repeated_commands = (0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x27)

    for transaction in transactions:
        wr = transaction["wr"]
        rd = transaction["rd"]
        if not isinstance(wr, list) or not wr or not isinstance(rd, list):
            continue
        kind = str(transaction["kind"])
        command = wr[0]
        if kind == "write" and command == 0x0E:
            if current_cycle:
                cycles.append(current_cycle)
            current_cycle = {}
        elif kind == "read" and command in repeated_commands and len(rd) >= 3:
            current_cycle[command] = (rd[0] << 8) | rd[1]
    if current_cycle:
        cycles.append(current_cycle)

    complete_cycles = [
        cycle for cycle in cycles if all(command in cycle for command in repeated_commands)
    ]

    lines: list[str] = []
    lines.append(f"file: {path}")
    lines.append(f"events: {len(events)}")
    lines.append(f"transactions: {len(transactions)} {dict(Counter(str(t['kind']) for t in transactions))}")
    lines.append(f"pec_errors: {len(pec_errors)}")
    if pec_errors:
        lines.extend(f"  {error}" for error in pec_errors[:10])
    lines.append(f"extra_read_bytes: {dict(extra_read_bytes)}")

    lines.append("")
    lines.append("command counts:")
    for (kind, command), count in sorted(command_counts.items(), key=lambda item: (item[0][1], item[0][0])):
        lines.append(f"  {kind:5s} 0x{command:02X}: {count}")

    lines.append("")
    lines.append("register stats:")
    for command in sorted(values):
        samples = values[command]
        read_words = [word for kind, word in samples if kind == "read"]
        write_words = [word for kind, word in samples if kind == "write"]
        if write_words:
            unique_writes = " ".join(f"0x{word:04X}" for word in sorted(set(write_words)))
            lines.append(f"  0x{command:02X} writes: {unique_writes}")
            if command == 0x0E:
                for word in sorted(set(write_words)):
                    lines.append(f"    0x{word:04X}: {decode_balance_word(word)}")
        if read_words:
            raws = [raw12(word) for word in read_words]
            lines.append(
                f"  0x{command:02X} reads: n={len(read_words)} "
                f"word=0x{min(read_words):04X}..0x{max(read_words):04X} "
                f"raw12={min(raws)}..{max(raws)} avg={sum(raws) / len(raws):.2f}"
            )

    lines.append("")
    lines.append(f"measurement cycles: {len(cycles)}, complete: {len(complete_cycles)}")
    if complete_cycles:
        all_cells = [
            [cell_mv(cycle[command]) for command in (0x21, 0x22, 0x23, 0x24, 0x25)]
            for cycle in complete_cycles
        ]
        spreads = [max(cells) - min(cells) for cells in all_cells]
        temp_raws = [raw12(cycle[0x20]) for cycle in complete_cycles]
        current_raws = [raw12(cycle[0x27]) for cycle in complete_cycles]

        lines.append("cell mV stats:")
        for index, column in enumerate(zip(*all_cells), start=1):
            samples = list(column)
            lines.append(
                f"  cell{index}: min={min(samples):.1f} "
                f"avg={sum(samples) / len(samples):.1f} max={max(samples):.1f}"
            )
        lines.append(
            f"  spread: min={min(spreads):.1f} avg={sum(spreads) / len(spreads):.1f} "
            f"max={max(spreads):.1f}"
        )
        lines.append(f"  EUB >=401mV cycles: {sum(1 for spread in spreads if spread >= 401)}")
        lines.append(
            f"  temp raw 0x20: min={min(temp_raws)} avg={sum(temp_raws) / len(temp_raws):.2f} "
            f"max={max(temp_raws)}"
        )
        lines.append(
            f"  current raw 0x27: min={min(current_raws)} avg={sum(current_raws) / len(current_raws):.2f} "
            f"max={max(current_raws)}"
        )

        first = complete_cycles[0]
        first_cells = [cell_mv(first[command]) for command in (0x21, 0x22, 0x23, 0x24, 0x25)]
        lines.append("first complete cycle:")
        lines.append(
            "  words: "
            + " ".join(
                f"0x{command:02X}=0x{first[command]:04X}"
                for command in (0x21, 0x22, 0x23, 0x24, 0x25, 0x27, 0x20)
            )
        )
        lines.append(
            "  cells_mV: "
            + ", ".join(f"{value:.1f}" for value in first_cells)
            + f" spread={max(first_cells) - min(first_cells):.1f}"
        )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "csv",
        nargs="?",
        default="files/ryobi_battery_i2c_read.csv",
        type=Path,
        help="Digilent WaveForms I2C event CSV",
    )
    args = parser.parse_args()
    print(decode(args.csv))


if __name__ == "__main__":
    main()
