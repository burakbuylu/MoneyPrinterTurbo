"""
Streamlit Autopilot sayfasi.

- Basliklari gir/duzenle (storage/autopilot/titles.txt ile senkron).
- Otomasyon ayarlari (interval, format, ses, gorsel kaynagi, BGM, YouTube).
- YouTube tek seferlik OAuth yetkilendirme.
- Manuel "Simdi bir tane uret" + durum tablosu.

Surekli zamanlama: autopilot Docker servisi (python3 -m app.autopilot_worker).
Bu sayfa konfig + manuel tetik + izleme icindir.
"""

import os
import sys

import streamlit as st

# Proje kokunu path'e ekle (Main.py ile ayni kalip)
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if root_dir not in sys.path:
    sys.path.append(root_dir)

from app.config import config  # noqa: E402
from app.services import autopilot  # noqa: E402
from app.services.youtube_upload import youtube_service  # noqa: E402

st.set_page_config(page_title="Autopilot", page_icon="🚀", layout="wide")
st.title("🚀 YouTube Autopilot")
st.caption(
    "Basliklari gir → AI gerisini doldurur → belirli araliklarla otomatik video "
    "uretip YouTube'a yukler. Dikey (9:16) Shorts, yatay (16:9) normal yuklenir."
)


def _save_config():
    config.save_config()


# ---------------------------------------------------------------- 1) Titles
st.header("1) Basliklar")
st.caption(
    "Her satira bir baslik. Opsiyonel format override: `Basligim | 16:9`. "
    "`#` ile baslayan satirlar ve bos satirlar atlanir."
)
titles_text = st.text_area(
    "Basliklar (alt alta)",
    value=autopilot.read_titles_raw(),
    height=240,
    placeholder="Gunluk verimlilik ipuclari\nUzayla ilgili 3 sasirtici gercek | 16:9\n# bu bir yorum, atlanir",
    label_visibility="collapsed",
)
col_a, col_b = st.columns([1, 4])
with col_a:
    if st.button("💾 Basliklari kaydet", use_container_width=True):
        autopilot.write_titles_raw(titles_text)
        st.success("Basliklar kaydedildi.")
with col_b:
    parsed = autopilot.read_titles()
    st.info(f"Gecerli baslik sayisi: {len(parsed)}")


# ---------------------------------------------------------------- 2) Settings
st.header("2) Otomasyon ayarlari")
c1, c2, c3 = st.columns(3)
with c1:
    interval = st.number_input(
        "Kac saatte bir (interval)",
        min_value=1, max_value=168,
        value=int(config.app.get("autopilot_interval_hours", 6) or 6),
    )
    aspect = st.selectbox(
        "Varsayilan format",
        options=["9:16", "16:9", "1:1"],
        index=["9:16", "16:9", "1:1"].index(
            str(config.app.get("autopilot_default_aspect", "9:16"))
            if str(config.app.get("autopilot_default_aspect", "9:16")) in ["9:16", "16:9", "1:1"]
            else "9:16"
        ),
        help="9:16 = Shorts (dikey), 16:9 = yatay video",
    )
    paragraph_number = st.number_input(
        "Script paragraf sayisi",
        min_value=1, max_value=10,
        value=int(config.app.get("autopilot_paragraph_number", 1) or 1),
        help="Kisa video icin 1 onerilir",
    )
with c2:
    voice_name = st.text_input(
        "TTS sesi (voice_name)",
        value=str(config.app.get("autopilot_voice_name", "") or ""),
        placeholder=autopilot.DEFAULT_VOICE,
        help="Bos = varsayilan (en-US-JennyNeural-Female)",
    )
    video_source = st.selectbox(
        "Gorsel kaynagi",
        options=["pexels", "pixabay"],
        index=["pexels", "pixabay"].index(
            str(config.app.get("autopilot_video_source", "pexels"))
            if str(config.app.get("autopilot_video_source", "pexels")) in ["pexels", "pixabay"]
            else "pexels"
        ),
    )
    bgm_volume = st.slider(
        "Arkaplan muzik sesi",
        min_value=0.0, max_value=1.0, step=0.05,
        value=float(config.app.get("autopilot_bgm_volume", 0.2) or 0.2),
    )
with c3:
    bgm_source = st.selectbox(
        "BGM kaynagi",
        options=["local", "jamendo"],
        index=["local", "jamendo"].index(
            str(config.app.get("bgm_source", "local"))
            if str(config.app.get("bgm_source", "local")) in ["local", "jamendo"]
            else "local"
        ),
        help="jamendo = telifsiz CC muzik otomatik ceker; local = resource/songs",
    )
    jamendo_client_id = st.text_input(
        "Jamendo client_id",
        value=str(config.app.get("jamendo_client_id", "") or ""),
        type="password",
        help="Ucretsiz: https://developer.jamendo.com",
    )
    jamendo_tags = st.text_input(
        "Jamendo etiketleri",
        value=str(config.app.get("jamendo_tags", "happy,upbeat") or "happy,upbeat"),
    )

st.subheader("YouTube yukleme ayarlari")
y1, y2, y3 = st.columns(3)
with y1:
    youtube_enabled = st.checkbox(
        "YouTube'a otomatik yukle",
        value=bool(config.app.get("youtube_enabled", False)),
    )
with y2:
    privacy = st.selectbox(
        "Gizlilik",
        options=["public", "unlisted", "private"],
        index=["public", "unlisted", "private"].index(
            str(config.app.get("youtube_privacy", "public"))
            if str(config.app.get("youtube_privacy", "public")) in ["public", "unlisted", "private"]
            else "public"
        ),
        help="Ilk testte 'unlisted' onerilir",
    )
with y3:
    made_for_kids = st.checkbox(
        "Cocuklara yonelik (COPPA)",
        value=bool(config.app.get("youtube_made_for_kids", False)),
    )

if st.button("💾 Ayarlari kaydet", type="primary"):
    config.app["autopilot_interval_hours"] = int(interval)
    config.app["autopilot_default_aspect"] = aspect
    config.app["autopilot_paragraph_number"] = int(paragraph_number)
    config.app["autopilot_voice_name"] = voice_name.strip()
    config.app["autopilot_video_source"] = video_source
    config.app["autopilot_bgm_volume"] = float(bgm_volume)
    config.app["bgm_source"] = bgm_source
    config.app["jamendo_client_id"] = jamendo_client_id.strip()
    config.app["jamendo_tags"] = jamendo_tags.strip()
    config.app["youtube_enabled"] = bool(youtube_enabled)
    config.app["youtube_privacy"] = privacy
    config.app["youtube_made_for_kids"] = bool(made_for_kids)
    _save_config()
    st.success("Ayarlar kaydedildi (config.toml).")


# ---------------------------------------------------------------- 3) YouTube auth
st.header("3) YouTube hesabi (tek seferlik yetkilendirme)")
cs_path = youtube_service.client_secret_path()
has_secret = os.path.isfile(cs_path)
has_token = youtube_service.has_token()

if has_token:
    st.success("✅ YouTube yetkilendirildi. Sunucu otomatik yukleyebilir.")
else:
    st.warning("⚠️ Henuz yetkilendirilmedi.")

if not has_secret:
    st.error(f"client_secret.json bulunamadi: `{cs_path}`")
    uploaded = st.file_uploader("client_secret.json yukle", type=["json"])
    if uploaded is not None:
        os.makedirs(os.path.dirname(cs_path), exist_ok=True)
        with open(cs_path, "wb") as f:
            f.write(uploaded.getbuffer())
        st.success("client_secret.json kaydedildi. Sayfayi yenile.")
    st.caption(
        "Google Cloud Console > YouTube Data API v3 > OAuth client (Desktop) > "
        "client_secret.json indir. Detay: docs/AUTOPILOT.md"
    )
else:
    st.caption(f"client_secret.json: `{cs_path}`")
    with st.expander("Yetkilendirme adimlari", expanded=not has_token):
        if st.button("1) Yetkilendirme URL'si olustur"):
            try:
                st.session_state["yt_auth_url"] = youtube_service.get_auth_url()
            except Exception as e:
                st.error(f"URL olusturulamadi: {e}")
        if st.session_state.get("yt_auth_url"):
            st.markdown(
                f"[➡️ Google ile yetkilendir]({st.session_state['yt_auth_url']})  "
                "(yeni sekmede ac, izin ver, yonlendirilen adresteki `code` degerini kopyala)"
            )
        code = st.text_input("2) Yonlendirme URL'si veya code degerini yapistir")
        if st.button("3) Yetkilendir"):
            res = youtube_service.exchange_code(code)
            if res.get("success"):
                st.success("✅ Yetkilendirildi! Sayfayi yenile.")
            else:
                st.error(f"Yetkilendirme basarisiz: {res.get('error')}")


# ---------------------------------------------------------------- 4) Run
st.header("4) Calistirma")
st.caption(
    "Surekli zamanlama autopilot Docker servisi tarafindan yapilir "
    "(`docker compose up -d autopilot`). Asagidaki buton tek seferlik uretir."
)
run_col, note_col = st.columns([1, 3])
with run_col:
    if st.button("▶️ Simdi bir tane uret", type="primary", use_container_width=True):
        with st.spinner("Video uretiliyor ve yukleniyor... (birkac dakika surebilir)"):
            result = autopilot.run_one()
        if result.get("status") == "idle":
            st.info("Bekleyen baslik yok.")
        elif result.get("status") == "uploaded":
            st.success(f"✅ Yuklendi: {result.get('url')}")
        elif result.get("status") == "generated":
            st.success(f"✅ Uretildi (YouTube kapali): {result.get('video_path')}")
        else:
            st.error(f"Durum: {result.get('status')} — {result.get('error', '')}")
with note_col:
    st.write(
        f"Worker dongusu: **{'AKTIF' if config.app.get('autopilot_enabled') else 'PASIF'}** "
        f"(`autopilot_enabled`). Ayarlar bolumunden kaydet ve `autopilot_enabled = true` "
        "yap (config.toml) ya da asagidan ac."
    )
    if st.checkbox(
        "Autopilot dongusunu aktif et (autopilot_enabled)",
        value=bool(config.app.get("autopilot_enabled", False)),
        key="autopilot_enabled_toggle",
    ) != bool(config.app.get("autopilot_enabled", False)):
        config.app["autopilot_enabled"] = st.session_state["autopilot_enabled_toggle"]
        _save_config()
        st.rerun()


# ---------------------------------------------------------------- 5) Status
st.header("5) Durum")
state = autopilot.load_state()
processed = state.get("processed", [])
history = state.get("history", [])
st.write(f"Islenen baslik: **{len(processed)}** · Kayit (history): **{len(history)}**")

if history:
    rows = []
    for h in reversed(history[-50:]):
        rows.append(
            {
                "baslik": h.get("title", ""),
                "format": h.get("aspect", ""),
                "durum": h.get("status", ""),
                "link": h.get("url", ""),
                "hata": (h.get("error") or "")[:120],
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)
else:
    st.info("Henuz kayit yok.")

if st.button("🗑️ Durumu sifirla (processed/history temizle)"):
    autopilot.save_state(autopilot._empty_state())
    st.success("Durum sifirlandi.")
    st.rerun()
