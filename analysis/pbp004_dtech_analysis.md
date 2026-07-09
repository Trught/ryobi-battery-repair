# PBP004 D-tech analysis

Datum posledni aktualizace: 2026-07-09

## Shrnuti

PBP004 je zatim jediny analyzovany model s jasnym D-tech UART packet parserem, challenge/response autentizaci a fixture key porovnanim.

PBP004 lockout:

```text
0x7E94 fixed   = 00 00 00 00
0x7E94 lockout = 04 00 00 00
```

Aktualni fault `0x04` odpovida `FOV` / marker4 / over-voltage kandidatu, stejne jako u PBP002.

## UART/D-tech parser

Parser:

```text
0x1D2E = DTech_RxFrameParser
```

D-tech parser state:

```text
0x10000200
```

Frame format:

```text
46 LEN PACKED FLAG OPCODE_HI OPCODE_LO HEADER_CRC PAYLOAD... CRC_HI CRC_LO
```

Parser:

- hleda start byte `0x46`
- cte 6 bajtu headeru po startu
- kontroluje header CRC jako low byte CRC po `LEN..OPCODE_LO`
- payload cte podle `LEN`
- final CRC validuje residualem `0`

Povoleni pred autentizaci:

```c
opcode_allowed = opcode in {0x0001, 0x0002, 0x0003, 0x0004}
                 || authenticated_flag == 1;
```

Funkce `0x1D18` potvrzuje, ze opcodes nad `0x0004` jsou bez authenticated flagu blokovane.

## Auth sekvence

Transport auth:

| Opcode | Funkce | Vyznam |
| --- | --- | --- |
| `0x0001` | `0x205A` | create/send slave challenge response |
| `0x0003` | `0x20CC` | final response check |
| `0x0005` | `0x36AC` | fixture request dispatcher po auth |

Challenge transform `0x4854`:

```c
uint16_t dtech_transform(uint16_t value)
{
    uint8_t inv_hi = ~(value >> 8);
    return (((value & 0xff) ^ inv_hi) << 8) | inv_hi;
}
```

Sekvence:

1. Host posle `opcode 0x0001`, payload `01 CHAL_HI CHAL_LO`.
2. MCU odpovi transformaci host challenge a prida vlastni slave challenge.
3. Host posle `opcode 0x0003`, payload = transformace slave challenge.
4. Firmware nastavi authenticated flag.
5. Teprve potom ma smysl `opcode 0x0005`.

## Fixture key

Dispatcher:

```text
0x36AC = DTech_Request_Dispatch_And_FixtureAuth
```

Fixture key tabulka:

```text
0x705C = C2 C7 60 7A B5 8F 44 D2 4E 7A
```

Fixture auth payload:

```text
01 C2 C7 60 7A B5 8F 44 D2 4E 7A
```

Pokud porovnani selze:

- loguje `D-tech Fixture Auth Failure Byte: %d`
- nuluje fixture auth flag
- posila odpoved/error

Pokud porovnani projde:

- loguje `DTk Fixture Auth Success`
- loguje `Auth DTk Fxtr Success`
- nastavuje fixture-auth state

## Transport opcodes

| Opcode | Riziko | Vyznam |
| --- | --- | --- |
| `0x0001` | auth | Slave challenge response; povoleno pred auth. |
| `0x0002` | reserved | Povoleno pre-auth allow-listem, presny vyznam jeste neni rozebran. |
| `0x0003` | auth | Final response; nastavuje authenticated flag. |
| `0x0004` | status | Data/error/status handling; povoleno pred auth. |
| `0x0005` | fixture | Fixture dispatcher; vyzaduje transport auth. |
| `0x0006` | state-changing | Baud-rate request; muze zmenit UART baud. |
| `0x0007` | state-changing | Tool barcode request/update kandidat. |
| `0x0008` | read | Revisions request kandidat. |
| `0x0009` | state-changing | Power-off request. |
| `0x0101/0x0102` | state-changing | OCP/current threshold related. |
| `0x0104+` | mixed/state-changing | OCP/OTP/UVP/pack-status family. |

## Fixture request IDs

Tyto requesty jsou payload uvnitr `opcode 0x0005`.

| Request ID | Riziko | Vyznam |
| --- | --- | --- |
| `0x01` | auth | Fixture auth: `0x01 + 10B key`. |
| `0x03` | state-changing | 32bit threshold/current request; vyzaduje delku 5 a hodnotu `>= 0x54A48E01`; vola `0x494E`. |
| `0x04` | write | Delka `0x29`; vola `0x3878`, `0x4040`, `0x3F64`, `0x3918`; zapisuje config/string RAM a marker/config word do `0x10000004` pres `0x4040`. Low byte wordu je v teto vetvi `0x5A`. Neni read-only. |
| `0x05..0x08` | ack | Jednoducha odpoved/error path pres `0x202E(1)`. |
| `0x09` | state-changing | Ack, delay `0x7D0`, vola `0x4998(0x40)` a `0x4A1E`. |
| `0x0A` | read | Kopiruje tri bloky po `0x2A` bajtech do odpovedi, posila delku `0x7F`. |
| `0x0B` | danger | Service patterny a nekonecna smycka; pravdepodobne reset/shutdown/fixture mode. Nepouzivat bez dalsi analyzy. |

Nejzajimavejsi read-only kandidat po auth je fixture request `0x0A`.
Opatrne: i po uspesne auth zustava `0x0005/0x0A` aktivni dotaz a neni jeste overeny na realnem HW.

## RAM markery a request 0x04

PBP004 marker helpery:

| Funkce | Vyznam |
| --- | --- |
| `0x4040` | zapise 32bit `r0` do `[0x10000000+4]` |
| `0x4046` | nastavi `[0x10000000+4]` a `[0x10000000+5]` na `0x5A` |
| `0x4050` | vraci 1, pokud `[0x10000000+4] != 0xA5` |
| `0x4060` | vraci 1, pokud `[0x10000000+5] != 0xA5` |

Init/default cesta:

```text
0x5570 -> 0x3878
0x5574 -> 0x4046  // reset markeru na 5A/5A
0x5578 -> 0x3918
```

D-tech fixture request `0x04` obsahuje call-site:

```text
0x3778 -> 0x3878
0x377C movs r0, #0x5A
0x3780 strb r0, [sp]
0x3782 ldr r0, [sp]   // low byte = 0x5A, horni 3 bajty nejsou v tomto rezu jasne inicializovane
0x3784 -> 0x4040  // write word to 0x10000004
0x378A -> 0x3F64
0x378E -> 0x3918
```

Staticky je tedy potvrzeno, ze request `0x04` muze zmenit bajty `0x10000004..0x10000007`, ale v teto vetevni sekvenci neni potvrzen zapis `0xA5`; potvrzeny je low byte `0x5A`.

Pomocne funkce:

| Funkce | Vyznam |
| --- | --- |
| `0x3878` | Defaultuje RAM config: kopiruje 0x28 bajtu default stringu `130597003|00000000000000|PBP004|` do `0x10000028`, inicializuje souvisejici pole a nastavuje dirty flag. |
| `0x3F64(payload+1)` | Kopiruje 0x28 bajtu z request payloadu `payload[1..0x28]` do `0x10000028`, nastavuje `0x100001B8 = 1` a bit `0x80` v `0x100001B0`. |
| `0x3918` | Pokud je dirty flag `0x100001B8` nastaveny, vola NVM/flash write `0x4A48(0x10000000, 0x7E00, 0x25)` a flag nuluje. `0x25` je pocet 32bit slov, tedy zapis `0x7E00..0x7E93`. |

Payload format requestu `0x04`:

| Offset v payloadu | Delka | Vyznam |
| --- | ---: | --- |
| `0x00` | 1 | Request ID `0x04`. |
| `0x01..0x28` | 0x28 / 40 B | Kopie do RAM `0x10000028`, persistovana na NVM `0x7E28..0x7E4F`. Default string je `130597003\|00000000000000\|PBP004\|` nasledovany paddingem/nulami. |

Poznamka: NVM byte `0x7E27` je posledni byte predchoziho wordu a ve fixed dumpu je ASCII `O`, takze WaveForms/ASCII string search ukazuje text jako `O130...`. Samotny request `0x04` ale zacina kopirovat az na offsetu `+0x28`.

Rozsah flash persistence je uzavreny pro tuto vetev: `0x7E00..0x7E93`.
Persistent fault word `0x7E94` request `0x04` primo neprepisuje.
Request `0x04` proto neni bezpecne pouzivat jako read-only diagnostiku.

## Fault 0x04

PBP004 lockout ma pouze `0x04`.

Relevantni log stringy:

- `FOV`
- `FUV`
- `FOT`
- `WFLR:x%X`
- `F2Flsh:x%X`

Interpretace:

- `0x04` = `FOV` / marker4 / over-voltage kandidat
- neni to D-tech auth failure
- fixture auth failure ma vlastni logy a neni primo persistentni fault `0x04`

## Python skript

Offline vypis PBP004 D-tech mapy:

```powershell
python tools\dtech_uart.py pbp004-requests
```

Auth test:

```powershell
python tools\dtech_uart.py --profile pbp004 --port COM5 --baud 115200 --verbose auth
```

Pasivni frame listen:

```powershell
python tools\dtech_uart.py --profile pbp004 --port COM5 --baud 115200 --verbose listen
```

Dekodovani logu:

```powershell
python tools\dtech_uart.py --profile pbp004 decode-log "DTk Fixture Auth Success"
python tools\dtech_uart.py --profile pbp004 decode-log "FOV"
```
