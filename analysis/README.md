# Ryobi battery firmware analysis

Tato slozka obsahuje prubezny stav staticke analyzy firmwaru Ryobi baterii.

Soubory:

- `firmware_static_analysis.md` - hlavni souhrn funkci, datovych struktur, NVM/RAM oblasti, fault bitu a otevrenych bodu.
- `dtech_uart_protocol.md` - zatim zjisteny D-tech/fixture UART protokol, delky ramcu, CRC, auth sekvence a klice.
- `pbp002_state_machine.md` - samostatny rozbor PBP002 lockout wordu, fault bitu a service/fixture state machine.
- `afe_3705t_smbus.md` - rozbor SMBus/I2C zarizeni `0x29`, realneho trace a pracovni register mapy AFE `3705T`.
- `../tools/decode_afe_i2c_trace.py` - offline dekoder Digilent WaveForms I2C CSV trace pro AFE `0x29`.
- `pbp004_dtech_analysis.md` - samostatny rozbor PBP004 D-tech parseru, auth sekvence, fixture key a requestu.
- `pbp005_state_machine.md` - samostatny rozbor PBP005 BMS a service/fixture state machine.
- `../tools/dtech_uart.py` - Python klient pro PBP004 D-tech auth/raw komunikaci a PBP002/PBP005 pasivni UART log/mapovani stavu.

Analyzovane firmware soubory:

- `firmware/pbp002_280109516-01_KC_20200925_lockout.hex`
- `firmware/pbp002_280109516-01_KC_20200925_fixed.hex`
- `firmware/pbp004_280109354-01_O_20200925_lockout.hex`
- `firmware/pbp004_280109354-01_O_20200925_fixed.hex`
- `firmware/pbp005_280109559-02_O_20221209_lockout.hex`
- `firmware/pbp005_280109559-02_O_20221209_fixed.hex`

Poznamka: nektere nazvy funkci jsou pracovni a vychazi ze staticke analyzy, ne z originalnich symbolu.
