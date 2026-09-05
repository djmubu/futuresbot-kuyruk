#!/usr/bin/env python3
"""kume_blok.py DIR [--split 2026-07-01] — cikarilan kume (A\\B) icin ZAMAN-BLOKLU belirsizlik (GPT yontem elestirisi, E154).
Islemler bagimsiz degil (ayni gun ayni piyasa hareketi). iid z'nin yaninda 24h ve 72h takvim bloklariyla (blok = tum sembollerin
o penceredeki islemleri) blok-bootstrap SE hesaplanir: z_iid, z_24h, z_72h; ayrica blok-esli B-A fark t'si. Salt-okur.
Kural: on-ilan esik (z <= -2) iid'de tutsa bile 24h VE 72h bloklarda isaret korunmali ve |z_blok| >= 1.5 (bolge kurali gibi, ilan: E154)."""
import sys, glob, csv, math, random
from collections import defaultdict
d = sys.argv[1]; split = sys.argv[3] if len(sys.argv) > 3 else "2026-07-01"
random.seed(7)
def load(arm):
    rows = []
    for f in sorted(glob.glob("%s/%s_c*.trades.csv" % (d, arm))):
        for r in csv.DictReader(open(f, newline="")): rows.append(r)
    out = []; seen = set()
    for r in rows:
        k = (r["symbol"], r["opened_utc"], r["direction"])
        if r["symbol"] == "BTCUSDT":
            if k in seen: continue
            seen.add(k)
        try: r["_R"] = float(r["true_R"])
        except Exception: continue
        out.append(r)
    return out
A = load("A_base"); B = load("B_bayrak")
kb = {(r["symbol"], r["opened_utc"], r["direction"]) for r in B}
rem = [r for r in A if (r["symbol"], r["opened_utc"], r["direction"]) not in kb]
def epoch_h(s):
    from datetime import datetime
    s = s.replace("T", " ")[:19]
    try: t = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError: t = datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
    return (t - datetime(2026, 1, 1)).total_seconds() / 3600.0
def z_iid(xs):
    n = len(xs); m = sum(xs) / n; v = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m, m / math.sqrt(v / n) if v > 0 else 0.0
def z_blok(rows, saat, B_=2000):
    """blok-bootstrap: bloklar = floor(epoch_h/saat); bloklari yerine koyarak ornekle, toplam/n ortalamasinin SE'si."""
    blk = defaultdict(list)
    for r in rows: blk[int(epoch_h(r["opened_utc"]) // saat)].append(r["_R"])
    keys = list(blk); nb = len(keys); n = len(rows); m = sum(r["_R"] for r in rows) / n
    ests = []
    for _ in range(B_):
        s = 0.0; c = 0
        for _ in range(nb):
            k = keys[random.randrange(nb)]; s += sum(blk[k]); c += len(blk[k])
        ests.append(s / c if c else 0.0)
    mu = sum(ests) / B_; se = math.sqrt(sum((e - mu) ** 2 for e in ests) / (B_ - 1))
    return m / se if se > 0 else 0.0, nb, se
def blok_esli(A, B, saat):
    """blok-esli fark: her blokta sum(B)-sum(A); t = ort/SE (bloklar bagimsiz varsayilir)."""
    sa = defaultdict(float); sb = defaultdict(float)
    for r in A: sa[int(epoch_h(r["opened_utc"]) // saat)] += r["_R"]
    for r in B: sb[int(epoch_h(r["opened_utc"]) // saat)] += r["_R"]
    ks = sorted(set(sa) | set(sb)); dif = [sb[k] - sa[k] for k in ks]
    n = len(dif); m = sum(dif) / n; v = sum((x - m) ** 2 for x in dif) / (n - 1)
    return m / math.sqrt(v / n) if v > 0 else 0.0, n, sum(1 for x in dif if x > 0)
print("DIR=%s A n=%d B n=%d | CIKARILAN n=%d toplam=%+.1f" % (d, len(A), len(B), len(rem), sum(r["_R"] for r in rem)))
for per, sel in (("all", lambda r: True), ("train", lambda r: r["opened_utc"] < split), ("test", lambda r: r["opened_utc"] >= split)):
    rr = [r for r in rem if sel(r)]
    if len(rr) < 5: continue
    m, zi = z_iid([r["_R"] for r in rr]); z24, nb24, se24 = z_blok(rr, 24); z72, nb72, se72 = z_blok(rr, 72)
    print("  cikarilan %-5s n=%4d ort=%+.4f | z_iid=%+.2f | z_24h=%+.2f (blok %d, SE %.4f) | z_72h=%+.2f (blok %d, SE %.4f) %s" % (
        per, len(rr), m, zi, z24, nb24, se24, z72, nb72, se72, "[OK]" if (zi <= -2 and z24 <= -1.5 and z72 <= -1.5) else "[RED]"))
for saat in (24, 72):
    t, n, pos = blok_esli(A, B, saat)
    print("  blok-esli B-A fark (%dh): t=%+.2f blok=%d B>A %d/%d" % (saat, t, n, pos, n))
print("NOT: z_iid mevcut kume_stats ile ayni tanim (ort/SE). Blok z'ler ayni-gun bagimliligini hesaba katar; kural E154'te ilan edildi.")
