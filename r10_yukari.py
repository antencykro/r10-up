# -*- coding: utf-8 -*-
# R10 KONU YUKARI TASIMA - Telegram bildirimli, cok konu destekli.
#
# Her calistiginda SIRADAKI konuyu yukari tasimayi dener (round-robin),
# cunku r10 limiti kullanici basina saatte 1. Sonucu Telegram'a bildirir.
#
# Calistirma:
#   python r10_yukari.py          -> normal calisma (zamanlayici boyle cagirir)
#   python r10_yukari.py test     -> test: sonuc ne olursa olsun Telegram'a yazar
#
# Sira "r10-sayac.txt"de tutulur. Basarili tasimada sonraki konuya gecer.

import os, sys, io, json, gzip, time, random
import urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

TR = timezone(timedelta(hours=3))   # Turkiye saati (bulutta UTC yerine bunu goster)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ---- Ayarlar: once ortam degiskeni (GitHub Actions Secret), yoksa config_r10.py ----
from types import SimpleNamespace
try:
    import config_r10 as _F   # yerel ayar dosyasi (sadece bu PC'de; repoda yok)
except Exception:
    _F = None

def _get(env, attr, default=None, cast=str):
    v = os.environ.get(env)
    if v not in (None, ""):
        v = v.strip().lstrip("﻿")   # BOM/bosluk temizligi (Secret'tan gelebilir)
        try: return cast(v)
        except Exception: return v
    if _F is not None and hasattr(_F, attr):
        return getattr(_F, attr)
    return default

def _bool(env, attr, default):
    v = os.environ.get(env)
    if v not in (None, ""):
        return v.strip().lower() in ("1", "true", "yes", "evet", "on")
    if _F is not None and hasattr(_F, attr):
        return getattr(_F, attr)
    return default

_DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0")

# Konular: bulutta tek konu env ile gelir; yerelde config dosyasindaki liste kullanilir.
_env_url = os.environ.get("R10_UP_URL")
if _env_url:
    _topics = [{"ad": os.environ.get("R10_TOPIC_NAME", "Konu"), "url": _env_url}]
elif _F is not None and hasattr(_F, "TOPICS"):
    _topics = _F.TOPICS
else:
    _topics = []

C = SimpleNamespace(
    TELEGRAM_BOT_TOKEN = _get("TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
    TELEGRAM_CHAT_ID   = _get("TG_CHAT_ID", "TELEGRAM_CHAT_ID", cast=int),
    NOTIFY_SUCCESS     = _bool("NOTIFY_SUCCESS", "NOTIFY_SUCCESS", True),
    NOTIFY_ERROR       = _bool("NOTIFY_ERROR", "NOTIFY_ERROR", True),
    NOTIFY_TOO_EARLY   = _bool("NOTIFY_TOO_EARLY", "NOTIFY_TOO_EARLY", False),
    USER_AGENT         = _get("R10_UA", "USER_AGENT", default=_DEFAULT_UA),
    COOKIE             = _get("R10_COOKIE", "COOKIE", default=""),
    TOPICS             = _topics,
)

SAYAC = "r10-sayac.txt"
LOG   = "r10-log.txt"
STATE = "r10-state.json"     # son tasima zamani + bir sonraki rastgele hedef (dk)
HOLD_MIN = 61                # r10 minimumu (saatte 1) + 1 dk guvenlik payi
HOLD_MAX = 61                # sabit: her zaman 1 saat 1 dakika sonra tasi
TEST  = (len(sys.argv) > 1 and sys.argv[1].lower() == "test") \
        or os.environ.get("R10_TEST", "").strip().lower() in ("1", "true", "yes")

def now():
    return datetime.now(TR).strftime("%Y-%m-%d %H:%M:%S")

def logla(msg):
    line = f"[{now()}] {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def telegram(text):
    """Admin'e DM atar. Hata olursa sessizce gecer (tasima yine de kayda gecer)."""
    try:
        url = f"https://api.telegram.org/bot{C.TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": C.TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": "true",
        }).encode()
        with urllib.request.urlopen(url, data=data, timeout=30) as r:
            res = json.load(r)
        if not res.get("ok"):
            logla(f"Telegram HATA: {res}")
    except Exception as e:
        logla(f"Telegram gonderilemedi: {e}")

def sayac_oku(n):
    try:
        i = int(open(SAYAC, encoding="utf-8").read().strip())
    except Exception:
        i = 0
    return i % n if n else 0

def sayac_yaz(i):
    try:
        open(SAYAC, "w", encoding="utf-8").write(str(i))
    except Exception as e:
        logla(f"Sayac yazilamadi: {e}")

def state_oku():
    """(son_tasima_iso, hedef_dk) doner. Dosya yoksa (None, HOLD_MIN)."""
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("last_bump"), int(d.get("target_min", HOLD_MIN))
    except Exception:
        return None, HOLD_MIN

def state_yaz(last_iso, target_min):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump({"last_bump": last_iso, "target_min": target_min}, f)
    except Exception as e:
        logla(f"State yazilamadi: {e}")

# ---- cron-job.org: kendi kendine sonraki tetigi kur (kota dostu) ----
# Mantik: her basarili tasimadan sonra cron-job.org isini "+minutes sonra TEK
# sefer calis" sekilde guncelliyoruz. Boylece GitHub Actions saatte ~1 kez
# calisir (15 dk'lik surekli tetik yerine) ve tasima ~61-62 dk'da net olur.
# Anahtar yoksa veya hata olursa SESSIZCE gecer; GitHub'in saatlik yedek
# cron'u ('17 * * * *') zincir koparsa devreye girer.
def _cron_api(method, path, api_key, body=None):
    url = "https://api.cron-job.org" + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read().decode("utf-8", "replace")

def cron_self_schedule(minutes):
    api_key = _get("R10_CRON_API_KEY", "CRON_API_KEY", default="")
    if not api_key:
        return
    try:
        job_id = _get("R10_CRON_JOB_ID", "CRON_JOB_ID", default="")
        if not job_id:
            # Job'u otomatik bul: URL'si bu repoyu (r10-up) dispatch edeni sec.
            # ('dispatch' tek basina yetmez; baska projelerin isleri de var.)
            _, raw = _cron_api("GET", "/jobs", api_key)
            jobs = json.loads(raw).get("jobs", [])
            cand = [j for j in jobs if "r10-up" in (j.get("url") or "").lower()]
            if not cand:
                logla("cron: dispatch isini bulamadim, planlama atlandi.")
                return
            job_id = cand[0].get("jobId")
        # Hedef an: +minutes, sonra dakikaya yuvarla. Saniyeyi attigimiz icin
        # tetik gercekte (minutes, minutes+1) dk araliginda olur -> 61-62 dk.
        target = (datetime.now(TR) + timedelta(minutes=minutes + 1)).replace(second=0, microsecond=0)
        sched = {
            "timezone": "Europe/Istanbul",
            "hours":   [target.hour],
            "mdays":   [target.day],
            "minutes": [target.minute],
            "months":  [target.month],
            "wdays":   [-1],   # haftanin her gunu (mday ile birlikte tek gune kilitlenir)
            # Tek seferlik etki: tetikten ~3 dk sonra sus (kendiliginden tekrar etmesin).
            "expiresAt": int((target + timedelta(minutes=3)).strftime("%Y%m%d%H%M%S")),
        }
        st, raw = _cron_api("PATCH", f"/jobs/{job_id}", api_key,
                            {"job": {"enabled": True, "schedule": sched}})
        if st in (200, 204):
            logla(f"cron: sonraki tetik {target.strftime('%H:%M')} (job #{job_id}) ayarlandi.")
        else:
            logla(f"cron: beklenmedik yanit HTTP {st}: {raw[:200]}")
    except Exception as e:
        logla(f"cron: planlama hatasi (yedek cron devrede): {e}")

def istek(url):
    """up.php'yi cagirir. (status, body_metni) doner; a<g engeli HTTPError olur."""
    req = urllib.request.Request(url, headers={
        "User-Agent": C.USER_AGENT,
        "Cookie": C.COOKIE,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr,en;q=0.9",
        "Referer": "https://www.r10.net/",
    })
    with urllib.request.urlopen(req, timeout=40) as r:
        raw = r.read()
        if "gzip" in (r.headers.get("Content-Encoding") or ""):
            try: raw = gzip.decompress(raw)
            except Exception: pass
        body = raw.decode("utf-8", "replace")
        return r.status, body

def sonucu_coz(status, body):
    """(durum, ozet) doner. durum: SUCCESS | TOO_EARLY | CLOUDFLARE | AUTH | UNKNOWN"""
    low = body.lower()
    # Cloudflare / bot engeli
    if status in (403, 503) or "just a moment" in low or "cf-mitigated" in low \
       or "attention required" in low or "cf-chl" in low:
        return "CLOUDFLARE", "Cloudflare engeli / cerez gecersiz"
    # Sure dolmadi (ASCII guvenli isaret: 'doldurma' = doldurmadiginiz)
    if "doldurma" in low or "gerekli kalan" in low:
        return "TOO_EARLY", "Sure dolmamis, konu tasinmadi"
    # Oturum dusmus / giris gerekli
    if ("giris yap" in low or "giriş yap" in low or "uye girisi" in low
            or 'name="vb_login_username"' in low or "oturum" in low and "kapand" in low):
        return "AUTH", "Oturum dusmus, tekrar giris gerek (cookie yenile)"
    # Basari isaretleri (vBulletin yonlendirme mesaji)
    if "tasindi" in low or "taşındı" in low or "basariyla" in low or "başarı" in low \
       or "yukari tasin" in low or "yukarı taşın" in low:
        return "SUCCESS", "Konu yukari tasindi"
    # 200 dondu, yukaridakilerin hicbiri degil -> buyuk ihtimalle tasindi
    if status == 200:
        return "SUCCESS", "Islem tamam (200) - tasinmis kabul edildi"
    return "UNKNOWN", f"Bilinmeyen cevap (HTTP {status})"

def main():
    topics = C.TOPICS
    if not topics:
        logla("TOPICS bos - eklenecek konu yok.")
        if TEST: telegram("⚠️ R10: TOPICS bos, eklenecek konu yok.")
        return

    # --- Vakti geldi mi? (rastgele 60-75 dk bekleme) ---
    last_iso, target_min = state_oku()
    if last_iso and not TEST:
        try:
            last = datetime.fromisoformat(last_iso)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 60.0
        except Exception:
            elapsed = 9999
        if elapsed < target_min:
            logla(f"Vakti degil: {elapsed:.1f}/{target_min} dk gecti. Atlandi.")
            return

    idx = sayac_oku(len(topics))
    konu = topics[idx]
    ad, url = konu["ad"], konu["url"]
    logla(f"Deneniyor: #{idx} '{ad}'")

    try:
        status, body = istek(url)
        durum, ozet = sonucu_coz(status, body)
    except urllib.error.HTTPError as e:
        status = e.code
        try: body = e.read().decode("utf-8", "replace")
        except Exception: body = ""
        durum, ozet = sonucu_coz(status, body)
        if durum not in ("CLOUDFLARE", "AUTH"):
            durum, ozet = "CLOUDFLARE", f"HTTP {status} (Cloudflare/engel olabilir)"
    except Exception as e:
        durum, ozet = "ERROR", f"Baglanti hatasi: {e}"

    logla(f"Sonuc: {durum} - {ozet}")

    # Telegram metni
    ikon = {"SUCCESS": "✅", "TOO_EARLY": "⏳", "CLOUDFLARE": "🚫",
            "AUTH": "🔑", "UNKNOWN": "❓", "ERROR": "❌"}.get(durum, "❓")
    msg = (f"{ikon} R10 Yukari Tasima\n\n"
           f"Konu: {ad}\n"
           f"Durum: {ozet}\n"
           f"Saat: {now()}")

    # Bildirim karari
    bildir = TEST
    if durum == "SUCCESS"   and C.NOTIFY_SUCCESS:   bildir = True
    if durum == "TOO_EARLY" and C.NOTIFY_TOO_EARLY: bildir = True
    if durum in ("CLOUDFLARE", "AUTH", "UNKNOWN", "ERROR") and C.NOTIFY_ERROR: bildir = True
    if bildir:
        telegram(msg)

    # Basarili tasimada: sirayi ilerlet + bir sonraki rastgele hedefi belirle
    if durum == "SUCCESS":
        sayac_yaz((idx + 1) % len(topics))
        yeni = random.randint(HOLD_MIN, HOLD_MAX)
        state_yaz(datetime.now(timezone.utc).isoformat(), yeni)
        # NOT (2026-06-29): cron-job.org self-scheduling KALDIRILDI (API kotasini yakip 429
        # yapiyordu). Yeni mimari: cron-job.org isi (#7849973) SABIT takvimde (saatte 1)
        # repository_dispatch atar; script API'yi cagirmaz.
        logla(f"State guncellendi. Sonraki tasima ~{yeni} dk sonra. Sira -> #{(idx + 1) % len(topics)}")
    else:
        # Basarisiz (Cloudflare/oturum/erken/hata): cron API'yi CAGIRMA (429 kotasini yorma).
        # Retry'i artik GitHub'in 20 dk'lik guvenilir schedule'i yapiyor -> zincir olmez.
        logla("Sira/state degismedi; GitHub schedule ~20 dk sonra tekrar deneyecek.")

if __name__ == "__main__":
    main()
