# Function map / stav rozpracovani

Tento soubor je prakticky checklist. Rozlisuje funkce, ktere uz jsou spustitelne v emulatoru nebo nastrojich, od funkci, ktere jsou zatim jen staticky rozebrane a cekaji na dalsi zpracovani.

## Legenda

| Stav | Vyznam |
| --- | --- |
| `EMU OK` | Funkce ma named harness v `tools/pbp005_emulator.py` a byla prakticky spustena. |
| `STUB OK` | Funkce je v emulatoru hookovana/stubovana a funguje jako podpora vyssich harnessu. |
| `TOOL OK` | Existuje samostatny Python nastroj mimo Unicorn emulator. |
| `STATIC OK` | Staticky rozebrano s dobrou jistotou, ale bez samostatneho harnessu. |
| `PARTIAL` | Cast vyznamu je znama, ale chybi presne napojeni, okraje nebo vsechny stavy. |
| `OPEN` | Dulezity kandidat, ktery je potreba jeste rozebrat. |

## PBP005 - emulovane / spustitelne

| Funkce / adresa | Pracovni nazev | Stav | Jak spustit / poznamka |
| --- | --- | --- | --- |
| `0x2120` | `Service_SetState_WithTimeout` | `EMU OK` | `python tools\pbp005_emulator.py led-state --state 0x82 --steps 3` |
| `0x213C` | `Service_LED_GPIO_StateMachine` | `EMU OK` | Soucast `led-state` a `ticker`; ovlada LED GPIO patterny. |
| `0x4E98` | `Wait_Indicator_Button_PressRelease` | `EMU OK` | `python tools\pbp005_emulator.py button-wait` |
| `0x04BC` | `VoltageClass_FromCellMv` | `EMU OK` | `python tools\pbp005_emulator.py voltage-class --mv 3700` |
| `0x56AE` | `ServiceState_UpdateForBmsState` | `EMU OK` | `python tools\pbp005_emulator.py bms-map ...` |
| `0x17AC` | `AFE3705_ReadRegister16_PEC` | `EMU OK` | `python tools\pbp005_emulator.py afe-read --cmd 0x02` |
| `0x1A04` | `AFE3705_ReadCellVoltage` | `EMU OK` | `python tools\pbp005_emulator.py afe-cell --index 4` |
| `0x1D48` | `AFE3705_Is_ADOC_Status_Set` | `EMU OK` | `python tools\pbp005_emulator.py afe-adoc --afe-reg 0x02=0x9082` |
| `0x1CDC` | `AFE3705_SetBalance_Control` | `EMU OK` | `python tools\pbp005_emulator.py afe-balance --cell 3` |
| `0x0338` | `AFE_Measurement_Update` | `EMU OK` | `python tools\pbp005_emulator.py afe-measure-update --max-instructions 120000` |
| `0x3788` | `NVM_UpdateWords` | `STUB OK` | `python tools\pbp005_emulator.py nvm-update --addr 0x7e94 --value 0x40` |
| stav `0xFF` | `FaultPersist_Sequence` | `EMU OK` | `python tools\pbp005_emulator.py fault-persist --current-fault 0x40` |
| `0x15C6` | `UART_CharSink` | `EMU OK` | `python tools\pbp005_emulator.py uart-send --text ABC` |
| `0x165C` | `Debug_Log` | `EMU OK` | `python tools\pbp005_emulator.py log-text --text "AINT:0x%X" --arg 0x9082` |
| `0x33CC` | `UART_ServicePump_RxTx` | `EMU OK` | `python tools\pbp005_emulator.py uart-pump --rx "46 01" --tx "41 42"` |
| `0x16BA` | `Firmware_UART_Service` | `PARTIAL` | `uart-runtime-pump`; realna PBP005 cesta predava `rx_ring=0`, tedy zatim bez RX parseru. |
| `0x4CD8` | `Periodic_Tick` | `EMU OK` | `python tools\pbp005_emulator.py ticker --state 0x82 --ticks 25` |
| `0x4268` | `BMS_Runtime_Body` | `EMU OK/PARTIAL` | `python tools\pbp005_emulator.py bms-runtime --max-instructions 160000`; bezi s perifernimi stuby, ne jako plny MCU boot. |

## PBP005 - stubovane periferie a podpurne funkce

| Funkce / adresa | Pracovni nazev | Stav | Poznamka |
| --- | --- | --- | --- |
| `0x36F8` | `SMBus_Transfer` | `STUB OK` | Emulator modeluje AFE slave `0x29`, word registry a SMBus PEC. |
| `0x01A6` | `Timer_Start` | `STUB OK` | Podporuje fixed i tick/deadline model. |
| `0x01E4` | `Timer_Expired` | `STUB OK` | Podporuje `--expire-timer` pro vynuceni vetvi. |
| `0x0128`, `0x0134`, `0x0148`, `0x3388` | delay/busy-wait/tick wait | `STUB OK` | Nutne pro beh service/BMS smycek bez uviznuti. |
| `0x40054004` | I2C ready status | `STUB OK` | Emulator drzi ready bit `0x01000000`. |
| `0xA0002200/2280/2300` | GPIO set/clear/not | `STUB OK` | Loguje LED masky. |
| `0xA000103C` | `PIO0_15 / IND_SW` | `STUB OK` | Aktivni-low tlacitko stavovych LED. |

## PBP005 - staticky rozebrane, zatim bez plneho harnessu

| Funkce / adresa | Pracovni nazev | Stav | Co vime / co chybi |
| --- | --- | --- | --- |
| `0x04E8` | `Timed_Event_Select_T1H` kandidat | `STATIC OK` | Rozhoduje runtime `ctx+0x08` mezi `0x08` a `0x04`; timeout objekt `0x100003E8`, log `T1H+TO`. Samostatny harness jeste neni. |
| `0x0BF4` | `Balance_Control` | `STATIC OK/PARTIAL` | Rozhoduje start/stop balancingu podle `min/max` clanku; vola `0x1CDC(max_cell_index)` a pozdeji `0x1CDC(0)`. Chybi samostatny scenario harness. |
| `0x0630` | `EUB_EUV_TimedFault_Check` kandidat | `STATIC OK/PARTIAL` | Pracuje s `min/max` clanku, nastavuje timed `0x20/0x40` do predaneho fault pointeru; potreba napojit na `afe-measure-update` scenario. |
| `0x2776` | `MeasurementStats_UpdateMinMaxAvg` | `STATIC OK/PARTIAL` | Kopiruje/update mereni a statistiky; callsite `0x494C`. Chybi named harness. |
| `0x28EC` | `RuntimeStats_StateDependent_Update` kandidat | `PARTIAL` | Navazuje na stav a measurement/statistiky; potreba docistit vetve. |
| `0x1D60` | `AFE3705_ClearAndConfirm_ADOC` | `STATIC OK/PARTIAL` | Smycka clear/ack `0x02=0xFFFF`, potom test `0x1000`; vhodny dalsi harness. |
| `0x1DFC` | `AFE3705_Read_Status02_ToPtr` | `STATIC OK` | Pouzito pro log `AINT:0x%X`. |
| `0x19B6` | `AFE3705_ClearStatus02_AllOnes` | `STATIC OK` | Zapisuje `0x02=0xFFFF`; emulator uz ma side-effect clear ADOC bitu. |
| `0x1A8C` | `AFE3705_ReadCurrentRaw27` kandidat | `PARTIAL` | Volano z `0x0338`; raw/current kandidat z registru `0x27`. Chybi samostatny harness/vystup. |
| `0x1B18` | `AFE3705_ReadTemperatureRaw20` kandidat | `PARTIAL` | Volano z `0x0338`; teplotni kandidat z registru `0x20`. Chybi samostatny harness/vystup. |
| `0x4DB8` | `ADOC_Interrupt_Handler` | `STATIC OK/PARTIAL` | Potvrzuje `event_latch+1`; bez NVIC/interrupt harnessu. |
| `0x4DFE` | `WakeLowPower_Event_Handler` | `STATIC OK/PARTIAL` | Nastavuje `event_latch+3` a `0x10000405`; bez interrupt harnessu. |
| `0x4D9C` | `Indicator_Button_SampleLow` | `STATIC OK/PARTIAL` | Cte `PIO0_15`, nastavuje event latch; dalsi kandidát pro ticker/peripheral harness. |
| `0x5488` | `WakeLowPower_Flag_Set` | `STATIC OK` | Nastavi `0x10000405 = 1`. |
| `0x57A8`, `0x57EC` | service/wake/log helpery | `PARTIAL` | Viditelne v runtime cestach, presny vyznam jeste neni uzavren. |
| `0x5684` | `ServicePulse_IsActive` kandidat | `PARTIAL` | Blokuje nektere prechody; vola `0x2670` a `0x0C90`. |
| `0x5744` | `Special_Service_ChargerCheckPulse` kandidat | `PARTIAL` | Loguje `SCc`, pracuje s `base+0x30`. |
| `0x5914`, `0x592E` | watchdog feed | `STATIC OK` | Feed WWDT/global watchdog. Neni prioritni pro diagnostiku. |

## PBP005 - otevrene / dalsi zpracovani

| Oblast | Stav | Dalsi prace |
| --- | --- | --- |
| RX parser | `OPEN` | PBP005 nema nalezeny jasny D-tech RX parser; `0x16BA -> 0x33CC` predava `rx_ring=0`. Zkusit dalsi string/callgraph/signature hledani. |
| `0x0338 -> fault runtime` napojeni | `OPEN` | Propojit measurement update se `0x0630`, `0x0BF4`, `0x2776`, `0x28EC` v named scenarich. |
| AFE status register `0x02` bit mapa | `PARTIAL` | Potvrzen `0x1000 = ADOC`; ostatni bity z `0x8082` jeste pojmenovat jen opatrne. |
| AFE register map `0x20..0x27` | `PARTIAL` | Pracovne sedi na O2Micro/OZ3705T-like chovani, ale dokumentace D/T variant se rozchazi. |
| Marker setter `0xA5` | `OPEN` | Setter pro `0x10000004/05 = 0xA5` stale nenalezen; PBP004 request `0x04` potvrzuje spise `0x5A` cestu. |
| Presne vyznamy BMS stavu `0x00/01/02/03/FE/FF` | `PARTIAL` | Hlavni dispatch znamy, presne prechody jeste dodelavat podle scenaru. |

## PBP004 - D-tech / UART

| Funkce / adresa | Pracovni nazev | Stav | Poznamka |
| --- | --- | --- | --- |
| `0x1D2E` | `DTech_RxFrameParser` | `STATIC OK` | Jasny UART frame parser; start `0x46`, hlavicka, payload, CRC. |
| `0x1E9C` | `DTech_TxFrameBuildAndSend` | `STATIC OK` | Vystupni frame, celkova delka `payload_len + 10`. |
| `0x202E` | `DTech_SendErrorOrOneByteResponse` | `STATIC OK` | Error/jednobajtova odpoved. |
| `0x205A` | `DTech_CreateSlaveChallengeResponse` | `STATIC OK` | Auth handshake typ `1`. |
| `0x20CC` | `DTech_CheckFinalResponse` | `STATIC OK` | Auth handshake typ `3`. |
| `0x2120` | `DTech_SendType5Payload` | `STATIC OK` | Payload descriptor type `5`. |
| `0x36AC` | `DTech_Request_Dispatch_And_FixtureAuth` | `STATIC OK/PARTIAL` | Dispatcher requestu a fixture auth; klic/tabulka potvrzena. Request `0x04` format jeste dodelat. |
| `0x3652` | `DTech_ChecksumFromGlobal` | `STATIC OK` | 16bit checksum/nonce z globalu XOR konstantou. |
| `0x4854` | `DTech_ChallengeTransform` | `STATIC OK` | Transformace challenge/response. |
| `tools/dtech_uart.py` | PBP004 auth/raw klient | `TOOL OK/PARTIAL` | Auth a read-only komunikace pripraveny, prakticke overeni proti baterii chybi. |

## PBP002 - fault / EUB/EUV

| Funkce / adresa | Pracovni nazev | Stav | Poznamka |
| --- | --- | --- | --- |
| `0x0900` | `EUB_EUV_TimedFault_Check` | `STATIC OK` | Nastavuje persistentni `0x20/EUB Flag` a `0x40/EUV Flag` pres pointer `ctx+0x3B`. |
| `0x0E8C` | `Balance_Control` kandidat | `STATIC OK/PARTIAL` | Analog PBP005 `0x0BF4`. |
| state machine | PBP002 BMS/state flow | `PARTIAL` | Lockout word a hlavni fault bity rozebrane, ale neni Unicorn harness. |
| bit `0x80` | neznamy fault bit | `OPEN` | Stale nerozklicovano u PBP002. |

## Spolecne / runtime / NVM

| Funkce / oblast | Stav | Poznamka |
| --- | --- | --- |
| Startup/reset trampoline | `STATIC OK` | Reset skoky a runtime init jsou popsane v hlavnim souhrnu. |
| BSS clear / zero init | `STATIC OK` | Tabulka `ZeroRegion { size, address }`. |
| NVM writers PBP002/PBP004/PBP005 | `STATIC OK/PARTIAL` | Zakladni write cesty do `0x7E00..0x7EFF` jsou zmapovane; detailni validace vsech config vetvi jeste pokracuje. |
| `0x7E94` fault history | `EMU OK` pro PBP005, `STATIC OK` pro PBP002/PBP004 | PBP005 ma `fault-persist` harness. |
| `CRC8_PEC` | `STATIC OK` | SMBus PEC polynom `0x07`; emulator ho pocita ve stubu. |
| UART/log vystup | `EMU OK` pro PBP005 | `log_enabled` je `0x100000B6`; vystup jde pres char sink/ring/UART cestu. |

## Prioritni dalsi kroky

1. Pridat scenario harness pro `0x0630` nad vystupem `afe-measure-update`, hlavne low-cell/EUV a unbalance/EUB.
2. Pridat named harness pro `0x0BF4 Balance_Control`, vcetne zapnuti a vypnuti balancingu.
3. Oddelit samostatne harnessy pro `0x1A8C` a `0x1B18`, aby byl jasny current/temperature prepocet.
4. Dodelat callgraph mapu PBP005 runtime `0x4268` po blocich `0x43xx..0x4Cxx`.
5. U PBP004 dokoncit request `0x04` payload format a read-only klienta proti realne baterii.
6. U PBP002 dohledat fault bit `0x80`.
