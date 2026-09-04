#!/usr/bin/env python3
"""kume_stats.py DIR [--split 2026-07-01]: A\\B (cikarilan) ve B\\A (eklenen) kumelerinin train/test n, sum, sumsq'sunu JSON basar (havuzlama icin)."""
import sys, glob, json, csv
d = sys.argv[1]; split = sys.argv[3] if len(sys.argv) > 3 else "2026-07-01"
def load(arm):
    rows = []
    for f in sorted(glob.glob(f"{d}/{arm}_c*.trades.csv")):
        with open(f, newline="") as cf:
            for r in csv.DictReader(cf): rows.append(r)
    return rows
def dedupe(rows):  # BTC yalniz en dusuk parcadan (eski kosular icin), yeni kosularda zaten tek
    out = []; seen = set()
    for r in rows:
        k = (r["symbol"], r["opened_utc"], r["direction"])
        if r["symbol"] == "BTCUSDT":
            if k in seen: continue
            seen.add(k)
        out.append(r)
    return out
A = dedupe(load("A_base")); B = dedupe(load("B_bayrak"))
ka = {(r["symbol"], r["opened_utc"], r["direction"]) for r in A}; kb = {(r["symbol"], r["opened_utc"], r["direction"]) for r in B}
def stats(rows):
    o = {}
    for r in rows:
        per = "train" if r["opened_utc"] < split else "test"; x = float(r["true_R"])
        s = o.setdefault(per, {"n": 0, "sum": 0.0, "sumsq": 0.0}); s["n"] += 1; s["sum"] += x; s["sumsq"] += x * x
        s2 = o.setdefault("all", {"n": 0, "sum": 0.0, "sumsq": 0.0}); s2["n"] += 1; s2["sum"] += x; s2["sumsq"] += x * x
    return o
rem = [r for r in A if (r["symbol"], r["opened_utc"], r["direction"]) not in kb]
add = [r for r in B if (r["symbol"], r["opened_utc"], r["direction"]) not in ka]
print(json.dumps({"dir": d, "cikarilan": stats(rem), "eklenen": stats(add), "A": stats(A), "B": stats(B)}, indent=1))
