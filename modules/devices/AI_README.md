# Devices: pin table import format (for AI assistants)

Use this when someone asks you to turn a pinout (from a datasheet, drawing, spreadsheet or
photo) into a file they can import into Osmia. In Osmia they open a device, pick a connector,
click **Edit pins**, and use **Import a pin table** at the bottom of the page.

Produce **one CSV file per connector**. Give the user the file content in a code block, and
tell them which connector it's for.

## The file

- Plain text CSV, UTF-8. Comma, semicolon or tab separators all work; use commas.
- The **first row is the header**: the column names. Each following row is **one pin**.
- Columns are matched by name. Case, spaces, `_`, `-` and `.` don't matter (`Set type`,
  `set_type` and `SET-TYPE` are the same). Order doesn't matter.
- Leave a cell empty when the value is unknown. Never invent values.
- Quote a cell with double quotes if it contains a comma, e.g. `"V+, sense"`.
- At most 1000 pins and 1 MB per file.

### Columns

| Column | Required | What goes in it |
|---|---|---|
| `Pin` | yes | The pin's label exactly as printed on the connector or datasheet: `1`, `2`, `A1`, `B12`, `S` (shield), `+`, `-`. Labels must be unique within the file. Also accepted as the header name: `Pin label`, `Pin number`, `Label`, `Contact`, `Position`. |
| `Tag 1` | no | This connector's own name for what the pin carries, e.g. `MOTOR_A+`, `ETH1_TX+`, `CAN1_H`. `Tag` alone means Tag 1. |
| `Tag 2`, `Tag 3`, `Tag 4` | no | Further tags, e.g. a pair name (`Pair 2`), a group or a function. Only add these columns when they hold something. |
| `Signal` | no | The signal type, from the shared list in Osmia (Devices › Signals). Use the standard names below. A name Osmia doesn't know yet is added to that list, so keep names consistent and short. |
| `Set` | no | A whole number (1, 2, 3, …). Pins with the same number are one cable set, e.g. the two wires of a twisted pair. Leave empty for single wires. |
| `Set type` | no | How the set is run: `straight`, `twisted`, `shielded` or `twisted shielded`. Give every pin of a set the same type. Leave empty when there's no set. |

Tags are local to the connector: they don't need to match other connectors. Signals are
global: use the same name for the same kind of signal everywhere.

### Signal names to use

Prefer these (they are already in Osmia's list):

`GND`, `PWR`, `0V`, `+5V`, `+12V`, `+24V`, `VBAT`, `PE` (protective earth), `SHIELD`,
`TxP`, `TxN`, `RxP`, `RxN` (differential pairs), `TX`, `RX` (single-ended, e.g. UART),
`CAN_H`, `CAN_L`, `RS485_A`, `RS485_B`, `SDA`, `SCL`, `USB_D+`, `USB_D-`,
`AIN`, `AOUT`, `DIN`, `DOUT`, `NC` (not connected).

- A supply rail with a voltage gets the voltage name (`+24V`); an unspecified supply is `PWR`.
- Returns and grounds are `GND`, or `0V` when the datasheet calls it that.
- A pin the datasheet marks "NC", "n.c." or "reserved" is `NC`.
- If nothing fits, use a short, upper-case name the datasheet uses (e.g. `ENC_A`) and tell
  the user it will be added as a new signal.

## Example: an RJ45 Ethernet port (10/100)

```csv
Pin,Tag 1,Tag 2,Signal,Set,Set type
1,ETH_TX+,Pair 2,TxP,1,twisted
2,ETH_TX-,Pair 2,TxN,1,twisted
3,ETH_RX+,Pair 3,RxP,2,twisted
6,ETH_RX-,Pair 3,RxN,2,twisted
4,,Pair 1,NC,,
5,,Pair 1,NC,,
7,,Pair 4,NC,,
8,,Pair 4,NC,,
S,ETH_SHIELD,,SHIELD,,
```

## Example: a 4-pin M12 sensor connector

```csv
Pin,Tag 1,Signal
1,SENSOR_V+,+24V
2,SENSOR_OUT2,DIN
3,SENSOR_0V,0V
4,SENSOR_OUT1,DIN
```

## What happens on import

The user picks one of two modes:

- **Update** (default): pins whose label is in the file are updated, but only the columns the
  file has. A file without a `Set` column leaves the sets alone. New labels are added at the
  end. Pins not in the file are kept.
- **Replace**: the file is the whole pin table. Pins not in it are removed, and the pins are put
  in the file's order. Harness wires to this connector may then point at other pins, so suggest
  **Update** unless the user wants to start over.

Osmia reports what it did, including new signals it added and any rows it skipped (no pin label,
a `Set` that isn't a whole number, an unknown set type). The same pin label twice in one file
stops the import with nothing changed.

## Checklist before you hand over a file

1. Header row present, with a `Pin` column.
2. One row per physical pin, labels exactly as printed, no duplicates.
3. Signals from the list above where possible; `NC` for unused pins.
4. Pairs share a `Set` number and the same `Set type`.
5. Nothing guessed: unknown values are left empty, and you tell the user what was unclear.
