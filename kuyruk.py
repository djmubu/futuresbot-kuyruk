#!/usr/bin/env python3
"""kuyruk.py — FuturesBot arastirma kuyrugu daemon'u (VPS tarafi). stdlib-only.

Calisma: her POLL saniyede `git pull --rebase`; jobs/*.json icindeki isleri okur;
bu kutuya ait (box == BOX ya da "any") ve state/<BOX>/<id>.json'da bitmemis olanlari sirayla kosar
(kutu basina AYNI ANDA TEK is). Sonuclari results/<id>/ altina yazar, commit + push eder.

Is tipleri (jobs/<id>.json):
  {"id": "crowd_oos", "box": "ovh", "type": "run_ab",
   "args": ["--offset","310","--limit","130","--chunk","4","--jobs","62","--days","180",
            "--both-flags=--sr-geometry --block-down-regimes", "--b-flags=--crowd-calendar takvim_ucluTaban.csv", "--ab-set="],
   "out": "crowd_oos",                       # run_ab --out (BOT altinda; VARSA reddedilir — rm yok)
   "expect_md5": {"sim_e2e.py": "5cc8...", "run_ab.py": "f477..."},   # eslesmezse KOSMAZ (E87 kurali)
   "analiz": ["crowd_oos"]                   # bitince: ab_analiz.py <dirs...> -> results/<id>/analiz.txt
  }
  {"id": "deploy_htf", "box": "any", "type": "deploy",
   "files": {"code/sim_e2e.py": "sim_e2e.py", "code/exchange.py": "src/futuresbot/sim/exchange.py"},
   "expect_md5": {"sim_e2e.py": "d791..."}}   # kopyaladiktan sonra hedefte md5 + py_compile
  {"id": "cek_x", "box": "ovh", "type": "script", "script": "scripts/x.py", "args": ["srgeo3"]}
      -> ./.venv/bin/python <script> <args> BOT icinde; stdout -> results/<id>/out.txt

Kurallar (koda gomulu): rm/kill YOK; out dizini varsa reddet; md5 tutmuyorsa kosma; ayni anda tek is;
her adim state/<BOX>/<id>.json'a yazilir (status: queued|running|done|failed, zamanlar TR).
"""
import os, sys, json, time, hashlib, subprocess, shutil, datetime, traceback
from pathlib import Path

BOX = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KUYRUK_BOX", "")).strip().lower()
REPO = Path(os.environ.get("KUYRUK_REPO", "/root/kuyruk"))
BOT = Path(os.environ.get("KUYRUK_BOT", "/root/bot"))
PY = BOT / ".venv" / "bin" / "python"
POLL = int(os.environ.get("KUYRUK_POLL", "60"))
assert BOX in ("ovh", "box2"), "kullanim: kuyruk.py ovh|box2"

def tr_now():
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)).strftime("%Y-%m-%d %H:%M TR")

def md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()

def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=check)

def sync_pull():
    try:
        git("pull", "--rebase", "--quiet", check=False)
    except Exception:
        pass

def commit_push(msg):
    for i in range(5):
        try:
            git("add", "-A")
            r = git("commit", "-q", "-m", msg, check=False)
            git("pull", "--rebase", "--quiet", check=False)
            p = git("push", "--quiet", check=False)
            if p.returncode == 0:
                return True
            time.sleep(5 + i * 5)
        except Exception:
            time.sleep(5)
    return False

def state_path(jid): return REPO / "state" / BOX / f"{jid}.json"
def read_state(jid):
    p = state_path(jid)
    return json.loads(p.read_text()) if p.exists() else {}
def write_state(jid, **kw):
    p = state_path(jid); p.parent.mkdir(parents=True, exist_ok=True)
    st = read_state(jid); st.update(kw); st["box"] = BOX; st["updated"] = tr_now()
    p.write_text(json.dumps(st, indent=1, ensure_ascii=False))

def check_md5(expect: dict, base: Path):
    bad = {}
    for rel, want in (expect or {}).items():
        p = base / rel
        got = md5(p) if p.exists() else "YOK"
        if got != want:
            bad[rel] = {"beklenen": want, "bulunan": got}
    return bad

def tail(p: Path, n=40):
    try:
        return "\n".join(p.read_text(errors="replace").splitlines()[-n:])
    except Exception:
        return ""

def run_job(job):
    jid = job["id"]; typ = job["type"]
    res = REPO / "results" / jid; res.mkdir(parents=True, exist_ok=True)
    write_state(jid, status="running", started=tr_now(), type=typ)
    commit_push(f"[{BOX}] {jid} basladi")
    try:
        if typ == "deploy":
            bad = {}
            for src_rel, dst_rel in job["files"].items():
                src = REPO / src_rel; dst = BOT / dst_rel
                if not src.exists():
                    raise RuntimeError(f"kaynak yok: {src_rel}")
                dst.parent.mkdir(parents=True, exist_ok=True)
                bak = dst.with_suffix(dst.suffix + ".bak_kuyruk_" + jid)
                if dst.exists():
                    shutil.copy2(dst, bak)
                shutil.copy2(src, dst)
            bad = check_md5(job.get("expect_md5", {}), BOT)
            if bad:
                # geri al: yedekleri yerine koy
                for src_rel, dst_rel in job["files"].items():
                    dst = BOT / dst_rel; bak = dst.with_suffix(dst.suffix + ".bak_kuyruk_" + jid)
                    if bak.exists():
                        shutil.copy2(bak, dst)
                raise RuntimeError("md5 uyusmadi, GERI ALINDI: " + json.dumps(bad))
            comp = subprocess.run([str(PY), "-m", "py_compile", *[str(BOT / d) for d in job["files"].values() if d.endswith(".py")]],
                                  capture_output=True, text=True)
            if comp.returncode != 0:
                raise RuntimeError("py_compile hata: " + comp.stderr[-2000:])
            (res / "deploy.txt").write_text("OK " + tr_now() + "\n" + json.dumps({d: md5(BOT / d) for d in job["files"].values()}, indent=1))
        elif typ == "run_ab":
            bad = check_md5(job.get("expect_md5", {}), BOT)
            if bad:
                raise RuntimeError("md5 uyusmadi, KOSMADI: " + json.dumps(bad))
            out = job["out"]
            if (BOT / out).exists():
                raise RuntimeError(f"cikti dizini zaten var: {out} (rm yok) — yeni bir isim ver")
            log = BOT / f"{out}.log"
            with open(log, "w") as lf:
                proc = subprocess.Popen([str(PY), str(BOT / "run_ab.py"), *job["args"], "--out", out],
                                        cwd=BOT, stdout=lf, stderr=subprocess.STDOUT)
            t0 = time.time(); last = 0
            while proc.poll() is None:
                time.sleep(30)
                if time.time() - last > 600:   # 10 dk'da bir ilerleme
                    txt = tail(log, 400)
                    write_state(jid, bitti=txt.count("[BITTI"), basladi_n=txt.count("[BASLADI"), sure_dk=round((time.time() - t0) / 60))
                    commit_push(f"[{BOX}] {jid} ilerleme"); last = time.time()
            rc = proc.returncode
            (res / "run.log.tail").write_text(tail(log, 80))
            oz = BOT / out / "_ozet.json"
            if oz.exists():
                shutil.copy2(oz, res / "_ozet.json")
            if rc != 0 and not oz.exists():
                raise RuntimeError(f"run_ab rc={rc}")
            for d in job.get("analiz", [out]):
                pass
            an = subprocess.run([str(PY), str(BOT / "ab_analiz.py"), *job.get("analiz", [out])],
                                cwd=BOT, capture_output=True, text=True)
            (res / "analiz.txt").write_text(an.stdout + ("\n[stderr]\n" + an.stderr[-3000:] if an.returncode else ""))
            # kompakt islem ozeti (CSV'ler git'e girmez): kol/parca basina n ve R
            try:
                import csv, glob
                summ = {}
                for f in sorted(glob.glob(str(BOT / out / "*_c*.trades.csv"))):
                    arm = Path(f).name.split("_c")[0]; n = 0; R = 0.0
                    with open(f, newline="") as cf:
                        for row in csv.DictReader(cf):
                            n += 1; R += float(row.get("true_R") or 0)
                    s = summ.setdefault(arm, {"parca": 0, "n": 0, "R": 0.0}); s["parca"] += 1; s["n"] += n; s["R"] += R
                (res / "kol_ozet.json").write_text(json.dumps(summ, indent=1))
            except Exception as e:
                (res / "kol_ozet.json").write_text(json.dumps({"hata": str(e)}))
        elif typ == "script":
            sp = REPO / job["script"]
            pr = subprocess.run([str(PY), str(sp), *job.get("args", [])], cwd=BOT, capture_output=True, text=True, timeout=job.get("timeout", 7200))
            (res / "out.txt").write_text(pr.stdout + ("\n[stderr]\n" + pr.stderr[-5000:] if pr.returncode else ""))
            if pr.returncode != 0:
                raise RuntimeError(f"script rc={pr.returncode}")
        else:
            raise RuntimeError(f"bilinmeyen tip: {typ}")
        write_state(jid, status="done", finished=tr_now())
        commit_push(f"[{BOX}] {jid} BITTI")
    except Exception as e:
        (res / "hata.txt").write_text(tr_now() + "\n" + str(e) + "\n" + traceback.format_exc()[-3000:])
        write_state(jid, status="failed", finished=tr_now(), hata=str(e)[:500])
        commit_push(f"[{BOX}] {jid} HATA")

def pending_jobs():
    jobs = []
    for p in sorted((REPO / "jobs").glob("*.json")):
        try:
            j = json.loads(p.read_text())
        except Exception:
            continue
        if j.get("box", "any") not in (BOX, "any"):
            continue
        st = read_state(j["id"]).get("status")
        if st in ("done", "failed", "running"):
            continue
        # "any": diger kutu almis mi?
        if j.get("box", "any") == "any":
            other = "box2" if BOX == "ovh" else "ovh"
            op = REPO / "state" / other / f"{j['id']}.json"
            if op.exists():
                continue
        jobs.append(j)
    return jobs

def main():
    (REPO / "state" / BOX).mkdir(parents=True, exist_ok=True)
    hb = REPO / "state" / BOX / "_heartbeat.json"
    n = 0
    _own_md5 = md5(Path(__file__))
    while True:
        try:
            sync_pull()
            n += 1
            if n % 10 == 1:   # ~10 dk'da bir nabiz
                load = os.getloadavg()
                hb.write_text(json.dumps({"box": BOX, "zaman": tr_now(), "load": [round(x, 1) for x in load]}))
                commit_push(f"[{BOX}] nabiz")
            # E131: her isten SONRA bekleyen liste YENIDEN hesaplanir (araya eklenen/silinen isler etkili olur; eski toplu-liste yarisi yok)
            while True:
                _pj = pending_jobs()
                if not _pj:
                    break
                job = _pj[0]
                write_state(job["id"], status="queued", queued=tr_now())
                run_job(job)   # sirayla, tek is
                sync_pull()
                if md5(Path(__file__)) != _own_md5:
                    (REPO / "state" / BOX / "_daemon_reexec.txt").write_text(tr_now() + " kuyruk.py degisti, yeniden basliyor\n")
                    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), BOX])
        except Exception as e:
            try:
                (REPO / "state" / BOX / "_daemon_hata.txt").write_text(tr_now() + " " + str(e) + "\n" + traceback.format_exc()[-2000:])
            except Exception:
                pass
        try:
            if md5(Path(__file__)) != _own_md5:
                (REPO / "state" / BOX / "_daemon_reexec.txt").write_text(tr_now() + " kuyruk.py degisti (bosta), yeniden basliyor\n")
                os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), BOX])
        except Exception:
            pass
        time.sleep(POLL)

if __name__ == "__main__":
    main()
