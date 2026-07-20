#!/usr/bin/env python3
"""
Annotate a raw KLE layout with matrix (row,col) coordinates.

Emits:
  <stem>-matrix.json   raw KLE, every legend replaced with "r,c"
  <stem>-via.json      VIA-format wrapper around the same keymap

The annotated file is a BUILD ARTIFACT for kbplacer / kle2netlist.
Keep the original for the pretty legends.
"""
import copy
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------- geometry

def walk(kle):
    """Yield (row_idx, item_idx, x, y, w, h) for each key, in file order."""
    rows = [r for r in kle if isinstance(r, list)]
    y = 0.0
    for ri, row in enumerate(rows):
        x = 0.0
        w = h = 1.0
        for ii, item in enumerate(row):
            if isinstance(item, dict):
                x += item.get("x", 0.0)
                y += item.get("y", 0.0)
                w = item.get("w", w)
                h = item.get("h", h)
            else:
                yield (ri, ii, x, y, w, h)
                x += w
                w = h = 1.0
        y += 1.0


# ---------------------------------------------------------------- matrix

def assign_physical(keys):
    """row = physical row, col = index within that row."""
    out, counters = [], {}
    for (ri, ii, x, y, w, h) in keys:
        c = counters.get(ri, 0)
        counters[ri] = c + 1
        out.append((ri, c))
    return out


def assign_split(keys, splits=2):
    """Split each physical row into `splits` electrical rows, interleaved.

    Trades a couple of extra rows for far fewer columns.  Routing stays
    sane because adjacent keys land on different electrical rows and the
    column runs are still vertical.
    """
    out, counters = [], {}
    for (ri, ii, x, y, w, h) in keys:
        c = counters.get(ri, 0)
        counters[ri] = c + 1
        out.append((ri * splits + (c % splits), c // splits))
    return out


# ---------------------------------------------------------------- emit

def annotate(kle, coords):
    """Return a deep copy with legend index 0 replaced by 'r,c'."""
    out = copy.deepcopy(kle)
    rows = [r for r in out if isinstance(r, list)]
    for (ri, ii, *_), (r, c) in zip(walk(kle), coords):
        rows[ri][ii] = f"{r},{c}"
    return out


def strip_alignment(kle):
    """VIA expects legend[0] to be the matrix coord; force a:0 so index 0
    is the top-left slot and nothing else can shift it."""
    out = []
    for row in kle:
        if not isinstance(row, list):
            out.append(row)
            continue
        new = []
        for item in row:
            if isinstance(item, dict):
                item = {k: v for k, v in item.items() if k not in ("a", "c", "t", "f")}
                if not item:
                    continue
                item["a"] = 0
            new.append(item)
        out.append(new)
    return out


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "compaq-portable.json")
    mode = sys.argv[2] if len(sys.argv) > 2 else "physical"

    kle = json.loads(src.read_text())
    meta = kle[0] if isinstance(kle[0], dict) else {}
    keys = list(walk(kle))

    coords = assign_physical(keys) if mode == "physical" else assign_split(keys)
    nrows = max(r for r, _ in coords) + 1
    ncols = max(c for _, c in coords) + 1

    # sanity: no duplicate matrix positions
    assert len(set(coords)) == len(coords), "duplicate matrix coordinate!"

    annotated = annotate(kle, coords)
    keymap = strip_alignment([r for r in annotated if isinstance(r, list)])

    Path(f"{src.stem}-matrix.json").write_text(json.dumps(annotated, indent=2))
    Path(f"{src.stem}-via.json").write_text(json.dumps({
        "name": meta.get("name", src.stem),
        "vendorId": "0xFEED",
        "productId": "0x0000",
        "matrix": {"rows": nrows, "cols": ncols},
        "layouts": {"keymap": keymap},
    }, indent=2))

    print(f"{len(keys)} keys -> {nrows} rows x {ncols} cols "
          f"({nrows + ncols} GPIO, {nrows * ncols - len(keys)} unused slots)")
    for r in range(nrows):
        print(f"  row {r}: {sum(1 for rr, _ in coords if rr == r):2d} keys")


if __name__ == "__main__":
    main()
