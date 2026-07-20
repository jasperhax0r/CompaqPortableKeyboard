# Build Guide

Assembly and bring-up for the Compaq Portable replacement keyboard PCB. The
board is designed to be hand-solderable; the only fine-pitch parts are the two
level-shifter buffers, and there's a through-hole alternative for those below.

Read the [safety note](#a-word-on-first-power-up) before you apply power the
first time — it's the difference between finding a fault on a meter and finding
it with your fingertip.

\---

## Tools \& consumables

* Soldering iron with a **chisel/C tip** (not a fine conical — you'll fight the
ground planes), \~370 °C / 700 °F for the plane-connected pins
* Solder, **flux** (liquid or gel — essential for the TSSOP buffers), solder wick
* Solder sucker for through-hole rework
* Multimeter with continuity/diode mode
* **A current-limited bench supply** for first power-up (strongly recommended —
see below)
* Kapton tape (holds the fine-pitch chips while you tack them)

\---

## Bill of materials notes

Most of the board is through-hole on purpose. Key parts:

* **Regulator:** 7805 (TO-220). At the board's \~60 mA load it dissipates well
under half a watt and needs no heatsink. (A TO-220 was chosen for parts
availability; rev2 moves to a copper-pour thermal pad — see `rev2-notes.md`.)
* **Level shifters:** `SN74LVC07A` (open-drain, outbound clock/data) and
`SN74LVC14A` (Schmitt, inbound reset/clock-sense). TSSOP-14.
* **Rail protection:** reverse-polarity Schottky (SS14), polyfuse, TVS clamps at
the connector, and a 5 V crowbar (SMAJ5.0A) across the rail.
* **Matrix:** 83 switches, one diode each (1N4148-class), COL2ROW with cathodes
on the rows.
* **MCU:** Raspberry Pi Pico, on headers so it can be programmed before install.

### All-through-hole option

If you want to avoid SMD entirely, the two LVC buffers can be replaced with DIP
parts:

* `SN74LVC07A` → **7407** (hex open-collector buffer, DIP-14)
* `SN74LVC14A` → **CD4050B** (hex buffer, DIP-16; its inputs tolerate 5 V when
run at 3.3 V, so it level-shifts inbound cleanly)

You lose the Schmitt hysteresis on the input side. On a short bench cable that's
fine; on the full coiled cable the hysteresis helps reject ringing (see
`protocol.md`), so socket them and keep the option open.

\---

## Soldering the fine-pitch buffers

The two `SN74LVC0x` are the only hard parts. Technique:

1. **Flux the pads heavily**, then tack one corner pin to hold the chip square.
Kapton tape across the body helps hold it while you tack.
2. Tack the diagonal corner. Now it's located; both hands are free.
3. **Drag-solder** each row, or deliberately blob all pins and **wick the excess
off** — the flux pulls solder to the pins and releases the bridge to the wick.
Either works; the blob-and-wick is very forgiving.
4. Inspect under magnification and buzz **every adjacent pin pair** for bridges,
and each pin to its destination for opens — *before* powering anything.

The ground planes suck heat. Use a big tip, run hot, and touch briefly — a quick
hot touch dumps less total heat into the board than a cool tip lingering.

\---

## Assembly order

Populate in this order so each stage sits on a verified foundation:

### 1\. Regulator section first

Populate the 7805, its input/output caps, and the rail protection. **Do not
populate anything else yet.**

Apply 12 V and confirm:

* **5.0 V at the rail**, and
* **5.0 V at the buffer VCC pins** (where the LVC chips will sit).

If the rail is wrong, you find out with nothing downstream to damage.

> \*\*Watch diode orientation on the 5 V rail.\*\* A reversed rail diode is a dead
> short across the regulator. The etched cathode marks on small SMD diodes are
> genuinely hard to read (dark on black); verify orientation with the meter's
> diode mode, not just by eye. (Rev2 uses an axial diode with a visible band
> here for exactly this reason.)

### 2\. Pico

Seat the Pico (on headers). Confirm 3.3 V on its 3V3 pin and that the 5 V→VSYS
Schottky is feeding it.

### 3\. Level shifters

Populate the LVC buffers (or DIP alternatives). Re-verify VCC and grounds.

### 4\. Matrix

Switches and diodes. All the same orientation — COL2ROW, diode cathodes toward
the rows.

\---

## A word on first power-up

**Use a current-limited bench supply set to \~150 mA for the first power-on of
each stage.** A wiring fault then shows up as the supply pegging to its current
limit at near-zero volts — instantly, silently, with no heat. Without a limit,
the same fault dumps its power into the regulator as heat; a shorted 7805 gets
hot enough to burn you before you've found the problem.

If you only have a fixed 12 V supply, at least watch the current draw: 12 V in
pulling 400 mA at idle means something is wrong — kill it and investigate before
touching the board.

\---

## Firmware bring-up

The firmware is CircuitPython — flash CircuitPython, then copy `firmware/code.py`
to the `CIRCUITPY` drive.

`code.py` has three switches at the top for bring-up:

```python
DEBUG        = True    # print key make/break to USB serial
RAW\_SCAN     = True    # print raw (row,col) of every press, ignore keymap
XT\_CONNECTED = False   # scan/print only; do NOT drive the XT lines
```

### Step 1 — verify the matrix over USB (nothing connected to the Compaq)

Set `RAW\_SCAN = True`, `XT\_CONNECTED = False`. Open the serial console and press
**every key**, in physical order. Each press prints `row N col M`. Confirm the
map matches your board — this catches a swapped row/column or a solder-bridged
diode while it's trivial to fix, with zero risk to anything.

If **every** key reads pressed (or none do), the scan polarity is inverted — flip
the drive/sense sense noted in `scan\_and\_emit()`.

### Step 2 — connect and go live

When the matrix checks out, set:

```python
DEBUG        = False
RAW\_SCAN     = False
XT\_CONNECTED = True
```

Plug into the Compaq. It should POST off the BAT handshake and type. For one
final confidence pass you can leave `DEBUG = True` with `XT\_CONNECTED = True` —
you'll see makes/breaks *and* the machine will type — then set `DEBUG = False`
for the quiet production build (the prints cost scan time).

\---

## Troubleshooting

**Keys scan correctly (RAW\_SCAN looks right) but the Compaq gets garbage, and
the same key gives *different* random results each time.**
The XT output isn't being driven to a solid high — the clock/data pins must be
**push-pull** (they drive the LVC07A's CMOS input; there's no pull-up on that
net). If you ported config from the BSS138 test rig, it was set to open-drain
there — change it. Confirm with a scope on the Pico's clock pin: you want a clean
0↔3.3 V square wave, not a ragged/floating high.

**Nothing registers; serial shows `queue full, dropping`.**
`xt\_ready()` is returning false, so nothing transmits and the send queue backs
up. Check that **RESET (input) reads high** with the machine running, and that
**CLOCK\_SENSE idles high**. A stuck-low reset or clock-sense blocks all sends.

**Keys lag / need to be held.**
Debounce too aggressive for your switches, or the scan interval is stretched.
Lower `DEBOUNCE\_SCANS` (try 1) and reduce the per-row settle. If it still drags
under fast typing, the XT transmit time between scans is the cost — scan more
often than you send.

**Machine throws a keyboard POST error (e.g. 301).**
The BAT handshake isn't landing. Confirm the \~172 ms delay after reset release
before `0xAA` (some BIOSes miss it if it's too early), and that reset polarity is
read correctly (active low).

**Occasional wrong/garbled keys under a marginal cable.**
Ringing on the clock line. Make sure the input path uses the Schmitt buffer
(`SN74LVC14A`), keep the connector-side TVS/cap, and don't speed up the edges.
See the signal-integrity section of `protocol.md`.

