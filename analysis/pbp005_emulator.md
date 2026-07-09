# PBP005 hybridni emulator

Soubor:

```text
tools/pbp005_emulator.py
```

Ucel:

- spoustet izolovane funkce PBP005 firmware pod Unicorn ARM Thumb emulaci,
- nemodelovat cely LPC804, ale stubovat periferie,
- logovat LED/GPIO, tlacitko `IND_SW`, casovace a budouci NVM/I2C/UART pristupy.

## Stav implementace

Podporovano:

| Oblast | Stav |
| --- | --- |
| Intel HEX loader | hotovo |
| Flash `0x00000000..0x0000FFFF` | hotovo |
| RAM `0x10000000..0x1000FFFF` | hotovo |
| GPIO/MMIO `0xA0000000..` | hotovo jako stubovana pamet |
| Peripheral MMIO `0x40000000..` | hotovo jako stubovana pamet |
| `0x2120 Service_SetState_WithTimeout` | spustitelne |
| `0x213C Service_LED_GPIO_StateMachine` | spustitelne |
| `0x4E98 Wait_Indicator_Button_PressRelease` | spustitelne |
| `0x4BC` cell-voltage classifier | spustitelne |
| `0x56AE ServiceState_UpdateForBmsState` | spustitelne pres `bms-map` harness |
| `0x36F8 SMBus_Transfer` | stubovana AFE odpoved pro slave `0x29` |
| `0x17AC AFE3705_ReadRegister16_PEC` | spustitelne pres `afe-read` |
| `0x1A04 AFE cell-voltage read` | spustitelne pres `afe-cell` |
| `0x1D48 AFE ADOC status check` | spustitelne pres `afe-adoc` |
| `0x1CDC AFE balance control write` | spustitelne pres `afe-balance` |
| `0x1A6 timer_start` | stub / volitelny tick deadline model |
| `0x1E4 timer_expired` | stub / rucne rizeny / tick deadline model |
| `0x148 delay` | stub; s `--timer-model` posouva emulator tick |
| `0x4CD8 periodic tick` | `ticker`, vola firmware periodickou obsluhu |
| `0x4268 BMS runtime` | `bms-runtime`, spusti cele telo runtime s perifernimi stuby |
| GPIO LED log | hotovo |
| SMBus PEC CRC-8 `0x07` | hotovo pro emulovanou AFE odpoved |
| `0x3788 NVM_UpdateWords` | stubovany bezpecny NVM update s old/new logem |
| NVM read/update CLI | `nvm-read`, `nvm-update` |
| BMS fault persistence harness | `fault-persist` pro stav `0xFF` / `0x7E94` |
| UART char sink `0x15C6` | `uart-send`, sbira vystup do `uart_tx` |
| Debug log `0x165C` | `log-text`, stub formatter + UART TX |
| UART service pump `0x33CC` | `uart-pump`, emulovane TX/RX ring buffery |
| Firmware UART service `0x16BA` | `uart-runtime-pump`, overuje realne firmware napojeni `0x33CC` |
| AFE measurement snapshot | `afe-scan` |
| obecne `run-func` stack args | hotovo |
| obecne `run-func` memory pokes | hotovo |

## Priklady

Spustit cekani na tlacitko `IND_SW`.
Emulator vygeneruje nejdrive nestisknuty stav, potom aktivni-low stisk a uvolneni:

```powershell
python tools\pbp005_emulator.py button-wait --press-after 1 --release-after 1
```

Ocekavane chovani:

```text
0x4E98 ceka na GPIO_W15 == 0
0x4E98 ceka na navrat GPIO_W15 != 0
```

Spustit LED state `0x07`:

```powershell
python tools\pbp005_emulator.py led-state --state 0x07
```

Potvrzeny vystup:

```text
GPIO_CLR0 0x1C100000 -> clear vsech 4 LED bitu
GPIO_SET0 0x18000000 -> LED1+LED2
```

Spustit LED state `0x0C`:

```powershell
python tools\pbp005_emulator.py led-state --state 0x0C
```

Potvrzeny vystup:

```text
GPIO_SET0 0x04100000 -> LED3+LED4
```

Spustit fault/blink state `0x02` a rucne oznacit pattern timer `0x10000240` jako vyprsely:

```powershell
python tools\pbp005_emulator.py led-state --state 0x02 --steps 2 --expire-timer 0x10000240
```

Potvrzeny vystup:

```text
GPIO_CLR0 0x1C100000
GPIO_SET0 0x1C100000
```

Spustit cell-voltage band state `0x82` s vyprselym pattern timerem:

```powershell
python tools\pbp005_emulator.py led-state --state 0x82 --steps 3 --expire-timer 0x10000240
```

Potvrzeny vystup:

```text
GPIO_SET0 0x18000000
GPIO_CLR0 0x04100000
```

Spustit napetovy klasifikator `0x4BC`:

```powershell
python tools\pbp005_emulator.py voltage-class --mv 3901
```

Potvrzeny vystup:

```text
cell_mv=3901
voltage_class=4
```

Spustit mapper BMS stavu na service/LED state `0x56AE`:

```powershell
python tools\pbp005_emulator.py bms-map --bms-state 0xff --cell-mv 3700
```

Potvrzeny vystup:

```text
service_state=0x02 (fault/lockout blink all LED bits)
```

Standby/sleep:

```powershell
python tools\pbp005_emulator.py bms-map --bms-state 0xfe --cell-mv 3700
```

Potvrzeny vystup:

```text
service_state=0x80 (standby/default)
```

Special service flag:

```powershell
python tools\pbp005_emulator.py bms-map --bms-state 0x01 --flag-byte 0x08 --special-flag 1
```

Potvrzeny vystup:

```text
service_state=0x87 (special service flag active)
```

BMS state `0x03` special load/sleep pulse:

```powershell
python tools\pbp005_emulator.py bms-map --bms-state 0x03 --extra-arg 3
```

Potvrzeny vystup:

```text
service_state=0x8C (BMS state 0x03 special pattern)
```

Obecne volani libovolne funkce s registry, stack argumenty a predzapisem pameti:

```powershell
python tools\pbp005_emulator.py run-func 0x4bc --r0 3901
python tools\pbp005_emulator.py run-func 0x56ae --r0 0xff --r1 0x10001000 --r2 0x10001010 --r3 0x10001020 --mem16 0x1000102a=3700 --stack 0
```

## AFE / SMBus emulace

Emulator stubuje `0x36F8 SMBus_Transfer`. Pro slave `0x29` generuje odpovedi z emulovane mapy AFE registru a pocita SMBus PEC:

```text
read PEC = crc8([0x52, command, 0x53, data_hi, data_lo])
write PEC = crc8([0x52, command, data_hi, data_lo])
```

Vychozi AFE registry:

| Command | Default word | Vyznam |
| --- | --- | --- |
| `0x02` | `0x8082` | status/protection, bez ADOC bitu |
| `0x03` | `0xFC9C` | config/control hodnota ze sniffu |
| `0x05` | `0x0010` | power/init kandidat |
| `0x0E` | `0xFFC0` | balance off |
| `0x20` | `0xFBBF` | NTC/temperature raw |
| `0x21` | `0xFC72` | cell 1 raw |
| `0x22` | `0xFC72` | cell 2 raw |
| `0x23` | `0xFC73` | cell 3 raw |
| `0x24` | `0xFC4E` | cell 4 raw |
| `0x25` | `0xFC72` | cell 5 raw |
| `0x27` | `0xF63C` | current/shunt raw kandidat |

Read word pres firmware funkci `0x17AC`:

```powershell
python tools\pbp005_emulator.py afe-read --cmd 0x02
```

Potvrzeny vystup:

```text
smbus ... tx=02 ... read cmd=0x02 word=0x8082 pec=0xBA
status=0
word=0x8082
```

Cell voltage read pres firmware funkci `0x1A04`:

```powershell
python tools\pbp005_emulator.py afe-cell --index 1
```

Potvrzeny vystup:

```text
smbus ... tx=21 ... read cmd=0x21 word=0xFC72 pec=0xCE
millivolts=3982
```

ADOC status check pres firmware funkci `0x1D48`:

```powershell
python tools\pbp005_emulator.py afe-adoc
python tools\pbp005_emulator.py afe-adoc --afe-reg 0x02=0x9082
```

Potvrzene vystupy:

| `0x02` word | `adoc_status` |
| --- | --- |
| `0x8082` | `0` |
| `0x9082` | `1` |

Balance write pres firmware funkci `0x1CDC`:

```powershell
python tools\pbp005_emulator.py afe-balance --cell 3
```

Potvrzeny vystup:

```text
smbus ... tx=0E FF C8 5D ... write cmd=0x0E 0xFFC0->0xFFC8 pec=0x5D calc=0x5D
afe_reg_0e=0xFFC8
```

AFE registry lze prepsat:

```powershell
python tools\pbp005_emulator.py afe-cell --index 4 --afe-reg 0x24=0xFC4E
```

AFE komunikacni chybu lze vynutit:

```powershell
python tools\pbp005_emulator.py afe-read --cmd 0x02 --afe-fail
```

Potvrzeny vystup:

```text
status=1
word=0x0000
```

AFE measurement snapshot pres firmware read helpery:

```powershell
python tools\pbp005_emulator.py afe-scan
python tools\pbp005_emulator.py afe-scan --afe-reg 0x02=0x9082
```

Potvrzeny defaultni vystup:

```text
cells_mv=3982,3982,3983,3937,3982
cell_min_mv=3937
cell_max_mv=3983
cell_spread_mv=46
raw_0x20=0xFBBF
raw_0x27=0xF63C
adoc_status=0
```

`--afe-reg 0x02=0x9082` prepne `adoc_status=1`.

Plny measurement update helper `0x0338` je dostupny pres:

```powershell
python tools\pbp005_emulator.py afe-measure-update --max-instructions 120000
```

Tento harness spousti firmware cestu:

```text
0x0338 -> 0x1A04 cell 1..5 -> 0x1A8C current/raw 0x27 -> 0x1B18 temperature/raw 0x20
```

Potvrzeny defaultni vystup:

```text
status=1
cells_mv=3982,3982,3983,3937,3982
min_cell_mv=3937
max_cell_mv=3983
cell_sum_mv=19866
min_cell_index=4
max_cell_index=3
temperature_1c=1
computed_limit_24=1000
```

Negativni/variantni testy:

```powershell
python tools\pbp005_emulator.py afe-measure-update --max-instructions 120000 --afe-fail
python tools\pbp005_emulator.py afe-measure-update --max-instructions 120000 --afe-reg 0x24=0xF060
```

- `--afe-fail` vrati `status=0` a struktura zustane nulova.
- `0x24=0xF060` simuluje velmi nizky clanek 4; vystup ma `cells_mv=3982,3982,3983,120,3982`, `min_cell_mv=120`, `min_cell_index=4`.

Struktura z `0x0338`:

| Offset | Vyznam |
| --- | --- |
| `+0x00..+0x09` | pet `uint16_t` cell napeti v mV |
| `+0x0A` | minimum cell mV |
| `+0x0C` | maximum cell mV |
| `+0x0E` | soucet cell mV |
| `+0x10` | index clanku s minimem, 1..5 |
| `+0x11` | index clanku s maximem, 1..5 |
| `+0x14` | raw/current hodnota kandidat z cesty `0x1A8C` |
| `+0x18` | filtrovana raw/current hodnota kandidat |
| `+0x1C` | teplota kandidat z `0x1B18` |
| `+0x20..+0x2C` | vypoctove limity/akumulatory pro runtime fault logiku |

Emulovany AFE model ma side-effect pro status clear:

```text
write 0x02 = 0xFFFF
```

se neulozi jako literalni status `0xFFFF`, ale vycisti ADOC bit `0x1000`.
Tedy `0x9082 -> 0x8082`, coz odpovida pouziti firmware jako clear/ack.
Pro kontrolni test lze side-effect vypnout volbou `--no-afe-status-clear`.

## NVM / fault history emulace

Emulator stubuje PBP005 `0x3788 NVM_UpdateWords`. Misto realneho erase/program po 64B blocich provede bezpecny zapis do emulovane flash oblasti `0x7E00..0x7EFF` a zaloguje puvodni i novou hodnotu.

Cteni defaultniho persistent fault wordu:

```powershell
python tools\pbp005_emulator.py nvm-read --addr 0x7e94 --words 1
```

Potvrzeny vystup pro fixed image:

```text
0x00007E94: 0x00000000
```

Primy NVM update pres stub `0x3788`:

```powershell
python tools\pbp005_emulator.py nvm-update --addr 0x7e94 --value 0x00000040
```

Potvrzeny vystup:

```text
nvm        pc=0x3788 update src=0x10001100 dst=0x00007E94 words=1
nvm-write  pc=0x3788 0x00007E94: 0x00000000->0x00000040
status=0
0x00007E94: 0x00000040
```

Vice slov, napr. config flags oblast:

```powershell
python tools\pbp005_emulator.py nvm-update --addr 0x7ec0 --value 0x80000000 --value 0x02000000
```

### BMS fault persistence harness

Prvni BMS harness je `fault-persist`. Modeluje stav `0xFF` nad persistent fault wordem `0x7E94` podle rozebrane vetve:

```c
if ((fault_word & 0xff) != current_fault) {
    fault_word = current_fault | (fault_word << 8);
    write_nvm_0x3788(0x7E94, fault_word);  // pc 0x4C1C
}

fault_word <<= 8;
write_nvm_0x3788(0x7E94, fault_word);      // pc 0x4CA8
```

Priklad PBP005 ADOC/fault `0x40` z cisteho fixed stavu:

```powershell
python tools\pbp005_emulator.py fault-persist --current-fault 0x40 --old-word 0x00000000
```

Potvrzeny vystup:

```text
nvm-write  pc=0x4C1C 0x00007E94: 0x00000000->0x00000040
nvm-write  pc=0x4CA8 0x00007E94: 0x00000040->0x00004000
first_word=0x00000040
final_word=0x00004000
```

Pokud low byte persistent wordu uz odpovida aktualnimu faultu, prvni zapis se preskoci:

```powershell
python tools\pbp005_emulator.py fault-persist --current-fault 0x40 --old-word 0x00000040
```

Potvrzeny vystup:

```text
nvm-write  pc=0x4CA8 0x00007E94: 0x00000040->0x00004000
```

Poznamka: `fault-persist` je zatim cilenejsi a stabilnejsi nez pokus o cele spusteni runtime tela `0x4268`. Plne `0x4268` pouziva mnoho runtime/peripheral helperu, stack-local promennych a globalu, takze pro nej bude potreba dalsi sada stubu.

## UART / log emulace

Emulator ma tri urovne UART podpory:

| Firmware funkce | Emulator prikaz | Vyznam |
| --- | --- | --- |
| `0x15C6` | `uart-send` | char sink; pri `r0 == 0` posila znak do emulovaneho UART TX |
| `0x165C` | `log-text` | debug logger; emulator cte format string, zformatuje argumenty a zapise do `uart_tx` |
| `0x33CC` | `uart-pump` | UART RX/TX pump nad ring buffery |

Primy znakovy vystup:

```powershell
python tools\pbp005_emulator.py uart-send --text HELLO
```

Potvrzeny vystup:

```text
uart_tx='HELLO'
```

Debug log formatovani:

```powershell
python tools\pbp005_emulator.py log-text --text "AINT:0x%X" --arg 0x9082
python tools\pbp005_emulator.py log-text --text "PS:%u %u" --arg 255 --arg 64
```

Potvrzene vystupy:

```text
uart_tx='AINT:0x9082'
uart_tx='PS:255 64'
```

UART service pump s emulovanymi ring buffery:

```powershell
python tools\pbp005_emulator.py uart-pump --rx "46 01 02" --tx ascii:OK
```

Potvrzeny vystup:

```text
uart-pump  pc=0x33CC uart=0 tx_drained=2 rx_inserted=3
uart_tx='OK'
rx_ring=46 01 02
```

Vyklad:

- `--tx` predplni firmware TX ring a `0x33CC` ho vycerpa do emulovaneho `uart_tx`.
- `--rx` simuluje bajty prijate z fyzickeho `D-RX`; `0x33CC` je vlozi do firmware RX ring bufferu.
- Toto je harness-only test: volame `0x33CC` s vlastnim RX ring descriptorem.

Skutecna firmware cesta `0x16BA` vola `0x33CC` jen pro logger/TX servis a predava `rx_ring = 0`.
To lze overit:

```powershell
python tools\pbp005_emulator.py uart-runtime-pump --rx "46 01 02"
```

Potvrzeny vystup:

```text
uart-pump  pc=0x33CC uart=0 tx_drained=0 rx_inserted=0 rx_dropped=3
uart_rx_remaining=
```

Vyklad:

- `RingBuffer_GetByte 0x334A` ma v PBP005 jediny nalezeny caller `0x33F8`, uvnitr `0x33CC`.
- `0x33CC` ma jediny BL caller `0x16CE`, uvnitr firmware service funkce `0x16BA`.
- `0x16BA` predava do `0x33CC` `r2 = 0`, tedy zadny RX ring parser neni pripojeny.
- Scan instrukci nenasel `cmp #0x46`, tedy ani zjevny PBP004-style start-byte parser.

Pracovni zaver: PBP005 ma realny UART TX/log a low-level RX pumpu, ale firmware nema potvrzenou vyssi RX parser/dispatcher cestu.

## Ticker / casovy beh

Prikaz `ticker` zapina emulatorovy tick model a opakovane vola firmware periodickou funkci `0x4CD8`.
Ta podle staticke analyzy inkrementuje interny tick, vola service/LED FSM `0x213C` a obsluhuje periodicke priznaky.

Napetovy LED state `0x82`, kde pattern timer `0x10000240` vyprsi po 20 ticich:

```powershell
python tools\pbp005_emulator.py ticker --state 0x82 --ticks 25
```

Potvrzeny vystup:

```text
[tick 000001] timer_start(... timeout=20) deadline=21
[tick 000021] timer_expired(timer=0x10000240) -> 1
[tick 000021] GPIO_SET0 0x18000000 -> LED1+LED2
[tick 000021] GPIO_CLR0 0x04100000 -> LED3+LED4
led_state=0x18000000 (LED1+LED2)
```

Fault/lockout blink state `0x02`, kde pattern timer prepina po 250 ticich:

```powershell
python tools\pbp005_emulator.py ticker --state 0x02 --ticks 260
```

Potvrzeny vystup:

```text
[tick 000001] timer_start(... timeout=250) deadline=251
[tick 000251] timer_expired(timer=0x10000240) -> 1
[tick 000251] GPIO_SET0 0x1C100000 -> LED1+LED2+LED3+LED4
```

Uzitecne volby:

- `--print-each` vypise stav/substav/LED po kazdem ticku.
- `--realtime-ms 10` prida realne cekani 10 ms po kazdem ticku.
- `--show-timers` vypise i nevyprsene dotazy `timer_expired -> 0`.
- `--timer-model` lze zapnout i u ostatnich prikazu; `ticker` ho zapina automaticky.

## BMS runtime 0x4268

Prikaz `bms-runtime` spousti cele telo PBP005 runtime kolem `0x4268` s perifernimi stuby:

```powershell
python tools\pbp005_emulator.py bms-runtime --max-instructions 160000
```

Oproti obecnemu `run-func 0x4268` dela navic:

- zapne debug log flag, takze `0x165C` posila text do `uart_tx`,
- zapne tick/deadline model timeru,
- pouziva stuby pro busy-wait helpery `0x0128`, `0x0134`, `0x3388`,
- mapuje ARM SCS/NVIC oblast `0xE0000000`,
- drzi I2C status bit `0x40054004 & 0x01000000`, aby low-level I2C wait neuvizl,
- po dobehu vypise BMS context `0x10000150`, event latch `0x100003C8` a `0x7E94`.

Potvrzene chovani v defaultnim modelu:

```text
smbus write 0x03 = 0xFCAC
smbus write 0x05 = 0x0010
smbus read 0x21..0x25
smbus read 0x27
smbus read 0x20
smbus read 0x02 -> 0x8082
smbus write 0x02 = 0xFFFF clear-status=>0x8082
uart_tx='FOfFOfAINT:0x8082PS:0 1FOfAINT:0x8082FOfSlp 0'
bms_fault_event=0x08
nvm_7e94=0x00000000
```

Instrumentace ukazala, ze defaultni `bms_fault_event=0x08` v tomto harnessu nevznika v pozdejsi marker-B vetvi `0x4840/EUVs`.
Zapisuje se drive na `0x4608`:

```text
0x45C4 ldrb r5, [ctx + 0x08]
0x45CA bl 0x04E8
0x45CE cmp r0, #0
0x45D0 beq 0x4600
0x45DA strb ..., [ctx + 0x08]   ; helper vratil 1, nastavuje 0x04
0x4608 strb ..., [ctx + 0x08]   ; helper vratil 0, nastavuje 0x08
```

`0x04E8` je volano jako `0x04E8(r0=1, state=sp+0x14)`.
V defaultnim behu je `state=0`, service/window flag `0x100003B4` je `0`, helper zalozi/pouzije timer `0x100003E8` s timeoutem `1000` a na prvnim pruchodu vraci `0`.
Proto runtime zvoli bit `0x08`. `0x4446` tento byte na zacatku dalsi smycky znovu cisti a `0x7E94` zustava `0x00000000`.

Pracovni zaver: tento defaultni `0x08` je per-loop event/status selector z `0x04E8`, ne persistentni lockout zapis a ne dukaz aktivni `EUVs` vetve.

Kontrolni beh s vynucenym timeoutem:

```powershell
python tools\pbp005_emulator.py bms-runtime --max-instructions 160000 --expire-timer 0x100003E8
```

potvrdil alternativni vetev:

```text
timer_expired(timer=0x100003E8) -> 1 (forced)
debug-log 'T1H+TO'
0x45DA ctx+0x08 = 0x04
bms_fault_event=0x04
nvm_7e94=0x00000000
```

Pozor: `bms-runtime` stale neni plny MCU boot s realnymi interrupt zdroji.
Je to stabilni harness pro runtime telo a pro porovnavani AFE/NVM/log vetvi bez fyzicke baterie.

## Dulezite adresy

| Adresa | Vyznam |
| --- | --- |
| `0x4CD8` | periodic tick handler |
| `0x10000230` | service/LED state struct |
| `0x10000238` | hlavni timeout objekt `base+8` |
| `0x10000240` | pattern timeout objekt `base+0x10` |
| `0xA000103C` | `GPIO_W15`, `PIO0_15 / IND_SW` |
| `0xA0001050` | `GPIO_W20`, `PIO0_20 / LED4` |
| `0xA0002200` | `GPIO_SET0` |
| `0xA0002280` | `GPIO_CLR0` |
| `0xA0002300` | `GPIO_NOT0` |
| `0x100003D8` | byte flag cteny helperem `0x0C90` |
| `0x10001000` | emulator harness: flag byte pointer pro `0x56AE` |
| `0x10001010` | emulator harness: gate byte pointer pro `0x56AE` |
| `0x10001020` | emulator harness: measurement struct pro `0x56AE` a `0x0338` |
| `0x10001040` | emulator harness: AFE output word pointer |
| `0x10001100` | emulator harness: NVM source buffer |
| `0x10001200` | emulator harness: UART/log text buffer |
| `0x10001300` | emulator harness: UART TX ring descriptor |
| `0x10001320` | emulator harness: UART RX ring descriptor |
| `0x10001400` | emulator harness: UART TX ring data |
| `0x10001500` | emulator harness: UART RX ring data |
| `0x7E94` | persistent fault/status word |
| `0x7EC0` | config flags |
| `0x40054004` | emulovany I2C status/ready register |
| `0xE0000000..` | stubovana ARM SCS/NVIC oblast |

## Omezeni

- Emulator zatim nespousti cely reset/startup tok.
- Casovace jsou stale hookovane na urovni helperu, ale umi jednoduchy emulatorovy tick/deadline model.
- Ticker neni plny interrupt/NVIC model; opakovane vola znamou periodickou funkci `0x4CD8`.
- GPIO polarita LED je stale logicka podle firmware bitu; fyzicke sviceni zavisi na zapojeni.
- AFE je zatim word-register model na urovni `0x36F8`, ne bitove presny LPC I2C peripheral model.
- Plny BMS runtime `0x4268` je spustitelny pres `bms-runtime`, ale stale s periferii na urovni stubu, ne jako plny MCU/NVIC model.
- UART je zatim modelovany na urovni char sinku/debug loggeru/ring pumpy, ne jako bitove presny USART peripheral.
- Watchdog je zatim jen mapovana pamet nebo budouci hook.

## Dalsi kroky

1. Napojit ticker na dalsi periodicke/globalni priznaky, pokud se potvrdi jejich adresy.
2. Rozsirit `afe-measure-update` o automatizovane scenare nizkeho clanku, rozbalancovani a AFE communication fail.
3. Rozebrat, kam presne navazuje `0x0338` na runtime fault funkce `0x0630`, `0x0BF4`, `0x2776` a `0x28EC`.
4. Pridat detailnejsi AFE status bit mapu pro registr `0x02`.
5. Pokud se najde PBP005 RX parser, napojit ho na `uart-pump` RX ring.
