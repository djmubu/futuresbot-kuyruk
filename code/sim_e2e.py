#!/usr/bin/env python3
"""sim_e2e.py — futuresbot.sim'i GERCEK parquet verisiyle uctan uca kosturur.

Kullanim:
  python sim_e2e.py                      -> yalniz ENVANTER (hicbir sey kosmaz)
  python sim_e2e.py --run                -> envanter + sim kosusu
  python sim_e2e.py --run --days 5 --symbols BTCUSDT ETHUSDT

Wiring sablonu: tests/regression/strategy_fixture_builder.py (gercek
StrategyEngine + StrategyAdapter + SimExchange). FARK: orada sentetik veri ve
strategy_selector/regime_router=None kullaniliyor; burada GERCEK parquet var
ama selector/router yine None (Faz 1). Canli ile fark RAPORLANIR.

.env'deki 10 canli parametre (v4.6.56) burada da uygulanir — yoksa sim
settings.py varsayilanlariyla kosar ve canliyla ayni sey olmaz.
"""
from __future__ import annotations
import os, sys, time, json, argparse
from pathlib import Path

# Tasinabilir kok tespiti: --root, yoksa cwd, yoksa scriptin yani.
_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--root", default=None)
_known, _ = _pre.parse_known_args()
def _find_root(explicit):
    cands = []
    if explicit: cands.append(Path(explicit))
    cands.append(Path.cwd())
    cands.append(Path(__file__).resolve().parent)
    cands.append(Path(__file__).resolve().parent.parent)
    for c in cands:
        if (c / "src" / "futuresbot").is_dir():
            return c.resolve()
    raise SystemExit("HATA: bot kokunu bulamadim (src/futuresbot yok). --root ile ver.")
ROOT = _find_root(_known.root)
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))

# --- LOG SUSTURMA -------------------------------------------------
# sim_e2e setup_logging() cagirmaz -> structlog VARSAYILAN yapilandirmada
# kalir: her seviye acik, dogrudan stdout. engine.py scan basina 3-5 satir
# JSON basar (strategy.mr_signal_detected INFO + birkac debug). 180 gunde
# 2M+ satir eder ve kosunun baskin maliyeti log yazimi olur.
# Bu blok TUM futuresbot importlarindan ONCE gelmeli.
import logging as _lg, structlog as _sl
_LEVEL = _lg.ERROR if "--quiet" not in sys.argv else _lg.CRITICAL
if "--verbose" in sys.argv:
    _LEVEL = _lg.DEBUG
_sl.configure(
    wrapper_class=_sl.make_filtering_bound_logger(_LEVEL),
    logger_factory=_sl.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)
_lg.basicConfig(level=_LEVEL, stream=sys.stderr)
_lg.getLogger().setLevel(_LEVEL)
print("log seviyesi: %s  (--verbose ile acilir)" % _lg.getLevelName(_LEVEL))
# ------------------------------------------------------------------
print("kok: %s" % ROOT)

CANDLES = ROOT / "data" / "backtest_candles"

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=None, help="bot kok dizini (varsayilan: cwd)")
ap.add_argument("--run", action="store_true", help="envanterden sonra sim'i kostur")
ap.add_argument("--days", type=float, default=90.0, help="son N gunluk pencere (default 90)")
ap.add_argument("--symbols", nargs="+", default=None, help="default: BTCUSDT + en buyuk 1 sembol")
ap.add_argument("--balance", type=float, default=1000.0)
ap.add_argument("--risk-frac", type=float, default=0.01, dest="risk_frac",
                help="sim R birimi: islem basina ozkaynagin bu orani SL'ye riske atilir "
                     "(default 0.01 = 1R). CANLI sizing.py ile ayni anlamda DEGIL.")
ap.add_argument("--env-set", action="append", default=[], metavar="KEY=VAL",
                help="'.env' degerini EZ (A/B icin). Birden fazla kez verilebilir. "
                     "Ornek: --env-set EXIT_HYBRID_ENABLED=false")
ap.add_argument("--json-out", default=None, metavar="PATH",
                help="ozet metrikleri JSON olarak yaz (toplama icin)")
ap.add_argument("--tag", default="", help="kosuya etiket (JSON'a yazilir)")
ap.add_argument("--no-gates", action="store_true", dest="no_gates",
                help="bootstrap giris kapilarini uygulama (A/B icin)")
ap.add_argument("--no-selector", action="store_true", dest="no_selector",
                help="strategy_selector'i bagla-ma (A/B icin)")
ap.add_argument("--no-portfolio", action="store_true", dest="no_portfolio",
                help="Portfolio ledger'i bagla-ma (A/B icin)")
ap.add_argument("--no-persym", action="store_true", dest="no_persym",
                help="per_symbol_loader'i bagla-ma (A/B icin)")
ap.add_argument("--sr-geometry", action="store_true", dest="sr_geometry",
                help="stop/TP icin SR yiginin v2_sl/v2_tp1 degerlerini kullan "
                     "(SL_MAX_DIST_PCT=0 ile birlikte verilmeli)")
ap.add_argument("--mr-anchor-min-pct", type=float, default=0.0, dest="mr_anchor_min_pct",
                help="mr yolunda, cipa girise bu %%'den YAKINSA girme (seviye-dibinde "
                     "giris korumasi; 0 = kapali). E15 analizi: <%%0,5 kanama, >=%%2 kar")
ap.add_argument("--fng-csv", default=None, dest="fng_csv",
                help="Fear&Greed tarihsel CSV yolu (fng_kur.py uretir); verilirse "
                     "haber filtresi F&G saglayiciyla ACIK kosulur (E27 arastirmasi)")
ap.add_argument("--sr-buffer-atr", type=float, default=0.0, dest="sr_buffer_atr",
                help="yapisal stopu cipanin K x ATR OTESINE tasi (fitil korumasi; "
                     "yalniz --sr-geometry ile anlamli). 0 = kapali")
ap.add_argument("--sr-max-pct", type=float, default=10.0, dest="sr_max_pct",
                help="SR stop girisin bu %%'sinden uzaksa REDDET (bozuk veri korumasi)")
ap.add_argument("--watchlist-json", default="", dest="watchlist_json",
                help="SABIT izleme listesi (json, 'symbols' alani): liste disi girisler reddedilir")
ap.add_argument("--watchlist-daily", default="", dest="watchlist_daily",
                help="GUNLUK liste dizini: watchlist.json.bak_forager_YYYYMMDD_* dosyalari. "
                     "D gunu etkin liste = D tarihli yedek (bot benimseme gecikmesi modeli)")
ap.add_argument("--btc-hard-gate", type=float, default=None, dest="btc_hard_gate",
                metavar="ESIK",
                help="BTC bias skoru bu ESIK'in ALTINDA ise TUM girisleri kapat "
                     "(E27 arastirmasi; ornek -40 = guclu ayi). None = kapali. "
                     "Skor -100..+100; _btcf.get_btc_bias().score kullanilir.")
ap.add_argument("--no-trade-symbols", default="", dest="no_trade_symbols", metavar="SYM,SYM",
                help="bu semboller YALNIZ BAGLAM (BTCDirectionFilter vb.) icin yuklenir, ISLEM ACILMAZ. "
                     "E89: run_ab BTC'yi her parcaya ekler; parca>0 icin BTCUSDT buraya verilir ki BTC islemleri kopyalanmasin.")
ap.add_argument("--cost-model", default="", dest="cost_model", choices=["", "taker", "live"],
                help="maliyet modeli (madde 6): taker = mevcut (bayt-aynı), live = E73/E76 olcumu "
                     "(giris/TP maker-ish 0.0242%%, SL taker + 8bps slip). bos = SIM_COST_MODEL env ya da taker.")
ap.add_argument("--exit-ladder", default="", dest="exit_ladder",
                metavar="MERDIVEN",
                help="kademeli cikis: 'rMult,frac/...' ornek '3,0.4/6,0.3/9,0.3'. "
                     "bos = taban (tek TP). E39/E40. fraclar toplami ~1 olmali.")
ap.add_argument("--block-regimes", default="", dest="block_regimes",
                metavar="LISTE",
                help="virgul-ayrik rejim listesi; bu rejimlerde giris YOK. "
                     "E34 verisi: ranging(-0.074,n=3359) choppy(-0.098) weakening_up(-0.138) negatif.")
ap.add_argument("--block-down-regimes", action="store_true", dest="block_down_regimes",
                help="early_reversal_down / trending_down / weakening_down rejimlerinde giris ACMA "
                     "(srgeo2 orneklem-ici tahmini: +0.056 R/islem)")
# ── E102/E103: KALABALIK (crowding) KAPISI ve YAPISAL DUSUS-SHORT IZNI ──
ap.add_argument("--crowd-calendar", default="", dest="crowd_calendar", metavar="CSV[,CSV..]",
                help="E102: evren-capinda giris takvimi (opened_utc,symbol kolonlu trades.csv; virgulle coklu "
                     "veya glob). Girisin oncesindeki --crowd-min dakikada takvimde >= --crowd-n giris varsa "
                     "GIRME (kendisi haric: [t-win, t) araligi). Bos = kapali (bayt-ayni).")
ap.add_argument("--crowd-n", type=int, default=6, dest="crowd_n", help="kalabalik esigi (varsayilan 6, train'den on ilan)")
ap.add_argument("--crowd-min", type=int, default=120, dest="crowd_min", help="kalabalik penceresi dk (varsayilan 120)")
ap.add_argument("--down-short-structural", action="store_true", dest="down_short_structural",
                help="E103: --block-down-regimes ile birlikte: dusus rejimlerinde SHORT'a, yapisal kosulla IZIN "
                     "(v2_sl mesafesi >= --dss-srdist-min %% VE v2_anchor_resistance mesafesi >= --dss-anchor-min %%). "
                     "Longlar kapali kalir. Bayrak yokken bayt-ayni.")
ap.add_argument("--dss-srdist-min", type=float, default=1.5, dest="dss_srdist_min")
ap.add_argument("--dss-anchor-min", type=float, default=1.0, dest="dss_anchor_min")
# ── E109/madde 20: UST-TF YON FILTRESI (Elder: D1 yon, alt TF giris) ──
ap.add_argument("--htf-align", action="store_true", dest="htf_align",
                help="madde 20: sembolun GUNLUK trendi giris sarti. 4h kapanislardan gunluk seri (6 bar) turetilir; "
                     "long yalniz close > EMA(N) VE EMA yukselirken (EMA[-1] > EMA[-1-slope]); short simetrik. "
                     "Veri yetersizse (< N+slope gun) giris SERBEST (bayt-ayni davranis). Bayrak yokken bayt-ayni.")
ap.add_argument("--htf-ema", type=int, default=20, dest="htf_ema", help="gunluk EMA uzunlugu (varsayilan 20)")
ap.add_argument("--htf-slope", type=int, default=3, dest="htf_slope", help="EMA egim penceresi, gun (varsayilan 3)")
# ── E120 / madde 19: RETEST GIRISI (impuls sonrasi geri cekilme + tutma teyidi) ──
ap.add_argument("--entry-retest", action="store_true", dest="entry_retest",
                help="madde 19: breakout/momentum sinyalinde HEMEN girme; bekleyen giris: fiyat sinyal fiyatindan >= --retest-pct geri cekilip "
                     "(long: low <= P0*(1-X)) ardindan seviye ustunde yukari kapanis (close>open ve close>=seviye) yaparsa o barda gir. "
                     "Geri cekilme yapisal stop'un (v2_sl) altina inerse iptal ('kirildi'); --retest-bars icinde olmazsa zaman asimi. mr yolu etkilenmez.")
ap.add_argument("--retest-pct", type=float, default=0.5, dest="retest_pct", help="geri cekilme esigi %% (varsayilan 0.5 = kazananlarin MAE medyani)")
ap.add_argument("--retest-bars", type=int, default=8, dest="retest_bars", help="bekleme suresi, tarama bari (15m) sayisi (varsayilan 8 = 2 saat)")
ap.add_argument("--retest-paths", default="breakout,momentum", dest="retest_paths", help="retest uygulanacak yollar (varsayilan breakout,momentum)")
ap.add_argument("--retest-tf", type=int, default=15, dest="retest_tf", choices=[5, 15, 60],
                help="teyit bari: 5 = 1m'den toplanan 5m alt-barlar (15m taramasinda 3 alt-bar sirayla; giris tarama kapanisinda), "
                     "15 = tarama bari; 60 = 1h bari (saat kapanisina denk gelen taramada)")
ap.add_argument("--retest-level", default="signal", dest="retest_level", choices=["signal", "anchor"],
                help="E129 hipotez 1/2: retest seviyesi 'signal' = P0*(1-X) (varsayilan) | 'anchor' = sinyalin A+ cipasi (v2_anchor_support/resistance): "
                     "dokunus = low <= cipa*(1+tol), teyit = close>open ve close>=cipa; cipa yoksa signal seviyesine duser (sayac retest_cipa_yok)")
ap.add_argument("--retest-tol", type=float, default=0.2, dest="retest_tol", help="anchor modunda dokunus/gecersizleme toleransi %% (motor retest_confirm ile ayni: 0.2)")
ap.add_argument("--retest-cancel", default="sl", dest="retest_cancel", choices=["sl", "close"],
                help="iptal kurali: 'sl' = v2_sl dokunusu (varsayilan) | 'close' = ek olarak alt-bar kapanisi cipa*(1-tol) altinda (motor 'invalidated' esdegeri)")
ap.add_argument("--retest-atr", type=float, default=0.0, dest="retest_atr",
                help="geri cekilme esigi = k x ATR(14, 15m) / fiyat (yuzde yerine). >0 ise --retest-pct yok sayilir. E124 secimi: 0.5")
ap.add_argument("--htf-paths", default="", dest="htf_paths", metavar="YOL,YOL",
                help="E118: --htf-align yalniz bu yollara uygulansin (orn. breakout,momentum; mr = mean reversion tanimi geregi karsi-trend). Bos = tum yollar.")
ap.add_argument("--toy-sizer", action="store_true", dest="toy_sizer",
                help="eski default_sizer'i kullan (1R=ozkaynagin %%1'i). Varsayilan: GERCEK PositionSizer")
ap.add_argument("--verbose", action="store_true", help="motor loglarini ac (COK yavas)")
ap.add_argument("--quiet", action="store_true", help="ERROR loglarini da sustur")
A = ap.parse_args()

def sec(t): print("\n" + "=" * 72); print(t); print("=" * 72)

# ── 0. ENVANTER ───────────────────────────────────────────────────
sec("[0] data/backtest_candles ENVANTERI")
if not CANDLES.exists():
    print("  DIZIN YOK: %s" % CANDLES.resolve()); sys.exit(1)
files = sorted(CANDLES.glob("*.parquet"))
by_tf = {}
for f in files:
    tf = f.stem.rsplit("_", 1)[-1]
    by_tf.setdefault(tf, []).append(f)
print("  toplam parquet: %d" % len(files))
for tf, fl in sorted(by_tf.items()):
    tot = sum(x.stat().st_size for x in fl)
    print("    *_%-4s : %4d dosya | %8.1f MB | ornek: %s" % (
        tf, len(fl), tot / 1e6, fl[0].name if fl else "-"))
ones = by_tf.get("1m", [])
if not ones:
    print("\n  !! 1m parquet YOK — sim 1m'den 15m/1h/4h TURETIYOR, calisamaz.")
    print("     Cozum: scripts/download_bulk_historical.py ile 1m indir")
    print("     (auto_recalibrate 15m indiriyor, sim 1m istiyor).")
    sys.exit(2)
ones_sorted = sorted(ones, key=lambda p: -p.stat().st_size)
print("\n  en buyuk 8 adet 1m dosyasi:")
for f in ones_sorted[:8]:
    print("    %-34s %8.1f MB" % (f.name, f.stat().st_size / 1e6))

if not A.run:
    print("\n  (kosmak icin --run ekle)")
    sys.exit(0)

# ── 1. AYARLAR: .env'deki canli degerleri uygula ──────────────────
sec("[1] Ayarlar — .env canli parametreleri uygulaniyor")
from futuresbot.config.settings import AppSettings
settings = AppSettings()
# ── bootstrap PARITESI ────────────────────────────────────────────
# Bu tablo app/bootstrap.py'nin .env'den okudugu HER anahtari birebir
# yansitir (bootstrap _PARAM_ENV dongusu + tek tek os.environ.get
# atamalari). Elle bakim yerine asagidaki DRIFT DENETIMI bootstrap'i
# okur ve tabloda olmayan bir anahtar varsa bagirir — "bir yeri gozden
# kacirma" hatasi sessizce yasayamasin.
#
# Tip donusumu deklare EDILMEZ: mevcut alanin tipinden turetilir, boylece
# settings.py'de bir alan int'ten float'a donerse tablo bozulmaz.
_BOOT_ENV = (
    # bootstrap _PARAM_ENV (10)
    ("ATR_PERIOD",                       "strategy", "atr_period"),
    ("ATR_SL_MULTIPLIER",                "strategy", "atr_sl_multiplier"),
    ("ATR_TP_MULTIPLIER",                "strategy", "atr_tp_multiplier"),
    ("RISK_PER_TRADE_PCT",               "risk",     "risk_per_trade_pct"),
    ("TP_MAX_ROI_PCT",                   "risk",     "tp_max_roi_pct"),
    ("TP_REGIME_MAX_ATR_RANGING",        "risk",     "tp_regime_max_atr_ranging"),
    ("TP_REGIME_MAX_ATR_EARLY_REVERSAL", "risk",     "tp_regime_max_atr_early_reversal"),
    ("TP_REGIME_MAX_ATR_TRENDING",       "risk",     "tp_regime_max_atr_trending"),
    ("TP_REGIME_MAX_ATR_PARABOLIC",      "risk",     "tp_regime_max_atr_parabolic"),
    ("TP_RECENT_RANGE_MAX_MULTIPLE",     "risk",     "tp_recent_range_max_multiple"),
    # FAZ0 / FAZ1 tek tek atamalar
    ("GLOBAL_SIZE_MULT",                 "risk",     "global_size_multiplier"),
    ("MAX_DAILY_TRADES",                 "risk",     "max_daily_trades"),
    ("CONSECUTIVE_LOSS_PAUSE_HOURS",     "risk",     "consecutive_loss_pause_hours"),
    ("RISK_CLOSE_DEDUP_SEC",             "risk",     "close_dedup_sec"),
    ("MIN_ENTRY_ADX",                    "risk",     "min_entry_adx"),
    ("ENTRY_SPREAD_GATE_PCT",            "risk",     "entry_spread_gate_pct"),
    ("SL_MAX_DIST_PCT",                  "risk",     "sl_max_dist_pct"),
    ("SL_MIN_DIST_PCT",                  "risk",     "sl_min_dist_pct"),
    # FAZ1.5 hibrit cikis — TP merdivenini komple yeniden kurar (sizing.py:138)
    ("EXIT_HYBRID_ENABLED",              "risk",     "exit_hybrid_enabled"),
    ("EXIT_TP1_SL_MULT",                 "risk",     "exit_tp1_sl_mult"),
    ("EXIT_TP1_SPLIT_PCT",               "risk",     "exit_tp1_split_pct"),
    ("EXIT_TRAIL_DIST_PCT",              "risk",     "exit_trail_dist_pct"),
    # BTC short kapisi
    ("SHORT_BTC_GATE_ENABLED",           "risk",     "short_btc_gate_enabled"),
    ("SHORT_BTC_MIN",                    "risk",     "short_btc_min"),
    ("SHORT_BTC_MAX",                    "risk",     "short_btc_max"),
    # break-even (pozisyon yoneticisi — sim'de etkisiz, paritede tutuluyor)
    ("BE_EARLY_MFE_PCT",                 "position", "be_early_mfe_pct"),
    ("BE_EARLY_MFE_PCT_SHORT",           "position", "be_early_mfe_pct_short"),
    # v4.6.x md.16 — regime routing lever (adaptif ADX percentile/floor A/B)
    ("ADAPTIVE_ADX_PERCENTILE",          "strategy", "adaptive_adx_percentile"),
    ("ADAPTIVE_ADX_FLOOR",               "strategy", "adaptive_adx_floor"),
)
# bootstrap'in okudugu ama SIM'i ilgilendirmeyen anahtarlar (bilincli haric)
_BOOT_ENV_IGNORE = {
    "BINANCE_TESTNET", "LIVE_MODE_EXPLICIT_ENABLE", "TRADING_MODE",
    "TELEGRAM_ADMIN_IDS", "AUTO_RESTART_HOURS", "OPTUNA_BRIDGE_ENABLED",
    "RL_MODEL_PATH", "ENSEMBLE_ML_MODEL_PATH",
    # asagida ozel olarak ele aliniyor (loader kurulumu, duz alan atamasi degil)
    "PER_SYMBOL_CALIBRATION_ENABLED", "PER_SYMBOL_CALIBRATION_YAML_PATH",
}

env = {}
if (ROOT / ".env").exists():
    for ln in open(ROOT / ".env", encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1); env[k.strip()] = v.strip()


# --env-set: .env'in uzerine yaz. A/B'nin tek degisken kalmasini saglar.
_ENV_OVERRIDES = {}
for _ov in A.env_set:
    if "=" not in _ov:
        print("  !! --env-set gecersiz (KEY=VAL bekleniyor): %r" % _ov); continue
    _ok, _ov_v = _ov.split("=", 1)
    _ok = _ok.strip(); _ov_v = _ov_v.strip()
    _ENV_OVERRIDES[_ok] = _ov_v
    env[_ok] = _ov_v
if _ENV_OVERRIDES:
    print("  !! ENV EZME (A/B): %s" % _ENV_OVERRIDES)


def _coerce(cur, raw):
    """Mevcut degerin tipine gore donustur. bool once: bool int'in alt tipi."""
    if isinstance(cur, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(cur, int):
        return int(float(raw))
    if isinstance(cur, float):
        return float(raw)
    return type(cur)(raw) if cur is not None else raw


_applied, _skipped = 0, []
for _k, _sect, _attr in _BOOT_ENV:
    if _k not in env:
        continue
    obj = getattr(settings, _sect, None)
    if obj is None or not hasattr(obj, _attr):
        _skipped.append("%s (settings.%s.%s YOK)" % (_k, _sect, _attr)); continue
    try:
        old = getattr(obj, _attr)
        setattr(obj, _attr, _coerce(old, env[_k]))
        new_v = getattr(obj, _attr)
        _applied += 1
        if str(old) != str(new_v):
            print("  %-32s %s -> %s" % (_k, old, new_v))
        else:
            print("  %-32s %s (degismedi)" % (_k, new_v))
    except Exception as e:
        _skipped.append("%s (%r)" % (_k, e))
print("  --- %d anahtar uygulandi" % _applied)
for s in _skipped:
    print("  !! UYGULANAMADI: %s" % s)

# ── DRIFT DENETIMI: bootstrap ne okuyorsa tablo da bilmeli ────────
import re as _re
_bp = ROOT / "src" / "futuresbot" / "app" / "bootstrap.py"
if _bp.exists():
    _boot_keys = set(_re.findall(r'os\.environ\.get\(\s*"([A-Z0-9_]+)"', _bp.read_text(encoding="utf-8")))
    _boot_keys |= {m for m in _re.findall(r'\(\s*"([A-Z0-9_]+)",\s*"(?:risk|strategy|position)"', _bp.read_text(encoding="utf-8"))}
    _known = {k for k, _, _ in _BOOT_ENV} | _BOOT_ENV_IGNORE
    _drift = sorted(_boot_keys - _known)
    if _drift:
        print("\n  !! DRIFT: bootstrap bu anahtarlari okuyor, sim tablosunda YOK:")
        for d in _drift:
            print("       %-32s %s" % (d, ".env'de VAR" if d in env else "(.env'de yok)"))
        print("     -> _BOOT_ENV'e ekle, yoksa sim canliyla ayni ayarlarla kosmaz.")
    else:
        print("  drift denetimi: bootstrap ile parite TAM (%d anahtar)" % len(_boot_keys))
else:
    print("  !! drift denetimi ATLANDI: %s yok" % _bp)

# ── 2. SEMBOL + PENCERE ───────────────────────────────────────────
sec("[2] Sembol ve zaman penceresi")
avail = {f.stem[:-3] for f in ones}           # "_1m" ekini at
_all = [f.stem[:-3] for f in ones_sorted]
syms = A.symbols or ((["BTCUSDT"] if "BTCUSDT" in _all else [])
                     + sorted(x for x in _all if x != "BTCUSDT"))
syms = [s for s in syms if s in avail]
if not syms: print("  istenen semboller icin 1m parquet yok. mevcut: %s" % sorted(avail)[:15]); sys.exit(3)
print("  semboller: %s" % syms)

from futuresbot.sim.data import DataCatalog, SymbolSpec
specs = [SymbolSpec(symbol=s, parquet_1m_path=CANDLES / f"{s}_1m.parquet",
                    timeframes=("1m", "15m", "1h", "4h")) for s in syms]
catalog = DataCatalog(data_dir=CANDLES, symbols=specs)
t0 = time.monotonic()
catalog.load()
print("  catalog.load() %.1fs" % (time.monotonic() - t0))
spans = {s: catalog.span(s) for s in syms}
for s, sp in spans.items():
    import datetime as _dt
    a = _dt.datetime.fromtimestamp(sp.first_open_ns / 1e9, _dt.UTC)
    b = _dt.datetime.fromtimestamp(sp.last_close_ns / 1e9, _dt.UTC)
    print("  %-12s %s .. %s  (%.1f gun)" % (s, a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d"),
                                            (sp.last_close_ns - sp.first_open_ns) / 86400e9))
# BIRLESIM penceresi (KESISIM DEGIL).
# Kesisim alinirsa parcadaki EN GEC listelenen sembol tum parcanin
# penceresini keser: 2026-08-31 olcumunde 9 parcanin 7'si bu yuzden
# isinmayi bile dolduramadi (c06: 15.6 gun -> 0 islem). Katalog,
# aralik disindaki sembol icin zaten olay uretmiyor; gec listelenen
# sembol yalnizca kendi barlarini katkilar ve motorun kendi
# per-sembol isinma kapisi (len(htf_df) >= 200) onu dogal olarak eler.
end_ns = max(sp.last_close_ns for sp in spans.values())
start_ns = max(min(sp.first_open_ns for sp in spans.values()),
               end_ns - int(A.days * 86400 * 1e9))
_warm_ns = int(200 * 4 * 3600 * 1e9)   # 200 x 4h
_late = [s for s, sp in spans.items() if sp.first_open_ns > end_ns - _warm_ns]
if _late:
    print("  !! ISINMAYA YETMEYEN SEMBOL (%d): %s" % (len(_late), _late[:8]))
    print("     bunlar sinyal uretemez; pencereyi KISALTMAZ (birlesim kullaniliyor).")
_win_d = (end_ns - start_ns) / 86400e9
print("  KOSU PENCERESI: son %.1f gun" % _win_d)
_htf_h = {"4h": 4, "1h": 1, "15m": 0.25}.get(htf_probe := (
    s0.value if hasattr(s0 := settings.strategy.htf_timeframe, "value") else str(s0)), 4)
_warm_d = 200 * _htf_h / 24.0
print("  ISINMA: engine htf=%s icin 200 bar sart -> %.1f gun" % (htf_probe, _warm_d))
if _win_d <= _warm_d:
    print("  !! UYARI: pencere isinmadan kisa — HICBIR sinyal uretilemez.")
else:
    print("  -> gercek tarama penceresi ~%.1f gun (%.0f%% isinmaya gidiyor)"
          % (_win_d - _warm_d, 100.0 * _warm_d / _win_d))

# ── 3. WIRING ─────────────────────────────────────────────────────
sec("[3] Motor kurulumu (regression fixture sablonu)")
from futuresbot.sim.bridge import (AdapterConfig, EventBarStore, StrategyAdapter,
                                   _infer_source_path)
from futuresbot.sim.clock import SimClock
from futuresbot.sim.exchange import SimExchange, SimExchangeConfig
from futuresbot.sim.runner import SimRunner
from futuresbot.sim.metrics import compute_fold_metrics
from futuresbot.strategy.btc_filter import BTCDirectionFilter
from futuresbot.strategy.correlation_filter import CorrelationFilter
from futuresbot.strategy.engine import StrategyEngine
from futuresbot.strategy.validation import SignalValidator

s = settings.strategy
htf = s.htf_timeframe.value if hasattr(s.htf_timeframe, "value") else str(s.htf_timeframe)
mtf = s.mtf_timeframe.value if hasattr(s.mtf_timeframe, "value") else str(s.mtf_timeframe)
# 2026-09-01: canli bootstrap.py:517 candle_buffer_size=1500 kullaniyor.
# 5000, canlinin sema tavaninin (settings.py:512 le=3000) bile ustundeydi.
# Olculdu: 1500 -> 1.78x hiz VE farkli islem kumesi (buf5000 5 momentum-long
# islemi gormuyordu, ort -0.68 R). Sim kendini kayiriyordu.
_CBUF = int(os.environ.get("SIM_CANDLE_BUF", "1500"))
print("  MUM TAMPONU: max_size=%d  (canli=1500)" % _CBUF)
bar_store = EventBarStore(max_size=_CBUF)
_exit_ladder = ()
if getattr(A, "exit_ladder", ""):
    _exit_ladder = tuple(
        (float(x.split(",")[0]), float(x.split(",")[1]))
        for x in A.exit_ladder.split("/") if x.strip()
    )
    print("  KADEMELI CIKIS: %s" % (_exit_ladder,))
# ── Maliyet modeli (madde 6): SIM_COST_MODEL=taker (varsayilan, bayt-aynı) | live (E73/E76 olcumu)
_COST = (getattr(A, "cost_model", "") or os.environ.get("SIM_COST_MODEL", "taker")).strip().lower()
if _COST not in ("taker", "live"):
    raise SystemExit("SIM_COST_MODEL taker|live olmali, alinan: %r" % _COST)
if _COST == "live":
    print("  MALIYET MODELI: live (giris/TP %.4f%% maker-ish, SL %.2f%% taker; slip giris 2 / SL 8 / TP 0 bps)"
          % (0.0242, 0.04))
exchange = SimExchange(SimExchangeConfig(
    fee_rate_taker=0.0004, slippage_bps=2.0, initial_balance=A.balance,
    exit_ladder=_exit_ladder, cost_model=_COST))
# ── strategy_selector (bootstrap.py:714 — KOSULSUZ, canlida hep var) ──
_strategy_selector = None
if not A.no_selector:
    try:
        from futuresbot.strategy.selector import StrategySelector, StrategyMode
        _strategy_selector = StrategySelector(StrategyMode.MOMENTUM)
        print("  STRATEGY_SELECTOR: StrategySelector(MOMENTUM)")
    except Exception as _se:
        print("  !! STRATEGY_SELECTOR kurulamadi: %r" % (_se,))
else:
    print("  STRATEGY_SELECTOR: kapali")

# ── per_symbol_loader (bootstrap.py:910-923 ile ayni mantik) ──────
def _psc_env_bool(name, default):
    raw = env.get(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")

per_symbol_loader = None
if not A.no_persym and _psc_env_bool("PER_SYMBOL_CALIBRATION_ENABLED", True):
    _psc_path = env.get("PER_SYMBOL_CALIBRATION_YAML_PATH",
                        "config/per_symbol_calibration.yaml")
    try:
        from futuresbot.config.per_symbol_loader import PerSymbolCalibrationLoader
        per_symbol_loader = PerSymbolCalibrationLoader(_psc_path)
        _ps = per_symbol_loader.diagnostic_summary()
        print("  PER_SYMBOL_LOADER: %s | yuklendi=%s | sembol=%s" % (
            _ps["yaml_path"], _ps["loaded"], _ps["yaml_symbol_count"]))
        print("     kademeler: proven=%s unverified=%s overfit=%s global=%s"
              " | kapali long=%s short=%s" % (
              _ps["tiers"]["per_symbol_proven"], _ps["tiers"]["per_symbol_unverified"],
              _ps["tiers"]["per_symbol_overfit"], _ps["tiers"]["global"],
              _ps["disabled"]["long"], _ps["disabled"]["short"]))
        if _ps.get("load_error"):
            print("     !! load_error: %s" % _ps["load_error"])
    except Exception as _pe:
        print("  !! PER_SYMBOL_LOADER kurulamadi: %r" % (_pe,))
        per_symbol_loader = None
else:
    print("  PER_SYMBOL_LOADER: kapali")

def _fng_filtre_kur(_s, _a):
    if not getattr(_a, "fng_csv", None):
        return None
    from futuresbot.backtest.news_adapter import (BacktestNewsFilter,
                                                  HistoricalFearGreedProvider)
    if hasattr(_s.news, "model_copy"):
        _ns = _s.news.model_copy(update={"enabled": True})
    else:
        _ns = _s.news.copy(update={"enabled": True})
    _f = BacktestNewsFilter(_ns, HistoricalFearGreedProvider(_a.fng_csv))
    print("  FNG filtre: ACIK (%s)" % _a.fng_csv)
    return _f

_btcf = BTCDirectionFilter(bar_store, htf, mtf)
engine = StrategyEngine(
    settings=s, candle_cache=bar_store,
    btc_filter=_btcf,
    correlation_filter=CorrelationFilter(settings.risk.max_correlated_exposure),
    signal_validator=SignalValidator(settings.risk),
    news_filter=_fng_filtre_kur(settings, A), funding_oi=None,
    strategy_selector=_strategy_selector,
    regime_router=None, market_context_provider=None,
    per_symbol_loader=per_symbol_loader,
)
_GATES = {"off": True, "min_adx": 0.0, "short_btc": False,
          "btc_min": 0.0, "btc_max": 0.0, "btc": None, "counts": {}}

# --- sinyal teshis proxy'si: neden actionable degil? ---
class _Probe:
    """StrategyEngine'i sarar, her scan_symbol sonucunu kaydeder."""
    def __init__(self, inner):
        self._inner = inner
        self.n = 0
        self.none_count = 0
        self.reasons = {}
        self.dir_counts = {}
        self.exec_allowed = 0
        self.no_sl = 0
        self.no_tp = 0
        self.regimes = {}
        self.active_strats = {}
        self.samples = []
        self.warmup = 0          # "yeterli mum yok" ile donen scan sayisi
        self.first_real_n = None # ilk isinma-disi scan'in sirasi
        self._t0 = time.monotonic()
        self._hb = 5000          # her N scan'de bir ilerleme satiri
        self.last_sig = {}       # symbol -> son TradeSignal (source_path icin)
        self.retest_pending = {} # E120: symbol -> bekleyen giris
        self.retest_stats = {}
    def __getattr__(self, k):
        return getattr(self._inner, k)
    def _rt_count(self, k):
        self.retest_stats[k] = self.retest_stats.get(k, 0) + 1
    def scan_symbol(self, *a, **kw):
        sig = self._inner.scan_symbol(*a, **kw)
        # ── E120: RETEST GIRISI (--entry-retest) ─────────────────────
        if getattr(A, "entry_retest", False):
            try:
                _sym = a[0] if a else kw.get("symbol")
                _pend = self.retest_pending.get(_sym)
                _fresh = None
                if sig is not None and getattr(sig, "is_actionable", False):
                    _pth = (_infer_source_path(sig) or "").lower()
                    if _pth in {x.strip().lower() for x in A.retest_paths.split(",") if x.strip()}:
                        _fresh = sig
                if _pend is not None:
                    _pend["bars"] += 1
                    _df15 = bar_store.get_closed_candles(_sym, adapter._cfg.scan_timeframe if 'adapter' in globals() else "15m")
                    _bar = _df15.iloc[-1] if _df15 is not None and len(_df15) else None
                    _tf = int(getattr(A, "retest_tf", 15) or 15)
                    _conf_ok = True
                    if _bar is not None and _tf == 60:
                        # 1h teyit: yalniz 1h bari bu 15m ile kapandiysa (close_time esit) degerlendir; teyit o,c 1h barindan, touch 15m low'undan
                        _df1h = bar_store.get_closed_candles(_sym, "1h")
                        _b1h = _df1h.iloc[-1] if _df1h is not None and len(_df1h) else None
                        if _b1h is None or int(_b1h["close_time"]) != int(_bar["close_time"]):
                            _conf_ok = False
                    if _bar is not None:
                        _long = _pend["long"]; _v2 = _pend["v2_sl"]
                        # degerlendirilecek alt-barlar: 5m -> 1m'den 3 alt-bar; 15m -> tek bar; 60m -> 1h bari (o,c) + 15m (h,l)
                        _subs = []
                        if _tf == 5:
                            _df1 = bar_store.get_closed_candles(_sym, "1m")
                            if _df1 is not None and len(_df1) >= 15:
                                _o1 = _df1["open"].to_numpy(float)[-15:]; _h1 = _df1["high"].to_numpy(float)[-15:]
                                _l1 = _df1["low"].to_numpy(float)[-15:]; _c1 = _df1["close"].to_numpy(float)[-15:]
                                for k in range(3):
                                    _subs.append((_o1[k * 5], _h1[k * 5:k * 5 + 5].max(), _l1[k * 5:k * 5 + 5].min(), _c1[k * 5 + 4]))
                            else:
                                _subs.append((float(_bar["open"]), float(_bar["high"]), float(_bar["low"]), float(_bar["close"])))
                        elif _tf == 60 and _conf_ok:
                            _subs.append((float(_b1h["open"]), float(_bar["high"]), float(_bar["low"]), float(_b1h["close"])))
                        else:
                            _subs.append((float(_bar["open"]), float(_bar["high"]), float(_bar["low"]), float(_bar["close"])))
                        _sonuc = None
                        _touch = _pend.get("touch", _pend["lvl"]); _cancl = _pend.get("cancel_close", 0.0)
                        for (_op, _hi, _lo, _cl) in _subs:
                            if (_v2 > 0) and ((_lo <= _v2) if _long else (_hi >= _v2)):
                                _sonuc = "kirildi"; break
                            if _cancl > 0 and ((_cl < _cancl) if _long else (_cl > _cancl)):
                                _sonuc = "kirildi"; self._rt_count("retest_kapanis_iptal"); break
                            if (_lo <= _touch) if _long else (_hi >= _touch):
                                _pend["touched"] = True
                            if _conf_ok and _pend["touched"] and ((_cl > _op and _cl >= _pend["lvl"]) if _long else (_cl < _op and _cl <= _pend["lvl"])):
                                _sonuc = ("giris", _cl); break
                        if _sonuc == "kirildi":
                            del self.retest_pending[_sym]; self._rt_count("retest_kirildi"); _pend = None
                        elif isinstance(_sonuc, tuple):
                            _s = _pend["sig"]
                            from decimal import Decimal as _Dz
                            _s.entry_zone = (_Dz(str(_sonuc[1])), _Dz(str(_sonuc[1])))
                            del self.retest_pending[_sym]; self._rt_count("retest_giris")
                            sig = _s; _fresh = None; _pend = None
                        elif _pend["bars"] > int(A.retest_bars):
                            del self.retest_pending[_sym]; self._rt_count("retest_zaman_asimi"); _pend = None
                if _fresh is not None:
                    # yeni aksiyon sinyali: bekleyen giris olarak kaydet (varsa yenile), simdi girme
                    _p0 = float(_fresh.entry_price)
                    _long = str(getattr(_fresh.direction, "value", _fresh.direction)).lower().startswith("l")
                    _X = float(A.retest_pct) / 100.0
                    if float(getattr(A, "retest_atr", 0) or 0) > 0:
                        try:
                            _dfa = bar_store.get_closed_candles(_sym, "15m")
                            _h = _dfa["high"].to_numpy(float)[-15:]; _l = _dfa["low"].to_numpy(float)[-15:]; _c = _dfa["close"].to_numpy(float)[-15:]
                            _trs = [max(_h[i] - _l[i], abs(_h[i] - _c[i - 1]), abs(_l[i] - _c[i - 1])) for i in range(1, len(_h))]
                            _atrp = (sum(_trs) / len(_trs)) / _p0 if _trs and _p0 > 0 else 0.0
                            if _atrp > 0: _X = float(A.retest_atr) * _atrp
                        except Exception:
                            pass
                    _srd = (getattr(_fresh, "indicators_snapshot", None) or {}).get("sr_decision") or {}
                    try: _v2 = float(_srd.get("v2_sl") or 0)
                    except Exception: _v2 = 0.0
                    _lvl = _p0 * (1 - _X) if _long else _p0 * (1 + _X); _touch = _lvl; _cancl = 0.0
                    if str(getattr(A, "retest_level", "signal")) == "anchor":
                        try: _anc = float((_srd.get("v2_anchor_support") if _long else _srd.get("v2_anchor_resistance")) or 0)
                        except Exception: _anc = 0.0
                        if not (_anc > 0 and ((_anc < _p0) if _long else (_anc > _p0))):
                            # A+ cipa yoksa: stopun asildigi en yakin seviye (sr_decision 'graceful degradation' ile ayni kaynak)
                            try: _anc = float((_srd.get("nearest_support") if _long else _srd.get("nearest_resistance")) or 0)
                            except Exception: _anc = 0.0
                            if _anc > 0: self._rt_count("retest_cipa_nearest")
                        _tol = float(getattr(A, "retest_tol", 0.2) or 0) / 100.0
                        if _anc > 0 and ((_anc < _p0) if _long else (_anc > _p0)):
                            _lvl = _anc; _touch = _anc * (1 + _tol) if _long else _anc * (1 - _tol)
                            if str(getattr(A, "retest_cancel", "sl")) == "close":
                                _cancl = _anc * (1 - _tol) if _long else _anc * (1 + _tol)
                        else:
                            self._rt_count("retest_cipa_yok")
                    self.retest_pending[_sym] = {"sig": _fresh, "p0": _p0, "lvl": _lvl, "touch": _touch, "cancel_close": _cancl,
                                                 "long": _long, "v2_sl": _v2, "bars": 0, "touched": False}
                    self._rt_count("retest_beklemeye_alindi" if _pend is None else "retest_yenilendi")
                    sig = None
                elif _pend is not None:
                    sig = None   # beklerken engine'in yeni-olmayan sinyali gecmesin
            except Exception as _rte:
                self._rt_count("retest_exc")
        self.n += 1
        if self.n % self._hb == 0:
            el = time.monotonic() - self._t0
            print("    ... scan %d | %.0fs | isinma %d | actionable %d"
                  % (self.n, el, self.warmup, self.exec_allowed), flush=True)
        if sig is None:
            self.none_count += 1
            return sig
        _rr = [str(x) for x in (getattr(sig, "rejection_reasons", None) or [])]
        if any(("yeterli" in x or "Yetersiz" in x) for x in _rr):
            self.warmup += 1
            return sig
        if self.first_real_n is None:
            self.first_real_n = self.n
        try: self.last_sig[getattr(sig, "symbol", "?")] = sig
        except Exception: pass
        d_ = str(getattr(getattr(sig, "direction", None), "value", getattr(sig, "direction", "?")))
        # ── bootstrap GIRIS KAPILARI (app/bootstrap.py:1856-1893) ──
        # Bu kapilar StrategyEngine'de DEGIL, bootstrap'in tarama dongusunde
        # yasiyor; sim o donguyu kosturmadigi icin bugune kadar hic
        # uygulanmadi. Canli 27-31 Agu verisi: ADX<30 ile ACILAN islem %0,
        # oysa execution_allowed=1 sinyallerin %65'i ADX<30. Kapisiz sim,
        # canlinin bloke ettigi islemleri de olcuyordu.
        if not _GATES["off"] and getattr(sig, "execution_allowed", False):
            _snap = getattr(sig, "indicators_snapshot", None) or {}
            _blocked = None
            # 1) MIN_ENTRY_ADX  (bootstrap.py:1856-1868)
            _ma = float(_GATES["min_adx"] or 0)
            if _ma > 0:
                _av = _snap.get("adx")
                if _av is not None and float(_av) < _ma:
                    _blocked = "adx_gate"
            # 2) SHORT BTC bandi  (bootstrap.py:1869-1893)
            if _blocked is None and _GATES["short_btc"] and d_ == "short":
                _sc = None
                try:
                    _lb = getattr(_GATES["btc"], "last_bias", None)
                    if _lb is not None and getattr(_lb, "score", None) is not None:
                        _sc = float(_lb.score)
                except Exception:
                    _sc = None
                if _sc is not None and not (_GATES["btc_min"] < _sc <= _GATES["btc_max"]):
                    _blocked = "short_btc_band"
            if _blocked:
                _GATES["counts"][_blocked] = _GATES["counts"].get(_blocked, 0) + 1
                try:
                    sig.execution_allowed = False
                    sig.rejection_reasons.append("bootstrap kapisi: %s" % _blocked)
                except Exception:
                    pass
        d = str(getattr(getattr(sig, "direction", None), "value", getattr(sig, "direction", "?")))
        self.dir_counts[d] = self.dir_counts.get(d, 0) + 1
        if getattr(sig, "execution_allowed", False):
            self.exec_allowed += 1
        try:
            if not (getattr(sig, "stop_loss", 0) or 0) > 0: self.no_sl += 1
        except Exception: self.no_sl += 1
        if not (getattr(sig, "take_profit_levels", None) or []): self.no_tp += 1
        for r in (getattr(sig, "rejection_reasons", None) or []):
            r = str(r)[:70]
            self.reasons[r] = self.reasons.get(r, 0) + 1
        snap = getattr(sig, "indicators_snapshot", None) or {}
        _mr_ = getattr(sig, "market_regime", None)
        rg = str(getattr(_mr_, "value", _mr_) or "-")
        self.regimes[rg] = self.regimes.get(rg, 0) + 1
        ast = str(snap.get("active_strategy") or "-")
        self.active_strats[ast] = self.active_strats.get(ast, 0) + 1
        if len(self.samples) < 3:
            self.samples.append({
                "symbol": getattr(sig, "symbol", "?"), "direction": d,
                "execution_allowed": getattr(sig, "execution_allowed", None),
                "stop_loss": str(getattr(sig, "stop_loss", None)),
                "tps": len(getattr(sig, "take_profit_levels", None) or []),
                "confidence": str(getattr(sig, "confidence_score", None)),
                "reasons": [str(x)[:60] for x in (getattr(sig, "rejection_reasons", None) or [])][:4],
                "snapshot_keys": sorted(snap.keys())[:14],
            })
        return sig

if not A.no_gates:
    _GATES.update({
        "off": False,
        "min_adx": float(getattr(settings.risk, "min_entry_adx", 0.0) or 0),
        "short_btc": bool(getattr(settings.risk, "short_btc_gate_enabled", False)),
        "btc_min": float(getattr(settings.risk, "short_btc_min", 0.0)),
        "btc_max": float(getattr(settings.risk, "short_btc_max", 0.0)),
        "btc": _btcf,
    })
    print("  GIRIS KAPILARI: ACIK | MIN_ENTRY_ADX=%s | SHORT_BTC_GATE=%s bant (%s,%s]"
          % (_GATES["min_adx"], _GATES["short_btc"], _GATES["btc_min"], _GATES["btc_max"]))
    print("     (bootstrap.py:1856-1893 aynasi. spread_gate ve risk_blocked HALA YOK.)")
else:
    print("  GIRIS KAPILARI: kapali (--no-gates)")

probe = _Probe(engine)
adapter = StrategyAdapter(
    strategy=probe, exchange=exchange, bar_store=bar_store,
    config=AdapterConfig(
        scan_symbols=list(syms), scan_timeframe=s.ltf_timeframe.value
        if hasattr(s.ltf_timeframe, "value") else "15m",
        max_open_positions=settings.risk.max_concurrent_positions,
        risk_per_trade_fraction=A.risk_frac,
        min_notional=5.0,
        min_confluence=None, atr_sl_floor_mult=0.0, atr_lookback=14,
    ),
)
# --- yol etiketini pozisyona baglama (iki ince sarmalayici) ---
_path_by_order = {}
_path_by_pos = {}
_sr_by_order = {}   # order_id -> (v2_reason, yapisal_mesafe_bandi)
_sr_by_pos = {}     # position_id -> ayni
# CIPA TUTTU/KIRILDI (canli bulgu: +0.3547 R tuttu / -0.9435 R kirildi, n=279/202)
_anchor_by_order = {}   # order_id -> (capa_fiyati, is_long)
_anchor_by_pos = {}     # position_id -> ayni
_ext_by_pos = {}        # position_id -> [islem boyunca min_low, max_high] (1m)
_SIM_SAAT = {"ns": 0}   # son 1m bar kapanisi (izleme listesi gun secimi icin)
_PF_REF = {"pf": None}   # sizer blogu kurulduktan sonra doldurulur
_meta_by_pos = {}        # position_id -> (rejim, yol)
_orig_submit = exchange.submit_market_entry
def _submit_tagged(**kw):
    oid = _orig_submit(**kw)
    # source_path motorun TradeSignal'inde YOK; adapter onu TradeSignalLike'a
    # cevirirken _infer_source_path ile uretiyor (bridge.py:795). Ayni
    # fonksiyonu ayni girdiyle biz cagiriyoruz.
    _sg = probe.last_sig.get(kw.get("symbol"))
    _path_by_order[oid] = _infer_source_path(_sg) if _sg is not None else "?"
    # SR karari: capa gerekcesi + yapisal stop mesafesi bandi
    _rz, _bd = "-", "-"
    if _sg is not None:
        _srd = (getattr(_sg, "indicators_snapshot", None) or {}).get("sr_decision") or {}
        _rz = str(_srd.get("v2_reason") or "-")
        try:
            _isl_a = str(getattr(_sg.direction, "value", _sg.direction)).lower().startswith("l")
            _a_px = _srd.get("v2_anchor_support") if _isl_a else _srd.get("v2_anchor_resistance")
            if _a_px:
                _anchor_by_order[oid] = (float(_a_px), _isl_a)
        except Exception:
            pass
        _v2, _ep = _srd.get("v2_sl"), float(getattr(_sg, "entry_price", 0) or 0)
        if _v2 and _ep > 0:
            try:
                _p = abs(float(_v2) - _ep) / _ep * 100.0
                _bd = ("<0.8%" if _p < 0.8 else "0.8-1.5%" if _p < 1.5
                       else "1.5-2.5%" if _p < 2.5 else "2.5-10%" if _p <= 10 else ">10%(bozuk)")
            except Exception:
                _bd = "-"
        else:
            _bd = "v2_sl_yok"
    _sr_by_order[oid] = (_rz, _bd)
    return oid
exchange.submit_market_entry = _submit_tagged
_orig_fill = exchange._fill_entry
def _fill_tagged(order, event):
    f = _orig_fill(order, event)
    try:
        _pos = exchange._positions[-1]
        _pid = _pos.position_id
        _path_by_pos[_pid] = _path_by_order.get(order.order_id, "?")
        _sr_by_pos[_pid] = _sr_by_order.get(order.order_id, ("-", "-"))
        _anchor_by_pos[_pid] = _anchor_by_order.get(order.order_id)
        if _last_risk["amt"]: _risk_by_pos[_pid] = _last_risk["amt"]
        _meta_by_pos[_pid] = (_last_risk.get("regime", "-"), _last_risk.get("path", "-"))
        if _PF_REF["pf"] is not None:
            from decimal import Decimal as _D2
            _PF_REF["pf"].on_fill(
                _pos.symbol, qty=_D2(str(_pos.qty)),
                side=("LONG" if str(getattr(_pos.direction, "value", _pos.direction)).lower()
                      in ("long", "buy") else "SHORT"),
                entry_price=_D2(str(_pos.entry_px)))
    except Exception: pass
    return f
exchange._fill_entry = _fill_tagged
_orig_obc = exchange.on_bar_close
def _obc_tracked(event):
    # capa testi icin fiyat yolu: pozisyon ACIKKEN gorulen 1m dip/tepe.
    # wrapper orijinalden ONCE kostugu icin giris barinin kendisi sayilmaz
    # (pozisyon o an henuz yok) — canlideki MAE/MFE olcumuyle ayni taraf.
    _SIM_SAAT["ns"] = event.close_ts_ns
    _nf = engine._news_filter if hasattr(engine, "_news_filter") else None
    if _nf is not None and hasattr(_nf, "set_time"):
        import datetime as _dtf
        _nf.set_time(_dtf.datetime.fromtimestamp(event.close_ts_ns / 1e9,
                                                 tz=_dtf.timezone.utc))
    try:
        for _p in exchange.open_positions(event.symbol):
            _e = _ext_by_pos.get(_p.position_id)
            if _e is None:
                _ext_by_pos[_p.position_id] = [event.low_px, event.high_px]
            else:
                if event.low_px < _e[0]: _e[0] = event.low_px
                if event.high_px > _e[1]: _e[1] = event.high_px
    except Exception:
        pass
    return _orig_obc(event)
exchange.on_bar_close = _obc_tracked

# ── 1. ADIM: GERCEK PositionSizer'i sim'e bagla ───────────────────
# bridge.py:26-28 "production swaps in the real PositionSizer" diyor ama
# StrategyAdapter(sizer=) kancasi hic doldurulmamis; sim bugune kadar
# duman-testi sizer'iyla kostu. Cagri alanlari canlidaki
# app/bootstrap.py:1592 ile BIREBIR ayni.
# izleme listesi verisi (None = kapali; set = sabit; dict[gun]=set = gunluk)
_WL = None
if A.watchlist_json:
    import json as _j
    _WL = set(_j.load(open(A.watchlist_json)).get("symbols", []))
    print("  IZLEME LISTESI: sabit, %d sembol (%s)" % (len(_WL), A.watchlist_json))
elif A.watchlist_daily:
    import json as _j, re as _re
    _WL = {}
    for _f in sorted(Path(A.watchlist_daily).glob("watchlist.json.bak_forager_*")):
        _m = _re.search(r"bak_forager_(\d{8})_", _f.name)
        if _m:
            _WL[_m.group(1)] = set(_j.load(open(_f)).get("symbols", []))
    print("  IZLEME LISTESI: gunluk, %d gun (%s)" % (len(_WL), A.watchlist_daily))
_NO_TRADE = {x.strip().upper() for x in (getattr(A, "no_trade_symbols", "") or "").split(",") if x.strip()}
if _NO_TRADE:
    print("  BAGLAM-ONLY (islem yok): %s" % sorted(_NO_TRADE))
# ── E102: kalabalik takvimi (epoch-dakika, sirali) ──
_CROWD = None
if getattr(A, "crowd_calendar", ""):
    import glob as _gl, csv as _csvm, datetime as _dtm
    _mins = []
    _files = []
    for _pat in A.crowd_calendar.split(","):
        _pat = _pat.strip()
        if _pat:
            _files += sorted(_gl.glob(_pat)) or [_pat]
    for _f in _files:
        with open(_f, newline="", encoding="utf-8") as _cf:
            for _row in _csvm.DictReader(_cf):
                _ou = (_row.get("opened_utc") or "").strip()
                if not _ou:
                    continue
                try:
                    _dtv = _dtm.datetime.strptime(_ou[:16], "%Y-%m-%d %H:%M").replace(tzinfo=_dtm.timezone.utc)
                    _mins.append(int(_dtv.timestamp() // 60))
                except Exception:
                    continue
    _mins.sort()
    _CROWD = _mins
    print("  KALABALIK KAPISI: takvim %d giris (%d dosya) | N>=%d / %d dk" % (len(_mins), len(_files), A.crowd_n, A.crowd_min))

_sz = {"call":0,"no_raw":0,"exc":0,"invalid":0,"zero":0,"ok":0,"sl_capped":0}
_sz_reasons = {}
_risk_by_pos = {}
_last_risk = {"amt": None}
if not A.toy_sizer:
    from decimal import Decimal as _D
    import math as _math
    from futuresbot.risk.sizing import PositionSizer as _PS
    from futuresbot.exchange.metadata import SymbolInfo as _SI
    from futuresbot.sim.bridge import SizedOrder as _SO
    _psz = _PS(settings.risk, strategy_settings=settings.strategy)
    # ── Portfolio ledger (P-008/P-009) ────────────────────────────
    # Canlida en aktif kapilardan biri: 9 gunde portfolio_clamped 2017,
    # portfolio_exhausted 1136. sim'de yoktu.
    _pf = None
    _pfs = getattr(settings, "portfolio", None)
    if not A.no_portfolio and _pfs is not None and getattr(_pfs, "enabled", False):
        try:
            from futuresbot.strategy.portfolio import Portfolio as _PF
            _pf = _PF(
                symbols=list(syms),
                max_total_exposure_pct=float(_pfs.max_total_exposure_pct),
                max_open_positions=int(getattr(_pfs, "max_open_positions",
                                               settings.risk.max_concurrent_positions)),
                per_symbol_cap_pct=float(getattr(_pfs, "per_symbol_cap_pct", 0.10)),
                idm_enabled=bool(getattr(_pfs, "idm_enabled", True)),
                idm_cap=float(getattr(_pfs, "idm_cap", 2.5)),
                idm_use_correlation=bool(getattr(_pfs, "idm_use_correlation", False)),
            )
            _psz.set_portfolio(_pf)
            print("  PORTFOLIO: acik | max_exposure=%.2f max_pos=%d per_symbol_cap=%.2f"
                  " idm=%s(cap %.2f)" % (
                  _pf.max_total_exposure_pct, _pf.max_open_positions,
                  _pf.per_symbol_cap_pct, _pf.idm_enabled, _pf.idm_cap))
        except Exception as _pfe:
            print("  !! PORTFOLIO kurulamadi: %r" % (_pfe,))
            _pf = None
    else:
        print("  PORTFOLIO: kapali")
    _LEV = int(settings.exchange.default_leverage)
    _si_cache = {}
    def _sym_info(sym, px):
        si = _si_cache.get(sym)
        if si is None:
            _e = int(_math.floor(_math.log10(max(float(px), 1e-9))))
            _pp = max(0, min(8, 5 - _e)); _qp = max(0, min(8, 2 + _e))
            si = _SI(symbol=sym, base_asset=sym[:-4], quote_asset="USDT",
                     price_precision=_pp, quantity_precision=_qp,
                     tick_size=_D(1).scaleb(-_pp), step_size=_D(1).scaleb(-_qp),
                     min_qty=_D("0"), max_qty=_D("1000000000"), min_notional=_D("5"))
            _si_cache[sym] = si
        return si
    def _real_sizer(sig, equity):
        _sz["call"] += 1
        raw = probe.last_sig.get(sig.symbol)
        if raw is None:
            _sz["no_raw"] += 1; return None
        # ── BAGLAM-ONLY SEMBOL (--no-trade-symbols, E89) ──────────
        if _NO_TRADE and sig.symbol in _NO_TRADE:
            _sz["baglam_only_red"] = _sz.get("baglam_only_red", 0) + 1
            return None
        # ── IZLEME LISTESI KAPISI (--watchlist-json / --watchlist-daily) ──
        if _WL is not None:
            import datetime as _dt
            _gun = _dt.datetime.fromtimestamp(
                _SIM_SAAT["ns"] / 1e9, tz=_dt.timezone.utc
            ).strftime("%Y%m%d") if _SIM_SAAT["ns"] else None
            _liste = _WL.get(_gun) if isinstance(_WL, dict) else _WL
            if _liste is None and isinstance(_WL, dict) and _WL:
                _k = sorted(_WL)
                _liste = _WL[_k[0]] if (_gun or "") < _k[0] else _WL[_k[-1]]
            if _liste is not None and sig.symbol not in _liste:
                _sz["liste_disi_red"] = _sz.get("liste_disi_red", 0) + 1
                return None
        # ── MR CIPA-MESAFE TABANI (--mr-anchor-min-pct) ───────────
        _mrX = float(getattr(A, "mr_anchor_min_pct", 0.0) or 0.0)
        if _mrX > 0:
            try:
                if _infer_source_path(raw) == "mr":
                    _srdm = (getattr(raw, "indicators_snapshot", None) or {}).get("sr_decision") or {}
                    _lngm = str(getattr(raw.direction, "value", raw.direction)).lower().startswith("l")
                    _am = _srdm.get("v2_anchor_support") if _lngm else _srdm.get("v2_anchor_resistance")
                    _epm = float(getattr(raw, "entry_price", 0) or 0)
                    if _am and _epm > 0:
                        _dm = abs(float(_am) - _epm) / _epm * 100.0
                        if _dm < _mrX:
                            _sz["mr_cipa_yakin_red"] = _sz.get("mr_cipa_yakin_red", 0) + 1
                            return None
            except Exception:
                pass
        # ── BTC SERT KAPI (--btc-hard-gate): guclu ayi rejiminde hic girme ──
        _bhg = getattr(A, "btc_hard_gate", None)
        if _bhg is not None:
            try:
                _bscore = _btcf.get_btc_bias().score
                if _bscore is not None and _bscore < _bhg:
                    _sz["btc_sert_kapi_red"] = _sz.get("btc_sert_kapi_red", 0) + 1
                    return None
            except Exception:
                pass
        # ── DOWN-REJIM KAPISI (--block-down-regimes) ──────────────
        if getattr(A, "block_down_regimes", False):
            _rgx = str(getattr(raw.market_regime, "value", raw.market_regime) or "")
            if _rgx in ("early_reversal_down", "trending_down", "weakening_down"):
                _dss_ok = False
                # ── E103: yapisal dusus-short izni (--down-short-structural) ──
                if getattr(A, "down_short_structural", False):
                    try:
                        _isl_d = str(getattr(raw.direction, "value", raw.direction)).lower().startswith("l")
                        if not _isl_d:
                            _srdd = (getattr(raw, "indicators_snapshot", None) or {}).get("sr_decision") or {}
                            _epd = float(getattr(raw, "entry_price", 0) or 0)
                            _v2d = _srdd.get("v2_sl"); _and = _srdd.get("v2_anchor_resistance")
                            if _v2d and _and and _epd > 0:
                                _sdp = abs(float(_v2d) - _epd) / _epd * 100.0
                                _adp = abs(float(_and) - _epd) / _epd * 100.0
                                if (_sdp >= float(A.dss_srdist_min) and _sdp <= 10.0
                                        and _adp >= float(A.dss_anchor_min)):
                                    _dss_ok = True
                    except Exception:
                        _dss_ok = False
                if _dss_ok:
                    _sz["dss_izin"] = _sz.get("dss_izin", 0) + 1
                else:
                    _sz["down_rejim_red"] = _sz.get("down_rejim_red", 0) + 1
                    return None
        # ── E109 / madde 20: UST-TF YON FILTRESI (--htf-align) ──────
        _htf_ok_path = True
        if getattr(A, "htf_align", False) and (getattr(A, "htf_paths", "") or "").strip():
            try:
                _hp = {x.strip().lower() for x in A.htf_paths.split(",") if x.strip()}
                _htf_ok_path = (_infer_source_path(raw) or "").lower() in _hp
            except Exception:
                _htf_ok_path = True
            if not _htf_ok_path:
                _sz["htf_yol_disi"] = _sz.get("htf_yol_disi", 0) + 1
        if getattr(A, "htf_align", False) and _htf_ok_path:
            try:
                _df4 = bar_store.get_closed_candles(sig.symbol, "4h")
                _isl_h = str(getattr(raw.direction, "value", raw.direction)).lower().startswith("l")
                _n_ema, _n_sl = int(A.htf_ema), int(A.htf_slope)
                if _df4 is not None and len(_df4) >= 6 * (_n_ema + _n_sl + 2):
                    _cl = _df4["close"].to_numpy(dtype=float)
                    _nd = len(_cl) // 6
                    _dcl = _cl[len(_cl) - _nd * 6:].reshape(_nd, 6)[:, -1]   # gunluk kapanis = her 6. 4h bar
                    _k = 2.0 / (_n_ema + 1.0)
                    _e = _dcl[0]
                    _ema = []
                    for _v in _dcl:
                        _e = _v * _k + _e * (1.0 - _k)
                        _ema.append(_e)
                    _yukari = (_dcl[-1] > _ema[-1]) and (_ema[-1] > _ema[-1 - _n_sl])
                    _asagi = (_dcl[-1] < _ema[-1]) and (_ema[-1] < _ema[-1 - _n_sl])
                    if (_isl_h and not _yukari) or ((not _isl_h) and not _asagi):
                        _sz["htf_yon_red"] = _sz.get("htf_yon_red", 0) + 1
                        return None
                    _sz["htf_yon_gecti"] = _sz.get("htf_yon_gecti", 0) + 1
                else:
                    _sz["htf_veri_yok"] = _sz.get("htf_veri_yok", 0) + 1
            except Exception as _hexc:
                _sz["htf_exc"] = _sz.get("htf_exc", 0) + 1
        # ── E102: KALABALIK KAPISI (--crowd-calendar) ──────────────
        if _CROWD is not None and _SIM_SAAT["ns"]:
            from bisect import bisect_left as _bl
            _tm = int(_SIM_SAAT["ns"] // 60_000_000_000)
            _cnt = _bl(_CROWD, _tm) - _bl(_CROWD, _tm - int(A.crowd_min))
            if _cnt >= int(A.crowd_n):
                _sz["kalabalik_red"] = _sz.get("kalabalik_red", 0) + 1
                return None
            _sz["kalabalik_gecti"] = _sz.get("kalabalik_gecti", 0) + 1
        # ── EK REJIM KAPISI (--block-regimes) — E34 ────────────────
        _brs = getattr(A, "block_regimes", "") or ""
        if _brs:
            _rgx2 = str(getattr(raw.market_regime, "value", raw.market_regime) or "")
            if _rgx2 in set(x.strip() for x in _brs.split(",") if x.strip()):
                _sz["ek_rejim_red"] = _sz.get("ek_rejim_red", 0) + 1
                return None
        _um = _D("0")
        for _op in exchange.open_positions():
            try: _um += (_D(str(_op.entry_px)) * _D(str(_op.qty))) / _D(str(_LEV))
            except Exception: pass
        # ── SR GEOMETRISI (--sr-geometry) ──────────────────────────
        # v2_sl motorun HER islemde hesapladigi yapisal stop; normalde
        # sizer'in %0.80 tavani onu eziyor (canli: %81.3). Burada onu
        # sizer'a ASIL stop olarak veriyoruz.
        _sl_in = raw.stop_loss
        _tp_in = raw.take_profit_levels
        if getattr(A, "sr_geometry", False):
            _srd = (raw.indicators_snapshot or {}).get("sr_decision") or {}
            _v2 = _srd.get("v2_sl")
            _ep = float(raw.entry_price)
            _islong = str(getattr(raw.direction, "value", raw.direction)).lower().startswith("l")
            if _v2 is None:
                _sz["sr_yok"] = _sz.get("sr_yok", 0) + 1
            else:
                try:
                    _v2f = float(_v2)
                except Exception:
                    _v2f = 0.0
                _d_pct = (abs(_v2f - _ep) / _ep * 100.0) if _ep > 0 else 1e9
                _dogru_taraf = (_v2f < _ep) if _islong else (_v2f > _ep)
                if _v2f <= 0 or not _dogru_taraf:
                    _sz["sr_ters_taraf"] = _sz.get("sr_ters_taraf", 0) + 1
                elif _d_pct > float(getattr(A, "sr_max_pct", 10.0)):
                    # canlida v2_sl'in %28332 oldugu satir gordumuz icin sart
                    _sz["sr_absurt"] = _sz.get("sr_absurt", 0) + 1
                else:
                    _v2k = _v2f
                    # ── SR TAMPONU (--sr-buffer-atr): stop cipanin K x ATR otesine ──
                    _K = float(getattr(A, "sr_buffer_atr", 0.0) or 0.0)
                    if _K > 0:
                        try:
                            _atrv = float(getattr(raw, "atr_value", 0) or 0)
                        except Exception:
                            _atrv = 0.0
                        if _atrv > 0:
                            _v2k = _v2f - _K * _atrv if _islong else _v2f + _K * _atrv
                            _dk = abs(_v2k - _ep) / _ep * 100.0
                            if _v2k <= 0 or _dk > float(getattr(A, "sr_max_pct", 10.0)):
                                _v2k = _v2f  # tampon absurte tasiyorsa tamponsuz kullan
                            else:
                                _sz["sr_tampon_kullanildi"] = _sz.get("sr_tampon_kullanildi", 0) + 1
                    _sl_in = _D(str(_v2k))
                    _sz["sr_sl_kullanildi"] = _sz.get("sr_sl_kullanildi", 0) + 1
                    _tps = [_srd.get("v2_tp1"), _srd.get("v2_tp2"), _srd.get("v2_tp3")]
                    _tps = [_D(str(float(t))) for t in _tps if t]
                    if _tps:
                        _tp_in = _tps
                        _sz["sr_tp_kullanildi"] = _sz.get("sr_tp_kullanildi", 0) + 1
        try:
            res = _psz.calculate(
                direction=raw.direction,
                entry=raw.entry_price,
                stop_loss=_sl_in,
                tp_targets=_tp_in,
                balance=_D(str(equity)),
                leverage=_LEV,
                symbol_info=_sym_info(sig.symbol, sig.entry_px),
                confidence_score=float(raw.confidence_score or 0.5),
                regime=getattr(raw.market_regime, "value", None),
                atr_value=getattr(raw, "atr_value", None),
                recent_high=getattr(raw, "recent_high_20", None),
                recent_low=getattr(raw, "recent_low_20", None),
                open_position_count=len(exchange.open_positions()),
                used_margin=_um,
                active_strategy=(raw.indicators_snapshot or {}).get("active_strategy"),
            )
        except Exception as _e:
            _sz["exc"] += 1
            if _sz["exc"] <= 3: print("    !! sizer istisnasi: %r" % (_e,), flush=True)
            return None
        if _sz["call"] <= 3:
            _tps = [str(x) for x in (raw.take_profit_levels or [])]
            _e = float(raw.entry_price); _s = float(raw.stop_loss)
            print("    [dbg#%d] %s %s entry=%.6f sl=%.6f (%.3f%%) tps=%s" % (
                _sz["call"], sig.symbol, getattr(raw.direction,"value",raw.direction),
                _e, _s, 100*abs(_e-_s)/_e if _e else 0, _tps), flush=True)
            print("            res.sl=%s res.tp1=%s res.tp2=%s res.tp3=%s eff_rr=%s qty=%s notional=%s" % (
                res.stop_loss, getattr(res,"tp1","?"), getattr(res,"tp2","?"),
                getattr(res,"tp3","?"), getattr(res,"effective_risk_reward","?"),
                res.quantity, getattr(res,"notional","?")), flush=True)
            print("            atr=%s rh20=%s rl20=%s regime=%s strat=%s" % (
                getattr(raw,"atr_value",None), getattr(raw,"recent_high_20",None),
                getattr(raw,"recent_low_20",None), getattr(raw.market_regime,"value",None),
                (raw.indicators_snapshot or {}).get("active_strategy")), flush=True)
        if not getattr(res, "is_valid", False):
            _sz["invalid"] += 1
            for _r in (getattr(res, "rejection_reasons", None) or ["(sebep yok)"]):
                _k = str(_r)[:70]
                _sz_reasons[_k] = _sz_reasons.get(_k, 0) + 1
            return None
        _q = float(res.quantity)
        if _q <= 0:
            _sz["zero"] += 1; return None
        _slp = float(res.stop_loss)
        if abs(_slp - float(raw.stop_loss)) > 1e-12: _sz["sl_capped"] += 1
        _sz["ok"] += 1
        _last_risk["amt"] = float(res.risk_amount)
        _last_risk["regime"] = str(getattr(raw.market_regime, "value", raw.market_regime) or "-")
        _last_risk["path"] = (raw.indicators_snapshot or {}).get("active_strategy") or "-"
        # TP sizer'in DONDURDUGU tp1 olmali — sig.take_profit motorun ham
        # TP'si; onu kullanmak hibrit merdivenini (sizing.py:138) ve rejim
        # TP kirpmasini (calculator.py:381) tamamen atlar. 2026-08-31:
        # EXIT_TP1_SL_MULT 1.5->3.0 A/B'si bu yuzden bit-bit ayni sonuc
        # vermisti (5/5 parca), yani hicbir sey olcmemisti.
        _tp = None
        try:
            _t1 = float(getattr(res, "tp1", 0) or 0)
            if _t1 > 0: _tp = _t1
        except Exception:
            _tp = None
        if _tp is None: _tp = sig.take_profit
        _sz["tp_from_sizer"] = _sz.get("tp_from_sizer", 0) + (1 if _tp != sig.take_profit else 0)
        return _SO(qty=_q, sl_px=_slp, tp_px=_tp)
    adapter._sizer = _real_sizer
    _PF_REF["pf"] = _pf
    if _pf is not None:
        _orig_close = exchange._close_position
        def _close_tagged(pos, exit_px, ts_ns, reason):
            f = _orig_close(pos, exit_px, ts_ns, reason)
            try: _pf.on_close(pos.symbol)
            except Exception: pass
            return f
        exchange._close_position = _close_tagged
    print("  SIZER: GERCEK risk/sizing.PositionSizer  (leverage=%d, SL tavani=%s%%, "
          "SL taban=%s%%, GLOBAL_SIZE_MULT=%s, risk_per_trade_pct=%s)" % (
          _LEV, settings.risk.sl_max_dist_pct, settings.risk.sl_min_dist_pct,
          settings.risk.global_size_multiplier, settings.risk.risk_per_trade_pct))
else:
    print("  SIZER: default_sizer (OYUNCAK — bridge.py:181, 1R=ozkaynagin %.1f%%'i)" % (100*A.risk_frac))

print("  scan_tf=%s  max_pos=%s  risk_frac=%.4f  balance=%.0f" % (
    adapter._cfg.scan_timeframe, adapter._cfg.max_open_positions,
    adapter._cfg.risk_per_trade_fraction, A.balance))
print("  CANLIDAN FARK (kalan): regime_router=None, market_context_provider=None")
print("     (canlida ikisi de kosullu: kill-switch + enforce bayraklari acikken kuruluyor)")
print("  !! BOYUTLANDIRMA UYARISI")
print("     sim/bridge.py:199  -> risk = OZKAYNAK * frac        (frac=%.4f)" % A.risk_frac)
print("     risk/sizing.py:477 -> risk = NOTIONAL * pct/100     (pct=%s) x GLOBAL_SIZE_MULT"
      % settings.risk.risk_per_trade_pct)
print("     Ayni isim, FARKLI anlam. Bu kosuda 1R = ozkaynagin %%%.1f'i." % (100*A.risk_frac))
print("     -> net%%/maxDD%% CANLI ILE KIYASLANAMAZ. Kiyaslanabilir olan: WR, PF, R-cinsi beklenti.")

# ── 4. KOSU ───────────────────────────────────────────────────────
sec("[4] Kosu")
clock = SimClock(start_ts_ns=start_ns, seed=42)
runner = SimRunner(clock=clock, handler=adapter)
t0 = time.monotonic()
res = runner.run(catalog.events(start_ns, end_ns))
el = time.monotonic() - t0
print("  %.1fs | islenen olay: %s" % (el, getattr(res, "events_total", "?")))

# ── 5. SONUC ──────────────────────────────────────────────────────
sec("[5] SONUC — huni ve metrikler")
print("  scans_total            %s" % adapter.scans_total)
print("  scans_actionable       %s" % adapter.scans_actionable)
print("  entries_submitted      %s" % adapter.entries_submitted)
print("  skipped_cap            %s" % adapter.scans_skipped_cap)
print("  skipped_sizing         %s" % adapter.scans_skipped_sizing)
print("  skipped_confluence     %s" % adapter.scans_skipped_confluence)
for name in ("signals_detected_by_path", "actionable_by_path", "entries_by_path"):
    d = getattr(adapter, name, None)
    if d: print("  %-24s %s" % (name, dict(d)))
sk = getattr(adapter, "skipped_by_path_reason", None)
if sk:
    print("  skipped_by_path_reason:")
    for k, v in sorted(dict(sk).items(), key=lambda kv: -kv[1])[:15]:
        print("      %-52s %s" % (str(k)[:52], v))
print("\n  --- SINYAL TESHISI (probe) ---")
print("  scan cagrisi=%s | None donen=%s | sinyal nesnesi=%s" % (probe.n, probe.none_count, probe.n - probe.none_count))
print("  bunun ISINMA olani     : %s  (ilk gercek scan: #%s)" % (probe.warmup, probe.first_real_n))
print("  ISINMA SONRASI scan    : %s" % (probe.n - probe.none_count - probe.warmup))
print("  execution_allowed=True : %s" % probe.exec_allowed)
print("  stop_loss yok          : %s" % probe.no_sl)
print("  take_profit yok        : %s" % probe.no_tp)
print("  yon dagilimi           : %s" % dict(sorted(probe.dir_counts.items(), key=lambda kv: -kv[1])[:6]))
print("  rejim dagilimi         : %s" % dict(sorted(probe.regimes.items(), key=lambda kv: -kv[1])[:8]))
print("  active_strategy        : %s" % dict(sorted(probe.active_strats.items(), key=lambda kv: -kv[1])[:6]))
if _GATES["counts"]:
    print("  BOOTSTRAP KAPILARININ BLOKLADIGI: %s" % dict(_GATES["counts"]))
print("  EN SIK RED SEBEPLERI:")
for r, c in sorted(probe.reasons.items(), key=lambda kv: -kv[1])[:12]:
    print("      %-70s %s" % (r, c))
if not probe.reasons:
    print("      (rejection_reasons bos — sinyal sessizce NO_TRADE donuyor)")
print("  ORNEK SINYALLER:")
for smp in probe.samples:
    print("      %s" % json.dumps(smp, ensure_ascii=False, default=str)[:340])

closed = exchange.closed_positions()
fold = compute_fold_metrics(fold_id=0, positions=closed, initial_balance=A.balance,
                            fold_start_ns=start_ns, fold_end_ns=end_ns)
print("\n  trades=%s wins=%s losses=%s WR=%.1f%% net=%.4f final_equity=%.2f maxDD=%.4f PF=%.2f" % (
    fold.trades, fold.wins, fold.losses, 100 * fold.win_rate, fold.realized_pnl,
    fold.final_equity, fold.max_drawdown, fold.profit_factor))
sec("[5b] SIZER TESHISI")
print("  sizer cagrisi          %s" % _sz["call"])
print("    ham sinyal yok       %s" % _sz["no_raw"])
print("    istisna              %s" % _sz["exc"])
print("    is_valid=False       %s" % _sz["invalid"])
print("    qty<=0               %s" % _sz["zero"])
print("    KABUL                %s" % _sz["ok"])
print("    SL tavana carpti     %s" % _sz["sl_capped"])
for _ek in ("down_rejim_red", "dss_izin", "kalabalik_red", "kalabalik_gecti", "htf_yon_red", "htf_yon_gecti", "htf_veri_yok", "htf_exc", "htf_yol_disi", "mr_cipa_yakin_red", "baglam_only_red"):
    pass
for _ek, _ev in sorted(getattr(probe, "retest_stats", {}).items()):
    print("    %-24s %s" % (_ek, _ev))
for _ek in ("down_rejim_red", "dss_izin", "kalabalik_red", "kalabalik_gecti", "htf_yon_red", "htf_yon_gecti", "htf_veri_yok", "htf_exc", "htf_yol_disi", "mr_cipa_yakin_red", "baglam_only_red"):
    if _sz.get(_ek):
        print("    %-20s %s" % (_ek, _sz[_ek]))
if _sz_reasons:
    print("  SIZER RED SEBEPLERI:")
    for _r, _c in sorted(_sz_reasons.items(), key=lambda kv: -kv[1])[:12]:
        print("      %-70s %s" % (_r, _c))

# ── 6. KIRILIMLAR ─────────────────────────────────────────────────
sec("[6] KIRILIM — yol, sembol, maliyet, cikis sebebi")
_Ru = A.balance * A.risk_frac
def _pos_risk(pos):
    """Pozisyonun KENDI riski = qty x |entry - sl|.

    Sabit bir R birimi kullanmak yaniltir: sizer her islemde O ANKI
    ozkaynaga gore boyut veriyor, ozkaynak degistikce islemin USDT
    cinsinden riski de degisiyor. Sabit paydayla bolunce stop yiyen
    islem -1R yerine -0.5R gibi gorunuyor. Her islemi kendi riskine
    boluyoruz -> hakiki R.
    """
    try:
        _sl = getattr(pos, 'orig_sl_px', None) or pos.sl_px  # laddered: BE'ye tasinmadan onceki SL
        if _sl is None or float(_sl) <= 0: return None
        r = abs(float(pos.entry_px) - float(_sl)) * float(pos.qty)
        return r if r > 0 else None
    except Exception:
        return None

def _agg(key_fn, title):
    buckets = {}
    for pos in closed:
        k = key_fn(pos)
        b_ = buckets.setdefault(k, {"n":0,"w":0,"pnl":0.0,"fee":0.0,"slip":0.0,
                                    "notional":0.0,"R":0.0,"nR":0,"risk":0.0})
        b_["n"] += 1
        if pos.realized_pnl > 0: b_["w"] += 1
        b_["pnl"] += float(pos.realized_pnl)
        b_["fee"] += float(pos.entry_fee) + float(pos.exit_fee)
        _notl = abs(float(pos.entry_px) * float(pos.qty))
        b_["notional"] += _notl
        b_["slip"] += 2.0 * _notl * (2.0 / 10_000.0)   # SimExchangeConfig.slippage_bps=2
        _r = _pos_risk(pos)
        if _r:
            b_["R"] += float(pos.realized_pnl) / _r
            b_["risk"] += _r
            b_["nR"] += 1
    print("\n  %s   (R = her islemin KENDI riski)" % title)
    print("    %-16s %5s %6s %9s %9s %9s %10s" % (
        "", "n", "WR%", "net R", "R/islem", "ort.risk$", "maliyet R"))
    for k, b_ in sorted(buckets.items(), key=lambda kv: kv[1]["R"]):
        _nR = max(1, b_["nR"])
        _cost_R = 0.0
        for pos in closed:
            if key_fn(pos) != k: continue
            _r = _pos_risk(pos)
            if not _r: continue
            _n2 = abs(float(pos.entry_px) * float(pos.qty))
            _cost_R += (float(pos.entry_fee) + float(pos.exit_fee)
                        + 2.0 * _n2 * (2.0/10_000.0)) / _r
        print("    %-16s %5d %6.1f %9.2f %9.3f %9.2f %10.3f" % (
            str(k)[:16], b_["n"], 100.0*b_["w"]/b_["n"], b_["R"],
            b_["R"]/_nR, b_["risk"]/_nR, _cost_R/_nR))
    return buckets

_agg(lambda ps: _path_by_pos.get(ps.position_id, "?"), "YOL BAZINDA")
_agg(lambda ps: ps.symbol, "SEMBOL BAZINDA")
_agg(lambda ps: str(ps.close_reason or "?"), "CIKIS SEBEBI")
_agg(lambda ps: str(getattr(getattr(ps, "direction", None), "value", ps.direction)), "YON BAZINDA")

_tot_fee = sum(float(x.entry_fee)+float(x.exit_fee) for x in closed)
_tot_notl = sum(abs(float(x.entry_px)*float(x.qty)) for x in closed)
_tot_slip = 2.0 * _tot_notl * (2.0/10_000.0)
_net = sum(float(x.realized_pnl) for x in closed)
_tR = [( float(x.realized_pnl)/_pos_risk(x), _pos_risk(x), x) for x in closed if _pos_risk(x)]
if _tR:
    _sumR = sum(v for v,_,_ in _tR)
    _costR = sum((float(x.entry_fee)+float(x.exit_fee)
                  + 2.0*abs(float(x.entry_px)*float(x.qty))*(2.0/10_000.0))/r
                 for _, r, x in _tR)
    print("\n  HAKIKI R (her islem kendi riskine bolunmus, n=%d)" % len(_tR))
    print("    net beklenti          %9.3f R/islem   (toplam %.1f R)" % (_sumR/len(_tR), _sumR))
    print("    maliyet               %9.3f R/islem   (toplam %.1f R)" % (_costR/len(_tR), _costR))
    print("    BRUT beklenti         %9.3f R/islem" % ((_sumR+_costR)/len(_tR)))
    _w = [v for v,_,_ in _tR if v > 0]; _l = [v for v,_,_ in _tR if v <= 0]
    if _w and _l:
        print("    ort.kazanc %+.3f R | ort.kayip %+.3f R | oran %.2f | WR %.1f%%" % (
            sum(_w)/len(_w), sum(_l)/len(_l), abs(sum(_w)/len(_w))/abs(sum(_l)/len(_l)),
            100.0*len(_w)/len(_tR)))
        _need = abs(sum(_l)/len(_l)) / (abs(sum(_w)/len(_w)) + abs(sum(_l)/len(_l)))
        print("    bu oranla basabas icin gereken WR: %.1f%%  (mevcut %.1f%%)" % (
            100*_need, 100.0*len(_w)/len(_tR)))

print("\n  MALIYET MUHASEBESI — SABIT PAYDA (yaniltici, karsilastirma icin) (n=%d)" % len(closed))
print("    net (komisyon dahil)      %9.2f R" % (_net/_Ru))
print("    komisyon                  %9.2f R  (islem basina %.4f R)" % (_tot_fee/_Ru, _tot_fee/_Ru/max(1,len(closed))))
print("    slipaj (tahmini)          %9.2f R  (islem basina %.4f R)" % (_tot_slip/_Ru, _tot_slip/_Ru/max(1,len(closed))))
print("    BRUT (maliyet oncesi)     %9.2f R  (islem basina %.4f R)" % (
    (_net+_tot_fee+_tot_slip)/_Ru, (_net+_tot_fee+_tot_slip)/_Ru/max(1,len(closed))))
print("    ortalama notional         %9.2f USDT  (ozkaynagin ~%.0f katı riski)" % (
    _tot_notl/max(1,len(closed)), _tot_notl/max(1,len(closed))/A.balance))
print("    yol etiketi eslesmeyen    %9d" % sum(1 for x in closed if _path_by_pos.get(x.position_id,"?")=="?"))

_R = A.balance * A.risk_frac
if fold.trades:
    print("  R BIRIMI = %.2f USDT (baslangic ozkaynaginin %%%.1f'i)" % (_R, 100*A.risk_frac))
    print("  toplam %.2f R | islem basina beklenti %.3f R" % (
        fold.realized_pnl / _R, fold.realized_pnl / _R / fold.trades))
if A.json_out:
    _J = {
        "tag": A.tag,
        "env_overrides": _ENV_OVERRIDES,
        "symbols": syms,
        "days": A.days,
        "balance": A.balance,
        "sizer": "toy" if A.toy_sizer else "real",
        "selector": _strategy_selector is not None,
        "per_symbol_loader": per_symbol_loader is not None,
        "portfolio": bool(_PF_REF["pf"] is not None),
        "scans_total": adapter.scans_total,
        "scans_actionable": adapter.scans_actionable,
        "entries_submitted": adapter.entries_submitted,
        "warmup_scans": probe.warmup,
        "sizer_stats": dict(_sz),
        "entries_by_path": {k: v for k, v in dict(getattr(adapter, "entries_by_path", {}) or {}).items()},
        "regimes": dict(probe.regimes),
        "active_strategy": dict(probe.active_strats),
        "trades": fold.trades, "wins": fold.wins, "losses": fold.losses,
        "win_rate": fold.win_rate, "net_usdt": fold.realized_pnl,
        "final_equity": fold.final_equity, "max_drawdown": fold.max_drawdown,
        "profit_factor": fold.profit_factor,
        "elapsed_s": round(el, 1),
    }
    # hakiki R
    _tr = [(float(x.realized_pnl) / _pos_risk(x), x) for x in closed if _pos_risk(x)]
    if _tr:
        _vals = [v for v, _ in _tr]
        _w = [v for v in _vals if v > 0]; _l = [v for v in _vals if v <= 0]
        _J["true_R"] = {
            "n": len(_vals),
            "expectancy_R": sum(_vals) / len(_vals),
            "total_R": sum(_vals),
            "avg_win_R": (sum(_w) / len(_w)) if _w else 0.0,
            "avg_loss_R": (sum(_l) / len(_l)) if _l else 0.0,
            "win_rate": len(_w) / len(_vals),
        }
        _bypath = {}
        for v, x in _tr:
            k = _path_by_pos.get(x.position_id, "?")
            d = _bypath.setdefault(k, {"n": 0, "R": 0.0, "w": 0})
            d["n"] += 1; d["R"] += v
            if v > 0: d["w"] += 1
        _J["true_R_by_path"] = {
            k: {"n": d["n"], "total_R": d["R"], "expectancy_R": d["R"] / d["n"],
                "win_rate": d["w"] / d["n"]} for k, d in _bypath.items()}
        def _bucket(keyfn, name):
            d = {}
            for v, x in _tr:
                k = keyfn(x)
                b_ = d.setdefault(str(k), {"n": 0, "R": 0.0, "w": 0})
                b_["n"] += 1; b_["R"] += v
                if v > 0: b_["w"] += 1
            _J[name] = {k: {"n": b_["n"], "total_R": b_["R"],
                            "expectancy_R": b_["R"] / b_["n"],
                            "win_rate": b_["w"] / b_["n"]} for k, b_ in d.items()}
        _dirof = lambda x: str(getattr(getattr(x, "direction", None), "value", x.direction))
        _bucket(_dirof, "true_R_by_direction")
        _bucket(lambda x: _meta_by_pos.get(x.position_id, ("-", "-"))[0], "true_R_by_regime")
        _bucket(lambda x: str(x.close_reason or "?"), "true_R_by_exit")
        # capa gerekcesi (canlida +0.3547 R / -0.9435 R ayrisimini veren alan)
        _bucket(lambda x: _sr_by_pos.get(x.position_id, ("-", "-"))[0], "true_R_by_anchor")
        # yapisal stop mesafesi bandi — hipotezi A kolunda BILE test eder:
        # v2_sl girise UZAKSA, uygulanan %0.80 stop cok icerde demektir.
        _bucket(lambda x: _sr_by_pos.get(x.position_id, ("-", "-"))[1], "true_R_by_srdist")
        # capa TUTTU mu KIRILDI mi — fiyat yolu vs v2_anchor_support/resistance
        def _anchor_held(x):
            _a = _anchor_by_pos.get(x.position_id)
            if not _a:
                return "capa_yok"
            _e = _ext_by_pos.get(x.position_id)
            if not _e:
                return "yol_yok"
            _px, _isl = _a
            _brk = (_e[0] < _px) if _isl else (_e[1] > _px)
            return "kirildi" if _brk else "tuttu"
        _bucket(_anchor_held, "true_R_by_anchor_held")
        _bucket(lambda x: "%s|%s" % (_anchor_held(x), _dirof(x)), "true_R_by_anchor_held_dir")
        # ── ISLEM-BASINA CSV DOKUMU ──────────────────────────────
        # Amac: kova kesisimlerini (cipa x rejim x yon x mesafe ...) kosu
        # tekrarlamadan analiz edebilmek. JSON'un yanina .trades.csv yazar.
        try:
            import csv as _csv
            from datetime import datetime as _dt, timezone as _tz
            _csvp = Path(A.json_out).with_suffix(".trades.csv")
            with open(_csvp, "w", newline="", encoding="utf-8") as _cf:
                _w = _csv.writer(_cf)
                _w.writerow(["position_id", "symbol", "direction", "path", "regime",
                             "opened_utc", "closed_utc", "sure_dk", "close_reason",
                             "entry_px", "sl_px", "exit_px", "qty",
                             "risk_usdt", "true_R", "realized_pnl", "fee_toplam",
                             "v2_reason", "srdist_band",
                             "anchor_px", "anchor_dist_pct", "anchor_etiket",
                             "min_low", "max_high"])
                for _v, _x in _tr:
                    _pid = _x.position_id
                    _a = _anchor_by_pos.get(_pid)
                    _e = _ext_by_pos.get(_pid)
                    _rz2, _bd2 = _sr_by_pos.get(_pid, ("-", "-"))
                    _rg2, _pt2 = _meta_by_pos.get(_pid, ("-", "-"))
                    _adp = ""
                    if _a and float(_x.entry_px) > 0:
                        _adp = "%.4f" % (abs(_a[0] - float(_x.entry_px)) / float(_x.entry_px) * 100.0)
                    def _ts(ns):
                        return (_dt.fromtimestamp(ns / 1e9, tz=_tz.utc).strftime("%Y-%m-%d %H:%M")
                                if ns else "")
                    _sure = ""
                    if _x.closed_ts_ns and _x.opened_ts_ns:
                        _sure = "%.0f" % ((_x.closed_ts_ns - _x.opened_ts_ns) / 1e9 / 60)
                    _w.writerow([_pid, _x.symbol, _dirof(_x), _path_by_pos.get(_pid, "?"), _rg2,
                                 _ts(_x.opened_ts_ns), _ts(_x.closed_ts_ns), _sure,
                                 str(_x.close_reason or "?"),
                                 _x.entry_px, _x.sl_px, _x.exit_px, _x.qty,
                                 "%.6f" % _pos_risk(_x), "%.6f" % _v,
                                 "%.6f" % float(_x.realized_pnl),
                                 "%.6f" % (float(_x.entry_fee) + float(_x.exit_fee)),
                                 _rz2, _bd2,
                                 (_a[0] if _a else ""), _adp, _anchor_held(_x),
                                 (_e[0] if _e else ""), (_e[1] if _e else "")])
            print("TRADES CSV: %s (%d satir)" % (_csvp, len(_tr)))
        except Exception as _ce:
            print("!! trades csv yazilamadi: %r" % _ce)
        _bucket(lambda x: "%s|%s" % (_meta_by_pos.get(x.position_id, ("-", "-"))[1], _dirof(x)),
                "true_R_by_path_direction")
        _bysym = {}
        for v, x in _tr:
            d = _bysym.setdefault(x.symbol, {"n": 0, "R": 0.0, "w": 0})
            d["n"] += 1; d["R"] += v
            if v > 0: d["w"] += 1
        _J["true_R_by_symbol"] = {
            k: {"n": d["n"], "total_R": d["R"], "expectancy_R": d["R"] / d["n"],
                "win_rate": d["w"] / d["n"]} for k, d in _bysym.items()}
    _jp = pathlib.Path(A.json_out) if "pathlib" in dir() else Path(A.json_out)
    _jp.parent.mkdir(parents=True, exist_ok=True)
    _jp.write_text(json.dumps(_J, indent=1, default=str), encoding="utf-8")
    print("JSON: %s" % _jp)

print("\n=== BITTI ===")
