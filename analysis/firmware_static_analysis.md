# Ryobi firmware static analysis

Datum posledni aktualizace: 2026-07-09

## Platforma a deska

- MCU podle KiCad schematu: `LPC804M101JHI33`
- Jadro: ARM Cortex-M0+
- Flash image: priblizne `0x0000-0x7F7F`
- NVM/config oblast: `0x7E00+`
- Hlavni konektor baterie:
  - pin 2: `-/GND`
  - pin 4: `D-TX`
  - pin 5: `D-RX`
- UART fyzicky: pravdepodobne 3.3 V TTL, ne RS-232.
- Pravdepodobne UART nastaveni: `115200 8N1`
  - Duvod: init konstanta `0x00384000 = 3686400 = 115200 * 32`

Bezpecne zapojeni pro pasivni diagnostiku:

- USB-UART `GND` na baterii `-`
- USB-UART `RX` na baterii `D-TX`
- USB VCC nepripojovat
- Pred pripojenim overit multimetrem idle uroven `D-TX` okolo 3.3 V
- `D-RX` pripojit az pro aktivni testy, idealne pres `1k-4.7k`

## Firmware fixed vs lockout

Vsechny porovnane fixed/lockout pary se lisi pouze v NVM persistentnim fault wordu na `0x7E94`.

| Model | Lockout hodnota na `0x7E94` | Vyklad |
| --- | --- | --- |
| PBP002 | `24 20 21 20` | aktualni fault `0x24 = 0x20 | 0x04` + historie `0x20/0x21`; viz `analysis/pbp002_state_machine.md` |
| PBP004 | `04 00 00 00` | aktualni fault `0x04` |
| PBP005 | `40 00 00 00` | aktualni fault `0x40` |

`0x7E94 fixed` je `00 00 00 00`.

## Startup/runtime

| Puvodni nazev | Doporuceny nazev | Vyznam |
| --- | --- | --- |
| `UndefinedFunction_00000280` | `Reset_Trampoline` | Prvni resetovy skok. Nacte pointer z `0x284` a skoci na `0x5D3A`. |
| `LAB_00005d3a` | `Reset_Stage2` | Druha faze resetu, nekolik NOP, pak vola startup funkci. |
| `FUN_00005d08` | `Startup_Main` | Runtime/startovaci obal. Vola init a pote aplikační main. |
| `FUN_00005cb4` | `Runtime_Init / Early_Init` | Casna runtime inicializace. |
| `FUN_000041a4` | `App_Main_StateMachine` | Hlavni aplikační smycka / stavovy automat BMS. |
| `FUN_00005d26` | `Enter_Runtime_Exit` | Prechod do exit/trap smycky, pokud se aplikace vrati. |
| `FUN_00005d30` | `Runtime_Exit_Loop` | Nekonecna smycka volajici exit trap. |
| `FUN_00005b64` | `Semihosting_Exit_Trap` | `BKPT 0xAB`, ARM semihosting exit/trap. Nejde o scheduler. |
| `FUN_00005c78` | `Clear_BSS_Regions` | Nulovani `.bss` / ZI oblasti podle tabulky. |

## Hlavni aplikační logika

| Puvodni nazev | Doporuceny nazev | Vyznam |
| --- | --- | --- |
| `FUN_000041a4` | `App_Main_StateMachine` | Hlavni BMS smycka. Cte fault word z `0x7E94`, ridi stavy `0x00`, `0x01`, `0x02`, `0x03`, `0xFE`, `0xFF`. |
| `FUN_0000522c` | `Validate_NVM_Config_Block` | Validuje konfigurační hodnoty v oblasti `0x7EC0-0x7EEC`. |
| `FUN_00003838` | `Copy_Words32 / NVM_ReadWords` | Kopirovani 32bit slov. Pouziva se pro cteni z flash/NVM. |
| `FUN_000036f4` | `NVM_UpdateWords_64B` | Zapis do flash/NVM oblasti `0x7E00+` po 64B blocich. Pro read-only rezim nepouzivat. |

## Diagnosticky fault/status word

| Polozka | Vyznam |
| --- | --- |
| `0x7E94` | persistentni fault/status word |
| `ctx + 0x4C` | RAM kopie persistentniho fault wordu |
| `ctx + 0x3B` | aktualni fault flags = nejnizsi byte fault wordu |
| `ctx + 0x48` | citac pro automaticke cisteni nekterych fault bitu |

Pracovni vyznam fault bitu:

| Bit | Hodnota | Stav poznani |
| --- | --- | --- |
| bit 0 | `0x01` | Chyba komunikace / merici validace kandidat, souvisi s `FUN_00000678`. |
| bit 1 | `0x02` | Teplotni / rozsahovy fault kandidat. |
| bit 2 | `0x04` | Marker `[0x10000004] != 0xA5` po dobu > cca 200 cyklu. Aktivni v PBP004 lockout dumpu. |
| bit 3 | `0x08` | Marker `[0x10000005] != 0xA5` po dobu > cca 200 cyklu. |
| bit 4 | `0x10` | DOC / discharge over-current recovery kandidat podle logu `DOCRc`. |
| bit 5 | `0x20` | PBP002: persistentni fault nastavuje `0x0900` jako `EUB Flag`; nejlepsi vyklad cell-unbalance fault (`max-min >= 401 mV`, `min >= 3301 mV`, timer 2500). Aktivni v PBP002 lockoutu. PBP005: podobna maska je spojena s event latch `+3` a wake/low-power RAM flagem. |
| bit 6 | `0x40` | PBP002: persistentni fault nastavuje `0x0900` jako `EUV Flag`; nejlepsi vyklad extreme under-voltage (`min < 1000 mV`, timer 1000). PBP005 lockout: potvrzeno jako `ADOC` event podle log stringu `ADOC`. |
| bit 7 | `0x80` | PBP002: nebyl nalezen setter do `ctx+0x3B`; hodnota se pouziva jako timer/service/runtime flag, ne jako potvrzeny persistentni fault bit. |

## Slovnik diagnostickych zkratek

Tyto nazvy jsou pracovni, odvozene ze stringu ve firmware a statickych call-site. Oficialni expanze neni potvrzena datasheetem vyrobce.

### Napeti / teplota / unbalance

| Zkratka | Modely / log | Pracovni vyznam | Vazba na fault bit / podminku |
| --- | --- | --- | --- |
| `FOV` | PBP002/PBP004 | Fault Over Voltage | `0x04`; marker A `[0x10000004] != 0xA5` a vysoke cell napeti, limit kolem `0x10CC` / 4300 mV, counter prah `0xC9`. |
| `FUV` | PBP002/PBP004 | Fault Under Voltage | `0x08`; marker B `[0x10000005] != 0xA5` a nizke cell napeti, mimo stavy `0x02/0x03`, counter prah `0xC9`. |
| `FOT` | PBP002/PBP004 | Fault Over Temperature | `0x02`; teplotni hodnota kolem `>= 0x55` / 85 C po timeoutu/counteru. |
| `EOVs` | PBP005 | Event Over Voltage set | `0x04`; marker A `[0x10000004] != 0xA5`, max/cell napeti `>= 0x10CC` / cca 4300 mV, timerovana vetev. |
| `EUVs` | PBP005 | Event Under Voltage set | `0x08`; marker B `[0x10000005] != 0xA5`, min/cell napeti pod limitem, mimo stavy `0x02/0x03`, timerovana vetev. |
| `EOTs` | PBP005 | Event Over Temperature set | `0x02`; marker B `[0x10000005] != 0xA5`, teplota `>= 0x55` / 85 C, timerovana vetev. |
| `EUB Time St` | PBP002 | Event/Excessive UnBalance timer start | Start timeru pro cell-unbalance; podminka `max-min >= 401 mV` a `min >= 3301 mV`. |
| `EUB Flag` | PBP002 | Event/Excessive UnBalance fault flag | `0x20`; cell-unbalance fault po timeru 2500. |
| `EUV Time St` | PBP002 | Extreme Under Voltage timer start | Start timeru pro severe/extreme under-voltage; podminka `min < 1000 mV`. |
| `EUV Flag` | PBP002 | Extreme Under Voltage fault flag | `0x40`; severe/extreme under-voltage fault po timeru 1000. |

Poznamka k prefixum:

- `F*` stringy u PBP002/PBP004 oznacuji primo fault vetve (`FOV/FUV/FOT`).
- `E*...s` stringy u PBP005 oznacuji set/event vetve pred nebo pri nastaveni odpovidajiciho fault bitu (`EOVs/EUVs/EOTs`).
- `EUV` se pouziva ve dvou kontextech: PBP005 `EUVs` = bezne under-voltage event `0x08`, zatimco PBP002 `EUV Flag` = extreme/severe under-voltage `0x40`.

### Proud / AFE / runtime

| Zkratka | Pracovni vyznam | Poznamka |
| --- | --- | --- |
| `DOC` | Discharge Over Current | Kandidat pro bit `0x10`; recovery/clear log je `DOCRc`. |
| `DOCRc` | Discharge Over Current Recovery/Clear | Recovery/clear cesta pro DOC kandidata. |
| `ADOC` | AFE Discharge Over Current / AFE DOC event kandidat | PBP005 `0x40`; potvrzeno jako AFE/OZ3705 status `0x02 & 0x1000`, loguje se pri latch/polling ceste. |
| `ADOC_E` | ADOC recovery/error/exit kandidat | Recovery/clear souvisejici s ADOC cestou. |
| `AFEPNR` | AFE Power Not Ready kandidat | AFE/3705T neni v power-ready stavu. |
| `AFENR` | AFE Not Ready kandidat | AFE/3705T neni ready. |
| `AFECommErr(V)` | AFE communication error pri voltage mereni | Souvisi s measurement/fault bit `0x01` kandidatem. |
| `AFECommErr(I)` | AFE communication error pri current mereni | Souvisi s measurement/fault bit `0x01` kandidatem. |
| `AFERc` | AFE Recovery/Clear kandidat | Recovery/clear cesta AFE chyby. |
| `ChgWkI` | Charger/Wake Interrupt kandidat | Wake/charger event log. |
| `ChgV: %dmV %d fail` | Charge Voltage fail | Charge-voltage validation selhani; loguje mV a pocitadlo/stav. |
| `DPDWAKE` | Deep Power Down Wake | Wake z low-power/deep-power-down stavu. |
| `Bal%u>%u` | Balancing start | Start balancingu; loguje `max_cell_mv > min_cell_mv`. |
| `Bal Dn` | Balancing Done | Konec balancingu; firmware zapisuje AFE `0x0E = 0xFFC0`. |
| `BCHR_FV:%d` | Battery Charger Full Voltage kandidat | Charge/full-voltage threshold log. |
| `cllV>CFV` | Cell Voltage greater than Charge Full Voltage | Log pri prekroceni charge/full-voltage prahu clankem. |
| `SCc` | Service/Charger check kandidat | Kratky service/charger pulse/check helper. |
| `WFLR:x%X` | Word Fault/Lockout Related kandidat | Log fault wordu pred/okolo persistence. |
| `F2Flsh:x%X` | Fault To Flash | Fault word zapisovany do NVM `0x7E94`. |
| `Fail:x%X` | Failure/status bitmask | Obecny failure/status log. |
| `PS:%u %u` | Power/Service State | Stavovy log service/runtime automatu. |

### PBP005 fault/event doplneni

PBP005 hlavni runtime smycka zacina realne kolem `0x4268` (`App_Main_StateMachine` telo).
Pouziva base `0x10000150`.

Pracovni PBP005 offsety:

| Offset | Vyznam |
| --- | --- |
| `base + 0x08` | aktualni fault/event byte, do nej se ORuji bity `0x01/0x02/0x04/0x08/0x10/0x20/0x40` |
| `base + 0x0D` | aktualni stav/state kopirovany do stacku jako `sp+0x14` |
| `base + 0x0F` | persistent/previous fault byte nebo latch souvisejici s fault historii |
| `base + 0x18` | citac pro auto-clear fault stavu |
| `base + 0x1C` | persistent fault word nacteny z `0x7E94` |

Event latch base:

```text
0x100003C8
```

Zjistene latch offsety:

| Offset | Kdo nastavuje | Jak se projevi |
| --- | --- | --- |
| `+0` | interrupt handler `0x4D9C` | hlavni smycka nastavi fault bit `0x10` |
| `+1` | interrupt handler `0x4DB8` | potvrzuje AFE status `0x02 & 0x1000`; hlavni smycka nastavi fault bit `0x40` a loguje `ADOC` |
| `+3` | interrupt handler `0x4DFE` | hlavni smycka nastavi fault bit `0x20` |
| `+4` | handler `0x4D80/0x4D90` | hlavni smycka nastavi fault bit `0x02` |
| `+5` | handler `0x4D58` | wake/fixture/auth related latch, cteno v main smycce |

Interrupt vector table PBP005:

| Vector offset | Handler | Pracovni vyznam |
| --- | --- | --- |
| `0x7C` | `0x4DFF` -> `0x4DFE` | nastavuje latch `0x100003C8+3`, tj. fault bit `0x20`; vola `0x5488` |
| `0xAC` | `0x4DB9` -> `0x4DB8` | kontroluje `0x1D60(2)`, pri potvrzeni nastavuje latch `0x100003C8+1`, tj. fault bit `0x40/ADOC` |

`0x4DB8` pred nastavenim ADOC vola `0x1D60(2)`, ktere komunikuje se slave `0x29`.
`0x1D60` opakovane zapisuje `0x02 = 0xFFFF`, cte `0x02` a testuje masku `0x1000`.
`0x1D48` je jednodussi polling test stejne masky `0x1000`.
To potvrzuje interpretaci, ze PBP005 `0x40/ADOC` je AFE/OZ3705 status `0x02` bit 12, discharge-over-current notification kandidat.

`0x4DFE` nastavuje `0x100003C8+3`, vola `0x5488`, a upravuje register `0x40008000` (`REG = (REG & 0x07) | 0x02`).
`0x5488` nastavuje `*(uint8_t *)0x10000405 = 1`.
To potvrzuje wake/low-power/event interpretaci bitu `0x20`.

### PBP002 fault/event doplneni

PBP002 lockout ma aktualni fault byte `0x24`, tedy `0x20 | 0x04`.
Historie `20 21 20` ukazuje opakovany bit `0x20` a jednou kombinaci `0x20 | 0x01`.

PBP002 pouziva starsi `BmsCtx` layout:

- base kolem `0x100000B8`
- state `ctx+0x39`
- next state `ctx+0x3A`
- fault byte `ctx+0x3B`
- persistent fault word `ctx+0x4C`

Event latch base:

```text
0x10000380
```

PBP002 zpresneni fault bitu:

| Bit | Hodnota | PBP002 vyznam |
| --- | --- | --- |
| bit 0 | `0x01` | AFE/measurement communication failure kandidat; v historii lockoutu je `0x21`. |
| bit 1 | `0x02` | Over-temperature kandidat; pri `ctx+0x24 >= 0x55` po dobu > cca 200 cyklu loguje `FOT`. |
| bit 2 | `0x04` | FOV/marker4/over-voltage kandidat; aktivni v aktualnim PBP002 lockoutu. |
| bit 3 | `0x08` | FUV/marker5/under-voltage kandidat. |
| bit 4 | `0x10` | DOC/discharge over-current kandidat, recovery log `DOCRc`. |
| bit 5 | `0x20` | `0x0900` nastavuje pres pointer `ctx+0x3B`; logy `EUB Time St` a `EUB Flag`. Cell-unbalance kandidat: `max-min >= 401 mV`, `min >= 3301 mV`, timer 2500. Aktivni v aktualnim i historickem PBP002 lockoutu. |
| bit 6 | `0x40` | `0x0900` nastavuje pres pointer `ctx+0x3B`; logy `EUV Time St` a `EUV Flag`. Extreme under-voltage kandidat: `min < 1000 mV`, timer 1000. |
| bit 7 | `0x80` | Nebyl nalezen setter do `ctx+0x3B`; pouziti `0x80` v PBP002 patri timerum, service stavu `0x80` nebo runtime flagum. |

PBP002 nema nalezeny PBP004 fixture key ani D-tech/auth stringy.
Prakticky se proto chova blize PBP005: vhodny prvni test je pasivni UART log, ne aktivni auth.

PBP002 rozliseni persistentniho fault byte a runtime event byte:

- `0x0900` je volana z `0x491A` s `r1 = ctx+0x3B` a nastavuje persistentni `0x20/EUB Flag` a `0x40/EUV Flag`.
- interrupt/event latch `0x10000380+3` nastavuje runtime `ctx+0x34 |= 0x20`, inkrementuje `ctx+0x50` a vola wake helper.
- `ADOC` cesta nastavuje runtime `ctx+0x34 |= 0x40`; neni to stejny zapis jako persistentni `ctx+0x3B |= 0x40`.

PBP002 wake helper `0x52C0` nastavuje:

```c
*(uint8_t *)0x100003D9 = 1;
```

Tento flag je modelovy ekvivalent PBP005 `0x10000405`.

## RAM markery

RAM base helperu ukazuje na `0x10000000`.

Tedy ve vsech aktualne analyzovanych modelech:

- `0x10000004` = marker A
- `0x10000005` = marker B

Marker logika:

- `0x5A` = resetovany / nepotvrzeny stav
- `0xA5` = potvrzeny / OK stav

Vychozi NVM obraz u PBP002/PBP004/PBP005 obsahuje na `0x7E04/0x7E05` hodnoty `5A 5A`. Boot/runtime inicializace tyto RAM markery znovu nastavuje na `0x5A`.

Modelove helpery:

| Model | Set `5A/5A` | Test marker A | Test marker B | Dalsi zapis do `+4/+5` |
| --- | --- | --- | --- | --- |
| PBP002 | `0x2F54`, volano z init vetve `0x43CE` | `0x2F5E` = `[+4] != 0xA5` | `0x2F6E` = `[+5] != 0xA5` | nenalezen |
| PBP004 | `0x4046`, volano z init vetve `0x5574` | `0x4050` = `[+4] != 0xA5` | `0x4060` = `[+5] != 0xA5` | `0x4040` zapisuje 32bit word do `[0x10000000+4]`; vola ho D-tech fixture request `0x04` kolem `0x3784`, v teto vetvi je low byte vynucen na `0x5A` |
| PBP005 | `0x2EB8`, volano z init vetve `0x44A8` | `0x2EC2` = `[+4] != 0xA5` | `0x2ED2` = `[+5] != 0xA5` | nenalezen |

Nebyl nalezen primy lokalni zapis konstanty `0xA5` do `0x10000004/05`.
Nejsilnejsi aktualni zaver je, ze `0xA5` nevznika autonomne v beznem runtime; pokud se v realne baterii objevi, nejspis prichazi ze servisni/factory konfigurace nebo z kopirovane NVM/config oblasti.

PBP004 je v tomto smeru dulezita vyjimka: helper `0x4040` zapisuje cely 32bit word do `0x10000004`. Volani z D-tech requestu `0x04` proto muze ovlivnit markery i sousedni bajty `+6/+7`, ale tato konkretni vetev staticky vynucuje nejspodnejsi bajt zapisovaneho wordu na `0x5A`, ne `0xA5`. Request `0x04` tedy neni read-only, ale zatim neni potvrzen jako `0xA5` setter.

Fault vazba:

- PBP002/PBP004 marker A + vysoke napeti po `0xC9` cyklech nastavuje `0x04/FOV`.
- PBP002/PBP004 marker B + nizke napeti po `0xC9` cyklech nastavuje `0x08/FUV`.
- PBP005 marker A + over-voltage vetvi pres timer a log `EOVs`, pote nastavuje bit `0x04`.
- PBP005 marker B + under-voltage vetvi pres timer a log `EUVs`, pote nastavuje bit `0x08`.
- PBP005 marker B se pouziva i v over-temperature vetvi `>= 85 C`, log `EOTs`, pote nastavuje bit `0x02`.

## Merici / AFE / SMBus vrstva

| Puvodni nazev | Doporuceny nazev | Vyznam |
| --- | --- | --- |
| `FUN_00000678` | `Update_Measurements_And_Check_Ready` | Aktualizace mereni a validace stavu. Pri dlouhodobem selhani vede k fault bitu `0x01`. |
| `FUN_00001b6c` | `AFE3705_Read_NTC_And_Map_To_Temperature` | Cte 12bit hodnotu z AFE `3705T` na adrese `0x29`, prevadi ji pres tabulku. Silny kandidat na NTC/teplotu. |
| `FUN_000017d8` | `AFE3705_WriteRegister16_PEC` | SMBus/I2C zapis na AFE `3705T` s PEC. Pro read-only rezim nepouzivat. |
| `FUN_000033a4` | `CRC8_PEC` | Vypocet CRC-8 PEC s polynomem `0x07`, typicke pro SMBus PEC. |
| `FUN_00003664` | `SMBus_Transfer` | Obal pro SMBus/I2C transakci. Parametry: bus, adresa, TX buffer, TX delka, RX buffer, RX delka. |
| `FUN_00003de4` | `I2C_RunTransfer_Blocking` | Blokujici/pollovaci obal nad realnym I2C stavovym automatem. |
| `FUN_00003ce0` | `I2C_WriteTxDat_Burst4` | Rychla TX cast I2C/USART-like periferie; zapisuje az 4 bajty do TXDAT. Vlastni state-machine krok je PBP005 `0x3DAC`. |

Zarizeni:

- slave address = `0x29`
- KiCad/board marking: `U3`, blok `Analog Front End`, text `3705T`
- pracovni nazev: `AFE_3705T`
- detailni rozbor: `analysis/afe_3705t_smbus.md`

Potvrzena pracovni register mapa podle firmware a `files/ryobi_battery_i2c_read.csv`:

| Command | Pracovni vyznam |
| --- | --- |
| `0x02` | status/protection register kandidat |
| `0x03` | config/control register kandidat |
| `0x05` | init/power/control write `0x0010` |
| `0x0E` | cell-balance control kandidat; `0xFFC0` = balance off, `0xFFC0 | (1 << cell_index)` = balance cell kandidat |
| `0x20` | NTC/temperature ADC kandidat |
| `0x21..0x25` | pet cell-voltage kanalu, raw12 prepocet `raw * 5 / 4` |
| `0x27` | current/shunt ADC kandidat |

Pravdepodobny read ramec:

```text
START
ADDR 0x29 WRITE
COMMAND 0x20
RESTART
ADDR 0x29 READ
DATA0
DATA1
PEC
STOP
```

### PBP005 I2C/SMBus doplneni

`FUN_00003ce0` bylo zpresneno: nejde o zacatek celeho state machine, ale o rychlou cast pro zapis TX dat do I2C/USART-like periferie po blocich 4 bajtu:

```c
while (remaining >= 4 && (base->STAT & 4)) {
    base->TXDAT = buf[0];
    base->TXDAT = buf[1];
    base->TXDAT = buf[2];
    base->TXDAT = buf[3];
    buf += 4;
}
```

Relevantni PBP005 funkce:

| Adresa | Pracovni nazev | Vyznam |
| --- | --- | --- |
| `0x36F8` | `SMBus_Transfer` | Sestavi stack descriptor, vybere I2C base podle bus id, vola `0x3EA2`. Pri chybe manipuluje IRQ/clock maskami. |
| `0x3EA2` | `I2C_RunTransfer_Blocking` | Blocking/polling transfer loop. Volá `0x3E68` init/reset a opakovane `0x3DAC` krok transferu, dokud `descriptor.status` neni hotovy. |
| `0x3DAC` | `I2C_TransferStep` | Vlastni krok state machine podle status bitu a descriptoru. |
| `0x3E68` | `I2C_StartTransfer` | Inicializuje descriptor status na `0xFF`, zapisuje prvni address byte do `base+0x28`, posila START pres `base+0x20 = 2`. |
| `0x3CE0` | `I2C_WriteTxDat_Burst4` | Rychly TX path, zapisuje do registru `base+0x1C`. |
| `0x3F38` | `I2C_PinMuxClock_Init` kandidat | Konfiguruje IOCON/SYSCON pro I2C/UART periferie. |

`0x3DAC` pouziva `descriptor.status` na offsetu `+0x0C`:

| Status | Pracovni vyznam |
| --- | --- |
| `0xFF` | transfer bezi / not complete |
| `0x00` | hotovo OK |
| `0x01` | obecna/necekana state chyba |
| `0x02` | NACK/error state kandidat |
| `0x03` | status bit `0x40` error kandidat |
| `0x04` | STOP/complete transition kandidat |
| `0x05` | status bit `0x10` error kandidat |
| `0x06` | status bit `0x01000000` error/arbitration/timeout kandidat |

Krokova logika:

- kontroluje error bity v `base+0x04` (`0x10`, `0x40`, `0x01000000`) a pri nich nastavuje status `5/3/6`
- pokud je master pending bit aktivni, dekoduje stav z `((base->STAT >> 1) & 7)`
- pri RX-ready vetvi cte byte z `base+0x28` do `rx_buf`, dekrementuje `rx_len`
- pri TX-ready vetvi zapisuje dalsi byte z `tx_buf` do `base+0x28`, dekrementuje `tx_len`
- po dopsani TX a pokud existuje RX delka posle opakovany START s read adresou `(slave_addr << 1) | 1`
- po dokonceni bez chyby nastavuje status `0`

`0x3EA2` je blocking wrapper:

```c
I2C_StartTransfer(base, desc);
do {
    done = I2C_TransferStep(base, desc);
    // hlida take timeout/error flag souvisejici s 0x10000404
} while (!done);
return desc->status == 0;
```

Pouzite periferie/adresy:

- `0x40050000`
- `0x40054000`
- `0x40058000`
- `0x40064000`
- `0x40068000`

Pro SMBus slave `0x29` zustava interpretace `AFE` velmi silna podle logu `AFEPNR`, `AFENR`, `AFECommErr(I/V)`, `ADOC`.

### Cell balancing pres AFE `0x0E`

O2Micro katalog uvadi `OZ3705` jako 3-5 cell DFE s 12bit ADC, I2C a cell balance rizenym hostem pres I2C. To sedi na PBP005 `3705T` a na firmware zapis do commandu `0x0E`.

PBP005:

| Adresa | Pracovni nazev | Vyznam |
| --- | --- | --- |
| `0x0BF4` | `Balance_Control` | Rozhoduje start/stop balancingu podle min/max clanku. |
| `0x1CDC` | `AFE3705_SetBalance_Control` | Zapise command `0x0E` hodnotou `0xFFC0` nebo `0xFFC0 | (1 << cell_index)`. |

`0x0BF4` je volana z hlavniho automatu na `0x44C0`.

Podminky startu PBP005 balancingu:

```text
min_cell_mv >= 0x0E61 = 3681 mV
max_cell_mv - min_cell_mv >= 0x33 = 51 mV
state == 1, nebo state == 2 a nizke 2 bity predaneho flag byte jsou 3
```

Po startu vola `0x1CDC(max_cell_index)`; index je nacten z measurement struktury na offsetu `+0x11`.
Potom nastavi timer `500` ticku/cyklu a loguje `Bal%u>%u`.
Po dobehnuti timeru vola `0x1CDC(0)`, tedy zapis `0x0E = 0xFFC0`, loguje `Bal Dn` a balance vypina.

PBP002/PBP004 maji stejne balance stringy a analogicke funkce:

```text
PBP002 Balance_Control ~ 0x0E8C, callsite 0x43E8
PBP004 Balance_Control ~ 0x0EF4, callsite 0x558E
PBP005 Balance_Control   0x0BF4, callsite 0x44C0
```

Sniff `files/ryobi_battery_i2c_read.csv` obsahuje pouze `0x0E = 0xFFC0`, tedy zachytil stav bez aktivniho balancingu. To sedi s namerenym spreadem clanku jen `43.8..48.8 mV`, pod prahem `51 mV`.

## Logger / diagnosticky vystup

| Funkce | Nazev | Vyznam |
| --- | --- | --- |
| `0x160A/0x165C` | `Debug_Log / Log_Printf_Timestamped` | Timestampovany logger. |
| `FUN_000015ac` / `0x15C6` | `Debug_Printf / CharSink` | Fyzicky vystup logu pres UART/ring buffer. |
| `FUN_000011c0` | `VPrintf_Core` | Variadic/formatovaci jadro. |
| `FUN_000032f2` | `Get_Tick` | Vraci cas/tick pro log. |
| `0x331E` | `RingBuffer_PutByte` | Vkladani bajtu do ring bufferu. |
| `0x334A` | `RingBuffer_GetByte` | Cteni bajtu z ring bufferu. |
| `0x32EC` | `RingBuffer_IsEmpty` | Test prazdneho bufferu. |
| `0x32FC` | `RingBuffer_IsFull` | Test plneho bufferu. |
| `0x31A2/0x31C8` | `UART_TxKick/IRQ` | Prace s USART registry `0x40064000/0x40068000`. |

Logger je realny UART/ring-buffer vystup, ne semihosting.

Logger se spusti jen pokud:

```c
*(DAT_00001690 + 2) == 1
```

Pro PBP005:

- log state base: `0x100000B4`
- `log_enabled`: `0x100000B6 = base + 2`

Pro PBP002 bylo potvrzeno stejne:

- log state base: `0x100000B4`
- `log_enabled`: `0x100000B6 = base + 2`

## GPIO / board init

| Puvodni nazev | Doporuceny nazev | Vyznam |
| --- | --- | --- |
| `FUN_00003e24` | `Board_IO_Init / GPIO_PinMux_Init` | Inicializace pinu, clocku a pinmuxu. |
| `FUN_00004190` | `Set_Pin_Config_Bits_3_4` | Nastavi bity `3:4` v pin config registru. |

`FUN_00004190` dela:

```c
reg = base + index * 4;
*reg = (*reg & ~0x18) | (value << 3);
```

Tedy meni pin funkci / rezim.

## Matematicke/runtime helpery

| Puvodni nazev | Doporuceny nazev | Vyznam |
| --- | --- | --- |
| `FUN_00001ee6` | `__aeabi_uidivmod / Unsigned_DivMod32` | Softwarove unsigned deleni se zbytkem. |
| `FUN_00001ee0` | `__aeabi_idiv` kandidat | Pravdepodobne signed/related deleni, pouziva se ve vypoctech mereni. |

`FUN_00001ee6` vraci:

- low 32 bitu = quotient
- high 32 bitu = remainder

## Datove struktury

### `struct BmsCtx`

```c
struct BmsCtx {
    uint8_t  unk_00[0x37];

    uint8_t  init_done_37;              // ctx + 0x37
    uint8_t  unk_38;                    // ctx + 0x38
    uint8_t  current_state_39;          // ctx + 0x39
    uint8_t  unk_3a;                    // ctx + 0x3A
    uint8_t  fault_flags_3b;            // ctx + 0x3B
    uint8_t  unk_3c;                    // ctx + 0x3C
    uint8_t  retry_flag_3d;             // ctx + 0x3D
    uint8_t  unk_3e;                    // ctx + 0x3E
    uint8_t  periodic_counter_3f;       // ctx + 0x3F

    uint8_t  fault_counter_40;          // ctx + 0x40
    uint8_t  marker5_fault_counter_41;  // ctx + 0x41
    uint8_t  temp_fault_counter_42;     // ctx + 0x42 kandidat
    uint8_t  unk_43;                    // ctx + 0x43

    uint8_t  unk_44[4];

    uint32_t fault_clear_counter_48;    // ctx + 0x48
    uint32_t persistent_fault_word_4c;  // ctx + 0x4C, z 0x7E94
};
```

Zname stavy `current_state_39`:

| Hodnota | Pracovni vyznam |
| --- | --- |
| `0x00` | init / idle / rozhodovaci vstupni stav |
| `0x01` | normal / active kandidat |
| `0x02` | charge-management kandidat |
| `0x03` | discharge/load-management kandidat |
| `0xFE` | standby/sleep kandidat |
| `0xFF` | fault / lockout stav |

### PBP005 hlavni state machine doplneni

PBP005 hlavni stavovy automat ma telo kolem `0x4268`.
Pracovni `BmsCtx` base je `0x10000150`; aktualni stav je ulozen v `base + 0x0D`.
V main smycce se kopiruje do stack byte `sp+0x14`; novy stav se sklada do `sp+0x15`.

Prepinac stavu je v oblasti `0x4978-0x4996`:

```text
state 0x00 -> 0x4998
state 0x01 -> 0x49C4
state 0x02 -> 0x49EC
state 0x03 -> 0x4A82
state 0xFE -> 0x4BD2
state 0xFF -> 0x4C04
```

Pracovni vyklad PBP005 stavu:

| State | Vyznam | Duvod |
| --- | --- | --- |
| `0x00` | init/idle decision | Pokud je persistent fault byte nenulovy, prejde na `0xFF`; pokud je sleep/event bit aktivni, prejde na `0xFE`; jinak prejde do `0x01`. |
| `0x01` | normal/active | Pokud fault byte nenulovy, prejde do `0xFF`; umi prejit do `0xFE` nebo `0x03` podle runtime bitu. |
| `0x02` | charge-management | Pracuje s `base+0x14` substavem, vola `0x630`, `0x54CC`, `0x5744`; obsahuje charge/sleep rozhodovani. |
| `0x03` | discharge/load-management | Resi EOV/EUV/EOT/ADOC souvisejici bity, sleep prechod a kontrolu zateze pres `0x1D60`. |
| `0xFE` | sleep/standby | Pokud fault byte nenulovy, prejde do `0xFF`; jinak sleduje runtime bity a muze se vratit do `0x01/0x02`. |
| `0xFF` | fault persistence / lockout | Posouva a zapisuje fault historii do `0x7E94`; loguje `F2Flsh:x%X`; cisti nektere recoverable bity podle podminek. |

V `0xFF` vetvi:

- pokud low byte persistent wordu neodpovida aktualnimu fault byte, firmware udela `fault_word = current_fault | (fault_word << 8)`
- zapisuje zpet do `0x7E94` pres `0x3788`
- pozdeji dela `fault_word <<= 8` a znovu uklada do `0x7E94`
- log string: `F2Flsh:x%X`

Recovery/clear logy v PBP005:

- `ADOC_E` na `0x4CB8`
- `AFERc` na `0x4E84`
- `DOCRc` na `0x4E8C`

Funkce `0x56AE` (`ServiceState_UpdateForBmsState` kandidat) mapuje BMS stav a mereni na service/fixture state:

| Podminka | Service state |
| --- | --- |
| fault state `0xFF` | `0x02` |
| BMS state `0xFE` nebo default standby | `0x80` |
| `0x4BC(cell_voltage)` vrati `1` | `0x81` |
| `0x4BC(cell_voltage)` vrati `2` | `0x82` |
| `0x4BC(cell_voltage)` vrati `3` | `0x83` |
| `0x4BC(cell_voltage)` vrati `4` | `0x01` |
| special flag / sleep-active | `0x87` nebo `0x8C` |

Funkce `0x5684` kontroluje, zda je aktivni service/pulse output a nejspis blokuje nektere prechody:

- vola `0x2670`
- testuje timer `0x1E4`
- vola `0x0C90`

Funkce `0x5744` loguje `SCc` a kratce nastavuje `base+0x30`; vypada jako special service/charger check pulse.

### Persistent fault word

```c
union PersistentFaultWord {
    uint32_t word;
    uint8_t bytes[4];
};
```

Vyklad:

- `bytes[0]` = aktualni / posledni fault byte
- `bytes[1]` = historie 1
- `bytes[2]` = historie 2
- `bytes[3]` = historie 3

Firmware historii posouva priblizne:

```c
persistent_fault_word = current_fault_flags | (persistent_fault_word << 8);
```

a pozdeji:

```c
persistent_fault_word <<= 8;
```

### SMBus transfer descriptor

```c
struct BusTransfer {
    uint8_t  *tx_buf;      // +0x00
    uint8_t  *rx_buf;      // +0x04
    uint16_t tx_len;       // +0x08
    uint16_t rx_len;       // +0x0A
    uint8_t  status;       // +0x0C
    uint8_t  slave_addr;   // +0x0D
};
```

Pouziti:

```c
SMBus_Transfer(bus_id, 0x29, tx_buf, tx_len, rx_buf, rx_len);
```

### MeasurementCtx

`FUN_00000678` dostava ukazatel `param_1`, volano jako:

```c
FUN_00000678(ctx + 4, mode);
```

Pracovni struktura:

```c
struct MeasurementCtx {
    uint8_t  unk_00[0x0A];

    int16_t  value_0a;          // +0x0A, nesmi byt 0
    uint16_t value_0c;          // +0x0C, porovnava se s limitem

    uint8_t  unk_0e[0x06];

    int32_t  measured_14;       // +0x14, merena hodnota / proud kandidat
    int32_t  filtered_18;       // +0x18
    int16_t  temperature_1c;    // +0x1C, silny teplotni kandidat
    uint8_t  unk_1e[2];

    uint32_t accumulator_20;    // +0x20
    int32_t  computed_limit_24; // +0x24
    int32_t  predicted_28;      // +0x28
};
```

Teplotni kandidat je silny, protoze `FUN_00001b6c` cte 12bit hodnotu a prevadi ji pres tabulky na rozsah podobny stupnum Celsia.

### Zero-init tabulka

Pouzita v `Clear_BSS_Regions`:

```c
struct ZeroRegion {
    uint32_t size;
    uint32_t address;
};
```

Konec tabulky:

```c
size == 0
```

### NVM config oblast

Zname adresy:

- `0x7E00..0x7E93` = persistentni config/string/marker blok kopirovany z/do RAM `0x10000000`
- `0x7E94` = persistent fault/status word
- `0x7EC0` = config flags
- `0x7EDC` = config hodnota sada 1 / A
- `0x7EE0` = config hodnota sada 1 / B
- `0x7EE8` = config hodnota sada 0 / A
- `0x7EEC` = config hodnota sada 0 / B

`FUN_0000522c` validuje:

- horni bity `0x7EC0`
- `0x7EDC / 0x7EE0`
- `0x7EE8 / 0x7EEC`

### NVM/flash writer

Vsechny tri modely maji stejnou logiku flash/NVM update po 64B blocich. Parametry jsou:

```c
NVM_UpdateWords(src_words, dst_addr, word_count);
```

`word_count` je pocet 32bit slov. Funkce:

1. overi, ze `dst_addr >= 0x7E00` a ze je 4B zarovnana,
2. vypocte dotcene 64B bloky,
3. pro kazdy blok nacte existujici obsah, vlozi nove wordy,
4. pokud se blok lisi, provede erase/program,
5. po zapisu blok znovu porovna.

Modelove adresy:

| Model | Write helper | Read/copy helper | Flash prep/unlock kandidat | Erase kandidat | Program kandidat |
| --- | --- | --- | --- | --- | --- |
| PBP002 | `0x36F4` | `0x3838` | `0x3998` | `0x40FC` | `0x40D0` |
| PBP004 | `0x4A48` | `0x4B42` | `0x4C5C` | `0x4A36` | `0x49E0` |
| PBP005 | `0x3788` | `0x38C6` | `0x3A54` | `0x41B0` | `0x4184` |

Zname write cesty do `0x7E00..0x7EFF`:

| Cil | PBP002 | PBP004 | PBP005 | Vyznam |
| --- | --- | --- | --- | --- |
| `0x7E00..0x7E93` | `0x27EC -> 0x36F4(0x10000000,0x7E00,0x25)` | `0x3918 -> 0x4A48(0x10000000,0x7E00,0x25)` | `0x2728 -> 0x3788(0x10000000,0x7E00,0x25)` | Persistentni RAM config/string/marker blok. PBP004 request `0x04` nastavuje dirty flag a muze spustit tuto cestu. |
| `0x7E94` | `0x4B66`, `0x4C02` | `0x5D16`, `0x5DB4` | `0x4C1C`, `0x4CA8` | Persistentni fault history word; zapis ve fault state `0xFF`, nejdrive aktualni fault + historie, potom posun historie. |
| `0x7EC0` | `0x50EC`, `0x515E`, `0x517E`, `0x521E` | `0x5FD8`, `0x614A` a dalsi service/config vetve | `0x528C`, `0x52F6`, `0x5316`, `0x53C8` | Config flags; bity se ORuji s hodnotami typu `0x80000000` / `0x02000000`. |
| `0x7EDC` | `0x5128 -> 0x4F70` | analog ve service/config vetvi | `0x52C0 -> 0x5014` | Config/calibration hodnota sada 1 / A. |
| `0x7EE0` | `0x513A -> 0x4FAA` | analog ve service/config vetvi | `0x52D2 -> 0x504E` | Config/calibration hodnota sada 1 / B. |
| `0x7EE4` | `0x51B6 -> 0x4F1E` | analog ve service/config vetvi | `0x535E -> 0x4FC2` | Dalsi config hodnota / threshold kandidat. |
| `0x7EE8` | `0x51C8 -> 0x4F70` | `0x60F0 -> 0x4A48` | `0x5372 -> 0x5014` | Config/calibration hodnota sada 0 / A. |
| `0x7EEC` | `0x51DA -> 0x4FAA` | `0x6128 -> 0x4A48` | `0x5384 -> 0x504E` | Config/calibration hodnota sada 0 / B. |

Read-only diagnostika nesmi volat zadnou z techto cest. Bezpecnejsi jsou jen pasivni logy a explicitne cteci requesty.

## Dalsi identifikovane funkce

PBP005 / souvisejici adresy:

| Adresa | Pracovni nazev | Vyznam |
| --- | --- | --- |
| `0x33CC` | `UART_ServicePump_RxTx` | Presouva data mezi USART RX/TX a ring buffery. Potvrzuje aktivni vstup `D-RX`. |
| `0x213C` | `Service_GPIO_StateMachine` | Service/fixture/GPIO pattern FSM. Stav struct `0x10000230`. Neni potvrzen jako D-tech packet parser. |
| `0x2120` | `Service_SetState_WithTimeout` | Nastavi stav ve strukture `0x10000230`, armuje timeout objekt na `+8`. |
| `0x206E` | `Fixture_AuthWindow_Check` | Auth-window gate, pouziva `0x100003B0`. |
| `0x2600` | `DTech_SendPulseOrResponsePattern` | Delay/toggle/state byte, pravdepodobne vystupni odezva/pattern. |
| `0x26A0` | `Load_Defaults_And_NVM_Config` | Inicializuje RAM blok `0x10000000` z defaults a NVM. |
| `0x2744` | `MeasurementStats_InitFlags` | Inicializace mericich statistik/flags. |
| `0x2776` | `MeasurementStats_UpdateMinMaxAvg` | Update min/max/avg statistik. |
| `0x58D8` | `Watchdog_Init` | Inicializace watchdogu. |
| `0x5914` | `Watchdog_Feed_WWDT` | Feed WWDT. |
| `0x592E` | `Watchdog_Feed_Global` | Globalni watchdog feed. |
| `0x390C` | `Block_IsAllZero` kandidat | Kopiruje blok na stack a vraci 1, pokud je vse nula. |
| `0x38C6` | `BlockCopy` | Kopirovani bloku. |
| `0x1E18` | `memcpy` | Runtime copy. |
| `0x1E7E` | division helper | Runtime deleni. |

### PBP005 service state machine `0x213C`

`0x213C` je tabulkovy service/fixture state machine nad strukturou `0x10000230`.
Podrobnejsi samostatny rozbor je v `analysis/pbp005_state_machine.md`.

Zaklad struktury:

| Offset | Vyznam |
| --- | --- |
| `+0x00` | current service state |
| `+0x01` | previous service state |
| `+0x02` | substate/counter |
| `+0x08` | timeout/timer object |
| `+0x10` | dalsi timeout/timer object pro konkretni pattern |

Jump table:

```text
state 0x01 -> 0x22A4
state 0x02 -> 0x22AC
state 0x03 -> 0x22D2
state 0x04 -> 0x22DA
state 0x05 -> 0x22E2
state 0x06 -> 0x22EA
state 0x07 -> 0x22F0
state 0x08 -> 0x22F8
state 0x09 -> 0x2300
state 0x0A -> 0x2306
state 0x0B -> 0x230E
state 0x0C -> 0x2316
state 0x0D -> 0x231E
state 0x0E -> 0x2326
state 0x0F -> 0x232C
state 0x10 -> 0x2332
state 0x11 -> 0x22A4
state 0x12..0x7F -> default 0x25E4
state 0x80 -> 0x233A
state 0x81 -> 0x2360
state 0x82 -> 0x237E
state 0x83 -> 0x239E
state 0x84 -> 0x23D0
state 0x85 -> 0x23F8
state 0x86 -> 0x2426
state 0x87 -> 0x2454
state 0x88 -> 0x22A4
state 0x89 -> 0x2488
state 0x8A -> 0x24C2
state 0x8B -> 0x2530
state 0x8C -> 0x25A2
```

Pouzite vystupni/periferie konstanty v service FSM:

- `A0002200`
- `A0002280`
- `A0002300`
- `A0001050`
- `1C100000`
- `18100000`
- `14100000`
- `10100000`

Tato funkce tedy pravdepodobne neresi D-tech UART framing jako PBP004, ale board/service signalizaci pres GPIO/piny a timeout patterny.
PBP005 D-tech fixture auth tak zustava nepotvrzena.

Prakticky PBP005 tooling:

```powershell
python tools\dtech_uart.py --profile pbp005 --port COM5 --baud 115200 listen-log
python tools\dtech_uart.py pbp005-map
python tools\dtech_uart.py decode-log "F2Flsh:x40"
```

Aktivni `auth/raw/listen` PBP004 D-tech ramcu jsou ve skriptu pro `--profile pbp005` blokovane, protoze PBP005 parser/key nejsou potvrzene.

PBP004 D-tech parser/dispatcher:

| Adresa | Pracovni nazev | Vyznam |
| --- | --- | --- |
| `0x1D2E` | `DTech_RxFrameParser` | Parser UART ramcu. Hleda start `0x46`, cte hlavicku, payload a CRC. |
| `0x1E9C` | `DTech_TxFrameBuildAndSend` | Sestavuje vystupni ramec. Celkova delka = `payload_len + 10`. |
| `0x202E` | `DTech_SendErrorOrOneByteResponse` | Posila chybovy/jednobajtovy response. |
| `0x205A` | `DTech_CreateSlaveChallengeResponse` | Handshake `opcode/type 1`, generuje slave challenge response. |
| `0x20CC` | `DTech_CheckFinalResponse` | Handshake `opcode/type 3`, overuje final response. |
| `0x2120` | `DTech_SendType5Payload` | Descriptor type `5`, pointer na payload, delka. |
| `0x36AC` | `DTech_Request_Dispatch_And_FixtureAuth` | Dispatcher D-tech requestu a fixture auth payloadu. |
| `0x3652` | `DTech_ChecksumFromGlobal` | Vraci 16bit checksum/nonce z globalu XOR s konstantou. |
| `0x4854` | `DTech_ChallengeTransform` | Transformace 16bit challenge/response. |

Podrobny PBP004 D-tech rozbor vcetne rizikovosti requestu je v `analysis/pbp004_dtech_analysis.md`.

## D-tech log stringy v PBP004

Vybrane stringy:

- `0x6AFC`: `D-tech Creating/Sending Slave Challenge Response...`
- `0x6B30`: `Slave Challenge Response Sent Successfully`
- `0x6B5C`: `Slave Challenge Response Error: %d`
- `0x6B80`: `D-tech Received Final Response...`
- `0x6BA4`: `Final Response Received Successfully`
- `0x6BCC`: `Error on Final Response: %d`
- `0x6BE8`: `D-tech Authentication successful`
- `0x6C0C`: `D-tech Authentication failed`
- `0x6C4C`: `D-tech received CRC error message`
- `0x6C70`: `D-tech State Reset`
- `0x6F14`: `D-tech OCP3 Request Received.`
- `0x6F34`: `D-tech OTP Request Received.`
- `0x6F54`: `D-tech UVP%d Request Received.`
- `0x6F74`: `D-tech Pack Status Request Received.`
- `0x6F9C`: `D-tech Bad Header CRC Received`
- `0x6FBC`: `D-tech Other Error`
- `0x6FD0`: `Fxr%X`
- `0x6FD8`: `D-tech Fixture Auth Failure Byte: %d`
- `0x7000`: `DTk Fixture Auth Success`
- `0x701C`: `Auth DTk Fxtr Success`
- `0x7034`: `Unauthorized D-tech Fixture Request.`

## Internetovy pruzkum

Bylo hledano podle:

- `D-tech Authentication successful`
- `D-tech Bad Header CRC Received`
- `D-tech Fixture Auth Failure Byte`
- `Auth DTk Fxtr Success`
- `Ryobi PBP004/PBP005`
- `Ryobi D-TX D-RX`
- `Ryobi UART battery protocol`
- `Ryobi LPC804 battery`

Zaver: nebyl nalezen verejny rozbor konkretniho Ryobi D-tech protokolu, auth sekvence, framingu `0x46`, CRC ani fixture key.

## PBP005 fixture auth stav

PBP004 obsahuje jasnou fixture-auth smycku v `0x36AC`, ktera porovnava 10 bajtu proti tabulce na `0x705C`.

PBP005:

- byte pattern PBP004 klice `C2 C7 60 7A B5 8F 44 D2 4E 7A` se v PBP005 nevyskytuje
- heuristicky scan auth smycky typu `movs index,#0x0A; ldrb [payload,index]; compare with table; decrement` nasel tento vzor v PBP004, ale ne v PBP005
- `0x213C` v PBP005 je service/GPIO/pattern state machine, ne jasny UART packet parser jako PBP004
- PBP005 tedy pravdepodobne nepouziva stejnou 10B fixture-auth vetev jako PBP004, nebo je implementovana jinym mechanismem bez stejne smycky/stringu

Pro prakticky test plati:

- PBP004 default key je potvrzen staticky
- PBP005 key neni potvrzen
- Python klient `tools/dtech_uart.py` ma proto prepinac `--key`

## Otevrene body

Nejdulezitejsi nezname:

1. Oficialni expanze zkratky `EUB`; prakticky vyznam je uz posunuty na cell-unbalance fault (`max-min >= 401 mV`, `min >= 3301 mV`, timer 2500).
2. Oficialni vyznam nazvu `ADOC` u PBP005; staticky je potvrzen AFE/OZ3705 status `0x02 & 0x1000`, discharge-over-current notification kandidat.
3. Prakticky overit zdroj marker hodnoty `0xA5`: u PBP004 request `0x04` vola `0x4040`, ale v teto vetvi staticky vynucuje low byte `0x5A`; potvrzeny `0xA5` setter zatim neni nalezen u zadneho modelu.
4. Prakticky overit PBP004 read-like fixture request `0x0A` proti realne baterii; staticky kopiruje tri bloky po `0x2A` bajtech a posila delku `0x7F`.
5. Presny vyznam stavu `0x00`, `0x01`, `0x02`, `0x03`, `0xFE`, `0xFF`.
6. Plny datasheet/register mapa pro O2Micro `OZ3705` / board marking `3705T`, hlavne oficialni nazvy registru `0x02` a `0x0E`.
7. Zda PBP005 ma nejaky jiny aktivni service protokol mimo PBP004-style D-tech. PBP004 fixture key, auth stringy ani packet parser nebyly v PBP005 nalezeny.
8. Prakticke overeni D-tech CRC a auth proti realne baterii.
9. Prakticky overit, zda se PBP002 persistentni fault bit `0x80` v realnych dumpech vubec objevi; staticky nebyl nalezen zadny normalni setter do `ctx+0x3B`.

Nejlepsi dalsi funkce k rozebrani:

- `FUN_000015ac` - fyzicky diagnosticky vystup/log, pro modely mimo PBP005
- doplnit oficialni nazvy PBP005 I2C status/error bitu podle datasheetu AFE/OZ3705 nebo MCU I2C periferie
