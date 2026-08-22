#!/usr/bin/env python3
"""VAL-005 Phase 1: compare computed leg forces against the R5FORCE reference.

Reads VAL_005.th (written by trace_force from segments_VAL_005.yaml), forms
the combined-force equivalents

    CF201 = Riser leg
    CF202 = Header + LongRun legs (the expansion lip force is carried
            implicitly by the differing BOUNDED end-cap areas)
    CF203 = DownLeg

and prints peak magnitudes/times against the Appendix H tables of
EGG-EAST-9232 (dry case). Reference digits the 1990 scan cannot support are
marked (?) in the build spec; treat those rows as approximate.

Run from anywhere:  python3 test-validation/compare_VAL_005.py
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Appendix H, dry case: (max +N, at s, max -N, at s)
REFERENCE = {
    "CF201": (+4242.3, 1.43, -5456.8, 1.46),
    "CF202": (+5.4e3, 1.43, -4.242e4, 0.248),   # + peak digits uncertain
    "CF203": (+6.37e3, 1.44, -6962.5, 0.247),   # + peak digits uncertain
}


def load():
    lines = [l for l in open(os.path.join(HERE, "VAL_005.th"))
             if not l.startswith("#")]
    hdr = lines[0].split()
    rows = [list(map(float, l.split())) for l in lines[1:] if l.strip()]
    cols = {n: i for i, n in enumerate(hdr)}
    t = [r[0] for r in rows]

    def series(name):
        return [r[cols[name]] for r in rows]

    forces = {
        "CF201": series("VAL_005_CF201_Riser"),
        "CF202": [a + b for a, b in zip(series("VAL_005_CF202a_Header"),
                                        series("VAL_005_CF202b_LongRun"))],
        "CF203": series("VAL_005_CF203_DownLeg"),
        "Tail(OPEN)": series("VAL_005_Tail_OpenEnd"),
    }
    return t, forces


def main():
    t, forces = load()
    print("| Force | computed +peak | at | ref +peak | at "
          "| computed -peak | at | ref -peak | at |")
    print("|---|---|---|---|---|---|---|---|---|")
    for name, f in forces.items():
        fx, fn = max(f), min(f)
        tx, tn = t[f.index(fx)], t[f.index(fn)]
        if name in REFERENCE:
            rp, rtp, rn, rtn = REFERENCE[name]
            print(f"| {name} | {fx:+.0f} N | {tx:.3f} s | {rp:+.0f} N | "
                  f"{rtp:.2f} s | {fn:+.0f} N | {tn:.3f} s | {rn:+.0f} N | "
                  f"{rtn:.2f} s |")
        else:
            print(f"| {name} | {fx:+.0f} N | {tx:.3f} s | - | - | "
                  f"{fn:+.0f} N | {tn:.3f} s | - | - |")
    print()
    print("| Force | pre-opening mean | steady-blowdown plateau (0.6-0.9 s) "
          "| final |")
    print("|---|---|---|---|")
    for name, f in forces.items():
        pre = f[:100]
        mid = [fi for ti, fi in zip(t, f) if 0.6 <= ti <= 0.9]
        print(f"| {name} | {sum(pre)/len(pre):+.1f} N | "
              f"{sum(mid)/len(mid):+.1f} N | {f[-1]:+.1f} N |")


if __name__ == "__main__":
    main()
