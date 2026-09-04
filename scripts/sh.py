#!/usr/bin/env python3
"""sh.py — kuyruk 'script' isi: verilen kabuk komutunu /root/bot icinde calistirir, ciktiyi basar (salt-okur amacli; rm/kill YASAK)."""
import sys, subprocess, shlex
cmd = " ".join(sys.argv[1:])
for yasak in ("rm ", "rm\t", "kill", "pkill", "systemctl stop", "shutdown", "reboot"):
    if yasak in cmd:
        print("REDDEDILDI (yasakli komut):", cmd); sys.exit(2)
p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=1800)
print(p.stdout); 
if p.stderr: print("[stderr]", p.stderr[-3000:])
sys.exit(0)
