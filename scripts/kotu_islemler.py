#!/usr/bin/env python3
"""kotu_islemler.py — bir A/B cikti dizininde B kolunun en kotu 15 islemi + stop mesafesi dagilimi (retest6 anomali teshisi). Salt-okur."""
import sys, glob, csv, statistics as st
d = sys.argv[1]; arm = sys.argv[2] if len(sys.argv) > 2 else "B_bayrak"
rows = []
for f in sorted(glob.glob("%s/%s_c*.trades.csv" % (d, arm))):
    for r in csv.DictReader(open(f)):
        if r["symbol"] == "BTCUSDT": continue
        try: r["_R"] = float(r["true_R"]); r["_sd"] = abs(float(r["entry_px"]) - float(r["sl_px"])) / float(r["entry_px"]) * 100
        except Exception: continue
        rows.append(r)
rows.sort(key=lambda r: r["_R"])
print("n=%d toplamR=%+.1f" % (len(rows), sum(r["_R"] for r in rows)))
print("EN KOTU 15: symbol path opened R sure_dk entry sl exit stop%% qty risk pnl close_reason anchor_dist")
for r in rows[:15]:
    print("  %-12s %-9s %s %+8.1f %5s %s %s %s %5.2f%% q=%s risk=%s pnl=%s %s ad=%s" % (r["symbol"], r["path"], r["opened_utc"], r["_R"], r["sure_dk"], r["entry_px"][:10], r["sl_px"][:10], r["exit_px"][:10], r["_sd"], r["qty"], r["risk_usdt"], r["realized_pnl"][:9], r["close_reason"], r.get("anchor_dist_pct", "")))
sd = [r["_sd"] for r in rows]
print("stop mesafesi %%: min %.3f p5 %.3f p25 %.3f medyan %.3f" % (min(sd), st.quantiles(sd, n=20)[0], st.quantiles(sd, n=4)[0], st.median(sd)))
kucuk = [r for r in rows if r["_sd"] < 0.3]
print("stop<0.3%% olan: n=%d toplamR=%+.1f | R<-3 olan: n=%d toplamR=%+.1f" % (len(kucuk), sum(r["_R"] for r in kucuk), sum(1 for r in rows if r["_R"] < -3), sum(r["_R"] for r in rows if r["_R"] < -3)))
for b in ("breakout", "momentum"):
    rr = [r for r in rows if r["path"] == b]
    if rr: print("%s: n=%d R=%+.1f | R<-3 haric R=%+.1f (n=%d)" % (b, len(rr), sum(r["_R"] for r in rr), sum(r["_R"] for r in rr if r["_R"] >= -3), sum(1 for r in rr if r["_R"] >= -3)))
