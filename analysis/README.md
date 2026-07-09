# Ryobi battery firmware analysis

Tato slozka obsahuje prubezny stav staticke analyzy firmwaru Ryobi baterii.

Soubory:

- `firmware_static_analysis.md` - hlavni souhrn funkci, datovych struktur, NVM/RAM oblasti, fault bitu a otevrenych bodu.
- `dtech_uart_protocol.md` - zatim zjisteny D-tech/fixture UART protokol, delky ramcu, CRC, auth sekvence a klice.
- `pbp002_state_machine.md` - samostatny rozbor PBP002 lockout wordu, fault bitu a service/fixture state machine.
- `afe_3705t_smbus.md` - rozbor SMBus/I2C zarizeni `0x29`, realneho trace, OZ3705 kontextu, cell balancingu a pracovni register mapy AFE `3705T`.
- `../tools/decode_afe_i2c_trace.py` - offline dekoder Digilent WaveForms I2C CSV trace pro AFE `0x29`.
- `pbp004_dtech_analysis.md` - samostatny rozbor PBP004 D-tech parseru, auth sekvence, fixture key a requestu.
- `pbp005_state_machine.md` - samostatny rozbor PBP005 BMS a service/fixture state machine.
- `pbp005_emulator.md` - navod k hybridnimu Unicorn emulatoru pro PBP005 LED/tlacitkovy service automat.
- `function_map.md` - mapa funkci podle stavu: spustitelne v emulatoru, staticky rozebrane, rozpracovane a otevrene.
- `../tools/dtech_uart.py` - Python klient pro PBP004 D-tech auth/raw komunikaci a PBP002/PBP005 pasivni UART log/mapovani stavu.
- `../tools/pbp005_emulator.py` - cilena dynamicka emulace PBP005 firmware funkci `0x2120`, `0x213C` a `0x4E98`.
- `OZ3705D 3~5 LiIon Cells Digital Front End (DFE) with Embedde.pdf` - lokalni OZ3705D datasheet; potvrzuje rodinu AFE, ale register mapa se neshoduje s PBP00x firmware/sniffem.
- `OZ37205 Digital Front End (DFE) IC for 3 to 5 LiIon Cells.pdf` - dalsi lokalni O2Micro DFE reference.

Analyzovane firmware soubory:

- `firmware/pbp002_280109516-01_KC_20200925_lockout.hex`
- `firmware/pbp002_280109516-01_KC_20200925_fixed.hex`
- `firmware/pbp004_280109354-01_O_20200925_lockout.hex`
- `firmware/pbp004_280109354-01_O_20200925_fixed.hex`
- `firmware/pbp005_280109559-02_O_20221209_lockout.hex`
- `firmware/pbp005_280109559-02_O_20221209_fixed.hex`

Poznamka: nektere nazvy funkci jsou pracovni a vychazi ze staticke analyzy, ne z originalnich symbolu.
