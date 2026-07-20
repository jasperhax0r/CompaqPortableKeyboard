# Compaq Portable Keyboard Protocol

A scope-verified description of the keyboard protocol used by the **Compaq
Portable** (1983). Everything here was measured on a working original keyboard
with a logic analyzer, not taken from a datasheet — where a number is quoted, it
came off the wire.

The short version: it's **IBM XT (scancode set 1)**, clocked by the keyboard,
with one detail that trips people up (see [Reset](#reset-active-low-host-driven)).

---

## Connector

The keyboard attaches with a **2×3 connector** (AMP), of which **5 of 6
positions are populated**. It is **mechanically keyed** — the housing physically
prevents the plug from seating rotated. This matters: the +12 V and signal pins
are adjacent, so a rotated plug would put 12 V onto a logic line.

| Pin | Function |
|-----|----------|
| 1 | CLOCK |
| 2 | DATA |
| 3 | RESET |
| 4 | *(unpopulated)* |
| 5 | +12 V |
| 6 | GND |

> Pin numbering follows the board footprint. The clock/data assignment
> (pin 1 = clock, pin 2 = data) was **confirmed on the scope** — it is the
> reverse of what you might guess from the schematic, so verify before trusting
> a pinout diagram.

The unpopulated position (pin 4) has no conductor; the single ground is on pin 6.
Because the connector is keyed, there is no polarity protection from the pinout
itself — the board provides reverse-polarity protection instead.

---

## Electrical

- **12 V** supply down the cable. Everything on the keyboard PCB runs on 5 V
  behind a 78M05 regulator; the logic is 5 V.
- CLOCK and DATA are **open-collector, idle high**, with pull-ups on the
  keyboard board (~3.3 kΩ) and Schmitt-trigger receivers (74LS132 on the
  original). The Schmitt hysteresis is not decorative — see
  [Signal integrity](#signal-integrity).
- The keyboard's controller is an **8048-family** microcontroller
  (mask part `22-908-03A`) on a 4.6 MHz crystal — the same family as the
  IBM XT keyboard. The `RESET`, `PSEN`, and `ALE` pin labels on the schematic
  match an 8048 exactly (Compaq renamed ALE to "KBALE").

---

## Frame format

The **keyboard generates the clock.** Each byte is sent as:

```
        start   d0   d1   d2   d3   d4   d5   d6   d7
DATA  ──┐ 1  ┌──┐    ┌────┐ ...                      idle high
        └────┘  └────┘    └──
CLOCK ─┐  ┌─┐  ┌─┐  ┌─┐  ┌─┐  ...   ┌─────────────── idle high
       └──┘ └──┘ └──┘ └──┘ └──┘     ┘
         ^    ^    ^    ^
       data sampled on each FALLING edge
```

- **1 start bit** (value `1`), then **8 data bits, LSB first**.
- **Data is valid on the falling edge of CLOCK** — the host samples there.
- Idle state is CLOCK high, DATA high.

Measured bit period during a real transmission: **~41 µs (≈24.3 kHz)**. The host
is edge-driven, not timed, so exact frequency is not critical — but reproduce
the frame *shape* and edge relationship.

### Scancodes

Standard **XT scancode set 1**. A key press sends its **make code**; a key
release sends the **break code**, which is `make | 0x80` (bit 7 set).

Example, verified on the wire (key `A`):

```
make  0x1E
break 0x9E     (0x1E | 0x80)
```

The full make-code table for this keyboard is in the firmware
(`firmware/code.py`, the `KEYMAP` dict). Note that the *matrix position* → key
mapping is specific to this board's wiring; the *scancodes* are stock XT set 1.

---

## Reset (active-low, host-driven)

This is the detail that isn't obvious and will cost you an afternoon if you get
it wrong.

**RESET is active low and driven by the host**, not by the keyboard. The host
holds the line **low** during power-up and **releases it high** when it is ready
for the keyboard. On the original board this same node also carries the
keyboard's own power-on reset RC (≈33 kΩ / 39 µF), so it serves double duty.

For an emulator you do not have an 8048 reset pin to hold — you only need to
**read** this line and react to the low→high transition.

| Line state | Meaning |
|------------|---------|
| LOW  | host asserting reset — do not transmit |
| HIGH | normal operation |

---

## Power-on handshake (BAT)

Captured cold-boot sequence, keyboard attached, from power-on:

1. Host holds **RESET low** from power-on.
2. The keyboard's 8048 boots and emits a brief burst of clock/data garbage
   **while reset is still low** (the host isn't listening yet — ignore it).
3. Host **releases RESET high**.
4. The keyboard waits **~172 ms**, then clocks out **`0xAA`** (BAT — basic
   assurance test passed): start bit + `0xAA` LSB-first, falling-edge data,
   at 24.3 kHz.
5. The host accepts it and POST continues.

The ~172 ms delay is real and matters — some XT-family BIOSes miss the BAT byte
if it arrives too soon after reset release. Reproduce the delay.

```
reset released:  t = 2.5198 s
BAT frame (0xAA): t = 2.6916 s
delay:           172 ms
```

Raw capture: [`captures/keyboardboot.csv`](captures/keyboardboot.csv).

---

## Host inhibit

XT clock is bidirectional in the sense that the **host can pull CLOCK low to
inhibit** the keyboard (tell it to hold transmission). The 8048 reads the clock
line back on its `T1` test input to see this. An emulator should likewise sense
the clock line and buffer keystrokes while the host is holding it low, sending
them once the line is released.

---

## Signal integrity

During reverse engineering, some captured frames showed **extra clock edges** —
10 or 11 edges where a clean frame has 9 — decoding to garbage. These were not
keyboard faults. The keyboard's clock line **rings**, and a logic analyzer
sampling against a single threshold catches the sub-threshold crossings that the
original's **74LS132 Schmitt-trigger receivers reject**.

Two lessons:

1. When decoding captures from a passive tap, deglitch the clock edges (reject
   any falling edge closer than ~20 µs to the previous one — a real bit is
   ~41 µs). `tools/xtframe.py --deglitch-us 20` does this.
2. **Any receiver for this signal needs Schmitt hysteresis.** The replacement
   board uses an SN74LVC14A on the clock-sense and reset inputs for exactly this
   reason. A plain buffer would pass the ringing straight through.

This also explains the original's design: the series diodes and the 33 pF cap on
the reset line, and the 3.3 kΩ pull-ups, are all there to damp a two-foot coiled
cable running next to a CRT. Keep that philosophy — slow, weak, and damped is
what makes this link reliable. Don't speed up the edges.

---

## Decoding your own captures

`tools/xtframe.py` takes a logic-analyzer CSV with columns
`Time[s], CLOCK, DATA, RESET` and prints decoded scancodes:

```
python3 xtframe.py capture.csv --deglitch-us 20
```

It reports reset edges, clusters frames on inter-frame gaps, samples DATA on each
CLOCK falling edge, checks the start bit, and decodes LSB-first to a byte. A
frame with more than 9 edges or non-uniform bit periods is a glitch artifact, not
a scancode.

---

## Summary table

| Property | Value |
|----------|-------|
| Encoding | XT scancode set 1 |
| Clock source | keyboard |
| Bit order | LSB first |
| Frame | 1 start bit + 8 data bits |
| Data valid | falling edge of CLOCK |
| Bit rate | ~24.3 kHz (~41 µs/bit) |
| Idle | CLOCK high, DATA high |
| Break code | make \| 0x80 |
| Reset | active low, host-driven |
| Power-on | ~172 ms after reset release, send 0xAA |
| Host inhibit | host pulls CLOCK low; keyboard senses and buffers |
