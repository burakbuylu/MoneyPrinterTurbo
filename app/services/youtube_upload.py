"""
YouTube Data API v3 ile otomatik video yukleme.

Kimlik dogrulama (en basit guvenli yol):
  1. Google Cloud'da bir kez ucretsiz OAuth client olusturulur (client_secret.json).
  2. Tarayicidan bir kez izin verilir; refresh token storage'a kaydedilir.
  3. Sonra sunucu tekrar login olmadan otomatik yukler (token suresi gecince
     refresh token ile kendini yeniler).

Detayli kurulum: docs/AUTOPILOT.md
"""

import os
from typing import Optional
from urllib.parse import parse_qs, urlparse

from loguru import logger

from app.config import config
from app.utils import utils

# Sadece "video yukleme" izni isteriz; en dar kapsam, kanal okuma/silme yetkisi vermez.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Paste-code akisinda kullanilan varsayilan redirect. Desktop OAuth client'ta
# kullanici tarayicida buraya yonlenir (baglanti hata verir ama URL'deki ?code=...
# kopyalanir). Web client kullaniyorsan bu URI'yi de izinli redirect'lere ekle.
DEFAULT_REDIRECT_URI = "http://localhost"

YOUTUBE_TITLE_MAX = 100
YOUTUBE_DESC_MAX = 5000
YOUTUBE_TAGS_TOTAL_MAX = 480  # YouTube toplam 500 karakter; guvenli kenar birakiyoruz


class YouTubeUploadService:
    def __init__(self):
        self._reload_config()

    def _reload_config(self):
        self.enabled = config.app.get("youtube_enabled", False)
        self.privacy = str(config.app.get("youtube_privacy", "public")).lower().strip()
        self.category_id = str(config.app.get("youtube_category_id", "22"))
        self.made_for_kids = config.app.get("youtube_made_for_kids", False)

    # ------------------------------------------------------------------ paths
    def client_secret_path(self) -> str:
        cfg = str(config.app.get("youtube_client_secret_file", "client_secret.json")).strip()
        if not cfg:
            cfg = "client_secret.json"
        if os.path.isabs(cfg):
            return cfg
        return os.path.join(utils.root_dir(), cfg)

    def token_path(self) -> str:
        cfg = str(config.app.get("youtube_token_file", "")).strip()
        if cfg:
            return cfg if os.path.isabs(cfg) else os.path.join(utils.root_dir(), cfg)
        return os.path.join(utils.storage_dir(create=True), "youtube_token.json")

    # ------------------------------------------------------------------ status
    def is_configured(self) -> bool:
        """Yukleme yapilabilir mi: acik + client_secret mevcut."""
        self._reload_config()
        return bool(self.enabled and os.path.isfile(self.client_secret_path()))

    def has_token(self) -> bool:
        """Daha once yetkilendirilmis bir token var mi."""
        return os.path.isfile(self.token_path())

    # ------------------------------------------------------------------ oauth
    def _build_flow(self, redirect_uri: str):
        from google_auth_oauthlib.flow import Flow

        return Flow.from_client_secrets_file(
            self.client_secret_path(),
            scopes=SCOPES,
            redirect_uri=redirect_uri,
        )

    def get_auth_url(self, redirect_uri: str = DEFAULT_REDIRECT_URI) -> str:
        """Kullanicinin tarayicida acacagi izin (consent) URL'sini dondurur."""
        flow = self._build_flow(redirect_uri)
        auth_url, _state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",  # refresh_token'i her seferinde garanti et
        )
        return auth_url

    @staticmethod
    def extract_code(code_or_url: str) -> str:
        """Kullanici tam redirect URL'sini yapistirirsa icindeki ?code=... ayikla."""
        value = (code_or_url or "").strip()
        if value.startswith("http://") or value.startswith("https://"):
            qs = parse_qs(urlparse(value).query)
            if "code" in qs and qs["code"]:
                return qs["code"][0]
        return value

    def exchange_code(self, code_or_url: str, redirect_uri: str = DEFAULT_REDIRECT_URI) -> dict:
        """Yetkilendirme kodunu token'a cevirir ve diske kaydeder."""
        code = self.extract_code(code_or_url)
        if not code:
            return {"success": False, "error": "empty authorization code"}
        try:
            flow = self._build_flow(redirect_uri)
            flow.fetch_token(code=code)
            self._save_credentials(flow.credentials)
            logger.success("YouTube OAuth token saved.")
            return {"success": True}
        except Exception as e:
            logger.error(f"YouTube OAuth code exchange failed: {e}")
            return {"success": False, "error": str(e)}

    def authorize_local_server(self, port: int = 8090) -> dict:
        """Yerel makinede tarayici varsa tek tikla yetkilendirme (kolaylik)."""
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_secrets_file(
                self.client_secret_path(), scopes=SCOPES
            )
            creds = flow.run_local_server(port=port, prompt="consent")
            self._save_credentials(creds)
            logger.success("YouTube OAuth token saved (local server).")
            return {"success": True}
        except Exception as e:
            logger.error(f"YouTube local-server OAuth failed: {e}")
            return {"success": False, "error": str(e)}

    def _save_credentials(self, creds):
        path = self.token_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    def _load_credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        path = self.token_path()
        if not os.path.isfile(path):
            return None
        creds = Credentials.from_authorized_user_file(path, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self._save_credentials(creds)
        return creds

    def _build_service(self):
        from googleapiclient.discovery import build

        creds = self._load_credentials()
        if not creds or not creds.valid:
            raise RuntimeError(
                "YouTube credentials missing/invalid. Run OAuth authorization first."
            )
        return build("youtube", "v3", credentials=creds, cache_discovery=False)

    # ------------------------------------------------------------------ upload
    @staticmethod
    def _apply_shorts(title: str, description: str, tags: list) -> tuple:
        """Dikey/kisa video icin #Shorts isaretlerini idempotent ekler."""
        tags = list(tags or [])
        if not any(t.lower() == "shorts" for t in tags):
            tags.insert(0, "shorts")
        if "#shorts" not in title.lower():
            candidate = f"{title} #Shorts".strip()
            title = candidate if len(candidate) <= YOUTUBE_TITLE_MAX else title
        if "#shorts" not in description.lower():
            description = f"{description}\n\n#Shorts".strip()
        return title, description, tags

    @staticmethod
    def _clamp_tags(tags: list) -> list:
        out, total = [], 0
        for t in tags or []:
            t = str(t).strip()
            if not t:
                continue
            # YouTube tag toplam uzunlugunu sinirlar; tasarsak kalanini atla.
            if total + len(t) + 1 > YOUTUBE_TAGS_TOTAL_MAX:
                break
            out.append(t)
            total += len(t) + 1
        return out

    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str = "",
        tags: Optional[list] = None,
        privacy: Optional[str] = None,
        category_id: Optional[str] = None,
        made_for_kids: Optional[bool] = None,
        is_short: bool = False,
    ) -> dict:
        if not self.is_configured():
            return {"success": False, "error": "YouTube upload not configured"}
        if not os.path.isfile(video_path):
            return {"success": False, "error": f"video not found: {video_path}"}

        title = (title or "Untitled").strip()
        description = (description or "").strip()
        tags = list(tags or [])

        if is_short:
            title, description, tags = self._apply_shorts(title, description, tags)

        title = title[:YOUTUBE_TITLE_MAX]
        description = description[:YOUTUBE_DESC_MAX]
        tags = self._clamp_tags(tags)

        privacy = (privacy or self.privacy or "public").lower().strip()
        if privacy not in ("public", "unlisted", "private"):
            privacy = "public"
        category_id = str(category_id or self.category_id or "22")
        if made_for_kids is None:
            made_for_kids = bool(self.made_for_kids)

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": bool(made_for_kids),
            },
        }

        try:
            from googleapiclient.http import MediaFileUpload

            service = self._build_service()
            media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
            request = service.videos().insert(
                part="snippet,status", body=body, media_body=media
            )

            logger.info(f"Uploading to YouTube: '{title}' ({privacy}, short={is_short})")
            response = None
            while response is None:
                _status, response = request.next_chunk()

            video_id = response.get("id")
            url = f"https://youtu.be/{video_id}" if video_id else ""
            logger.success(f"✅ YouTube upload done: {url}")
            return {"success": True, "video_id": video_id, "url": url}
        except Exception as e:
            logger.error(f"YouTube upload failed: {e}")
            return {"success": False, "error": str(e)}


# Singleton
youtube_service = YouTubeUploadService()
