#!/usr/bin/env python3
"""eklenen_analiz.py — HIPOTEZ 3 (E128): retest kolunda EKLENEN (retest-zamanli) girisler neden kaybediyor?
A (taban) breakout+momentum islemleri ile B'nin breakout+momentum islemlerini (retest teyidiyle acilanlar) karsilastirir:
n, WR, ort kazanc R, ort kayip R, close_reason dagilimi, sure medyani, stop mesafesi % (|entry-sl|/entry), cipa etiketi (tuttu/kirildi),
MAE/MFE (min_low/max_high'tan, R cinsinden), aylik. BTC haric (E89), mr haric. Salt-okur.
Kullanim: python3 eklenen_analiz.py <out_dir> [<out_dir2> ...]   (A_base_c*.trades.csv / B_bayrak_c*.trades.csv)
"""
import sys, glob, csv, statistics as st
from collections import Counter, defaultdict

def yukle(pattern):
    rows = []
    for f in sorted(glob.glob(pattern)):
        for r in csv.DictReader(open(f)):
            if r["symbol"] == "BTCUSDT" or r.get("path") == "mr": continue
            try: r["_R"] = float(r["true_R"])
            except Exception: continue
            rows.append(r)
    return rows

def ozet(ad, rows):
    n = len(rows)
    if not n: print("%s: bos" % ad); return
    R = [r["_R"] for r in rows]; w = [x for x in R if x > 0]; l = [x for x in R if x <= 0]
    print("\n== %s: n=%d toplamR=%+.1f mR=%+.4f WR=%.1f%% ortKazanc=%+.3f ortKayip=%+.3f (kazanc/kayip=%.2f) besabas WR=%.1f%%" % (
        ad, n, sum(R), sum(R)/n, 100*len(w)/n, (st.mean(w) if w else 0), (st.mean(l) if l else 0),
        (abs(st.mean(w)/st.mean(l)) if w and l else 0), (100*abs(st.mean(l))/(st.mean(w)+abs(st.mean(l))) if w and l else 0)))
    cr = Counter(r["close_reason"] for r in rows)
    print("  close_reason:", ", ".join("%s %d (%.0f%%, R %+.1f)" % (k, v, 100*v/n, sum(r["_R"] for r in rows if r["close_reason"] == k)) for k, v in cr.most_common(8)))
    def med(key, f=float):
        xs = []
        for r in rows:
            try: xs.append(f(r[key]))
            except Exception: pass
        return (st.median(xs), st.quantiles(xs, n=4)) if len(xs) > 3 else (None, None)
    sd = []
    for r in rows:
        try:
            e = float(r["entry_px"]); s = float(r["sl_px"]); sd.append(abs(e - s) / e * 100)
        except Exception: pass
    print("  stop mesafesi %%: medyan %.2f  q1 %.2f q3 %.2f | sure_dk medyan %s" % (st.median(sd), st.quantiles(sd, n=4)[0], st.quantiles(sd, n=4)[2], med("sure_dk")[0]))
    ce = Counter(r.get("anchor_etiket", "") for r in rows)
    for k in ("tuttu", "kirildi"):
        rr = [r["_R"] for r in rows if r.get("anchor_etiket") == k]
        if rr: print("  cipa %s: n=%d (%.0f%%) mR=%+.3f" % (k, len(rr), 100*len(rr)/n, sum(rr)/len(rr)))
    # MAE/MFE R cinsinden (long: (entry-min_low)/risk, (max_high-entry)/risk)
    mae = []; mfe = []
    for r in rows:
        try:
            e = float(r["entry_px"]); s = float(r["sl_px"]); risk = abs(e - s)
            lo = float(r["min_low"]); hi = float(r["max_high"])
            if risk <= 0: continue
            if r["direction"] == "long": mae.append((e - lo) / risk); mfe.append((hi - e) / risk)
            else: mae.append((hi - e) / risk); mfe.append((e - lo) / risk)
        except Exception: pass
    if mae:
        print("  MAE(R) medyan %.2f q3 %.2f | MFE(R) medyan %.2f q3 %.2f | MFE>=1.5R olan %.0f%% | MFE>=1R ama kayip %.0f%%" % (
            st.median(mae), st.quantiles(mae, n=4)[2], st.median(mfe), st.quantiles(mfe, n=4)[2],
            100*sum(1 for x in mfe if x >= 1.5)/len(mfe), 100*sum(1 for x, r in zip(mfe, rows) if x >= 1.0 and r["_R"] <= 0)/len(mfe)))
    ay = defaultdict(list)
    for r in rows: ay[r["opened_utc"][:7]].append(r["_R"])
    print("  aylik:", " | ".join("%s n%d %+.1f" % (k, len(v), sum(v)) for k, v in sorted(ay.items())))
    yol = defaultdict(list)
    for r in rows: yol[(r["path"], r["direction"])].append(r["_R"])
    print("  yol/yon:", " | ".join("%s/%s n%d mR%+.3f" % (k[0], k[1], len(v), sum(v)/len(v)) for k, v in sorted(yol.items())))

for d in sys.argv[1:]:
    A = yukle("%s/A_base_c*.trades.csv" % d); B = yukle("%s/B_bayrak_c*.trades.csv" % d)
    key = lambda r: (r["symbol"], r["opened_utc"], r["direction"])
    ka = {key(r) for r in A}
    eklenen = [r for r in B if key(r) not in ka]
    print("\n######## %s : A breakout+momentum n=%d | B n=%d | eklenen (retest-zamanli) n=%d" % (d, len(A), len(B), len(eklenen)))
    ozet("A taban (breakout+momentum, impulsta giris)", A)
    ozet("B eklenen (retest teyidinde giris)", eklenen)
    # ayni sembol+gun icinde eslesen ciftler: A'daki sinyal -> B'deki retest girisi (ilk 8 saat icinde ayni yon)
    import datetime as dt
    def t(s): return dt.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
    bi = defaultdict(list)
    for r in eklenen: bi[(r["symbol"], r["direction"])].append(r)
    cift = []
    for a in A:
        for b in bi.get((a["symbol"], a["direction"]), []):
            dm = (t(b["opened_utc"]) - t(a["opened_utc"])).total_seconds() / 60
            if 0 < dm <= 480:
                cift.append((a, b, dm)); break
    if cift:
        fiyat = []
        for a, b, dm in cift:
            e0 = float(a["entry_px"]); e1 = float(b["entry_px"])
            fiyat.append(((e0 - e1) / e0 * 100) if a["direction"] == "long" else ((e1 - e0) / e0 * 100))
        print("\n  ESLESEN CIFT (ayni sembol/yon, retest girisi sinyalden <=8s sonra): n=%d | gecikme medyan %.0f dk | giris fiyati kazanci medyan %+.2f%% | A R %+.1f -> B R %+.1f (mR %+.3f -> %+.3f)" % (
            len(cift), st.median([c[2] for c in cift]), st.median(fiyat), sum(c[0]["_R"] for c in cift), sum(c[1]["_R"] for c in cift),
            sum(c[0]["_R"] for c in cift)/len(cift), sum(c[1]["_R"] for c in cift)/len(cift)))
        sa = [abs(float(c[0]["entry_px"]) - float(c[0]["sl_px"])) / float(c[0]["entry_px"]) * 100 for c in cift]
        sb = [abs(float(c[1]["entry_px"]) - float(c[1]["sl_px"])) / float(c[1]["entry_px"]) * 100 for c in cift]
        print("  ciftlerde stop mesafesi %%: A medyan %.2f -> B medyan %.2f (ayni yapisal stop, giris yaklasti mi?)" % (st.median(sa), st.median(sb)))
print("\nNOT: 'eklenen' = B'de olup A'da olmayan (sembol, acilis, yon) islemler = retest teyidiyle acilanlar. Harita (giris_mimari) tek TP 1.5R ile olcmustu; burada motorun gercek cikisi.")
