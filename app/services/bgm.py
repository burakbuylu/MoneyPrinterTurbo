"""
Telifsiz arkaplan muzigi.

YouTube Audio Library'nin resmi API'si yok. Bunun yerine Jamendo API'sini
(ucretsiz Creative Commons muzik katalogu) kullaniyoruz. Indirilen parça
`resource/songs/` altina cache'lenir; boylece mevcut pipeline (video.get_bgm_file)
dogrudan kullanabilir. API yoksa/calismazsa yerel sarkilara duser.

Jamendo client_id (ucretsiz): https://developer.jamendo.com
NOT: CC-BY lisansli parçalar icin video aciklamasina atif (attribution) eklenir.
Ticari/monetize kullanim icin lisansi dogrula (Jamendo Licensing).
"""

import os
import random
from typing import Optional, Tuple

import requests
from loguru import logger

from app.config import config
from app.services.material import _get_tls_verify
from app.utils import utils

JAMENDO_API = "https://api.jamendo.com/v3.0/tracks/"


def _license_label(ccurl: str) -> str:
    """license_ccurl'den okunabilir kisa lisans adi cikar."""
    if not ccurl:
        return "Creative Commons"
    u = ccurl.lower()
    parts = []
    if "/by-nc-sa/" in u:
        parts = ["CC", "BY-NC-SA"]
    elif "/by-nc-nd/" in u:
        parts = ["CC", "BY-NC-ND"]
    elif "/by-nc/" in u:
        parts = ["CC", "BY-NC"]
    elif "/by-sa/" in u:
        parts = ["CC", "BY-SA"]
    elif "/by-nd/" in u:
        parts = ["CC", "BY-ND"]
    elif "/by/" in u:
        parts = ["CC", "BY"]
    else:
        return "Creative Commons"
    # surum (orn. 3.0) varsa ekle
    for v in ("4.0", "3.0", "2.0", "1.0"):
        if f"/{v}/" in u:
            parts.append(v)
            break
    return " ".join(parts)


def fetch_bgm(tags: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Jamendo'dan telifsiz bir parça çeker, resource/songs altina indirir.

    Returns:
        (absolute_path | None, attribution | None)
    """
    client_id = str(config.app.get("jamendo_client_id", "")).strip()
    if not client_id:
        logger.warning("jamendo_client_id not set; skipping Jamendo fetch.")
        return None, None

    tags = (tags or config.app.get("jamendo_tags", "happy,upbeat") or "").strip()

    params = {
        "client_id": client_id,
        "format": "json",
        "limit": 1,
        # Rastgelelik icin populer havuzdan rastgele offset.
        "offset": random.randint(0, 199),
        "audioformat": "mp32",
        "audiodlformat": "mp32",
        "include": "musicinfo licenses",
        "order": "popularity_total",
    }
    if tags:
        params["tags"] = tags

    try:
        resp = requests.get(
            JAMENDO_API, params=params, timeout=30, verify=_get_tls_verify()
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Jamendo request failed: {e}")
        return None, None
    except ValueError as e:
        logger.error(f"Jamendo response is not valid JSON: {e}")
        return None, None

    results = data.get("results") or []
    if not results:
        logger.warning(f"Jamendo returned no tracks for tags='{tags}'.")
        return None, None

    track = results[0]
    track_id = str(track.get("id", "")).strip()
    name = (track.get("name") or "Untitled").strip()
    artist = (track.get("artist_name") or "Unknown").strip()
    share_url = (track.get("shareurl") or "").strip()
    license_url = (track.get("license_ccurl") or "").strip()

    download_url = ""
    if track.get("audiodownload_allowed") and track.get("audiodownload"):
        download_url = track["audiodownload"]
    elif track.get("audio"):
        download_url = track["audio"]
    if not download_url or not track_id:
        logger.warning("Jamendo track has no downloadable audio URL.")
        return None, None

    out_path = os.path.join(utils.song_dir(), f"jamendo_{track_id}.mp3")
    attribution = f"Music: {name} by {artist}"
    if license_url:
        attribution += f" ({_license_label(license_url)})"
    if share_url:
        attribution += f" — {share_url}"

    # Cache: ayni parça daha once indirildiyse tekrar indirme.
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        logger.info(f"Jamendo track cached: {out_path}")
        return out_path, attribution

    try:
        with requests.get(
            download_url, stream=True, timeout=120, verify=_get_tls_verify()
        ) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
    except requests.exceptions.RequestException as e:
        logger.error(f"Jamendo download failed: {e}")
        if os.path.isfile(out_path):
            os.remove(out_path)
        return None, None

    if os.path.getsize(out_path) == 0:
        os.remove(out_path)
        logger.error("Jamendo downloaded file is empty.")
        return None, None

    logger.success(f"✅ Jamendo BGM downloaded: {out_path} ({attribution})")
    return out_path, attribution


def ensure_bgm(tags: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Autopilot icin arkaplan muzigi hazirla.

    bgm_source == "jamendo" ve client_id varsa Jamendo'dan ceker; basarisiz olursa
    (None, None) doner ve pipeline yerel `resource/songs` icinden random secer.
    """
    source = str(config.app.get("bgm_source", "local")).lower().strip()
    if source == "jamendo":
        path, attribution = fetch_bgm(tags)
        if path:
            return path, attribution
        logger.warning("Jamendo BGM unavailable; falling back to local songs.")
    return None, None
