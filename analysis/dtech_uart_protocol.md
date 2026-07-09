# D-tech UART protocol notes

Datum posledni aktualizace: 2026-07-09

Tyto poznamky vychazi hlavne z PBP004, protoze obsahuje bohate D-tech log stringy.
Podrobnejsi PBP004 rozbor je v `analysis/pbp004_dtech_analysis.md`.

## Fyzicka vrstva

- Linka: UART pres konektor baterie
- Pravdepodobne: `115200 8N1`, 3.3 V TTL
- Konektor:
  - `D-TX` = vystup z baterie do adapteru
  - `D-RX` = vstup do baterie
  - `-` = GND

Pro prvni testy:

```powershell
python tools\dtech_uart.py --profile pbp004 --port COM5 --baud 115200 --verbose listen
python tools\dtech_uart.py --profile pbp002 --port COM5 --baud 115200 listen-log
python tools\dtech_uart.py --profile pbp005 --port COM5 --baud 115200 listen-log
```

## Ramec

Potvrzeny format podle PBP004 parseru `0x1D2E` a TX builderu `0x1E9C`:

```text
46 LEN PACKED FLAG OPCODE_HI OPCODE_LO HEADER_CRC PAYLOAD... CRC_HI CRC_LO
```

Pole:

| Offset | Pole | Vyznam |
| --- | --- | --- |
| `0` | `0x46` | start byte, ASCII `F` |
| `1` | `LEN` | delka payloadu |
| `2` | `PACKED` | horni/lower nibble, firmware uklada jako `desc[3]` a `desc[4]` |
| `3` | `FLAG` | jeden bajt, zatim typicky `0` |
| `4` | `OPCODE_HI` | horni bajt 16bit opcode/type |
| `5` | `OPCODE_LO` | dolni bajt 16bit opcode/type |
| `6` | `HEADER_CRC` | low byte CRC pres `LEN..OPCODE_LO` |
| `7..` | `PAYLOAD` | `LEN` bajtu |
| last-1 | `CRC_HI` | finalni CRC high byte |
| last | `CRC_LO` | finalni CRC low byte |

Celkova delka ramce:

```text
LEN + 10
```

Duvod:

- TX builder v `0x1E9C` kontroluje volne misto jako `payload_len + 0x0A`.
- RX parser cte po startu 6 bajtu hlavicky, pak `LEN + 2` bajtu.

## CRC

Firmware pouziva LPC CRC periferii:

- reset/init v `0x362C`
- zapis bajtu do CRC periferie v `0x363A`
- cteni aktualni CRC v `0x3640`

Kandidat, ktery sedi s parserem a offline roundtripem:

- CRC-16/CCITT-FALSE
- seed `0xFFFF`
- polynomial `0x1021`
- no reflection
- final xor `0`

Header CRC:

```text
HEADER_CRC = low8(crc16_ccitt([LEN, PACKED, FLAG, OPCODE_HI, OPCODE_LO]))
```

Finalni CRC:

```text
CRC16([LEN, PACKED, FLAG, OPCODE_HI, OPCODE_LO, HEADER_CRC, PAYLOAD..., CRC_HI, CRC_LO]) == 0
```

V TX smeru tedy:

```text
CRC_HI, CRC_LO = crc16_ccitt([LEN, PACKED, FLAG, OPCODE_HI, OPCODE_LO, HEADER_CRC, PAYLOAD...])
```

## Auth/handshake vrstvy

Ve firmware jsou dve vrstvy:

1. D-tech transport auth
2. Fixture auth payload uvnitr `opcode/type 0x0005`

Pracovni sekvence:

```text
1. opcode 0x0001: host posle challenge
2. MCU odpovi slave challenge response
3. opcode 0x0003: host posle final response
4. po uspesnem type 3 je D-tech authenticated
5. opcode 0x0005 payload 0x01 + fixture key otevira fixture requesty
```

Parser pred autentizaci odmita opcode/type nad `0x0004` hlaskou:

```text
Received OpCode is not yet authorized
```

Proto samotny `type 5` fixture auth dava smysl az po handshake `type 1` a `type 3`.

## Challenge transform

Firmware funkce `0x4854`:

```c
uint16_t dtech_transform(uint16_t value)
{
    uint8_t inv_hi = ~(value >> 8);
    return (((value & 0xff) ^ inv_hi) << 8) | inv_hi;
}
```

Python ekvivalent je v `tools/dtech_uart.py` jako `dtech_transform`.

## Fixture auth

Dispatcher PBP004 `0x36AC`:

- bere payload pointer
- `payload[0]` = request ID
- pokud `payload[0] == 1`, jedna se o fixture auth
- porovnava `payload[1..10]` proti 10B tabulce na `0x705C`

PBP004 fixture key:

```text
C2 C7 60 7A B5 8F 44 D2 4E 7A
```

Fixture auth payload:

```text
01 C2 C7 60 7A B5 8F 44 D2 4E 7A
```

Priklad celeho `type 5` fixture auth ramce s defaultnim `PACKED=0x01`, `FLAG=0`:

```text
46 0B 01 00 00 05 E2 01 C2 C7 60 7A B5 8F 44 D2 4E 7A 1C 22
```

Tento ramec prosel offline build/parse roundtripem ve skriptu.

## D-tech request IDs po fixture auth

Podle PBP004 dispatcheru `0x36AC`:

| `payload[0]` | Pracovni vyznam |
| --- | --- |
| `0x01` | Fixture auth request |
| `0x03` | Request s 32bit hodnotou `payload[1..4]`, vyzaduje delku/arg `5`, porovnava threshold `0x54A48E01`, vola `0x494E` |
| `0x04` | Vyzaduje arg/delku `0x29`, vola `0x3878`, `0x4040`, `0x3F64`, `0x3918`; kopiruje `payload[1..0x28]` do `0x10000028`, pres `0x4040` zapisuje marker/config word do `0x10000004` s low byte `0x5A`, potom muze zapsat NVM `0x7E00..0x7E93`; neni read-only |
| `0x05..0x08` | Posila jednoduchou odpoved/error pres `0x202E(1)` |
| `0x09` | Posle ack, delay `0x7D0`, vola `0x4998(0x40)` a `0x4A1E` |
| `0x0A` | Kopiruje 3 bloky po `0x2A` bajtech do payloadu, potom send delka `0x7F` |
| `0x0B` | Dve sekvence `0x25D4`/delay, pak nekonecna smycka; pravdepodobne reset/shutdown/fixture mode; nepouzivat bez dalsi analyzy |

Pozor: nektere requesty jsou pravdepodobne zapisove nebo menici stav baterie.

## D-tech transport opcodes/types

Podle PBP004 dispatch v okoli `0x2CAC`:

| Opcode/type | Pracovni vyznam |
| --- | --- |
| `0x0001` | Create/send slave challenge response |
| `0x0003` | Final response |
| `0x0004` | Data/error/status handling |
| `0x0005` | Fixture/D-tech request dispatcher, payload na `r4 + 0x60` |
| `0x0006` | Baud rate request |
| `0x0007` | Barcode/tool request/update kandidat |
| `0x0008` | Revisions request |
| `0x0009` | Power off request |
| `0x0101/0x0102` | OCP related |
| `0x0104+` | OCP/OTP/UVP/Pack status related podle log stringu |

## Python klient

Soubor:

```text
tools/dtech_uart.py
```

Priklad auth:

```powershell
python -m pip install pyserial
python tools\dtech_uart.py --profile pbp004 --port COM5 --baud 115200 --verbose auth
```

Priklad raw ramce:

```powershell
python tools\dtech_uart.py --profile pbp004 --port COM5 --verbose raw --opcode 0x0005 --payload "01 c2 c7 60 7a b5 8f 44 d2 4e 7a"
```

Priklad listen:

```powershell
python tools\dtech_uart.py --profile pbp004 --port COM5 --baud 115200 --verbose listen
```

Priklad PBP002/PBP005 pasivniho logu:

```powershell
python tools\dtech_uart.py --profile pbp002 --port COM5 --baud 115200 listen-log
python tools\dtech_uart.py --profile pbp005 --port COM5 --baud 115200 listen-log
```

Priklad offline mapy PBP002/PBP005 stavu:

```powershell
python tools\dtech_uart.py pbp002-map
python tools\dtech_uart.py pbp002-map --state 0x8c
python tools\dtech_uart.py pbp004-requests
python tools\dtech_uart.py --profile pbp004 --port COM5 --baud 115200 --verbose pbp004-read-blocks
python tools\dtech_uart.py pbp005-map
python tools\dtech_uart.py pbp005-map --state 0x8c
python tools\dtech_uart.py --profile pbp002 decode-log "FOV"
python tools\dtech_uart.py decode-log "F2Flsh:x40"
```

Skript podporuje:

- `auth` - PBP004 type1/type3 challenge + type5 fixture auth
- `raw` - jeden rucne zadany PBP004 ramec
- `listen` - vypis prijatych PBP004 D-tech ramcu
- `listen-log` - pasivni ASCII log, hlavne pro PBP002/PBP005
- `pbp004-read-blocks` - PBP004-only bezpecnejsi cesta: auth a potom pouze fixture request `0x0A` read kandidat
- `pbp002-map` - offline mapa PBP002 BMS/service stavu
- `pbp004-requests` - offline mapa PBP004 D-tech transport opcodes a fixture requestu
- `pbp005-map` - offline mapa PBP005 BMS/service stavu
- `decode-log` - offline dekodovani znameho PBP002/PBP005 log radku
- `--key` - prepis 10B fixture key pro PBP004-style auth testy, pokud se nekdy potvrdi jina kompatibilni varianta

## Stav overeni

Overeno offline:

- `python -m py_compile tools\dtech_uart.py`
- CLI `--help`
- build/parse roundtrip pro `type 5` fixture auth
- finalni CRC residual vysel `0`

Neovereno na realnem HW:

- zda PBP005 pouziva stejny key jako PBP004
- zda CRC konfigurace sedi i proti fyzicke baterii
- konkretni odpovedi na `opcode 0x0001`, `0x0003`, `0x0005`

## PBP005 key/auth doplneni

Staticky scan PBP005:

- PBP004 key `C2 C7 60 7A B5 8F 44 D2 4E 7A` se v PBP005 nevyskytuje
- PBP004 auth-loop vzor:

```text
movs index, #0x0a
ldrb packet_byte, [payload, index]
ldrb key_byte, [key + index - 1]
cmp packet_byte, key_byte
subs index, index, #1
```

byl nalezen v PBP004 na `0x36C8`, ale nebyl nalezen v PBP005.

Zaver:

- PBP004 fixture key je staticky potvrzeny
- PBP005 fixture key zatim neni znamy
- PBP005 mozna nepouziva stejnou fixture-auth vetev jako PBP004
- realny test proti PBP005 by mel nejdrive pasivne cist UART log pres `listen-log`
- aktivni `auth/raw/listen` jsou ve skriptu pro `--profile pbp005` blokovane, dokud nebude potvrzen parser/key

## PBP002 key/auth doplneni

Staticky scan PBP002:

- PBP004 key `C2 C7 60 7A B5 8F 44 D2 4E 7A` se v PBP002 nevyskytuje
- PBP004 D-tech/auth stringy typu `D-tech Authentication successful` a `Fixture` nebyly nalezeny
- PBP002 obsahuje service/GPIO state machine `0x21A0`, ne zjevny PBP004 packet parser
- PBP002 tedy zatim nema potvrzenou aktivni D-tech auth cestu

Prakticky:

- pro PBP002 zacit pres `--profile pbp002 listen-log`
- aktivni `auth/raw/listen` jsou ve skriptu pro `--profile pbp002` blokovane

## Bezpecnostni poznamka

Pro prvni testy neposilat requesty, ktere mohou menit stav nebo NVM:

- fixture request `0x04`
- fixture request `0x09`
- fixture request `0x0B`
- obecne vse, co vola NVM update, reset, poweroff nebo state change

Nejbezpecnejsi poradi:

1. `listen-log` pro ASCII debug vystup, pokud je log aktivni.
2. `listen` pro pasivni PBP004 D-tech frame capture.
3. `auth` s verbose logem jen na PBP004.
4. `pbp004-read-blocks` jako jediny pripraveny read-like aktivni request.
5. `raw` pouzivat jen pro rucne zkontrolovane testy mimo bezpecny rezim.
