#!/usr/bin/env python3
"""topla_feat.py — <out_dir> altindaki <arm>_c*.trades.csv + .feat.csv dosyalarini position_id ile birlestirip TEK CSV olarak stdout'a basar (BTC haric). Salt-okur."""
import sys, glob, csv
d = sys.argv[1]; arm = sys.argv[2] if len(sys.argv) > 2 else "A_base"
w = None; n = 0
for tf in sorted(glob.glob("%s/%s_c*.trades.csv" % (d, arm))):
    ff = tf.replace(".trades.csv", ".feat.csv")
    feats = {}
    try:
        for r in csv.DictReader(open(ff)): feats[r["position_id"]] = r
    except Exception: pass
    chunk = tf.split("_c")[-1].split(".")[0]
    for r in csv.DictReader(open(tf)):
        if r["symbol"] == "BTCUSDT": continue
        f = feats.get(r["position_id"], {})
        row = dict(r); row["chunk"] = chunk
        for k, v in f.items():
            if k != "position_id": row[k] = v
        if w is None:
            w = csv.DictWriter(sys.stdout, fieldnames=list(row.keys())); w.writeheader()
        w.writerow(row); n += 1
print("# satir=%d" % n, file=sys.stderr)
