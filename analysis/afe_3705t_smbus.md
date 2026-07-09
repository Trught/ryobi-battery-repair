# AFE 3705T SMBus/I2C analysis

Datum posledni aktualizace: 2026-07-09

## Shrnuti

Zarizeni na SMBus/I2C adrese `0x29` je v aktualnim stavu nejlepe identifikovat jako:

```text
AFE_3705T
```

Dukazy:

- KiCad schema PBP005 ma blok `Analog Front End` a u `U3` text `3705T`.
- `U3` pouziva lokalni symbol `3705`.
- Realy logic-analyzer trace `files/ryobi_battery_i2c_read.csv` ukazuje opakovane transakce na `h29`.
- Firmware PBP002/PBP005/PBP004 pouziva SMBus PEC s CRC-8 polynomem `0x07`.
- Registry `0x21..0x25` se chovaji jako pet cell-voltage kanalu.
- Register `0x20` se pouziva pro NTC/temperature ADC prevod.
- Register `0x27` se pouziva jako proudovy/shunt ADC kandidat.
- O2Micro katalog uvadi `OZ3705` jako 3-5 cell DFE s 12bit ADC, I2C a cell-balance rizenym hostem pres I2C.

Nebyl zatim nalezen verejny plny datasheet/register-map dokument. Katalog ale posouva identifikaci z obecneho `3705T` na velmi silny kandidat `OZ3705`.
Porad neni spravne tvrdit oficialni nazvy registru bez plneho datasheetu.

`BQ7718` je na PBP005 schematu take pritomen (`U10`), ale jde o samostatny over-voltage protector. Neni to SMBus zarizeni na `0x29`.

## Adresa a PEC

7bit SMBus/I2C adresa:

```text
0x29
```

Do PEC se zahrnuje 8bit adresa:

```text
write address = 0x52
read address  = 0x53
```

PEC:

```text
CRC-8 polynomial 0x07, init 0x00
```

Overene vzorky z `files/ryobi_battery_i2c_read.csv`:

| Transakce | PEC vstup | PEC |
| --- | --- | --- |
| write `0x03 = 0xFC9C` | `52 03 FC 9C` | `58` |
| write `0x02 = 0xFFFF` | `52 02 FF FF` | `22` |
| read `0x02 -> 0x8082` | `52 02 53 80 82` | `BA` |
| write `0x05 = 0x0010` | `52 05 00 10` | `60` |
| write `0x0E = 0xFFC0` | `52 0E FF C0` | `65` |
| read `0x21 -> 0xFC72` | `52 21 53 FC 72` | `CE` |
| read `0x20 -> 0xFBBF` | `52 20 53 FB BF` | `DE` |

## PBP005 I2C state machine

Relevantni firmware funkce:

| Adresa | Pracovni nazev | Vyznam |
| --- | --- | --- |
| `0x36F8` | `SMBus_Transfer` | Sestavi transfer descriptor a vola blocking runner. |
| `0x3E68` | `I2C_StartTransfer` | Nastavi `descriptor.status = 0xFF`, zapise prvni address byte do `base+0x28`, posle START pres `base+0x20 = 2`. |
| `0x3DAC` | `I2C_TransferStep` | Jeden krok podle status/error bitu a `MSTSTATE` z `base+0x04`. |
| `0x3EA2` | `I2C_RunTransfer_Blocking` | Smycka nad `0x3DAC`; vraci success, pokud finalni `descriptor.status == 0`. |

Descriptor offsety:

| Offset | Vyznam |
| --- | --- |
| `+0x00` | TX buffer pointer |
| `+0x04` | RX buffer pointer |
| `+0x08` | TX length |
| `+0x0A` | RX length |
| `+0x0C` | status (`0xFF` bezi, `0x00` OK, jina hodnota chyba/stav) |
| `+0x0D` | 7bit slave address |

Krok `0x3DAC`:

- error bity `0x10`, `0x40`, `0x01000000` v `base+0x04` mapuje na status `5`, `3`, `6`
- z `((base->STAT >> 1) & 7)` rozlisuje RX-ready, TX-ready, NACK/error a complete/STOP vetve
- pri TX vetvi bere bajty z `tx_buf` a zapisuje je do `base+0x28`
- pri RX vetvi cte `base+0x28` do `rx_buf`
- po TX casti a nenulove RX delce posila repeated START s `(slave_addr << 1) | 1`

## Realy I2C trace

Soubor:

```text
files/ryobi_battery_i2c_read.csv
```

Zachycene inicializacni / ridici zapisy:

```text
W 0x03 = 0xFC9C
W 0x02 = 0xFFFF
R 0x02 = 0x8082
W 0x05 = 0x0010
W 0x0E = 0xFFC0
```

Opakovany periodicky vzor:

```text
W 0x0E = 0xFFC0
R 0x21
R 0x22
R 0x23
R 0x24
R 0x25
R 0x27
R 0x20
```

Pocty v trace:

| Command | Typ | Pocet | Poznamka |
| --- | --- | ---: | --- |
| `0x02` | read/write | 1/1 | status/protection register kandidat |
| `0x03` | write | 1 | config/control register kandidat |
| `0x05` | write | 1 | init/power/control write `0x0010` |
| `0x0E` | write | 303 | periodicky write `0xFFC0`, balance-control kandidat; v trace balance off |
| `0x20` | read | 303 | NTC/temperature ADC kandidat |
| `0x21` | read | 303 | cell channel 1 kandidat |
| `0x22` | read | 303 | cell channel 2 kandidat |
| `0x23` | read | 303 | cell channel 3 kandidat |
| `0x24` | read | 303 | cell channel 4 kandidat |
| `0x25` | read | 303 | cell channel 5 kandidat |
| `0x27` | read | 303 | current/shunt ADC kandidat |

PEC validace celeho trace:

```text
events: 39746
transactions: 2428
reads: 2122
writes: 306
PEC errors: 0
extra read byte after PEC: 0xFF in all 2122 reads
```

Read transakce clockuji po `Re-Start` ctyri bajty. PEC sedi na prvnich dvou datovych bajtech:

```text
ADDR 0x29 WRITE, command
RESTART
ADDR 0x29 READ
DATA0
DATA1
PEC
0xFF
STOP
```

Ctvrty bajt je v celem trace vzdy `0xFF`. Pro dekodovani hodnot a PEC se proto pouzivaji jen `DATA0`, `DATA1`, `PEC`; `0xFF` je padding/over-read kandidat.

Opakovatelny dekoder je v:

```text
tools/decode_afe_i2c_trace.py
```

## O2Micro OZ3705 katalog

O2Micro produktovy katalog 2023 uvadi v rade AFE/DFE polozku `OZ3705`:

- 3 az 5 Li-ion clanku
- 12bit ADC
- I2C do 400 kHz
- mereni napeti clanku, proudu a externi teploty
- cell balance rizen hostem pres I2C
- host muze pres I2C cist napeti clanku v rozsahu 1.5 az 5 V

To velmi dobre sedi s lokalni evidenci:

- board marking `3705T`
- 5 cell kanalu `0x21..0x25`
- `raw12 * 5 / 4` dava rozsah do cca 5119 mV
- `0x20` odpovida externimu temperature/NTC kanalu
- `0x27` odpovida current-sense ADC
- `0x0E` se podle firmware chova jako hostem rizeny balance register

## Pracovni register mapa

| Command | Pracovni nazev | Staticky dukaz |
| --- | --- | --- |
| `0x02` | `AFE_Status_Protect` kandidat | PBP005 `0x1D48/0x1D60/0x1DFC/0x19B6`; read/clear/check status bits, ADOC mask `0x1000`. |
| `0x03` | `AFE_Config_Control` kandidat | `0x17D8`, `0x19C6`, `0x1B6C`; read/write word, docasne meneno pri temperature read path. |
| `0x05` | `AFE_Init_PowerControl` kandidat | `0x1888` zapisuje `0x0010` po `0x03` write. |
| `0x0E` | `AFE_CellBalance_Control` kandidat | PBP005 `0x1CDC` zapisuje `0xFFC0` nebo `0xFFC0 | (1 << cell_index)`; volano z balance funkce se stringy `Bal Dn` a `Bal%u>%u`. |
| `0x20` | `AFE_TemperatureOrNTC_ADC` | `0x1B6C` cte raw 12bit hodnotu a prevadi ji pres tabulku na teplotu. |
| `0x21..0x25` | `AFE_CellVoltage_1..5` | `0x1A5C(index)` cte command `0x20 + index`, bere `raw & 0x0FFF` a vraci `raw * 5 / 4`. |
| `0x27` | `AFE_CurrentOrShunt_ADC` kandidat | `0x18E2/0x1930` cte `0x27` 32x, prumeruje a pouziva pro vypocet proudu/offsetu. |

### Cell voltage format

Firmware v `0x1A5C`:

```c
command = 0x20 + cell_index;
word = AFE_ReadWord(command);
raw12 = word & 0x0FFF;
cell_mv = raw12 * 5 / 4;
```

Priklad z trace:

```text
R 0x21 -> 0xFC72
raw12 = 0xC72 = 3186
cell_mv ~= 3186 * 5 / 4 = 3982 mV
```

Tato skala odpovida realnym Li-ion clankum.

Statistika z celeho sniffu:

| Kanal | Raw12 min | Raw12 avg | Raw12 max | mV min | mV avg | mV max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `0x21` | 3184 | 3185.95 | 3187 | 3980.0 | 3982.4 | 3983.8 |
| `0x22` | 3184 | 3186.13 | 3188 | 3980.0 | 3982.7 | 3985.0 |
| `0x23` | 3185 | 3186.10 | 3188 | 3981.2 | 3982.6 | 3985.0 |
| `0x24` | 3148 | 3149.23 | 3151 | 3935.0 | 3936.5 | 3938.8 |
| `0x25` | 3184 | 3185.72 | 3188 | 3980.0 | 3982.1 | 3985.0 |

Rozptyl clanku v zachycenych 303 kompletnich cyklech:

```text
min spread = 43.8 mV
avg spread = 46.4 mV
max spread = 48.8 mV
cycles over EUB threshold 401 mV = 0
```

Tento sniff tedy nezachycuje EUB fault stav. Naopak potvrzuje, ze firmware prepocet `raw12 * 5 / 4` dava realisticke napeti 5s packu okolo `19.87 V`.

### Temperature / NTC path

Funkce `0x1B6C`:

- cte `0x20`
- kontroluje PEC
- maskuje raw 12bit hodnotu
- podle rozsahu docasne meni command `0x03`
- znovu cte `0x20`
- mapuje hodnotu pres tabulku na teplotu
- zapisuje PEC-korektni hodnotu zpet do AFE

To podporuje vyklad `0x20` jako NTC/temperature ADC multiplexed channel.

### Current / shunt path

Funkce `0x18E2`:

- 32x cte command `0x27`
- bere `word & 0x0FFF`
- prumer uklada jako baseline/offset

Funkce `0x1930`:

- znovu 32x cte `0x27`
- pocita rozdil proti baseline
- pouziva deleni a limity pro proudovy vypocet

Proto je `0x27` silny kandidat na current/shunt ADC.

Statistika z trace:

```text
0x20 raw12 = 3007..3008, avg 3007.01
0x27 raw12 = 1592..1600, avg 1595.94
```

Bez znalosti tabulek/kalibrace nelze z techto raw hodnot spolehlive udelat stupne C nebo ampery. Pro identifikaci registru jsou ale stabilni a periodicke hodnoty velmi uzitecne: `0x20` se chova jako pomalu meneny NTC/ADC kanal, `0x27` jako shunt/current ADC kanal s malym rozptylem kolem offsetu.

## Cell balancing

PBP005 obsahuje explicitni balance funkci:

```text
0x0BF4 = Balance_Control kandidat
0x1CDC = AFE3705_SetBalance_Control kandidat
```

Stejne log stringy jsou i v PBP002 a PBP004:

```text
Bal Dn
Bal%u>%u
```

PBP005 hlavni automat vola `0x0BF4` z `0x44C0`.

Pracovni pseudokod:

```c
min_cell_mv = meas->min_cell_0a;
max_cell_mv = meas->max_cell_0c;
max_cell_index = meas->max_cell_index_11;   // 1..5 kandidat

if (min_cell_mv >= 0x0E61 && (max_cell_mv - min_cell_mv) >= 0x33) {
    if (state == 1 || (state == 2 && ((*flags & 3) == 3))) {
        if (!balance_active) {
            AFE_Write0E(0xFFC0 | (1 << max_cell_index));
            start_timer(balance_timer, 500);
            log("Bal%u>%u", max_cell_mv, min_cell_mv);
            balance_active = 1;
        } else if (timer_expired(balance_timer)) {
            AFE_Write0E(0xFFC0);
            log("Bal Dn");
            balance_active = 0;
        }
    }
} else {
    AFE_Write0E(0xFFC0);
    balance_active = 0;
}
```

Prahy:

```text
0x0E61 = 3681 mV minimalni clanek
0x0033 = 51 mV minimalni rozdil clanku pro start balance
500 ticks/cycles = balance on-time kandidat
```

`AFE_Write0E(index)` v PBP005 `0x1CDC` dela:

```c
if (index != 0 && index < 6)
    word = 0xFFC0 | (1 << index);
else
    word = 0xFFC0;

write_register16(0x0E, word);
```

To znamena:

- `0x0E = 0xFFC0` vypina balancing
- `0x0E = 0xFFC2/0xFFC4/0xFFC8/0xFFD0/0xFFE0` pravdepodobne zapina balance pro cell index `1..5`
- ve sniffu je jen `0xFFC0`, tedy balance byl vypnuty
- sniff ma spread jen `43.8..48.8 mV`, coz je pod balance prahem `51 mV`

## Vztah k ADOC, AFEPNR, AFENR

PBP005 `ADOC` cesta pracuje s AFE status registrem `0x02`.

Relevantni funkce:

- `0x1D48` cte command `0x02` a vraci `(status & 0x1000) != 0`
- `0x1D60(tries)` zapisuje `0x02 = 0xFFFF`, znovu cte `0x02` a overuje, zda bit `0x1000` zmizel
- `0x1DFC` cte `0x02` pro log `AINT:0x%X`
- `0x19B6` zapisuje `0x02 = 0xFFFF`, clear/ack status kandidat
- `0x4DB8` je ADOC interrupt handler, ktery vola `0x1D60(2)` a pri potvrzeni nastavuje latch

To znamena, ze `ADOC` je protection/status bit z AFE_3705T/OZ3705, ne lokalni MCU-only stav.
Pracovni maska:

```text
register 0x02, bit 12, mask 0x1000
```

V normalnim sniffu byl `0x02 = 0x8082`, tedy `0x1000` nebyl nastaven.

Logy `AFEPNR`, `AFENR`, `AFECommErr(I/V)` sedi s tim, ze firmware rozlisuje:

- AFE power/not-ready stav
- AFE communication/PEC chybu
- merici hodnoty mimo validni rozsah

## Stav identifikace

Potvrzeno:

- SMBus/I2C 7bit address `0x29`
- SMBus PEC CRC-8 `0x07`
- realny board component label/marking `3705T`
- velmi silny katalogovy kandidat `OZ3705`
- funkce boardu: analog front end pro 5 clanku, NTC a proud/shunt
- firmware register subset `0x02`, `0x03`, `0x05`, `0x0E`, `0x20..0x25`, `0x27`

Nezjisteno:

- oficialni nazvy registru
- presny vyznam status bitu v `0x02`
- plna oficialni register mapa `OZ3705`

Nejlepsi dalsi overeni:

- makro fotka U3 markingu z desky
- porovnat pinout U3 se schematem a hledat podle 16pin 5s AFE `3705T`
- zachytit trace pri ADOC/FOV/FUV stavu a porovnat `0x02` bity
- pasivne cist `0x20..0x27` bez zapisu a overit skalu na znamem napeti clanku
