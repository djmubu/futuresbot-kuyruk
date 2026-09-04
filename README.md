# futuresbot-kuyruk
Arastirma kosulari icin posta kutusu. Claude `jobs/` altina is yazar; VPS daemon'lari (`kuyruk.py`, systemd `futuresbot-kuyruk`)
her dakika ceker, sirayla kosar, `results/<id>/` + `state/<box>/` altina yazip push'lar. Canli VPS'e DEPLOY buradan YAPILMAZ (Mustafa elle).
Kurallar koda gomulu: rm/kill yok, cikti dizini varsa reddet, md5 tutmuyorsa kosma, kutu basina tek is, saatler TR.
Kurulum (VPS'te): git clone https://<TOKEN>@github.com/<user>/futuresbot-kuyruk.git /root/kuyruk && cd /root/kuyruk && bash install.sh ovh   (Box2: box2)
