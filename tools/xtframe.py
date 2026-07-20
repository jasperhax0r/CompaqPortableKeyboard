#!/usr/bin/env python3
"""
xtframe.py - decode Compaq Portable / IBM XT keyboard captures.

Kingst KingstVIS CSV export, "transitions only" or sampled, columns:
    Time[s], pin1(CLOCK), pin2(DATA), pin3(RESET)

Protocol confirmed by scope on a real Compaq Portable keyboard:
    - clock idle high, keyboard-generated, ~24 kHz during a frame
    - each frame: 1 start bit (1) then 8 data bits, LSB first
    - DATA is valid on the FALLING edge of CLOCK
    - RESET active-low: host holds low, releases high when ready
    - after reset release the keyboard waits ~170 ms then sends 0xAA (BAT ok)

Usage:  python3 xtframe.py capture.csv [--gap-ms 5]
"""
import argparse, csv, sys

def load(path):
    rows=[]
    with open(path, newline='') as f:
        r=csv.reader(f)
        header=next(r)
        for line in r:
            if len(line) < 4:
                continue
            rows.append((float(line[0]), int(line[1]), int(line[2]), int(line[3])))
    return rows

def reset_edges(rows):
    for i in range(1, len(rows)):
        if rows[i-1][3]==0 and rows[i][3]==1:
            yield ('release', rows[i][0])
        if rows[i-1][3]==1 and rows[i][3]==0:
            yield ('assert', rows[i][0])

def falling_samples(rows, deglitch_us=0.0):
    """(time, data) at each clock falling edge.

    If deglitch_us > 0, a falling edge closer than that to the previously
    ACCEPTED falling edge is treated as ringing and dropped. A real bit is
    ~41us here, so ~20us cleanly separates bits from sub-threshold glitches
    that a Schmitt-trigger receiver (the original's 74LS132) would reject."""
    out=[]
    last_t=None
    min_gap=deglitch_us*1e-6
    dropped=0
    for i in range(1, len(rows)):
        if rows[i-1][1]==1 and rows[i][1]==0:
            t=rows[i][0]
            if last_t is not None and (t-last_t) < min_gap:
                dropped+=1
                continue
            out.append((t, rows[i][2]))
            last_t=t
    return out, dropped

def cluster(samples, gap_s):
    """split a flat list of (t,d) into frames on inter-edge gaps."""
    if not samples:
        return []
    groups=[[samples[0]]]
    for s in samples[1:]:
        if s[0]-groups[-1][-1][0] > gap_s:
            groups.append([])
        groups[-1].append(s)
    return groups

def decode_frame(bits):
    """bits = list of ints at falling edges. Expect [start=1, d0..d7 LSB-first]."""
    if len(bits) < 9:
        return None, "short frame (%d bits)" % len(bits)
    if bits[0] != 1:
        note="no start bit (got %d), assuming data starts at bit 0" % bits[0]
        data=bits[0:8]
    else:
        note="start bit ok"
        data=bits[1:9]
    val=0
    for i,b in enumerate(data):   # LSB first
        val |= (b & 1) << i
    return val, note

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('csv')
    ap.add_argument('--gap-ms', type=float, default=5.0,
                    help='inter-frame gap threshold in ms (default 5)')
    ap.add_argument('--deglitch-us', type=float, default=0.0,
                    help='drop falling edges closer than N us to the last one (try 20)')
    a=ap.parse_args()

    rows=load(a.csv)
    if not rows:
        sys.exit("no data rows parsed")

    print(f"loaded {len(rows)} transitions, "
          f"{rows[0][0]:.4f}s .. {rows[-1][0]:.4f}s\n")

    for kind,t in reset_edges(rows):
        arrow = "released (->run)" if kind=='release' else "asserted (->reset)"
        print(f"  RESET {arrow} at {t:.6f}s")
    print()

    samples,dropped=falling_samples(rows, a.deglitch_us)
    if a.deglitch_us:
        print(f"deglitch: dropped {dropped} edge(s) closer than {a.deglitch_us}us\n")
    frames=cluster(samples, a.gap_ms/1000.0)
    print(f"{len(frames)} frame(s) on falling-edge clustering "
          f"(gap > {a.gap_ms}ms):\n")

    for fr in frames:
        bits=[d for _,d in fr]
        t0=fr[0][0]
        if len(fr) > 1:
            pers=[fr[k][0]-fr[k-1][0] for k in range(1,len(fr))]
            avg=sum(pers)/len(pers)*1e6
            khz=1000.0/avg if avg else 0
        else:
            avg=khz=0
        val,note=decode_frame(bits)
        bitstr=''.join(str(b) for b in bits)
        if val is None:
            print(f"  t={t0:.6f}s  {len(bits):2d} edges  "
                  f"{avg:6.1f}us/{khz:4.1f}kHz  bits={bitstr}  [{note}]")
        else:
            print(f"  t={t0:.6f}s  {len(bits):2d} edges  "
                  f"{avg:6.1f}us/{khz:4.1f}kHz  bits={bitstr}  "
                  f"=> 0x{val:02X} ({val})  [{note}]")

if __name__=='__main__':
    main()
