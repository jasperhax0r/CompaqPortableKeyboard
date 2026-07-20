# code.py  --  Compaq Portable / IBM XT keyboard emulator (TEST RIG)
#              with PS/2 keyboard input  +  serial console fallback
#
# CircuitPython on a Raspberry Pi Pico.
#
# XT side (output to the Compaq) -- unchanged, validated on real hardware:
#     GP21  XT_CLOCK        open-drain out
#     GP22  XT_DATA         open-drain out
#     GP20  XT_RESET        input   (host drives; active LOW)
#     GP26  XT_CLOCK_SENSE  input   (reads XT clock, detects host inhibit)
#
# PS/2 side (input from a PS/2 keyboard):
#     GP18  PS2_CLOCK       in / open-drain out (in to read, low to inhibit)
#     GP19  PS2_DATA        input   (read-only; we never send commands)
#
# Level-shift the PS/2 5V clock/data through the same BSS138 approach.
# We act as a READ-ONLY PS/2 host: the keyboard powers up in Set 2 and sends
# scancodes on its own; we never send it commands, so there's no bidirectional
# handshake and no ACKs to service. We translate Set 2 -> XT Set 1 and clock
# the result out the XT side with the existing, proven send_byte().
#
# Serial console still works for injecting raw bytes / named keys. Type `help`.

import board
import digitalio
import supervisor
import sys
import time

# ----------------------------------------------------------------- timing
HALF_US = 20        # XT half clock period target
SETUP_US = 5        # XT data setup before falling edge
BAT_DELAY_MS = 170  # keyboard's wait after reset release
TAP_GAP_MS = 15     # gap between make and break for console `tap`
INHIBIT_TIMEOUT_MS = 1000
PS2_FRAME_TIMEOUT_MS = 6   # how long to wait for PS/2 activity per poll

# ----------------------------------------------------------------- XT pins
xt_clock = digitalio.DigitalInOut(board.GP21)
xt_clock.switch_to_output(value=True, drive_mode=digitalio.DriveMode.OPEN_DRAIN)

xt_data = digitalio.DigitalInOut(board.GP22)
xt_data.switch_to_output(value=True, drive_mode=digitalio.DriveMode.OPEN_DRAIN)

xt_reset = digitalio.DigitalInOut(board.GP20)
xt_reset.switch_to_input(pull=digitalio.Pull.UP)

xt_clock_sense = digitalio.DigitalInOut(board.GP26)
xt_clock_sense.switch_to_input(pull=digitalio.Pull.UP)

# ----------------------------------------------------------------- PS/2 pins
ps2_clock = digitalio.DigitalInOut(board.GP18)
ps2_clock.switch_to_input(pull=digitalio.Pull.UP)

ps2_data = digitalio.DigitalInOut(board.GP19)
ps2_data.switch_to_input(pull=digitalio.Pull.UP)


# ----------------------------------------------------------------- delays
def sleep_us(us):
    end = time.monotonic_ns() + int(us * 1000)
    while time.monotonic_ns() < end:
        pass


def sleep_ms(ms):
    end = time.monotonic_ns() + int(ms * 1_000_000)
    while time.monotonic_ns() < end:
        pass


# ----------------------------------------------------------------- PS/2 inhibit
def ps2_inhibit(on):
    """Hold PS/2 clock low so the keyboard buffers, or release it to receive."""
    if on:
        ps2_clock.switch_to_output(
            value=False, drive_mode=digitalio.DriveMode.OPEN_DRAIN)
    else:
        ps2_clock.switch_to_input(pull=digitalio.Pull.UP)


# ----------------------------------------------------------------- XT tx
def wait_clock_free(timeout_ms=INHIBIT_TIMEOUT_MS):
    end = time.monotonic_ns() + timeout_ms * 1_000_000
    while not xt_clock_sense.value:       # low == host inhibiting
        if time.monotonic_ns() > end:
            return False
    return True


def send_bit(bit):
    xt_data.value = bool(bit)
    sleep_us(SETUP_US)
    xt_clock.value = False                # FALLING EDGE -> host samples
    sleep_us(HALF_US)
    xt_clock.value = True
    sleep_us(HALF_US)


def send_byte(b):
    """One XT frame: start bit + 8 data bits LSB first. PS/2 is inhibited for
    the duration so an incoming keystroke can't collide with transmission."""
    if not xt_reset.value:
        print("  [xt reset asserted - dropping 0x%02X]" % b)
        return False
    ps2_inhibit(True)
    try:
        if not wait_clock_free():
            print("  [xt clock inhibited - dropping 0x%02X]" % b)
            return False
        send_bit(1)
        for i in range(8):
            send_bit((b >> i) & 1)
        xt_data.value = True
        xt_clock.value = True
        return True
    finally:
        ps2_inhibit(False)


def tap(scancode, gap_ms=TAP_GAP_MS):
    if send_byte(scancode):
        sleep_ms(gap_ms)
        send_byte(scancode | 0x80)


def do_bat():
    print("  BAT: waiting %d ms then sending 0xAA..." % BAT_DELAY_MS)
    sleep_ms(BAT_DELAY_MS)
    if send_byte(0xAA):
        print("  BAT: 0xAA sent")


# ----------------------------------------------------------------- PS/2 rx
def ps2_read_byte(timeout_ms=PS2_FRAME_TIMEOUT_MS):
    """Read one PS/2 frame (device->host). Returns the data byte, or None on
    timeout / framing error / parity error.

    Frame = start(0) + 8 data LSB-first + odd parity + stop(1), keyboard-
    clocked. Data is valid while clock is low, so we sample after each
    falling edge."""
    end = time.monotonic_ns() + timeout_ms * 1_000_000

    while not ps2_clock.value:             # ensure idle-high first
        if time.monotonic_ns() > end:
            return None
    while ps2_clock.value:                 # wait for start-bit falling edge
        if time.monotonic_ns() > end:
            return None

    samples = [1 if ps2_data.value else 0]   # start bit
    for _ in range(10):                      # 8 data + parity + stop
        while not ps2_clock.value:
            if time.monotonic_ns() > end:
                return None
        while ps2_clock.value:
            if time.monotonic_ns() > end:
                return None
        samples.append(1 if ps2_data.value else 0)

    if samples[0] != 0 or samples[10] != 1:  # start/stop framing
        return None
    data = 0
    for i in range(8):
        data |= samples[1 + i] << i
    ones = 0                                 # odd-parity check
    v = data
    while v:
        ones += v & 1
        v >>= 1
    if (ones + samples[9]) % 2 == 0:
        return None
    return data


# ----------------------- PS/2 Set 2 -> XT Set 1 make-code translation -------
# XT break = (make | 0x80). PS/2 signals break with a 0xF0 prefix; extended
# keys arrive with a 0xE0 prefix.
SET2_TO_SET1 = {
    0x1C: 0x1E, 0x32: 0x30, 0x21: 0x2E, 0x23: 0x20, 0x24: 0x12, 0x2B: 0x21,
    0x34: 0x22, 0x33: 0x23, 0x43: 0x17, 0x3B: 0x24, 0x42: 0x25, 0x4B: 0x26,
    0x3A: 0x32, 0x31: 0x31, 0x44: 0x18, 0x4D: 0x19, 0x15: 0x10, 0x2D: 0x13,
    0x1B: 0x1F, 0x2C: 0x14, 0x3C: 0x16, 0x2A: 0x2F, 0x1D: 0x11, 0x22: 0x2D,
    0x35: 0x15, 0x1A: 0x2C,
    0x16: 0x02, 0x1E: 0x03, 0x26: 0x04, 0x25: 0x05, 0x2E: 0x06, 0x36: 0x07,
    0x3D: 0x08, 0x3E: 0x09, 0x46: 0x0A, 0x45: 0x0B,
    0x0E: 0x29, 0x4E: 0x0C, 0x55: 0x0D, 0x5D: 0x2B, 0x54: 0x1A, 0x5B: 0x1B,
    0x4C: 0x27, 0x52: 0x28, 0x41: 0x33, 0x49: 0x34, 0x4A: 0x35,
    0x29: 0x39, 0x5A: 0x1C, 0x66: 0x0E, 0x0D: 0x0F, 0x76: 0x01, 0x58: 0x3A,
    0x12: 0x2A, 0x59: 0x36, 0x14: 0x1D, 0x11: 0x38,
    0x77: 0x45, 0x7E: 0x46,
    0x05: 0x3B, 0x06: 0x3C, 0x04: 0x3D, 0x0C: 0x3E, 0x03: 0x3F, 0x0B: 0x40,
    0x83: 0x41, 0x0A: 0x42, 0x01: 0x43, 0x09: 0x44, 0x78: 0x57, 0x07: 0x58,
    # numeric keypad
    0x70: 0x52, 0x69: 0x4F, 0x72: 0x50, 0x7A: 0x51, 0x6B: 0x4B, 0x73: 0x4C,
    0x74: 0x4D, 0x6C: 0x47, 0x75: 0x48, 0x7D: 0x49, 0x71: 0x53, 0x79: 0x4E,
    0x7B: 0x4A, 0x7C: 0x37,
}

# Extended (0xE0-prefixed) keys. The Compaq is XT-era and folds navigation
# into the numpad, so a modern nav cluster is remapped onto the Compaq's
# numpad Set 1 codes (NO 0xE0 prefix out). Adjust to taste.
SET2_EXT_TO_SET1 = {
    0x75: 0x48, 0x72: 0x50, 0x6B: 0x4B, 0x74: 0x4D,   # arrows -> numpad 8/2/4/6
    0x6C: 0x47, 0x69: 0x4F, 0x7D: 0x49, 0x7A: 0x51,   # home/end/pgup/pgdn
    0x70: 0x52, 0x71: 0x53,                           # insert/delete
    0x5A: 0x1C, 0x4A: 0x35,                           # KP-Enter, KP-/
    0x14: 0x1D, 0x11: 0x38,                           # R-Ctrl->Ctrl R-Alt->Alt
}

# bytes a read-only host should just swallow (BAT ok, echo, ack, errors)
PS2_IGNORE = (0xAA, 0x00, 0xFF, 0xFA, 0xEE, 0xFC, 0xFE)

_ps2_ext = False
_ps2_brk = False


def ps2_process(byte):
    global _ps2_ext, _ps2_brk
    if byte == 0xE0:
        _ps2_ext = True
        return
    if byte == 0xF0:
        _ps2_brk = True
        return
    if byte in PS2_IGNORE:
        _ps2_ext = False
        _ps2_brk = False
        return

    table = SET2_EXT_TO_SET1 if _ps2_ext else SET2_TO_SET1
    ext, brk = _ps2_ext, _ps2_brk
    _ps2_ext = False
    _ps2_brk = False

    sc = table.get(byte)
    if sc is None:
        print("  [unmapped PS/2 %s0x%02X]" % ("E0 " if ext else "", byte))
        return
    send_byte(sc | (0x80 if brk else 0x00))   # make, or break


# ----------------------------------------------------------------- scancodes
# (console convenience: type text/keys directly over serial)
BASE = {
    "a": 0x1E, "b": 0x30, "c": 0x2E, "d": 0x20, "e": 0x12, "f": 0x21,
    "g": 0x22, "h": 0x23, "i": 0x17, "j": 0x24, "k": 0x25, "l": 0x26,
    "m": 0x32, "n": 0x31, "o": 0x18, "p": 0x19, "q": 0x10, "r": 0x13,
    "s": 0x1F, "t": 0x14, "u": 0x16, "v": 0x2F, "w": 0x11, "x": 0x2D,
    "y": 0x15, "z": 0x2C,
    "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06, "6": 0x07,
    "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B,
    "-": 0x0C, "=": 0x0D, "[": 0x1A, "]": 0x1B, ";": 0x27, "'": 0x28,
    "`": 0x29, "\\": 0x2B, ",": 0x33, ".": 0x34, "/": 0x35, " ": 0x39,
}
SHIFTED = {
    "!": 0x02, "@": 0x03, "#": 0x04, "$": 0x05, "%": 0x06, "^": 0x07,
    "&": 0x08, "*": 0x09, "(": 0x0A, ")": 0x0B, "_": 0x0C, "+": 0x0D,
    "{": 0x1A, "}": 0x1B, ":": 0x27, '"': 0x28, "~": 0x29, "|": 0x2B,
    "<": 0x33, ">": 0x34, "?": 0x35,
}
NAMED = {
    "esc": 0x01, "bksp": 0x0E, "tab": 0x0F, "enter": 0x1C, "space": 0x39,
    "lctrl": 0x1D, "lshift": 0x2A, "rshift": 0x36, "lalt": 0x38, "caps": 0x3A,
    "f1": 0x3B, "f2": 0x3C, "f3": 0x3D, "f4": 0x3E, "f5": 0x3F,
    "f6": 0x40, "f7": 0x41, "f8": 0x42, "f9": 0x43, "f10": 0x44,
}
LSHIFT = 0x2A


def char_to_key(ch):
    if ch in BASE:
        return (BASE[ch], False)
    low = ch.lower()
    if ch.isalpha() and low in BASE:
        return (BASE[low], True)
    if ch in SHIFTED:
        return (SHIFTED[ch], True)
    return None


def type_char(ch):
    k = char_to_key(ch)
    if k is None:
        print("  [no mapping for %r]" % ch)
        return
    sc, shift = k
    if shift:
        send_byte(LSHIFT)
        tap(sc)
        send_byte(LSHIFT | 0x80)
    else:
        tap(sc)


# ----------------------------------------------------------------- console
HELP = """
PS/2 keyboard input is live -- just type on it.
Serial console commands (type + Enter):
  <char>        one key make+break, e.g.  a  |  A  |  $
  type <text>   send a whole string
  <name>        esc bksp tab enter space caps lctrl lshift rshift lalt f1..f10
  hex XX        raw make byte 0xXX          mk XX = same
  brk XX        break code (0xXX | 0x80)
  bat           send 0xAA self-test byte
  status        reset / xt-clock / ps2 line states
  help / ?      this text
"""


def parse_byte(tok):
    try:
        return int(tok, 16) & 0xFF
    except ValueError:
        return None


def handle(line):
    parts = line.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("help", "?"):
        print(HELP)
    elif cmd == "status":
        print("  xt_reset=%s  xt_clk_sense=%s  ps2_clk=%s  ps2_dat=%s" % (
            "HIGH" if xt_reset.value else "LOW(reset)",
            "HIGH" if xt_clock_sense.value else "LOW(inhibit)",
            "HIGH" if ps2_clock.value else "LOW",
            "HIGH" if ps2_data.value else "LOW"))
    elif cmd == "bat":
        do_bat()
    elif cmd == "type":
        for ch in arg:
            type_char(ch)
    elif cmd in ("hex", "mk"):
        b = parse_byte(arg)
        if b is None:
            print("  [bad hex: %r]" % arg)
        elif send_byte(b):
            print("  sent make 0x%02X" % b)
    elif cmd == "brk":
        b = parse_byte(arg)
        if b is None:
            print("  [bad hex: %r]" % arg)
        elif send_byte(b | 0x80):
            print("  sent break 0x%02X" % (b | 0x80))
    elif cmd in NAMED:
        tap(NAMED[cmd])
    elif len(line) == 1:
        type_char(line)
    else:
        for ch in line:
            type_char(ch)


_buf = ""


def poll_line():
    global _buf
    n = supervisor.runtime.serial_bytes_available
    if not n:
        return None
    s = sys.stdin.read(n)
    for ch in s:
        if ch in ("\n", "\r"):
            line = _buf.strip()
            _buf = ""
            if line:
                return line
        else:
            _buf += ch
    return None


# ----------------------------------------------------------------- main
print("=" * 56)
print(" Compaq XT keyboard emulator - TEST RIG  (PS/2 + serial)")
print(" XT:  CLK=GP21 DATA=GP22 RST=GP20 SENSE=GP26")
print(" PS2: CLK=GP18 DATA=GP19")
print("=" * 56)
print(HELP)

prev_reset = xt_reset.value
if prev_reset:
    print("[xt reset already high - sending BAT]")
    do_bat()
else:
    print("[xt reset asserted - waiting for host]")

while True:
    now_reset = xt_reset.value
    if now_reset and not prev_reset:
        print("[xt reset released - host ready]")
        do_bat()
    elif not now_reset and prev_reset:
        print("[xt reset asserted]")
    prev_reset = now_reset

    b = ps2_read_byte()
    if b is not None:
        ps2_process(b)

    line = poll_line()
    if line is not None:
        handle(line)
