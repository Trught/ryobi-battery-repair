# PBP005 state-machine analysis

Datum posledni aktualizace: 2026-07-09

## Shrnuti

PBP005 nepouziva stejne zjevny D-tech packet parser jako PBP004. Funkce `0x213C`, puvodne kandidat na D-tech automat, je podle staticke analyzy service/fixture/GPIO pattern state machine. Je periodicky volana z tick handleru `0x4CD8`, pracuje se strukturou `0x10000230`, timeouty a zapisuje hodnoty do periferii/GPIO registru.

Prakticky dopad:

- PBP005 auth parser ani fixture key nejsou zatim potvrzene.
- PBP004 fixture key `C2 C7 60 7A B5 8F 44 D2 4E 7A` se v PBP005 nenachazi.
- Pro PBP005 je nejbezpecnejsi prvni diagnostika pasivni UART log na `D-TX`.
- Python skript `tools/dtech_uart.py` ma proto PBP005 rezim pro logy a mapovani stavu, ne pro aktivni auth.

## Hlavni BMS automat

Hlavni PBP005 runtime smycka ma telo kolem `0x4268`.

Zakladni RAM base:

```text
0x10000150
```

Klicove offsety:

| Offset | Vyznam |
| --- | --- |
| `+0x08` | aktualni fault/event byte |
| `+0x0D` | aktualni BMS state |
| `+0x0F` | persistent/previous fault byte |
| `+0x18` | counter pro auto-clear |
| `+0x1C` | persistent fault word z `0x7E94` |

Dispatch:

```text
state 0x00 -> 0x4998
state 0x01 -> 0x49C4
state 0x02 -> 0x49EC
state 0x03 -> 0x4A82
state 0xFE -> 0x4BD2
state 0xFF -> 0x4C04
```

Pracovni vyznam:

| State | Vyznam |
| --- | --- |
| `0x00` | init/idle decision; persistent fault vede na `0xFF`, sleep/event na `0xFE`, jinak na `0x01` |
| `0x01` | normal/active; fault vede na `0xFF`, load/runtime event muze prejit do `0x03` |
| `0x02` | charge-management; vola `0x630`, `0x54CC`, `0x5744` |
| `0x03` | discharge/load-management; resi EOV/EUV/EOT/ADOC a sleep/load kontrolu |
| `0xFE` | sleep/standby; fault vede na `0xFF`, wake/runtime bity vraci do `0x01/0x02` |
| `0xFF` | fault persistence/lockout; zapisuje fault historii do `0x7E94` |

Ve vetvi `0xFF` firmware dela:

```c
if ((fault_word & 0xff) != current_fault) {
    fault_word = current_fault | (fault_word << 8);
    write_nvm(0x7E94, fault_word);
}

fault_word <<= 8;
write_nvm(0x7E94, fault_word);
```

Log string teto vetve:

```text
F2Flsh:x%X
```

## Fault bity PBP005

Event latch base:

```text
0x100003C8
```

| Latch offset | Fault bit | Stav poznani |
| --- | --- | --- |
| `+1` | `0x40` | `ADOC`; AFE/OZ3705 status `0x02 & 0x1000`, potvrzovano pres `0x1D60(2)` |
| `+3` | `0x20` | nastavuje handler `0x4DFE`; vola helper `0x5488`, ktery nastavuje `0x10000405 = 1`; wake/low-power runtime flag |
| `+4` | `0x02` | teplotni/rozsahovy event kandidat |

PBP005 lockout dump ma `0x7E94 = 40 00 00 00`, tedy aktivni bit `0x40`.

### Bit 0x40 / ADOC zpresneni

`0x40` je potvrzeny `ADOC` event od AFE/OZ3705 status registru, ne samostatny MCU-only latch.

Klicove funkce:

| Adresa | Pracovni nazev | Vyznam |
| --- | --- | --- |
| `0x17AC` | `AFE3705_ReadRegister16_PEC` | Cte word z AFE `0x29`, kontroluje PEC. |
| `0x19B6` | `AFE3705_ClearStatus02_AllOnes` | Zapisuje `0x02 = 0xFFFF`, clear/ack status kandidat. |
| `0x1D48` | `AFE3705_Is_ADOC_Status_Set` | Cte `0x02` a vraci bit `0x1000`. |
| `0x1D60` | `AFE3705_ClearAndConfirm_ADOC` | Smycka: zapis `0x02 = 0xFFFF`, read `0x02`, test `0x1000`, max pocet pokusu z parametru. |
| `0x1DFC` | `AFE3705_Read_Status02_ToPtr` | Cte `0x02` do predaneho pointeru; pouzito pro log `AINT:0x%X`. |
| `0x4DB8` | `ADOC_Interrupt_Handler` | Na interruptu vola `0x1D60(2)` a pri potvrzeni nastavuje latch `0x100003C8+1`. |

`0x1D48`:

```c
uint16_t st = AFE_ReadWord(0x02);
return (st & 0x1000) != 0;
```

`0x1D60(tries)`:

```c
status = 0x1000;  // lokalni pocatecni stav: bit je povazovan za aktivni
for (i = 0; (status & 0x1000) != 0; i++) {
    AFE_WriteWord(0x02, 0xFFFF);
    status = AFE_ReadWord(0x02);

    if (i >= tries || read_error)
        return 1;  // stale potvrzeno / nelze clear
}
return 0;          // status bit zmizel
```

Main smycka ma dve cesty na `0x40`:

1. Latch cesta:
   - `0x4DB8` nastavi `latch[1] = 1`, pokud `0x1D60(2)` potvrdi status.
   - Main kolem `0x4696` cte `latch[1]`, nuluje ho, nastavuje `ctx+0x08 |= 0x40` a loguje `ADOC`.
2. Polling cesta:
   - Main kolem `0x46BE` vola `0x1DFC` a loguje `AINT:0x%X`.
   - Potom vola `0x1D48`; pokud status `0x02 & 0x1000`, nastavuje `ctx+0x08 |= 0x40`.
   - Nasledne `0x19B6` zapisuje `0x02 = 0xFFFF`, tedy clear/ack status kandidat.

Zachyceny normalni I2C sniff mel `0x02 = 0x8082`, tedy bit `0x1000` nebyl nastaven.
Proto je nejpresnejsi pracovni vyznam PBP005 fault bitu `0x40`:

```text
ADOC = AFE/OZ3705 status 0x02 bit 12 / mask 0x1000, discharge-over-current notification kandidat.
```

### Bit 0x20 a wake/low-power flag

Handler `0x4DFE` dela:

```c
latch[3] = 1;
WakeLowPower_Flag_Set();  // 0x5488
REG_40008000 = (REG_40008000 & 0x07) | 0x02;
```

`0x5488` dela jen:

```c
*(uint8_t *)0x10000405 = 1;
```

Sousedi tohoto flagu:

- `0x10000404` se pouziva u I2C/USART transfer timeout/error flagu kolem `0x3EA2`.
- `0x10000405` se nastavuje pri wake/low-power eventu.

To potvrzuje, ze fault bit `0x20` neni primarni AFE measurement fault. Je to event/wake/low-power cesta, ktera se muze zapsat do persistentni historie.

## RAM markery

PBP005 pouziva stejne dva RAM markery v bloku `0x10000000`:

| Funkce | Vyznam |
| --- | --- |
| `0x2EB8` | nastavi `[0x10000000+4]` a `[0x10000000+5]` na `0x5A` |
| `0x2EC2` | vraci 1, pokud `[0x10000000+4] != 0xA5` |
| `0x2ED2` | vraci 1, pokud `[0x10000000+5] != 0xA5` |

Init/default cesta:

```text
0x44A4 -> 0x26B4
0x44A8 -> 0x2EB8  // reset markeru na 5A/5A
0x44AC -> 0x2728
```

Vychozi NVM obraz ma na `0x7E04/0x7E05` hodnoty `5A 5A`.
Primy zapis konstanty `0xA5` do `0x10000004/05` zatim nebyl v PBP005 nalezen.

Pouziti markeru ve fault vetvich:

| Marker helper | Call-site | Podminka | Log / fault |
| --- | --- | --- | --- |
| `0x2EC2` | `0x47CA` | max/cell napeti `>= 0x10CC` / cca 4300 mV a marker A neni `0xA5` | `EOVs`, potom bit `0x04` |
| `0x2ED2` | `0x4840` | min/cell napeti pod limitem, mimo stavy `0x02/0x03`, marker B neni `0xA5` | `EUVs`, potom bit `0x08` |
| `0x2ED2` | `0x48BA` | teplota `>= 0x55` / 85 C a marker B neni `0xA5` | `EOTs`, potom bit `0x02` |

To je drobny rozdil proti PBP002/PBP004: PBP005 marker B gateuje nejen under-voltage, ale i over-temperature vetev.

## Service/fixture automat 0x213C

Struktura:

```text
0x10000230
```

| Offset | Vyznam |
| --- | --- |
| `+0x00` | current service state |
| `+0x01` | previous service state |
| `+0x02` | substate/counter |
| `+0x08` | timeout object |
| `+0x10` | dalsi timeout/pattern object |

Funkce `0x2120` nastavuje state:

```c
timer_start(base + 8, timeout);
base[0] = state;
```

Funkce `0x213C`:

1. kontroluje timeout `base+8`; po timeoutu nuluje state,
2. pri zmene state resetuje timeout/pattern `base+0x10`,
3. podle state skace do tabulky handleru,
4. zapisuje pattern hodnoty do GPIO/periferii,
5. uklada novy substate/counter do `base+2`.

Jump table:

| State | Handler | Pracovni vyznam |
| --- | --- | --- |
| `0x01` | `0x22A4` | service voltage class 4 / normal-active class |
| `0x02` | `0x22AC` | fault/lockout service state |
| `0x03` | `0x22D2` | sleep-entry/service pattern step |
| `0x04` | `0x22DA` | sleep-entry/service pattern step |
| `0x05` | `0x22E2` | sleep-entry/service pattern step |
| `0x06` | `0x22EA` | sleep-entry/service pattern step |
| `0x07` | `0x22F0` | GPIO/peripheral pattern output |
| `0x08` | `0x22F8` | GPIO/peripheral pattern output |
| `0x09` | `0x2300` | GPIO/peripheral pattern output |
| `0x0A` | `0x2306` | GPIO/peripheral pattern output |
| `0x0B` | `0x230E` | auto-clear/fault-history service pulse kandidat |
| `0x0C` | `0x2316` | GPIO/peripheral pattern output |
| `0x0D` | `0x231E` | GPIO/peripheral pattern output |
| `0x0E` | `0x2326` | GPIO/peripheral pattern output |
| `0x0F` | `0x232C` | GPIO/peripheral pattern output |
| `0x10` | `0x2332` | GPIO/peripheral pattern output |
| `0x11` | `0x22A4` | alias state `0x01` |
| `0x80` | `0x233A` | standby/default service state |
| `0x81` | `0x2360` | cell-voltage band 1 |
| `0x82` | `0x237E` | cell-voltage band 2 |
| `0x83` | `0x239E` | cell-voltage band 3 |
| `0x84` | `0x23D0` | timed service pattern |
| `0x85` | `0x23F8` | timed service pattern |
| `0x86` | `0x2426` | timed service pattern |
| `0x87` | `0x2454` | special service flag active |
| `0x88` | `0x22A4` | alias state `0x01` |
| `0x89` | `0x2488` | configuration/service pulse sequence |
| `0x8A` | `0x24C2` | configuration/service pulse sequence |
| `0x8B` | `0x2530` | configuration/service pulse sequence |
| `0x8C` | `0x25A2` | BMS state `0x03` special load/sleep pulse |
| `0x12..0x7F` | `0x25E4` | default |
| `>0x8C` | `0x25E4` | default |

Pouzite vystupni/periferie konstanty:

```text
A0002200
A0002280
A0002300
A0001050
1C100000
18100000
14100000
10100000
```

## Mapovani BMS -> service state

Funkce `0x56AE` mapuje BMS stav a mereni na service state pres `0x2120`.

| Podminka | Service state |
| --- | --- |
| special flag bit 3 a `0x0C90() != 0` | `0x87` |
| BMS state `0x03` a extra argument `3` | `0x8C` |
| BMS state `0xFF` | `0x02` |
| BMS state `0xFE` | `0x80` |
| `0x4BC(cell_voltage)` vrati `4` | `0x01` |
| `0x4BC(cell_voltage)` vrati `3` | `0x83` |
| `0x4BC(cell_voltage)` vrati `2` | `0x82` |
| `0x4BC(cell_voltage)` vrati `1` | `0x81` |
| default | `0x80` |

## Python skript

PBP005 pasivni log:

```powershell
python tools\dtech_uart.py --profile pbp005 --port COM5 --baud 115200 listen-log
```

S hex dumpem bajtu:

```powershell
python tools\dtech_uart.py --profile pbp005 --port COM5 listen-log --hex
```

Offline mapa stavu:

```powershell
python tools\dtech_uart.py pbp005-map
python tools\dtech_uart.py pbp005-map --state 0x8c
```

Offline dekodovani log radku:

```powershell
python tools\dtech_uart.py decode-log "F2Flsh:x40"
```

Aktivni `auth` a `raw` jsou pro `--profile pbp005` schvalne blokovane, protoze PBP005 auth/parser neni staticky potvrzen.

## PBP005 D-tech / service zaver

PBP005 zatim nema staticky potvrzeny PBP004-style D-tech packet parser.

Negativni dukazy:

- PBP004 obsahuje mnoho `D-tech ...`, `Auth ...`, `Fixture ...` stringu; PBP005 neobsahuje zadny takovy D-tech/auth/fixture string.
- PBP004 fixture key `C2 C7 60 7A B5 8F 44 D2 4E 7A` je jen v PBP004 na `0x705C`; v PBP005 neni.
- PBP004 parser `0x1D2E` nema v PBP005 analogii; PBP005 oblast `0x1D2E` patri AFE/balance/status I2C kodu.
- `0x213C` ma jediny caller `0x4CF2` z periodicke tick funkce.
- Setter `0x2120` ma mnoho internich BMS calleru a nastavuje service state/timeouts, ne prijaty UART ramec.
- UART/ring-buffer funkce kolem `0x31A2/0x31C8/0x331E/0x334A` jsou pouzite pro logger/byte pump, ale nebyl nalezen PBP004-like frame parser s hlavickou `0x46`, CRC a request dispatch tabulkou.

Pracovni zaver:

```text
PBP005 ma pasivni UART log a service/GPIO pattern automat.
Aktivni D-tech auth/fixture protokol jako u PBP004 neni potvrzen.
```

## Logger

PBP005 logger:

```text
log state base = 0x100000B4
log_enabled    = 0x100000B6
```

Timestampovany log se vypise pouze pokud:

```c
*(uint8_t *)0x100000B6 == 1
```

Vybrane PBP005 log stringy:

- `DPDWAKE`
- `ADOC`
- `AINT:0x%X`
- `EOVs` - over-voltage set/event, fault bit `0x04`
- `EUVs` - under-voltage set/event, fault bit `0x08`
- `EOTs` - over-temperature set/event, fault bit `0x02`
- `WFLR:x%X`
- `F2Flsh:x%X`
- `!Slp %u`
- `HB %u mx %u-%4dmV mn %u-%4dmV %dC %7dmA %7dmA`
- `PS:%u %u`
