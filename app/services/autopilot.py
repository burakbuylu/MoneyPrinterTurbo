"""
Autopilot: basliklar listesinden otomatik video uret + YouTube'a yukle.

Akis (her tur):
  1. titles.txt'den siradaki islenmemis basligi al (satir bazinda `| 16:9` override).
  2. Jamendo'dan telifsiz BGM hazirla (yoksa yerel sarkilara duser).
  3. task.start() ile tam pipeline'i calistir (script/ses/altyazi/gorsel/final mp4).
  4. LLM ile YouTube metadata (title/caption/hashtags) uret.
  5. Aspect'e gore Shorts/normal olarak YouTube'a yukle.
  6. Durumu storage/autopilot/state.json'a yaz.

Surekli zamanlama icin `app/autopilot_worker.py` -> run_forever() (Docker servisi).
WebUI tek seferlik tetik icin run_one() cagirir.
"""

import json
import os
import re
import shutil
import time
from typing import List, Optional, Tuple

import toml
from loguru import logger

from app.config import config
from app.models.schema import VideoAspect, VideoParams
from app.services import bgm, llm
from app.services import task as tm
from app.services.youtube_upload import youtube_service
from app.utils import utils

DEFAULT_VOICE = "en-US-JennyNeural-Female"
MAX_FAILURES = 3  # bir baslik bu kadar kez patlarsa atla (kuyrugu tikamasin)

_ASPECT_ALIASES = {
    "9:16": "9:16", "portrait": "9:16", "shorts": "9:16", "short": "9:16",
    "dikey": "9:16", "vertical": "9:16",
    "16:9": "16:9", "landscape": "16:9", "horizontal": "16:9", "yatay": "16:9",
    "1:1": "1:1", "square": "1:1", "kare": "1:1",
}


# --------------------------------------------------------------------- paths
def autopilot_dir() -> str:
    return utils.storage_dir("autopilot", create=True)


def titles_path() -> str:
    return os.path.join(autopilot_dir(), "titles.txt")


def state_path() -> str:
    return os.path.join(autopilot_dir(), "state.json")


# --------------------------------------------------------------------- config
def _refresh_config():
    """config.toml'u tazele; WebUI'den yapilan ayar degisikliklerini yansit."""
    try:
        fresh = toml.load(config.config_file)
        config.app.clear()
        config.app.update(fresh.get("app", {}))
    except Exception as e:
        logger.warning(f"config refresh failed: {e}")


# --------------------------------------------------------------------- titles
def normalize_aspect(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    return _ASPECT_ALIASES.get(raw.strip().lower())


def parse_line(line: str) -> Optional[Tuple[str, Optional[str]]]:
    """
    Bir satiri (title, aspect|None) olarak ayristir.
    Bos satir ve `#` ile baslayan yorum satirlari None doner.
    `Baslik | 16:9` formatinda opsiyonel aspect override desteklenir.
    """
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return None
    if "|" in line:
        title, _, raw = line.partition("|")
        title = title.strip()
        aspect = normalize_aspect(raw)
    else:
        title, aspect = line, None
    if not title:
        return None
    return title, aspect


def read_titles_raw() -> str:
    path = titles_path()
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_titles_raw(text: str):
    with open(titles_path(), "w", encoding="utf-8") as f:
        f.write(text or "")


def read_titles() -> List[Tuple[str, Optional[str]]]:
    out = []
    for line in read_titles_raw().splitlines():
        parsed = parse_line(line)
        if parsed:
            out.append(parsed)
    return out


def append_titles(lines: List[str]):
    """Yeni basliklari titles.txt sonuna ekle (kalici + WebUI'de gorunur)."""
    if not lines:
        return
    current = read_titles_raw()
    sep = "" if (not current or current.endswith("\n")) else "\n"
    block = "\n".join(line.strip() for line in lines if line.strip())
    write_titles_raw(f"{current}{sep}{block}\n")


def _parse_generated_titles(text: str, count: int) -> List[str]:
    """LLM ciktisindaki basliklari temizle (numara/madde/tirnak at)."""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^\s*\d+[\.\)\-:]\s*", "", s)  # "1. " "1) " "1- "
        s = re.sub(r"^\s*[\-\*•]\s*", "", s)        # madde isaretleri
        s = s.strip().strip('"').strip("'").strip()
        # JSON/markdown artiklari atla
        if not s or s.startswith(("{", "}", "[", "]", "#", "```")):
            continue
        out.append(s)
        if len(out) >= count:
            break
    return out


def generate_titles(topic: str, count: int = 5, language: str = "auto") -> List[str]:
    """
    Kanal konusuna gore YouTube'da trend olmaya yatkin SEO basliklar uret.
    Otomasyon kuyrugu bosaldiginda kendi kendine besler.
    """
    topic = (topic or "").strip()
    if not topic:
        return []
    lang_line = (
        ""
        if (not language or language.lower() == "auto")
        else f"Write the titles in this language: {language}."
    )
    prompt = f"""You are a YouTube growth strategist for a channel about: {topic}.

Generate {count} fresh, highly clickable, SEO-optimized YouTube video titles that are likely to trend RIGHT NOW for this niche.

Rules:
- Output ONLY the titles, each on its own line. No numbering, no quotes, no bullets, no extra commentary.
- Front-load the main search keyword. Combine curiosity with clear value.
- No misleading clickbait, no ALL-CAPS, at most one emoji per title.
- Keep each title under 80 characters.
- Make them specific and current: new models, comparisons, prices, reviews, top-lists, tips, "2026", etc.
{lang_line}
""".strip()
    try:
        resp = llm._generate_response(prompt)
    except Exception as e:
        logger.error(f"Autopilot title generation failed: {e}")
        return []
    titles = _parse_generated_titles(resp, count)
    logger.info(f"Autopilot generated {len(titles)} titles for topic '{topic}'.")
    return titles


# --------------------------------------------------------------------- state
def _empty_state() -> dict:
    return {"processed": [], "failures": {}, "history": [], "last_run_ts": 0}


def load_state() -> dict:
    path = state_path()
    if not os.path.isfile(path):
        return _empty_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # eksik anahtarlari tamamla (geri uyum)
        base = _empty_state()
        base.update(data or {})
        return base
    except Exception as e:
        logger.warning(f"failed to read state.json, resetting: {e}")
        return _empty_state()


def save_state(state: dict):
    with open(state_path(), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _norm_title(title: str) -> str:
    return " ".join((title or "").strip().lower().split())


def next_pending(
    titles: List[Tuple[str, Optional[str]]], state: dict
) -> Optional[Tuple[str, Optional[str]]]:
    processed = set(state.get("processed", []))
    for title, aspect in titles:
        if _norm_title(title) not in processed:
            return title, aspect
    return None


# --------------------------------------------------------------------- run
def _resolve_aspect(aspect: Optional[str]) -> str:
    if aspect:
        return aspect
    return normalize_aspect(str(config.app.get("autopilot_default_aspect", "9:16"))) or "9:16"


def _subtitle_bg():
    """
    Altyazi arkaplan stili (config: autopilot_subtitle_background):
      - "rounded": metni saran dar siyah plaka (varsayilan, en hos durur)
      - "box":     tam genislikte siyah kutu (eski davranis)
      - "none":    arkaplan yok, sadece siyah kenar (stroke)
    Returns: (text_background_color, rounded_subtitle_background)
    """
    mode = str(config.app.get("autopilot_subtitle_background", "rounded")).lower().strip()
    if mode == "none":
        return False, False
    if mode == "box":
        return "#000000", False
    return "#000000", True  # rounded (default)


def _build_params(title: str, aspect: str) -> VideoParams:
    voice = str(config.app.get("autopilot_voice_name", "") or "").strip() or DEFAULT_VOICE
    text_bg, rounded_bg = _subtitle_bg()
    params = VideoParams(
        video_subject=title,
        video_aspect=aspect,
        voice_name=voice,
        video_language=str(config.app.get("autopilot_video_language", "") or ""),
        video_source=str(config.app.get("autopilot_video_source", "pexels") or "pexels"),
        paragraph_number=int(config.app.get("autopilot_paragraph_number", 1) or 1),
        bgm_volume=float(config.app.get("autopilot_bgm_volume", 0.2) or 0.2),
        subtitle_enabled=True,
        text_background_color=text_bg,
        rounded_subtitle_background=rounded_bg,
        stroke_color="#000000",
        stroke_width=float(config.app.get("autopilot_subtitle_stroke_width", 1.5) or 1.5),
    )
    return params


def _build_description(meta: dict, attribution: Optional[str]) -> str:
    parts = []
    caption = (meta.get("caption") or "").strip()
    if caption:
        parts.append(caption)
    hashtags = meta.get("hashtags") or []
    if hashtags:
        parts.append(" ".join(hashtags))
    if attribution:
        parts.append(attribution)
    return "\n\n".join(parts)


def _maybe_autogenerate(titles, state) -> Optional[Tuple[str, Optional[str]]]:
    """Kuyruk bosaldiginda konuya gore yeni basliklar uret, titles.txt'ye ekle, sirayi sec."""
    if not config.app.get("autopilot_auto_titles", False):
        return None
    topic = str(config.app.get("autopilot_topic", "") or "").strip()
    if not topic:
        logger.warning("autopilot_auto_titles is on but autopilot_topic is empty.")
        return None
    count = int(config.app.get("autopilot_auto_titles_count", 5) or 5)
    lang = str(config.app.get("autopilot_video_language", "") or "") or "auto"

    new_titles = generate_titles(topic, count, lang)
    if not new_titles:
        return None

    # Mevcut basliklar + islenenlerle cakisanlari ele.
    existing = {_norm_title(t) for t, _ in titles} | set(state.get("processed", []))
    fresh = [t for t in new_titles if _norm_title(t) not in existing]
    if not fresh:
        logger.info("Autopilot: generated titles all duplicates; skipping.")
        return None

    append_titles(fresh)
    logger.success(f"Autopilot: added {len(fresh)} auto-generated titles to queue.")
    return next_pending(read_titles(), state)


def run_one() -> dict:
    """Tek bir basligi uret ve (yapilandirilmissa) yukle. Sonuc dict'i doner."""
    _refresh_config()
    titles = read_titles()
    state = load_state()
    state["last_run_ts"] = time.time()

    pick = next_pending(titles, state)
    if not pick:
        # Kuyruk bos: auto-title acik ve konu varsa kendi basliklarini uret.
        pick = _maybe_autogenerate(titles, state)
        titles = read_titles()
    if not pick:
        logger.info("Autopilot: no pending titles.")
        save_state(state)
        return {"status": "idle", "message": "no pending titles"}

    title, raw_aspect = pick
    aspect = _resolve_aspect(raw_aspect)
    is_short = aspect == VideoAspect.portrait.value  # "9:16"
    norm = _norm_title(title)
    logger.info(f"Autopilot: processing '{title}' aspect={aspect} short={is_short}")

    entry = {
        "title": title,
        "aspect": aspect,
        "is_short": is_short,
        "status": "failed",
        "ts": time.time(),
    }

    try:
        # 1) BGM
        bgm_path, attribution = bgm.ensure_bgm()

        # 2) Pipeline
        params = _build_params(title, aspect)
        if bgm_path:
            params.bgm_type = "random"
            params.bgm_file = os.path.basename(bgm_path)
        else:
            params.bgm_type = "random"
            params.bgm_file = ""

        task_id = utils.get_uuid()
        entry["task_id"] = task_id
        result = tm.start(task_id, params, stop_at="video")
        if not result or not result.get("videos"):
            raise RuntimeError("video generation failed (no output)")
        video_path = result["videos"][0]
        script = result.get("script", "") or ""
        entry["video_path"] = video_path

        # 3) Metadata
        lang = str(config.app.get("autopilot_video_language", "") or "") or "auto"
        meta = llm.generate_social_metadata(
            video_subject=title,
            video_script=script,
            language=lang,
            platform="youtube_shorts",
        )
        yt_title = (meta.get("title") or title).strip()
        description = _build_description(meta, attribution)
        tags = [str(h).lstrip("#").strip() for h in (meta.get("hashtags") or []) if h]

        # 4) Upload
        if youtube_service.is_configured():
            up = youtube_service.upload_video(
                video_path=video_path,
                title=yt_title,
                description=description,
                tags=tags,
                is_short=is_short,
            )
            if up.get("success"):
                entry["status"] = "uploaded"
                entry["video_id"] = up.get("video_id")
                entry["url"] = up.get("url")
                # Upload basarili -> diskte tutmaya gerek yok; task klasorunu temizle.
                if config.app.get("autopilot_delete_after_upload", True):
                    _cleanup_task(task_id)
                    entry["cleaned"] = True
            else:
                entry["status"] = "upload_failed"
                entry["error"] = up.get("error")
        else:
            entry["status"] = "generated"
            entry["message"] = "youtube upload not configured"

        # basari sayilan durumlar -> islendi olarak isaretle
        if entry["status"] in ("uploaded", "generated"):
            if norm not in state["processed"]:
                state["processed"].append(norm)
            state["failures"].pop(norm, None)
        else:
            _record_failure(state, norm)

    except Exception as e:
        logger.error(f"Autopilot failed for '{title}': {e}")
        entry["status"] = "failed"
        entry["error"] = str(e)
        _record_failure(state, norm)

    state["history"].append(entry)
    state["history"] = state["history"][-200:]  # tarihçeyi sinirla
    save_state(state)
    return entry


def _cleanup_task(task_id: str):
    """Upload sonrasi task klasorunu (video, ses, materyaller) sil; disk sismesin."""
    try:
        task_path = utils.task_dir(task_id)
        if os.path.isdir(task_path):
            shutil.rmtree(task_path, ignore_errors=True)
            logger.info(f"Autopilot: cleaned task dir {task_path}")
    except Exception as e:
        logger.warning(f"Autopilot: task cleanup failed for {task_id}: {e}")


def _record_failure(state: dict, norm: str):
    """Basarisizligi say; esik asilirsa basligi atla (kuyrugu tikamasin)."""
    n = int(state["failures"].get(norm, 0)) + 1
    state["failures"][norm] = n
    if n >= MAX_FAILURES:
        if norm not in state["processed"]:
            state["processed"].append(norm)
        logger.warning(f"Autopilot: giving up on title after {n} failures: {norm}")


def run_one_safe() -> Optional[dict]:
    try:
        return run_one()
    except Exception as e:
        logger.error(f"Autopilot run_one crashed: {e}")
        return None


def run_forever(stop_event=None):
    """Surekli dongu: enabled ise interval'de bir run_one. Docker worker bunu calistirir."""
    logger.info("🚀 Autopilot worker started.")
    _refresh_config()
    if config.app.get("autopilot_run_on_start", False):
        run_one_safe()

    while not (stop_event and stop_event.is_set()):
        _refresh_config()
        interval_h = float(config.app.get("autopilot_interval_hours", 6) or 6)
        interval_s = max(60.0, interval_h * 3600.0)
        logger.info(f"Autopilot: next cycle in {interval_h:g}h")

        slept = 0.0
        while slept < interval_s:
            if stop_event and stop_event.is_set():
                logger.info("Autopilot worker stopping.")
                return
            step = min(5.0, interval_s - slept)
            time.sleep(step)
            slept += step

        _refresh_config()
        if not config.app.get("autopilot_enabled", False):
            logger.info("Autopilot disabled (autopilot_enabled=false); skipping cycle.")
            continue
        run_one_safe()
