#!/usr/bin/env python3
"""madde22_takvim.py DIR [--split 2026-07-01] — MADDE 22 (yon-bazli eszamanli maruziyet tavani) TAKVIM YONTEMI. Salt-okur, sim kosmaz.
Girdi: bir A/B cikti dizininin A_base parcalari (310 ucl taban). Tum islemler tek takvime dizilir (havuz), her giris aninda
 ayni yonde ACIK islem sayisi (eszamanlilik) sayilir. (1) Eszamanlilik kovasina gore R (giris kalitesi eszamanlilikla dusuyor mu?)
 (2) Tavan politikasi: giris aninda ayni yonde acik >= K ise girme (atlanan islem slot tutmaz, sirali yeniden hesap). Cikarilan kume
 n/ort/z (train/test), B toplam R, aylik, wf, havuz maxDD (kapanis sirasina gore kumulatif R). Olcek notu: canli 148 sembol / 5 slot;
 havuz 310 sembol -> K'nin canli karsiligi ~K*148/310.
"""
import sys, glob, csv, math, heapq
from datetime import datetime, timedelta
from collections import defaultdict

d = sys.argv[1]; split = sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--split" else "2026-07-01"
CLOSE_COLS = ["closed_utc", "close_utc", "exit_utc", "closed_at", "exit_time"]

def parse(s):
    s = s.replace("Z", "").replace("T", " ")
    for f in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try: return datetime.strptime(s[:26], f)
        except ValueError: pass
    raise ValueError(s)

rows = []; seen = set(); ccol = None
for f in sorted(glob.glob("%s/A_base_c*.trades.csv" % d)):
    for r in csv.DictReader(open(f, newline="")):
        k = (r["symbol"], r["opened_utc"], r["direction"])
        if r["symbol"] == "BTCUSDT":
            if k in seen: continue
            seen.add(k)
        try: R = float(r["true_R"])
        except Exception: continue
        o = parse(r["opened_utc"])
        if ccol is None:
            for c in CLOSE_COLS:
                if c in r and r[c]: ccol = c; break
            if ccol is None: ccol = "_sure"
        if ccol != "_sure" and r.get(ccol): c = parse(r[ccol])
        else:
            try: c = o + timedelta(minutes=float(r.get("sure_dk") or r.get("duration_min") or 0))
            except Exception: c = o
        if c <= o: c = o + timedelta(minutes=1)
        rows.append({"sym": r["symbol"], "dir": r["direction"], "o": o, "c": c, "R": R, "ay": r["opened_utc"][:7], "per": "train" if r["opened_utc"] < split else "test"})
rows.sort(key=lambda r: r["o"])
print("DIR=%s islem=%d kapanis kolonu=%s toplamR=%+.1f" % (d, len(rows), ccol, sum(r["R"] for r in rows)))

def z(xs):
    n = len(xs)
    if n < 2: return 0.0, 0.0
    m = sum(xs) / n; v = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m, (m / math.sqrt(v / n) if v > 0 else 0.0)

def maxdd(rs):
    rs = sorted(rs, key=lambda r: r["c"]); eq = 0.0; pk = 0.0; dd = 0.0
    for r in rs:
        eq += r["R"]; pk = max(pk, eq); dd = min(dd, eq - pk)
    return dd

# (1) eszamanlilik olcumu (tavansiz)
heap = []  # (kapanis, yon)
for r in rows:
    while heap and heap[0][0] <= r["o"]: heapq.heappop(heap)
    r["es_ayni"] = sum(1 for c, y in heap if y == r["dir"]); r["es_tum"] = len(heap)
    heapq.heappush(heap, (r["c"], r["dir"]))
print("\n[1] GIRIS ANINDA AYNI YONDE ACIK ISLEM SAYISI -> R (tavansiz havuz)")
kova = [(0, 2), (3, 5), (6, 10), (11, 20), (21, 40), (41, 10 ** 6)]
for per in ("all", "train", "test"):
    print("  -- %s" % per)
    for lo, hi in kova:
        xs = [r["R"] for r in rows if lo <= r["es_ayni"] <= hi and (per == "all" or r["per"] == per)]
        if not xs: continue
        m, zz = z(xs)
        print("     ayni-yon acik %3d-%-6s n=%5d ortR=%+.4f z=%+.2f toplam=%+.1f WR=%.1f%%" % (lo, ("%d" % hi if hi < 10 ** 6 else "+"), len(xs), m, zz, sum(xs), 100 * sum(1 for x in xs if x > 0) / len(xs)))
for yon in ("long", "short"):
    xs = [r["es_ayni"] for r in rows if r["dir"] == yon]
    if xs:
        xs.sort(); print("  %s eszamanlilik medyan %d p90 %d max %d" % (yon, xs[len(xs) // 2], xs[int(0.9 * len(xs))], xs[-1]))

# (2) tavan politikalari
print("\n[2] TAVAN POLITIKASI: ayni yonde acik >= K ise girme (sirali; atlanan slot tutmaz)")
A_tot = sum(r["R"] for r in rows); A_dd = maxdd(rows)
aylar = sorted({r["ay"] for r in rows})
A_ay = {a: sum(r["R"] for r in rows if r["ay"] == a) for a in aylar}
print("  A (tavansiz): n=%d toplamR=%+.1f maxDD=%+.1f | aylik %s" % (len(rows), A_tot, A_dd, " ".join("%s:%+.0f" % (a[2:], A_ay[a]) for a in aylar)))
for K in (3, 5, 8, 10, 15, 20, 30, 50):
    heap = []; kal = []; cik = []
    for r in rows:
        while heap and heap[0][0] <= r["o"]: heapq.heappop(heap)
        if sum(1 for c, y in heap if y == r["dir"]) >= K: cik.append(r); continue
        kal.append(r); heapq.heappush(heap, (r["c"], r["dir"]))
    if not cik: print("  K=%2d: hicbir islem cikmadi" % K); continue
    B_tot = sum(r["R"] for r in kal); B_dd = maxdd(kal)
    m_all, z_all = z([r["R"] for r in cik]); m_tr, z_tr = z([r["R"] for r in cik if r["per"] == "train"]); m_te, z_te = z([r["R"] for r in cik if r["per"] == "test"])
    B_ay = {a: sum(r["R"] for r in kal if r["ay"] == a) for a in aylar}
    ay_ok = sum(1 for a in aylar if B_ay[a] > A_ay[a])
    wf_tr = sum(r["R"] for r in kal if r["per"] == "train") - sum(r["R"] for r in rows if r["per"] == "train")
    wf_te = sum(r["R"] for r in kal if r["per"] == "test") - sum(r["R"] for r in rows if r["per"] == "test")
    print("  K=%2d (canli ~%2d): B n=%5d toplamR=%+.1f (fark %+.1f) maxDD=%+.1f | CIKARILAN n=%5d (%.0f%%) ort=%+.4f z=%+.2f [train n=%d ort %+.3f z %+.2f | test n=%d ort %+.3f z %+.2f] | aylik B>A %d/%d | wf train %+.1f test %+.1f %s" % (
        K, round(K * 148 / 310), len(kal), B_tot, B_tot - A_tot, B_dd, len(cik), 100 * len(cik) / len(rows), m_all, z_all,
        sum(1 for r in cik if r["per"] == "train"), m_tr, z_tr, sum(1 for r in cik if r["per"] == "test"), m_te, z_te, ay_ok, len(aylar), wf_tr, wf_te,
        "[7:OK]" if (z_all <= -2 and ay_ok * 2 > len(aylar) and wf_tr > 0 and wf_te > 0) else "[7:RED]"))
print("\nNOT: on-ilan kural [7] (filtre): cikarilan kume z <= -2 VE aylik cogunluk VE wf train+test pozitif. Bolge kurali: tek K degil, komsu K'lar ayni isaret olmali.")
print("UYARI: havuz eszamanliligi canlidan cok yuksek (310 sembol, 78 bagimsiz portfoy); K'nin canli karsiligi yaklasik K*148/310. Sonuc yalniz 'eszamanlilik giris kalitesini bozuyor mu' sorusuna cevap verir; USDT-DD korumasi R-degismezdir, burada olculmez.")
