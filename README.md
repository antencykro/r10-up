# R10 Yukarı Taşıma (bulut otomasyonu)

r10.net konusunu **saatte bir otomatik yukarı taşır** ve sonucu Telegram'daki
**"R10 Yukari Tasima"** kanalına bildirir. GitHub Actions'ta çalışır — **bilgisayar
kapalı olsa bile** çalışır.

## Nasıl çalışır
- `.github/workflows/r10-up.yml` her 30 dakikada bir `r10_yukari.py`'yi çalıştırır.
- Script `up.php` adresine giriş çereziyle istek atar.
  - **Taşındı** → Telegram'a ✅
  - **Süre dolmamış** → sessiz atlar (saatte 1 limiti; zararsız)
  - **Cloudflare / oturum hatası** → Telegram'a 🚫/🔑 (çerez yenilenmeli)

## Ayarlar (GitHub → Settings → Secrets and variables → Actions)
- `R10_COOKIE` — r10 giriş çerezi (süresi dolunca güncellenir)
- `TG_BOT_TOKEN` — Telegram bot token
- `TG_CHAT_ID` — bildirim kanalının id'si

Konu adresi ve adı workflow dosyasındaki `R10_UP_URL` / `R10_TOPIC_NAME` içinde.

## Elle test
GitHub → Actions → **R10 Yukari Tasima** → **Run workflow** (test açık) → ~1 dk içinde
kanala mesaj düşer.

## Çerez süresi dolarsa
Telegram'a "🚫 Cloudflare" / "🔑 oturum" bildirimi gelirse: tarayıcıda r10'a girip
F12 → Network'ten yeni çerezi al, `R10_COOKIE` secret'ını güncelle.
