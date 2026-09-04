#!/bin/bash
# install.sh ovh|box2  — /root/kuyruk icinde calistir. systemd servisi kurar ve baslatir.
set -e
BOX="$1"; [ "$BOX" = "ovh" ] || [ "$BOX" = "box2" ] || { echo "kullanim: bash install.sh ovh|box2"; exit 1; }
cd /root/kuyruk
git config user.name "kuyruk-$BOX"; git config user.email "kuyruk-$BOX@futuresbot.local"
git config pull.rebase true
cat > /etc/systemd/system/futuresbot-kuyruk.service <<UNIT
[Unit]
Description=FuturesBot arastirma kuyrugu ($BOX)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/kuyruk
Environment=KUYRUK_BOX=$BOX KUYRUK_REPO=/root/kuyruk KUYRUK_BOT=/root/bot KUYRUK_POLL=60
ExecStart=/usr/bin/python3 /root/kuyruk/kuyruk.py $BOX
Restart=always
RestartSec=20

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now futuresbot-kuyruk
sleep 3
systemctl is-active futuresbot-kuyruk && echo "KUYRUK_$BOX""_AKTIF"
