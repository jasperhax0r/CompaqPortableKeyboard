# code.py  --  Compaq Portable keyboard firmware (FINAL BOARD)
#
# CircuitPython on the onboard Raspberry Pi Pico. Scans the 10x10 diode
# matrix and emits IBM XT (Set 1) scancodes on the keyboard connector.
# Full NKRO (every key has its own diode). No PS/2, no translation --
# this reads physical keys and speaks XT directly.
#
# --- pin map (from the board schematic) ---
# Rows  ROW0..9  = GP0..GP9      (driven low one at a time)
# Cols  COL0..9  = GP10..GP19    (inputs, pull-up; pressed reads LOW)
# XT    CLOCK        = GP21  open-drain out
#       DATA         = GP22  open-drain out
#       RESET        = GP20  input  (host drives; active LOW)
#       CLOCK_SENSE  = GP26  input  (reads XT clock; host inhibit = low)
# LED   onboard      = board.LED   (stands in for a Caps Lock lamp)
#
# Matrix is COL2ROW (diode cathodes on the rows): drive a row LOW, the
# pressed key pulls its column LOW through the forward diode. If EVERY key
# reads pressed or none do, the drive/sense polarity is inverted -- flip
# ROW_ACTIVE / the pressed test below.

import board
import digitalio
import supervisor
import sys
import time

# ----------------------------------------------------------------- timing
HALF_US = 20            # XT half clock period (~24 kHz nominal)
SETUP_US = 5            # XT data setup before falling edge
BAT_DELAY_MS = 170      # keyboard's wait after reset release, then 0xAA
DEBOUNCE_SCANS = 2      # scans a change must persist before it commits
TYPEMATIC_DELAY_MS = 500
TYPEMATIC_PERIOD_MS = 100   # ~10 chars/sec
SEND_QUEUE_MAX = 64
DEBUG = False           # print key events to the USB serial console
XT_CONNECTED = True     # driving the real Compaq now

# ----------------------------------------------------------------- XT pins
xt_clock = digitalio.DigitalInOut(board.GP21)
xt_clock.switch_to_output(value=True)   # PUSH-PULL: drives the SN74LVC07A input
xt_data = digitalio.DigitalInOut(board.GP22)
xt_data.switch_to_output(value=True)    # (open-drain behavior lives in the buffer,
                                        #  not the Pico -- the LVC07A input needs a
                                        #  clean CMOS drive, there is no pull-up here)
xt_reset = digitalio.DigitalInOut(board.GP20)
xt_reset.switch_to_input(pull=digitalio.Pull.UP)
xt_clock_sense = digitalio.DigitalInOut(board.GP26)
xt_clock_sense.switch_to_input(pull=digitalio.Pull.UP)

led = digitalio.DigitalInOut(board.LED)
led.switch_to_output(value=False)

# ----------------------------------------------------------------- matrix pins
ROW_PINS = (board.GP0, board.GP1, board.GP2, board.GP3, board.GP4,
            board.GP5, board.GP6, board.GP7, board.GP8, board.GP9)
COL_PINS = (board.GP10, board.GP11, board.GP12, board.GP13, board.GP14,
            board.GP15, board.GP16, board.GP17, board.GP18, board.GP19)

rows = []
for p in ROW_PINS:
    d = digitalio.DigitalInOut(p)
    d.switch_to_output(value=True)      # idle HIGH; active row driven LOW
    rows.append(d)
cols = []
for p in COL_PINS:
    d = digitalio.DigitalInOut(p)
    d.switch_to_input(pull=digitalio.Pull.UP)   # pressed reads LOW
    cols.append(d)

# ----------------------------------------------------------------- keymap
# (row, col) -> XT Set 1 make code. Break code = make | 0x80.
KEYMAP = {
    (0, 0): 0x3B,  # F1
    (0, 1): 0x3C,  # F2
    (0, 2): 0x01,  # Esc
    (0, 3): 0x02,  # 1
    (0, 4): 0x03,  # 2
    (0, 5): 0x04,  # 3
    (0, 6): 0x05,  # 4
    (0, 7): 0x06,  # 5
    (0, 8): 0x07,  # 6
    (1, 0): 0x08,  # 7
    (1, 1): 0x09,  # 8
    (1, 2): 0x0A,  # 9
    (1, 3): 0x0B,  # 0
    (1, 4): 0x0C,  # -
    (1, 5): 0x0D,  # =
    (1, 6): 0x0E,  # Backspace
    (1, 7): 0x45,  # numlock
    (1, 8): 0x46,  # scrollock
    (2, 0): 0x3D,  # F3
    (2, 1): 0x3E,  # F4
    (2, 2): 0x0F,  # Tab
    (2, 3): 0x10,  # Q
    (2, 4): 0x11,  # W
    (2, 5): 0x12,  # E
    (2, 6): 0x13,  # R
    (2, 7): 0x14,  # T
    (2, 8): 0x15,  # Y
    (2, 9): 0x16,  # U
    (3, 0): 0x17,  # I
    (3, 1): 0x18,  # O
    (3, 2): 0x19,  # P
    (3, 3): 0x1A,  # [
    (3, 4): 0x1B,  # ]
    (3, 5): 0x1C,  # ENTER
    (3, 6): 0x47,  # NP7
    (3, 7): 0x48,  # NP8
    (3, 8): 0x49,  # NP9
    (3, 9): 0x4A,  # NP-
    (4, 0): 0x3F,  # F5
    (4, 1): 0x40,  # F6
    (4, 2): 0x1D,  # CTRL
    (4, 3): 0x1E,  # A
    (4, 4): 0x1F,  # S
    (4, 5): 0x20,  # D
    (4, 6): 0x21,  # F
    (4, 7): 0x22,  # G
    (4, 8): 0x23,  # H
    (4, 9): 0x24,  # J
    (5, 0): 0x25,  # K
    (5, 1): 0x26,  # L
    (5, 2): 0x27,  # ;
    (5, 3): 0x28,  # '
    (5, 4): 0x29,  # `
    (5, 5): 0x4B,  # KP4
    (5, 6): 0x4C,  # KP5
    (5, 7): 0x4D,  # KP6
    (5, 8): 0x4E,  # KP+
    (6, 0): 0x41,  # F7
    (6, 1): 0x42,  # F8
    (6, 2): 0x2A,  # LSHIFT
    (6, 3): 0x2B,  # \
    (6, 4): 0x2C,  # Z
    (6, 5): 0x2D,  # X
    (6, 6): 0x2E,  # C
    (6, 7): 0x2F,  # V
    (6, 8): 0x30,  # B
    (6, 9): 0x31,  # N
    (7, 0): 0x32,  # M
    (7, 1): 0x33,  # ,
    (7, 2): 0x34,  # .
    (7, 3): 0x35,  # /
    (7, 4): 0x36,  # RSHIFT
    (7, 5): 0x37,  # */PRNTSCREEN
    (7, 6): 0x4F,  # NP1
    (7, 7): 0x50,  # NP2
    (7, 8): 0x51,  # NP3
    (8, 0): 0x43,  # F9
    (8, 1): 0x44,  # F10
    (8, 2): 0x38,  # ALT
    (8, 3): 0x39,  # SPACE
    (9, 0): 0x3A,  # CAPS
    (9, 1): 0x52,  # NP0
    (9, 2): 0x53,  # NP.
}

# modifiers / toggles that must NOT auto-repeat
NO_REPEAT = (0x2A, 0x36, 0x1D, 0x38, 0x3A, 0x45, 0x46)
CAPS = 0x3A

# ----------------------------------------------------------------- delays
def sleep_us(us):
    end = time.monotonic_ns() + int(us * 1000)
    while time.monotonic_ns() < end:
        pass

def sleep_ms(ms):
    end = time.monotonic_ns() + int(ms * 1_000_000)
    while time.monotonic_ns() < end:
        pass

# ----------------------------------------------------------------- XT tx
def xt_ready():
    return xt_reset.value and xt_clock_sense.value

def send_bit(bit):
    xt_data.value = bool(bit)
    sleep_us(SETUP_US)
    xt_clock.value = False               # FALLING EDGE -> host samples
    sleep_us(HALF_US)
    xt_clock.value = True
    sleep_us(HALF_US)

def send_byte(b):
    send_bit(1)
    for i in range(8):
        send_bit((b >> i) & 1)
    xt_data.value = True
    xt_clock.value = True

def do_bat():
    sleep_ms(BAT_DELAY_MS)
    if xt_ready():
        send_byte(0xAA)
        if DEBUG:
            print("BAT 0xAA")

# ----------------------------------------------------------------- send queue
_queue = []

def enqueue(b):
    if len(_queue) < SEND_QUEUE_MAX:
        _queue.append(b)
    elif DEBUG:
        print("queue full, dropping 0x%02X" % b)

def drain_queue():
    if not XT_CONNECTED:
        _queue.clear()          # bench mode: serial events only, never back up
        return
    # send as long as the host isn't holding us off
    while _queue and xt_ready():
        send_byte(_queue.pop(0))

# ----------------------------------------------------------------- scan/debounce
_raw_state = {}
_raw_pending = {}
state = {}       # (r,c) -> committed pressed bool
pending = {}     # (r,c) -> consecutive-scans-differing counter
for key in KEYMAP:
    state[key] = False
    pending[key] = 0

RAW_SCAN = False  # True: print (row,col) for every press, ignore KEYMAP gaps

def scan_and_emit():
    for r in range(10):
        rows[r].value = False                 # drive this row LOW
        sleep_us(3)                           # settle
        for c in range(10):
            key = (r, c)
            if RAW_SCAN:
                pressed = not cols[c].value
                if pressed != _raw_state.get(key, False):
                    _raw_pending[key] = _raw_pending.get(key, 0) + 1
                    if _raw_pending[key] >= DEBOUNCE_SCANS:
                        _raw_state[key] = pressed
                        _raw_pending[key] = 0
                        if pressed:
                            sc = KEYMAP.get(key)
                            tag = ("-> 0x%02X" % sc) if sc is not None else "(unmapped)"
                            print("row %d col %d  %s" % (r, c, tag))
                elif _raw_pending.get(key, 0) > 0:
                    _raw_pending[key] -= 1
                continue
            if key not in KEYMAP:
                continue
            pressed = not cols[c].value        # LOW == pressed
            if pressed != state[key]:
                pending[key] += 1                  # integrate toward the change
                if pending[key] >= DEBOUNCE_SCANS:
                    state[key] = pressed
                    pending[key] = 0
                    on_key_event(KEYMAP[key], pressed)
            elif pending[key] > 0:
                pending[key] -= 1                  # decay, don't hard-reset
        rows[r].value = True                  # release row

# ----------------------------------------------------------------- events
_repeat_code = None
_next_repeat = 0
_caps_on = False

def on_key_event(scancode, pressed):
    global _repeat_code, _next_repeat, _caps_on
    if pressed:
        enqueue(scancode)
        if DEBUG:
            print("make  0x%02X" % scancode)
        if scancode == CAPS:
            _caps_on = not _caps_on
            led.value = _caps_on
        if scancode not in NO_REPEAT:
            _repeat_code = scancode
            _next_repeat = time.monotonic_ns() + TYPEMATIC_DELAY_MS * 1_000_000
    else:
        enqueue(scancode | 0x80)
        if DEBUG:
            print("break 0x%02X" % (scancode | 0x80))
        if scancode == _repeat_code:
            _repeat_code = None

def typematic():
    global _next_repeat
    if _repeat_code is None:
        return
    now = time.monotonic_ns()
    if now >= _next_repeat:
        enqueue(_repeat_code)
        _next_repeat = now + TYPEMATIC_PERIOD_MS * 1_000_000

# ----------------------------------------------------------------- main
print("Compaq Portable keyboard firmware - matrix scan, XT out")
print("%d keys mapped" % len(KEYMAP))

prev_reset = xt_reset.value
if XT_CONNECTED and prev_reset:
    do_bat()

while True:
    if XT_CONNECTED:
        now_reset = xt_reset.value
        if now_reset and not prev_reset:
            if DEBUG:
                print("reset released")
            do_bat()
        prev_reset = now_reset

    scan_and_emit()
    typematic()
    drain_queue()
