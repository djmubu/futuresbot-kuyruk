#!/usr/bin/env python3
"""ab_analiz.py — run_ab trades.csv ciktisindan A vs B tam karar tablosu (walk-forward disiplini).

Kullanim: python3 ab_analiz.py DIR [DIR2 ...] [--a A_base] [--b B_bayrak] [--split 2026-07-01]
  DIR icinde  <ARM>_cNN.trades.csv  dosyalari (run_ab.py ciktisi). Birden fazla dizin birlesir.

Verdikleri:
 1. Kol toplamlari (n, toplamR, mR, WR, maxDD)              -> kaldirac mi?
 2. Fark (B-A): mR farki, Welch z, parca-esli t             -> gurultu mu?
 3. Aylik A vs B                                            -> tek-ay siskinligi mi?
 4. WALK-FORWARD: split oncesi (train) / sonrasi (test) ayri -> donem-ozel overfit mi? (3 Eyl dersi)
 5. Yol (mr/momentum/breakout) ve yon dagilimi              -> mekanizma dogru mu calisti?
 6. Esik karari (onceden ilan): z>=2, test-donemi ayni isaret, aylik B>A cogunluk, toplamR dusmuyor.
"""
import sys, os, glob, argparse
import numpy as np, pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("dirs", nargs="+")
ap.add_argument("--a", default="A_base"); ap.add_argument("--b", default="B_bayrak")
ap.add_argument("--split", default="2026-07-01", help="walk-forward: bu tarihten once train, sonra test")
ap.add_argument("--z", type=float, default=2.0)
ap.add_argument("--btc", default="dedupe", choices=["dedupe", "drop", "keep"],
                help="run_ab BTCUSDT'yi HER parcaya ekler -> BTC islemleri parca sayisi kadar KOPYALANIR (E89). "
                     "dedupe = her dizin+kol icin BTC'yi yalniz en dusuk chunk'tan al (varsayilan); drop = BTC'yi at; keep = ham (eski, sisik)")
o = ap.parse_args()

def load(arm):
    fs = []
    for d in o.dirs: fs += glob.glob(os.path.join(d, "%s_c*.trades.csv" % arm))
    if not fs: return None
    parts = []
    for f in sorted(fs):
        df = pd.read_csv(f); df["chunk"] = os.path.basename(f).split("_c")[-1].split(".")[0]
        df["kutu"] = os.path.basename(os.path.dirname(f)); parts.append(df)
    df = pd.concat(parts, ignore_index=True)
    if o.btc != "keep":
        btc = df["symbol"] == "BTCUSDT"
        nb = int(btc.sum())
        if o.btc == "drop":
            df = df[~btc]
        else:
            keep = pd.Series(False, index=df.index)
            for k, sub in df[btc].groupby("kutu"):
                c0 = sorted(sub["chunk"].unique())[0]
                keep[sub.index[sub["chunk"] == c0]] = True
            df = df[~btc | keep]
        print("  [%s] BTC %s: %d BTC satiri -> %d (parca-kopyasi ayiklandi)" % (arm, o.btc, nb, int((df["symbol"] == "BTCUSDT").sum())))
    # TEKILLIK DENETIMI (E89): ayni islem birden fazla parcada sayiliyor mu?
    dup = df.duplicated(subset=["symbol", "opened_utc", "direction"], keep=False)
    if dup.any():
        ds = df[dup].groupby("symbol").size().sort_values(ascending=False)
        print("  !! [%s] TEKILLIK: %d satir baska parcada da var (sembol: %s) -> toplamlar SISIK, --btc dedupe/drop kullan"
              % (arm, int(dup.sum()), ", ".join("%s x%d" % (k, v) for k, v in ds.head(5).items())))
    df["true_R"] = pd.to_numeric(df["true_R"], errors="coerce")
    df["t"] = pd.to_datetime(df["opened_utc"], utc=True)
    df["ay"] = df["t"].dt.strftime("%Y-%m")
    return df.dropna(subset=["true_R", "t"]).sort_values("t")

def dd(s):
    c = s.cumsum().values; p = np.maximum.accumulate(c); return float((c - p).min()) if len(c) else 0.0

def satir(df, l):
    if df is None or not len(df): print("  %-14s VERI YOK" % l); return
    r = df["true_R"]
    print("  %-14s n=%6d toplamR=%+9.1f mR=%+.4f WR=%5.1f%% maxDD=%8.1f" % (
        l, len(r), r.sum(), r.mean(), 100 * (r > 0).mean(), dd(r)))

def welch_z(a, b):
    if a is None or b is None or len(a) < 2 or len(b) < 2: return float("nan")
    return (b.mean() - a.mean()) / np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))

def paired_t(A, B):
    ga = A.groupby(["kutu", "chunk"])["true_R"].sum(); gb = B.groupby(["kutu", "chunk"])["true_R"].sum()
    idx = ga.index.intersection(gb.index)
    if len(idx) < 3:
        # kollar ayri dizinlerde (orn. pct_A + pct_b, ayni offset/chunk): yalniz chunk ile esle
        ga = A.groupby("chunk")["true_R"].sum(); gb = B.groupby("chunk")["true_R"].sum()
        idx = ga.index.intersection(gb.index)
        if len(idx) < 3: return float("nan"), 0, 0
        print("  (parca-esleme: dizinler farkli, yalniz chunk no ile eslendi: %d parca)" % len(idx))
    d = (gb[idx] - ga[idx]).values
    return float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))) if d.std(ddof=1) else float("nan"), int((d > 0).sum()), len(d)

A, B = load(o.a), load(o.b)
print("=== A/B ANALIZ  dirs=%s  A=%s  B=%s ===" % (",".join(o.dirs), o.a, o.b))
print("\n[1] KOL TOPLAMLARI"); satir(A, "A"); satir(B, "B")
if A is None or B is None: sys.exit(1)

z = welch_z(A["true_R"], B["true_R"]); t, npos, nch = paired_t(A, B)
dmR = B["true_R"].mean() - A["true_R"].mean(); dR = B["true_R"].sum() - A["true_R"].sum()
print("\n[2] FARK (B-A): mR %+.4f | toplamR %+.1f | islem %+d | Welch z=%+.2f | parca-esli t=%+.2f (%d/%d parca B>A)"
      % (dmR, dR, len(B) - len(A), z, t, npos, nch))

print("\n[3] AYLIK toplam R")
moA = A.groupby("ay")["true_R"].sum(); moB = B.groupby("ay")["true_R"].sum()
ays = sorted(set(moA.index) | set(moB.index)); bwin = 0
for m in ays:
    a_, b_ = moA.get(m, 0.0), moB.get(m, 0.0); bwin += b_ > a_
    print("    %s  A=%+8.1f (n=%4d)  B=%+8.1f (n=%4d)  fark=%+7.1f  %s" % (
        m, a_, (A["ay"] == m).sum(), b_, (B["ay"] == m).sum(), b_ - a_, "B>A" if b_ > a_ else "A>B"))
print("    aylik B>A: %d/%d" % (bwin, len(ays)))

print("\n[4] WALK-FORWARD (split=%s)" % o.split)
sp = pd.Timestamp(o.split, tz="UTC"); wf = {}
for ad, (lo, hi) in (("train", (None, sp)), ("test", (sp, None))):
    a_ = A[(A["t"] < hi) if hi is not None else (A["t"] >= lo)]; b_ = B[(B["t"] < hi) if hi is not None else (B["t"] >= lo)]
    zz = welch_z(a_["true_R"], b_["true_R"]); d_ = b_["true_R"].mean() - a_["true_R"].mean() if len(a_) and len(b_) else float("nan")
    wf[ad] = d_
    print("    %-5s A: n=%5d mR=%+.4f R=%+8.1f | B: n=%5d mR=%+.4f R=%+8.1f | fark mR=%+.4f z=%+.2f" % (
        ad, len(a_), a_["true_R"].mean() if len(a_) else 0, a_["true_R"].sum(), len(b_), b_["true_R"].mean() if len(b_) else 0,
        b_["true_R"].sum(), d_, zz))

print("\n[5] YOL x KOL (mekanizma kontrolu)")
for col in ("path", "direction", "regime"):
    if col not in A.columns: continue
    ga = A.groupby(col)["true_R"].agg(["count", "sum", "mean"]); gb = B.groupby(col)["true_R"].agg(["count", "sum", "mean"])
    print("  -- %s" % col)
    for k in sorted(set(ga.index) | set(gb.index)):
        ra = ga.loc[k] if k in ga.index else None; rb = gb.loc[k] if k in gb.index else None
        print("    %-22s A: n=%5d (%4.1f%%) R=%+8.1f mR=%+.3f | B: n=%5d (%4.1f%%) R=%+8.1f mR=%+.3f" % (
            k, ra["count"] if ra is not None else 0, 100 * (ra["count"] / len(A)) if ra is not None else 0,
            ra["sum"] if ra is not None else 0, ra["mean"] if ra is not None else 0,
            rb["count"] if rb is not None else 0, 100 * (rb["count"] / len(B)) if rb is not None else 0,
            rb["sum"] if rb is not None else 0, rb["mean"] if rb is not None else 0))

print("\n[6] ESIK KARARI (onceden ilan edilmis kriterler)")
k1 = z >= o.z; k2 = wf.get("test", float("nan")) > 0 and wf.get("train", float("nan")) > 0
k3 = bwin > len(ays) / 2; k4 = dR >= -0.05 * abs(A["true_R"].sum())
for ok, s in ((k1, "Welch z >= %.1f  (z=%+.2f)" % (o.z, z)),
              (k2, "walk-forward: train VE test farki pozitif (train %+.4f, test %+.4f)" % (wf.get("train", float("nan")), wf.get("test", float("nan")))),
              (k3, "aylik B>A cogunluk (%d/%d)" % (bwin, len(ays))),
              (k4, "toplam R dusmuyor (>= -%%5)  (fark %+.1f)" % dR)):
    print("    [%s] %s" % ("OK " if ok else "RED", s))
print("\n  SONUC: %s" % ("KALDIRAC — tum kriterler gecti" if all((k1, k2, k3, k4)) else
                          "REDDET/ASKIDA — gecmeyen kriter var (ayna / donem-ozel / gurultu riski)"))

# ── [7] FILTRE / HUCUM TESTI (E106, on ilan 2026-09-04) ─────────────────────
# Filtre kaldiraci (B, A'dan islem CIKARIR): dogru soru "cikarilan kume kaybediyor mu".
# Hucum kaldiraci (B, A'ya islem EKLER): "eklenen kume kazaniyor mu".
# Kriterler (her ikisi icin simetrik): (1) kume ortalamasi dogru isaretli ve z >= 2 (vs 0),
# (2) aylik: kume toplami aylarin cogunda dogru isaretli, (3) walk-forward: train VE test'te AYRI AYRI z >= 2 (donem-yogunlasmasi elenir),
# (4) toplam R iyilesiyor (fark >= 0). Sadece ortak islemler degismemis olmali (aksi halde sinif "karisik").
def _kume(A, B):
    key = ["symbol", "opened_utc", "direction"]
    ka = set(map(tuple, A[key].values)); kb = set(map(tuple, B[key].values))
    rem = A[[tuple(r) not in kb for r in A[key].values]]
    add = B[[tuple(r) not in ka for r in B[key].values]]
    ortak_a = A[[tuple(r) in kb for r in A[key].values]]; ortak_b = B[[tuple(r) in ka for r in B[key].values]]
    return rem, add, ortak_a, ortak_b
_rem, _add, _oa, _ob = _kume(A, B)
print("\n[7] FILTRE/HUCUM TESTI (E106): cikarilan n=%d R=%+.1f | eklenen n=%d R=%+.1f | ortak n=%d (A R %+.1f, B R %+.1f, fark %+.1f)"
      % (len(_rem), _rem["true_R"].sum(), len(_add), _add["true_R"].sum(), len(_oa), _oa["true_R"].sum(), _ob["true_R"].sum(),
         _ob["true_R"].sum() - _oa["true_R"].sum()))
def _kume_test(K, isaret, ad):
    if len(K) < 30:
        print("    %s: n=%d < 30, test yok" % (ad, len(K))); return None
    r = K["true_R"]; zz = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if r.std(ddof=1) else float("nan")
    ay = K["opened_utc"].str[:7]; ayl = r.groupby(ay).sum()
    tr = r[K["opened_utc"] < o.split]; te = r[K["opened_utc"] >= o.split]
    c1 = (zz * isaret) >= o.z; c2 = int(((ayl * isaret) > 0).sum()) > len(ayl) / 2
    def _z(x):
        return (x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 1 and x.std(ddof=1) else float("nan")
    ztr, zte = _z(tr), _z(te)
    # walk-forward: kume HER IKI donemde de anlamli dogru isaretli (z >= 2) — donem-yogunlasmasini (E99 down-gate) yakalar
    c3 = (ztr * isaret >= o.z) and (zte * isaret >= o.z)
    print("    %s: ort %+.4f z(vs0) %+.2f [%s] | aylik dogru-isaret %d/%d [%s] | wf train R %+.1f z %+.2f / test R %+.1f z %+.2f [%s]"
          % (ad, r.mean(), zz, "OK " if c1 else "RED", int(((ayl * isaret) > 0).sum()), len(ayl), "OK " if c2 else "RED",
             tr.sum(), ztr, te.sum(), zte, "OK " if c3 else "RED"))
    return c1 and c2 and c3
_ortak_fark = _ob["true_R"].sum() - _oa["true_R"].sum()
_ortak_kucuk = abs(_ortak_fark) <= 0.2 * max(abs(dR), 1.0)
if not _ortak_kucuk:
    print("    SINIF: ORTAK ISLEMLER DEGISIYOR (ortak fark %+.1f, toplam fark %+.1f) -> filtre/hucum degil, GEOMETRI/CIKIS sinifi; karar [6]" % (_ortak_fark, dR))
elif len(_rem) >= 30 and len(_add) < 30:
    _ok = _kume_test(_rem, -1, "FILTRE (cikarilan kume negatif mi)")
    print("    toplam R farki %+.1f [%s]" % (dR, "OK " if dR >= 0 else "RED"))
    print("  FILTRE SONUCU: %s" % ("KALDIRAC (filtre testi)" if (_ok and dR >= 0) else "REDDET/ASKIDA (filtre testi)"))
elif len(_add) >= 30 and len(_rem) < 30:
    _ok = _kume_test(_add, +1, "HUCUM (eklenen kume pozitif mi)")
    print("    toplam R farki %+.1f [%s]" % (dR, "OK " if dR >= 0 else "RED"))
    print("  HUCUM SONUCU: %s" % ("KALDIRAC (hucum testi)" if (_ok and dR >= 0) else "REDDET/ASKIDA (hucum testi)"))
elif len(_add) >= 30 and len(_rem) >= 30:
    print("    KARISIK: hem cikarilan hem eklenen kume var -> ikisi ayri raporlanir, karar [6] + mekanizma okumasi")
    _kume_test(_rem, -1, "cikarilan"); _kume_test(_add, +1, "eklenen")
else:
    print("    kume kucuk (<30): filtre/hucum testi uygulanmaz; [6] gecerli")
