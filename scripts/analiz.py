#!/usr/bin/env python3
"""analiz.py — /root/bot/ab_analiz.py'yi verilen argumanlarla kosturur (kuyruk 'script' isi)."""
import sys, subprocess
p = subprocess.run([sys.executable, "ab_analiz.py", *sys.argv[1:]], capture_output=True, text=True, timeout=1800)
print(p.stdout)
if p.returncode: print("[stderr]", p.stderr[-3000:])
