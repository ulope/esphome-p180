#!/usr/bin/env python3
"""Diff P180 register captures taken from ESPHome logs.

The component logs its register table chunked 16 registers per line, e.g.

    [13:20:01][D][p180:281]: input regs 000-015: 0000 0000 04B4 1770 ...

Save a log for each experiment condition (one file per condition), then:

    tools/regdiff.py before.log after.log

to see which registers moved. With a single file it prints the decoded table
instead, which is handy for eyeballing one capture.

Nothing here talks to the device - it only parses text you already captured.
"""

import argparse
import re
import signal
import sys
from pathlib import Path

# Matches the dump lines emitted by P180Component::dump_registers_().
LINE_RE = re.compile(
    r"(?P<source>input|holding)\s+regs\s+(?P<start>\d+)-(?P<end>\d+):\s+(?P<values>(?:[0-9A-Fa-f]{4}\s*)+)"
)

# Hypotheses from the Sydpower/AFERIY P280 and P310 maps (olofd/kraftverk,
# Ylianst/ESP-FBot). The P180's table is reordered, so these are only hints
# about what a moving register might turn out to be - never assume.
HINTS = {
    "input": {
        3: "charging power (W)?",
        4: "DC/solar input power (W)?",
        6: "total input power (W)?",
        8: "AC input voltage (x0.1V) [confirmed on P180]",
        9: "AC input frequency (x0.01Hz) [confirmed on P180]",
        10: "AC output voltage (x0.1V) [confirmed on P180]",
        11: "AC output frequency (x0.1Hz) [confirmed on P180]",
        12: "output power (W) [confirmed on P180]",
        13: "battery discharge power (W)? [suspect: may be a 2nd output-power reg]",
        31: "battery percent [confirmed on P180]",
        39: "total output power (W)?",
        41: "status bitmask? 0x200=USB 0x400=DC 0x800=AC 0x1000=light",
        48: "AC charging state bitmask?",
        56: "state of charge (x0.1%)?",
        58: "time to full (min)?",
        59: "time to empty (min)?",
    },
    "holding": {
        13: "AC charge rate step (1-5)?",
        24: "USB output toggle?",
        25: "DC output toggle?",
        26: "AC output toggle?",
        27: "light mode (0-3)?",
        66: "discharge floor (x0.1%)?",
        67: "AC charge ceiling (x0.1%)?",
        68: "sleep minutes? -- DANGER: writing 0 is reported to brick the station",
    },
}


def parse(path):
    """Return {source: {reg: value}} using the LAST dump of each source in the file."""
    tables = {}
    for line in Path(path).read_text(errors="replace").splitlines():
        m = LINE_RE.search(line)
        if not m:
            continue
        source = m.group("source")
        start = int(m.group("start"))
        values = m.group("values").split()
        # A dump restarting at register 0 means a newer capture - drop the old one.
        if start == 0:
            tables[source] = {}
        table = tables.setdefault(source, {})
        for offset, raw in enumerate(values):
            table[start + offset] = int(raw, 16)
    return tables


def hint(source, reg):
    text = HINTS.get(source, {}).get(reg)
    return f"  <- {text}" if text else ""


def show(path, tables):
    print(f"== {path}")
    for source in sorted(tables):
        table = tables[source]
        print(f"-- {source}: {len(table)} registers")
        for reg in sorted(table):
            value = table[reg]
            print(f"   reg {reg:3d}: 0x{value:04X} ({value:5d}){hint(source, reg)}")


def diff(path_a, tables_a, path_b, tables_b):
    total = 0
    for source in sorted(set(tables_a) | set(tables_b)):
        a = tables_a.get(source, {})
        b = tables_b.get(source, {})
        if not a or not b:
            print(f"-- {source}: only present in one capture, skipping")
            continue
        changed = [r for r in sorted(set(a) & set(b)) if a[r] != b[r]]
        only_b = sorted(set(b) - set(a))
        print(f"-- {source}: {len(changed)} of {len(set(a) & set(b))} shared registers changed")
        for reg in changed:
            delta = b[reg] - a[reg]
            print(
                f"   reg {reg:3d}: 0x{a[reg]:04X} -> 0x{b[reg]:04X}"
                f"  ({a[reg]:5d} -> {b[reg]:5d}, {delta:+d}){hint(source, reg)}"
            )
        if only_b:
            print(f"   (+{len(only_b)} registers present only in the second capture: {only_b[0]}-{only_b[-1]})")
        total += len(changed)
    return total


def main():
    # This tool is meant to be piped into head/less; don't traceback when the
    # reader closes the pipe early.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("before", help="log file captured before the change")
    parser.add_argument("after", nargs="?", help="log file captured after the change")
    args = parser.parse_args()

    tables_before = parse(args.before)
    if not tables_before:
        sys.exit(
            f"No register dumps found in {args.before}.\n"
            "Set `logger: logs: {p180: DEBUG}` and press a dump button, or set `debug_dump: true`."
        )

    if args.after is None:
        show(args.before, tables_before)
        return

    tables_after = parse(args.after)
    if not tables_after:
        sys.exit(f"No register dumps found in {args.after}.")

    print(f"== {args.before} -> {args.after}")
    if diff(args.before, tables_before, args.after, tables_after) == 0:
        print("\nNo registers changed. If you expected one to, check that the change "
              "actually took effect and that both captures are from the same session.")


if __name__ == "__main__":
    main()
