# PBP002 state-machine analysis

Datum posledni aktualizace: 2026-07-09

## Shrnuti

PBP002 fixed/lockout par se lisi pouze v NVM fault wordu:

```text
0x7E94 fixed   = 00 00 00 00
0x7E94 lockout = 24 20 21 20
```

Vyklad `24 20 21 20`:

- aktualni fault byte `0x24` = `0x20 | 0x04`
- historie obsahuje opakovane `0x20`
- jeden historicky byte `0x21` = `0x20 | 0x01`

Tedy PBP002 lockout neni jen marker fault `0x04`; soucasne je pritomen fault bit `0x20` s logem `EUB Flag` a v historii je i measurement/AFE komunikacni kandidat `0x01`.

PBP002 neobsahuje PBP004 fixture key:

```text
C2 C7 60 7A B5 8F 44 D2 4E 7A
```

Ani nebyly nalezeny PBP004 D-tech/auth stringy typu `D-tech Authentication successful` nebo `Fixture`. PBP002 ma misto toho stejnou rodinu service/GPIO pattern automatu jako PBP005.

## Hlavni BMS automat

Telo hlavniho automatu zacina kolem `0x4268`.

Pracovni RAM base:

```text
0x100000B8
```

Klicove offsety:

| Offset | Vyznam |
| --- | --- |
| `ctx + 0x34` | runtime event byte pouzivany pro prechody a service mapovani |
| `ctx + 0x39` | aktualni BMS state |
| `ctx + 0x3A` | novy/next BMS state |
| `ctx + 0x3B` | aktualni fault byte |
| `ctx + 0x40` | counter pro fault `0x04` |
| `ctx + 0x41` | counter pro fault `0x08` |
| `ctx + 0x42` | counter pro fault `0x02` |
| `ctx + 0x48` | auto-clear counter pro nektere fault bity |
| `ctx + 0x4C` | persistent fault word z `0x7E94` |
| `ctx + 0x50` | counter/event accumulator pro runtime bit `0x20` v `ctx+0x34` |

Dispatch:

```text
state 0x00 -> 0x487C
state 0x01 -> 0x48B8
state 0x02 -> 0x48EE
state 0x03 -> 0x49A6
state 0xFE -> 0x4B04
state 0xFF -> 0x4B3E
```

Pracovni vyznam stavu je shodny s PBP004/PBP005:

| State | Vyznam |
| --- | --- |
| `0x00` | init/idle decision |
| `0x01` | normal/active |
| `0x02` | charge-management |
| `0x03` | discharge/load-management |
| `0xFE` | sleep/standby |
| `0xFF` | fault persistence/lockout |

Ve vetvi `0xFF` se uklada fault historie do `0x7E94`:

```c
if ((fault_word & 0xff) != current_fault) {
    fault_word = current_fault | (fault_word << 8);
    write_nvm(0x7E94, fault_word);
    log("F2Flsh:x%X", fault_word);
}

fault_word <<= 8;
write_nvm(0x7E94, fault_word);
```

V PBP002 se navic v teto oblasti objevuje string:

```text
WFLR:x%X
```

## Fault bity PBP002

Event latch base:

```text
0x10000380
```

Pracovni vyznam:

| Bit | Hodnota | PBP002 poznatek |
| --- | --- | --- |
| bit 0 | `0x01` | AFE/measurement communication failure kandidat; nastavuje se po opakovanem selhani `0x0678`; v lockout historii je `0x21`. |
| bit 1 | `0x02` | Over-temperature kandidat; pokud `ctx+0x24 >= 0x55` dost dlouho, nastavi fault a loguje `FOT`. |
| bit 2 | `0x04` | Over-voltage/marker fault kandidat; pokud cell voltage prekroci limit a marker4 neni `0xA5` dost dlouho, nastavuje `0x04` a loguje `FOV`. Aktivni v PBP002 lockout. |
| bit 3 | `0x08` | Under-voltage/marker fault kandidat; pokud cell voltage klesne pod limit a marker5 neni `0xA5`, nastavuje `0x08` a loguje `FUV`. |
| bit 4 | `0x10` | DOC / discharge over-current kandidat; ve state `0x03` vola `0x1DC4(0x64)`, recovery log `DOCRc`. |
| bit 5 | `0x20` | Persistentni fault nastavuje `0x0900` pres pointer `ctx+0x3B`; logy `EUB Time St` a po timeoutu `EUB Flag`. Aktualne nejlepsi vyklad: cell-unbalance fault. Aktivni v aktualnim i historickem PBP002 lockout wordu. |
| bit 6 | `0x40` | Persistentni fault nastavuje `0x0900` pres pointer `ctx+0x3B`; logy `EUV Time St` a po timeoutu `EUV Flag`. Aktualne nejlepsi vyklad: extreme/severe under-voltage fault. |
| bit 7 | `0x80` | Nebyl nalezen setter do `ctx+0x3B`. V PBP002 se `0x80` pouziva jako timer/service/runtime flag, ne jako potvrzeny persistentni fault bit. |

PBP002 lockout `0x24` tedy znamena:

```text
0x20 = EUB cell-unbalance timed fault/flag z funkce 0x0900
0x04 = FOV/marker4/over-voltage kandidat
```

### Persistentni 0x20/0x40 vs runtime event bity

Funkce `0x0900` je volana jen z hlavniho automatu na `0x491A`:

```asm
0x4912: r1 = ctx + 0x3B
0x4916: r0 = ctx + 8
0x491A: bl 0x0900
```

Uvnit funkce `0x0900` jsou jedine dva zapisy do predaneho fault pointeru:

```asm
0x0978: movs r1, #0x20
0x097A: orrs r1, r0
0x097E: strb r1, [fault_ptr]   ; log "EUB Flag"

0x09CC: movs r1, #0x40
0x09CE: orrs r1, r0
0x09D2: strb r1, [fault_ptr]   ; log "EUV Flag"
```

K tomu patri logy:

- `EUB Time St`
- `EUB Flag`
- `EUV Time St`
- `EUV Flag`

Tim je PBP002 persistentni `0x20` lepe popsat jako `EUB Flag`, ne jako wake latch.
Podminka pro `EUB` je:

```c
min_cell_mv = measurement->value_0a;
max_cell_mv = measurement->value_0c;

if ((max_cell_mv - min_cell_mv) >= 0x191 && min_cell_mv >= 0x0CE5) {
    start_or_check_timer(0x09C4);   // log "EUB Time St" pri startu
    if (timer_expired) {
        *fault_ptr |= 0x20;         // log "EUB Flag"
    }
}
```

Tedy:

- `0x191` = 401 mV rozdil mezi nejvyssim a nejnizsim clankem
- `0x0CE5` = 3301 mV minimalni clanek musi byt nad timto prahem
- timer literal `0x09C4` = 2500 ticku/cyklu

Nejlepsi vyklad zkratky `EUB` je proto `cell unbalance` / `excessive unbalance`, ne undervoltage.
Je to fault pro velky rozdil clanku pri dostatecne nabitem packu.

Podminka pro `EUV` je:

```c
if (min_cell_mv < 1000) {
    start_or_check_timer(1000);     // log "EUV Time St" pri startu
    if (timer_expired) {
        *fault_ptr |= 0x40;         // log "EUV Flag"
    }
}
```

Tedy `EUV` je velmi pravdepodobne `extreme under-voltage`.

Soucasne firmware pouziva stejne masky i v runtime byte `ctx+0x34`.
To neni stejna promenna jako persistentni `ctx+0x3B`.

Handler pro latch `+3` je kolem `0x4D6A`:

```c
latch[3] = 1;
WakeLowPower_Flag_Set();  // 0x52C0
REG_40008000 = (REG_40008000 & 0x07) | 0x02;
```

`0x52C0` dela jen:

```c
*(uint8_t *)0x100003D9 = 1;
```

Sousedi tohoto flagu:

- `0x100003D8` se pouziva u I2C/USART transfer timeout/error flagu kolem `0x3DE4`.
- `0x100003D9` se nastavuje prave pri wake/low-power eventu.

Hlavni smycka z tohoto latch nastavuje `ctx+0x34 |= 0x20`, inkrementuje `ctx+0x50` a vola `0x5620(0)`.
Tento runtime bit muze ridit prechod/sleep/wake logiku, ale neni primy zapis do `ctx+0x3B`.

Podobne `ADOC` cesta nastavuje `ctx+0x34 |= 0x40` a loguje `ADOC`, zatimco persistentni `ctx+0x3B |= 0x40` ve zname ceste pochazi z `0x0900` jako `EUV Flag`.

### Bit 0x80 v PBP002

Cileny scan zapisu do `ctx+0x3B` nasel:

| Adresa | Operace |
| --- | --- |
| `0x42F6` | inicialni kopie low byte z `ctx+0x4C` do `ctx+0x3B` |
| `0x439E` | clear `0x0C` pres masku `0xF3` |
| `0x44B0`, `0x4BA4` | clear `0x01` pres masku `0xFE` |
| `0x44CC` | set `0x01` |
| `0x4734` | set `0x04/FOV` |
| `0x478C` | set `0x08/FUV` |
| `0x47F8` | set `0x02/FOT` |
| `0x4A46` | set `0x10/DOC` |
| `0x4BD0` | clear `0x10` pres masku `0xEF` |
| `0x0900` | set `0x20/EUB` a `0x40/EUV` pres predany pointer `ctx+0x3B` |

V techto znamych cestach neni `ctx+0x3B |= 0x80`.

Nalezena pouziti hodnoty `0x80` jsou jina:

- `0x45A6` cisti bit 7 v runtime byte `ctx+0x34` maskou `0x7F`.
- `0x4A74` nastavuje bit 7 v `ctx+0x00`, tedy lokalni runtime flag ve state `0x03`, ne fault byte.
- `0x172E/0x176C/0x17B0` pouzivaji bit 7 timer objektu na offsetu `+7`.
- `0x54EA` mapuje standby/default service stav na hodnotu `0x80`.
- dalsi vyskyty jsou periferie, watchdog, service GPIO patterny nebo NVM/config high bity.

Pracovni zaver: PBP002 bit `0x80` v persistentnim fault wordu je zatim nejspis nepouzity/rezervovany, nebo se nastavuje mimo dosud nalezenou normalni BMS cestu. Pokud by realna baterie mela v `0x7E94` bit `0x80`, je potreba brat ho jako samostatny pripad a dohledat zapis podle konkretniho dumpu.

## RAM markery

PBP002 ma stejne marker helpery jako PBP004:

| Funkce | Vyznam |
| --- | --- |
| `0x2F54` | nastavi `[0x10000000+4]` a `[0x10000000+5]` na `0x5A` |
| `0x2F5E` | vraci 1, pokud `[0x10000000+4] != 0xA5` |
| `0x2F6E` | vraci 1, pokud `[0x10000000+5] != 0xA5` |

V PBP002 jsou markery primo spojene s faulty:

- marker4 + vysoke napeti -> `0x04/FOV`
- marker5 + nizke napeti -> `0x08/FUV`

## Service/fixture automat

PBP002 service automat je `0x21A0`.

Setter state je `0x2184`.

Service struct base:

```text
0x100001E8
```

Offsety:

| Offset | Vyznam |
| --- | --- |
| `+0x00` | current service state |
| `+0x01` | previous service state |
| `+0x02` | substate/counter |
| `+0x08` | timeout object |
| `+0x10` | dalsi timeout/pattern object |

Tick handler kolem `0x4C38` vola:

```text
0x32E8
0x21A0
```

Tedy service automat bezi periodicky stejne jako PBP005.

Vybrane stavy:

| State | Handler | Vyznam |
| --- | --- | --- |
| `0x01` | `0x2308` | normal/service voltage class |
| `0x02` | `0x2312` | fault/lockout service state |
| `0x80` | `0x23A4` | standby/default |
| `0x81` | `0x23CC` | cell-voltage band 1 |
| `0x82` | `0x23EC` | cell-voltage band 2 |
| `0x83` | `0x240E` | cell-voltage band 3 |
| `0x87` | `0x24C8` | special service flag active |
| `0x8C` | `0x263A` | BMS state `0x03` special load/sleep pulse |

Mapovani BMS -> service dela funkce `0x54EA`, analog PBP005 `0x56AE`:

| Podminka | Service state |
| --- | --- |
| special flag bit 3 a `0x0F30() != 0` | `0x87` |
| BMS state `0x03` a extra argument `3` | `0x8C` |
| BMS state `0xFF` | `0x02` |
| BMS state `0xFE` | `0x80` |
| `0x780(cell_voltage)` vrati `4` | `0x01` |
| `0x780(cell_voltage)` vrati `3` | `0x83` |
| `0x780(cell_voltage)` vrati `2` | `0x82` |
| `0x780(cell_voltage)` vrati `1` | `0x81` |
| default | `0x80` |

## Logger

PBP002 logger pouziva stejnou RAM base jako PBP005:

```text
log state base = 0x100000B4
log_enabled    = 0x100000B6
```

`0x1614` vypisuje timestampovany log pouze pokud:

```c
*(uint8_t *)0x100000B6 == 1
```

Vybrane PBP002 log stringy:

- `FOV`
- `FUV`
- `FOT`
- `EUB Time St`
- `EUB Flag`
- `EUV Time St`
- `EUV Flag`
- `WFLR:x%X`
- `F2Flsh:x%X`
- `!Slp %u`
- `DPDWAKE`
- `ADOC`
- `AINT:0x%X`
- `HB %u mx %u-%4dmV mn %u-%4dmV %dC %7dmA %7dmA`
- `PS:%u %u`

## Python skript

PBP002 pasivni log:

```powershell
python tools\dtech_uart.py --profile pbp002 --port COM5 --baud 115200 listen-log
```

Offline mapa stavu:

```powershell
python tools\dtech_uart.py pbp002-map
python tools\dtech_uart.py pbp002-map --state 0x8c
```

Offline dekodovani logu:

```powershell
python tools\dtech_uart.py --profile pbp002 decode-log "FOV"
python tools\dtech_uart.py --profile pbp002 decode-log "WFLR:x24202120"
```

Aktivni `auth/raw/listen` jsou pro `--profile pbp002` blokovane, protoze PBP002 D-tech parser/key nejsou staticky potvrzene.
