#!/usr/bin/env python3
"""daemon_restart.py — kuyruk 'script' isi: daemon'u yeniden baslatir (yeni kuyruk.py'yi adopt etmek icin, BIR KEZ).
Calisan tek is bu scriptin kendisi oldugundan hicbir kosu kaybolmaz. Bu isin state'i 'running' kalir (zararsiz)."""
import subprocess, sys
subprocess.Popen(["systemctl", "restart", "futuresbot-kuyruk"], start_new_session=True)
print("restart verildi"); sys.exit(0)
