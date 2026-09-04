#!/usr/bin/env python3
"""giris_mimari.py — GIRIS mimarisi (madde 19, E122): retest girisinin parametre haritasi, KAYITLI sinyaller
uzerinde gercek 1dk yolla (exit_mimari.py'nin giris tarafi). Sim'e dokunmaz; sonuc ON ILAN icindir.

Her kayitli islem = bir sinyal (t0 = opened_utc, P0 = entry_px, SL = sl_px yapisal). Iki kol AYNI cikis kuraliyla:
  TABAN : t0'da gir, stop=SL, TP = 1.5R (R=|P0-SL|), muhafazakar bar-ici (once stop), max 10 gun.
  RETEST: girme; conf-TF barlarinda (1m'den toplanir) ilk 'low <= P0*(1-X)' (long) 'touched'; ardindan close>open ve
          close>=seviye olan ilk bar -> o barin close'unda gir; R' = |giris-SL|, TP' = giris+1.5R'. Bekleme N dk icinde
          olmazsa 'zaman asimi' (islem yok). Touched'dan once/sonra herhangi bir 1m low <= SL ise 'kirildi' (islem yok).
  X: sabit % listesi VE ATR(14, 15m, t0 oncesi) katlari. Rapor: her (X, TF, N) icin n_sinyal, giris %, kirildi %, zaman asimi %,
  TABAN toplam/ort R (ayni sinyaller), RETEST toplam R (girilmeyen=0) ve girilen basina ort R, WR; ay bazli (train Nis-Haz / test Tem-Agu).
Kullanim: python3 giris_mimari.py --trades 'srgeo3/B_bayrak_c*.trades.csv' [--limit N] [--paths breakout,momentum]
"""
import sys, glob, csv, math, argparse, datetime as dt
from pathlib import Path
from collections import defaultdict
import numpy as np, pandas as pd

TP_MULT = 1.5
MAX_BARS = 14400  # 10 gun 1m

def _epoch_ms(s):
    return int(dt.datetime.strptime(s[:16], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

def cikis_R(hi, lo, cl, start, entry, sl, long):
    """start indeksinden itibaren (dahil DEGIL: start barinda girildi, sonraki bardan) tek TP 1.5R / stop, R dondurur."""
    R = abs(entry - sl)
    if R <= 0: return 0.0
    tp = entry + TP_MULT * R if long else entry - TP_MULT * R
    h = hi[start + 1:start + 1 + MAX_BARS]; l = lo[start + 1:start + 1 + MAX_BARS]
    if len(h) == 0: return 0.0
    if long:
        s_hit = np.argmax(l <= sl) if (l <= sl).any() else 10**9
        t_hit = np.argmax(h >= tp) if (h >= tp).any() else 10**9
    else:
        s_hit = np.argmax(h >= sl) if (h >= sl).any() else 10**9
        t_hit = np.argmax(l <= tp) if (l <= tp).any() else 10**9
    if s_hit == 10**9 and t_hit == 10**9:
        c = cl[min(start + MAX_BARS, len(cl) - 1)]
        return (c - entry) / R if long else (entry - c) / R
    if s_hit <= t_hit: return -1.0   # muhafazakar: ayni barda stop once
    return TP_MULT

def agg(hi, lo, op, cl, start, tfm, nbars):
    """1m -> tfm dakikalik barlar, start'tan (dahil degil) itibaren nbars bar. Doner: (o,h,l,c, bitis_idx_1m) dizileri."""
    out = []
    i = start + 1
    for _ in range(nbars):
        j = i + tfm
        if j > len(cl): break
        out.append((op[i], hi[i:j].max(), lo[i:j].min(), cl[j - 1], j - 1))
        i = j
    return out

def kos(a):
    dosyalar = []
    for g in a.trades.split(","): dosyalar += sorted(glob.glob(g.strip()))
    rows = []
    for f in dosyalar:
        for r in csv.DictReader(open(f)): rows.append(r)
    paths = {x.strip() for x in a.paths.split(",") if x.strip()}
    rows = [r for r in rows if r.get("path") in paths and r["symbol"] != "BTCUSDT"]
    if a.limit: rows = rows[:a.limit]
    by_sym = defaultdict(list)
    for r in rows: by_sym[r["symbol"]].append(r)
    print("sinyal=%d sembol=%d yollar=%s" % (len(rows), len(by_sym), sorted(paths)))
    Xs = [float(x) for x in a.xs.split(",")]
    Ks = [float(x) for x in a.atr_ks.split(",")]
    TFs = [int(x) for x in a.tfs.split(",")]
    Ns = [int(x) for x in a.ns.split(",")]
    cfgs = [("pct", x, tf, n) for x in Xs for tf in TFs for n in Ns] + [("atr", k, tf, n) for k in Ks for tf in TFs for n in Ns]
    # sonuc: cfg -> dict(per period) : n, giris, kirildi, zaman, R_retest, R_taban, win, tuttu
    S = {c: defaultdict(lambda: defaultdict(float)) for c in cfgs}
    taban_S = defaultdict(lambda: defaultdict(float))
    atlanan = 0
    for sym, trs in by_sym.items():
        pq = Path(a.mum_dir) / (sym + "_1m.parquet")
        if not pq.exists(): atlanan += len(trs); continue
        df = pd.read_parquet(pq, columns=["timestamp", "open", "high", "low", "close"])
        ts = df["timestamp"].to_numpy(); op = df["open"].to_numpy(float); hi = df["high"].to_numpy(float)
        lo = df["low"].to_numpy(float); cl = df["close"].to_numpy(float)
        for r in trs:
            try:
                p0 = float(r["entry_px"]); sl = float(r["sl_px"]); long = r["direction"] == "long"
                if p0 <= 0 or sl <= 0: atlanan += 1; continue
                t0 = int(np.searchsorted(ts, _epoch_ms(r["opened_utc"]), side="right")) - 1
                if t0 < 300 or t0 + 60 >= len(cl): atlanan += 1; continue
                per = "train" if r["opened_utc"] < a.split else "test"
                # ATR(14) 15m, t0 oncesi
                a15 = agg(hi, lo, op, cl, t0 - 15 * 15 - 1, 15, 15)
                trs_ = [max(h - l, abs(h - pc), abs(l - pc)) for (o, h, l, c, _), pc in zip(a15[1:], [x[3] for x in a15[:-1]])]
                atr_pct = (sum(trs_) / len(trs_)) / p0 * 100 if trs_ else 0.0
                base = cikis_R(hi, lo, cl, t0, p0, sl, long)
                taban_S[per]["n"] += 1; taban_S[per]["R"] += base; taban_S[per]["win"] += (base > 0)
                _bars_tf = {tfm: agg(hi, lo, op, cl, t0, tfm, max(Ns) // tfm) for tfm in TFs}
                for c in cfgs:
                    kind, x, tfm, nmin = c
                    X = (x if kind == "pct" else x * atr_pct) / 100.0
                    lvl = p0 * (1 - X) if long else p0 * (1 + X)
                    st = S[c][per]; st["n"] += 1; st["R_taban"] += base
                    bars = _bars_tf[tfm][:nmin // tfm]
                    touched = False; entered = None; broke = False
                    for (o, h, l, cc, end_i) in bars:
                        # kirildi: yapisal stop asildi (girmeden)
                        if (l <= sl) if long else (h >= sl):
                            broke = True; break
                        if (l <= lvl) if long else (h >= lvl): touched = True
                        if touched and ((cc > o and cc >= lvl) if long else (cc < o and cc <= lvl)):
                            entered = (end_i, cc); break
                    if broke: st["kirildi"] += 1; continue
                    if entered is None: st["zaman"] += 1; continue
                    ei, ep = entered
                    st["giris"] += 1
                    rr = cikis_R(hi, lo, cl, ei, ep, sl, long)
                    st["R_retest"] += rr; st["win"] += (rr > 0)
                    st["fiyat_kazanc"] += ((p0 - ep) / p0 * 100) if long else ((ep - p0) / p0 * 100)
            except Exception:
                atlanan += 1
    print("atlanan=%d" % atlanan)
    def fmt(c):
        kind, x, tfm, nmin = c
        return "%s %-4s tf=%2dm N=%3ddk" % ("X%%=" if kind == "pct" else "ATRx", ("%.2f" % x).rstrip("0").rstrip("."), tfm, nmin)
    for per in ("train", "test", "all"):
        print("\n==== DONEM: %s ====" % per)
        def get(st, k):
            return st[per][k] if per != "all" else st["train"][k] + st["test"][k]
        tn = get(taban_S, "n"); tR = get(taban_S, "R")
        print("TABAN (t0'da gir, ayni cikis kurali): n=%d toplamR=%+.1f ortR=%+.4f WR=%.1f%%" % (tn, tR, tR / tn if tn else 0, 100 * get(taban_S, "win") / tn if tn else 0))
        print("%-26s %6s %6s %6s %6s %9s %9s %8s %6s %8s" % ("konfig", "giris%", "kir%", "zam%", "n_gir", "R_retest", "R_taban", "ortR_gir", "WR", "fiyat+%"))
        rowsout = []
        for c in cfgs:
            st = S[c]; n = get(st, "n")
            if not n: continue
            g = get(st, "giris"); rr = get(st, "R_retest"); rb = get(st, "R_taban")
            rowsout.append((rr - rb, c, n, g, get(st, "kirildi"), get(st, "zaman"), rr, rb, get(st, "win"), get(st, "fiyat_kazanc")))
        rowsout.sort(key=lambda t: -t[0])
        for d, c, n, g, k, z, rr, rb, w, fk in rowsout:
            print("%-26s %5.1f%% %5.1f%% %5.1f%% %6d %+9.1f %+9.1f %+8.3f %5.1f%% %+7.2f  (fark %+.1f)" % (
                fmt(c), 100 * g / n, 100 * k / n, 100 * z / n, g, rr, rb, rr / g if g else 0, 100 * w / g if g else 0, fk / g if g else 0, d))
    print("\nNOT: R brut, tek TP 1.5R, muhafazakar bar-ici; girilmeyen sinyal = 0 R. 'fark' = R_retest - R_taban (ayni sinyaller). "
          "Kararda toplam R (fark) VE girilen-basina ortR birlikte okunur; train/test tutarliligi sart.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="srgeo3/B_bayrak_c*.trades.csv")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--paths", default="breakout,momentum")
    ap.add_argument("--xs", default="0.3,0.5,0.75,1.0,1.5")
    ap.add_argument("--atr-ks", dest="atr_ks", default="0.5,1.0,1.5")
    ap.add_argument("--tfs", default="5,15,60")
    ap.add_argument("--ns", default="60,120,240,480")
    ap.add_argument("--split", default="2026-07-01")
    ap.add_argument("--mum-dir", dest="mum_dir", default="data/backtest_candles")
    kos(ap.parse_args())
