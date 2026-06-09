# YouTube Autopilot

Basliklari gir → AI gerisini (script, ses, altyazi, gorsel, BGM, metadata) doldurur →
belirli araliklarla otomatik video uretip YouTube'a yukler. Sunucuda Docker'da
surekli calisir. Dikey (9:16) video **Shorts**, yatay (16:9) video **normal** yuklenir.

## Mimari

- **WebUI > Autopilot sayfasi** — basliklari/ayarlari girersin, YouTube'u tek seferlik
  yetkilendirirsin, manuel "Simdi bir tane uret" + durum izleme.
- **autopilot worker** (`python3 -m app.autopilot_worker`) — Docker servisi; `autopilot_enabled = true`
  iken `autopilot_interval_hours` araligiyla siradaki basligi isler.
- Veriler `storage/autopilot/` altinda: `titles.txt` (basliklar), `state.json` (islenenler + tarihce).

## 1) Basliklar

`storage/autopilot/titles.txt` (veya WebUI textarea) — her satira bir baslik:

```
Gunluk verimlilik ipuclari
Uzayla ilgili 3 sasirtici gercek | 16:9
# bu satir yorum, atlanir
Sabah rutini onerileri | shorts
```

- Format override: `Baslik | 16:9` (yatay), `| 9:16` / `| shorts` (dikey). Yoksa
  `autopilot_default_aspect` kullanilir.
- `#` ile baslayan ve bos satirlar atlanir.
- Islenen basliklar `state.json > processed`'a yazilir; tekrar islenmez. Yeniden
  uretmek icin durumu sifirla (WebUI butonu) ya da satiri biraz degistir.

## 2) YouTube kimlik dogrulama (tek seferlik)

YouTube Data API v3 sifre kabul etmez; OAuth gerekir. Bir kez kurarsin, sonra
refresh token ile tam otomatik calisir.

1. https://console.cloud.google.com → yeni proje olustur.
2. **APIs & Services > Library** → **YouTube Data API v3** → Enable.
3. **OAuth consent screen** → External → uygulama adi gir → **Test users**'a kendi
   Google hesabini ekle (yayinlamadan test modu yeterli).
4. **Credentials > Create Credentials > OAuth client ID** → Application type:
   **Desktop app** → olustur → **client_secret.json** indir.
5. Dosyayi proje kokune koy (`client_secret.json`) ya da WebUI Autopilot sayfasindan yukle.
6. WebUI Autopilot > "3) YouTube hesabi":
   - "Yetkilendirme URL'si olustur" → linke tikla → Google ile izin ver.
   - Tarayici `http://localhost/?...code=...` adresine yonlenir (baglanti hatasi
     normal). Adres cubugundaki tam URL'yi veya sadece `code` degerini kopyala.
   - Kutuya yapistir → "Yetkilendir". Token `storage/youtube_token.json`'a kaydedilir.

> Headless sunucuda: ayni adimlari kendi bilgisayarinda yapip olusan
> `storage/youtube_token.json`'i sunucuya kopyalayabilirsin.

## 3) Telifsiz arkaplan muzigi (Jamendo)

YouTube Audio Library'nin API'si yok. Bunun yerine Jamendo (ucretsiz Creative
Commons muzik) kullaniyoruz; indirilen parça `resource/songs/` altinda cache'lenir.

1. https://developer.jamendo.com → ucretsiz uygulama olustur → **client_id** al.
2. config.toml ya da WebUI:
   - `bgm_source = "jamendo"`
   - `jamendo_client_id = "..."`
   - `jamendo_tags = "happy,upbeat"` (mood/tur)
3. Parça atifu (attribution) otomatik video aciklamasina eklenir.

> Jamendo yoksa/calismazsa otomatik olarak yerel `resource/songs/*.mp3`'e duser.
> **Monetizasyon** icin lisansi mutlaka dogrula (CC-BY atif ister; bazi parçalar
> ticari kullanima kapali olabilir — Jamendo Licensing'e bak).

## 4) Calistirma (Docker)

```bash
# ilk kurulum: ornek configi kopyala ve duzenle
cp config.example.toml config.toml

# servisleri ayaga kaldir (webui + api + autopilot)
docker compose up -d --build

# sadece autopilot loglari
docker compose logs -f autopilot
```

`config.toml`'da en az sunlari ayarla:

```toml
[app]
pexels_api_keys = ["..."]          # gorsel kaynagi
# LLM saglayici anahtarini da gir (orn. groq/openai/gemini ...)

autopilot_enabled = true
autopilot_interval_hours = 6
autopilot_default_aspect = "9:16"  # Shorts

youtube_enabled = true
youtube_privacy = "unlisted"       # ilk test icin unlisted onerilir

bgm_source = "jamendo"
jamendo_client_id = "..."
```

WebUI'ye `http://127.0.0.1:8501` adresinden eris → **Autopilot** sayfasi.

## Ipuclari

- Ilk testte `youtube_privacy = "unlisted"` ve `autopilot_run_on_start = true` ile
  hizlica bir tur dene; YouTube Studio'da kontrol et.
- WebUI'deki ayar degisiklikleri `config.toml`'a yazilir; worker her dongude tazeler.
- Shorts icin `#Shorts` etiketi title/description/tags'e otomatik eklenir.
- Bir baslik 3 kez ust uste patlarsa kuyrugu tikamamak icin atlanir (state.json).
