"""
tiktok_publish_client.py - Client xuất bản video chính thức lên TikTok Studio qua HTTP REST API.

Thực thi Bước 5:
POST /tiktok/web/project/post/v1/
Ký số: X-Bogus, X-Gnarly, msToken, tz_name
Tạo payload single_post_req_list: Caption, markup text <h id="...">, hashtags, text_extra,
vedit_common_info, privacy settings, poster_delay.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from tiktok_cookie_extractor import cookies_to_header_str
from tiktok_signer_bridge import sign_tiktok_url
from tls_client_engine import TLSSessionWrapper

logger = logging.getLogger(__name__)

POST_API_BASE = "https://www.tiktok.com/tiktok/web/project/post/v1/"


class TikTokPublishError(Exception):
    pass


class TikTokAccountBlockedError(TikTokPublishError):
    pass


class TikTokLoginRequiredError(TikTokPublishError):
    pass


@dataclass
class PublishResult:
    item_id: str
    video_url: str
    raw_response: Dict[str, Any]
    status_code: int = 0
    status_msg: str = ""


def format_caption_and_markup(caption: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Phân tích caption thành markup_text và text_extra theo chuẩn TikTok Studio.
    
    Quy tắc:
    - Các thẻ hashtag #abc được bọc trong <h id="">#abc</h> trong markup_text.
    - text_extra lưu mảng các đối tượng {type: 1, hashtag_name: 'abc', start: int, end: int}.
    - start/end là vị trí ký tự trong chuỗi caption thuần túy (không tính thẻ <h>).
    """
    clean_caption = (caption or "").strip()
    if not clean_caption:
        return "", []

    text_extra: List[Dict[str, Any]] = []
    # Regex nhận diện hashtag (hỗ trợ cả tiếng Việt unicode)
    pattern = re.compile(r"#([\w\u00C0-\u1EF9]+)", re.UNICODE)

    for match in pattern.finditer(clean_caption):
        tag_name = match.group(1)
        text_extra.append({
            "type": 1,
            "hashtag_name": tag_name,
            "start": match.start(),
            "end": match.end(),
        })

    # Xây dựng markup_text: thay thế các hashtag bằng <h id="">#tag</h>
    # Để tránh xung đột offset khi replace, ta thay thế từ cuối lên đầu
    markup_text = clean_caption
    for extra in reversed(text_extra):
        tag_raw = f"#{extra['hashtag_name']}"
        start = extra["start"]
        end = extra["end"]
        wrapped = f'<h id="">{tag_raw}</h>'
        markup_text = markup_text[:start] + wrapped + markup_text[end:]

    return markup_text, text_extra


class TikTokPublishClient:
    def __init__(
        self,
        session_cookies: Dict[str, str],
        proxy_cfg: Optional[Dict[str, Any]] = None,
        profile_path: Optional[str | Path] = None,
        user_agent: Optional[str] = None,
        tz_name: str = "America/New_York",
        status_callback: Optional[Callable[[str], Any]] = None,
    ):
        self.session_cookies = session_cookies
        self.proxy_cfg = proxy_cfg
        self.profile_path = profile_path
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        if isinstance(tz_name, dict):
            tz_str = tz_name.get("name") or tz_name.get("timezone") or tz_name.get("tz") or "America/New_York"
        else:
            tz_str = str(tz_name or "America/New_York").strip()
        self.tz_name = tz_str or "America/New_York"
        self.status_callback = status_callback or (lambda _: None)

        self.proxy_session = TLSSessionWrapper(proxy_cfg=self.proxy_cfg, timeout=45.0)

    def _log(self, message: str) -> None:
        try:
            self.status_callback(str(message))
        except Exception:
            pass
        logger.info(message)

    def publish_video(
        self,
        vid: str,
        video_meta: Dict[str, Any],
        caption: str = "",
        visibility_type: int = 0,
        allow_comment: bool = True,
        allow_duet: bool = True,
        allow_stitch: bool = True,
        allow_content_reuse: bool = True,
        poster_delay_ms: int = 1000,
        playlist_id: str = "",
    ) -> PublishResult:
        """Gửi request Bước 5 xuất bản bài đăng chính thức lên trang cá nhân TikTok."""
        self._log("Bước 5/5 (API): Đang ký chữ ký số và xuất bản bài đăng...")

        width = int(video_meta.get("Width") or 1080)
        height = int(video_meta.get("Height") or 1920)
        duration_sec = float(video_meta.get("Duration") or 15.0)
        duration_ms = max(1000, int(duration_sec * 1000))

        clean_caption = (caption or "").strip()
        markup_text, text_extra = format_caption_and_markup(clean_caption)

        # 1. Chuẩn bị payload JSON theo đúng chuẩn Ground Truth TikTok Studio
        creation_id = str(uuid.uuid4()).replace("-", "")[:21]
        payload = {
            "post_common_info": {
                "creation_id": creation_id,
                "enter_post_page_from": 2,
                "post_type": 3,
            },
            "feature_common_info_list": [
                {
                    "geofencing_regions": [],
                    "playlist_name": "",
                    "playlist_id": str(playlist_id or ""),
                    "tcm_params": '{"commerce_toggle_info":{}}',
                    "sound_exemption": 0,
                    "anchors": [],
                    "vedit_common_info": {
                        "draft": "",
                        "video_id": vid,
                    },
                    "privacy_setting_info": {
                        "visibility_type": int(visibility_type),
                        "allow_duet": 1 if allow_duet else 0,
                        "allow_stitch": 1 if allow_stitch else 0,
                        "allow_comment": 1 if allow_comment else 0,
                        "allow_content_reuse": 1 if allow_content_reuse else 0,
                        "allow_ai_remix": 2,
                    },
                    "content_check_id": "",
                }
            ],
            "single_post_req_list": [
                {
                    "batch_index": 0,
                    "video_id": vid,
                    "is_long_video": 1 if duration_sec > 60 else 0,
                    "single_post_feature_info": {
                        "text": clean_caption,
                        "text_extra": text_extra,
                        "markup_text": markup_text,
                        "music_info": {"origin_volume": "100"},
                        "poster_delay": max(0, int(poster_delay_ms)),
                        "cloud_edit_video_height": height,
                        "cloud_edit_video_width": width,
                        "cloud_edit_is_use_video_canvas": False,
                        "has_original_audio": 1,
                        "is_upload_audio_track": False,
                        "video_track_time_range_list": [
                            {
                                "start_time_in_ms": 0,
                                "end_time_in_ms": duration_ms,
                            }
                        ],
                        "mature_theme_type": 0,
                    },
                }
            ],
        }

        body_str = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)

        # 2. Xây dựng URL cơ sở để ký
        ms_token = self.session_cookies.get("msToken") or ""
        tz_encoded = urllib.parse.quote(self.tz_name)

        base_params = [
            ("app_name", "tiktok_web"),
            ("channel", "tiktok_web"),
            ("device_platform", "web"),
            ("aid", "1988"),
        ]
        if self.tz_name:
            base_params.append(("tz_name", self.tz_name))
        if ms_token:
            base_params.append(("msToken", ms_token))

        query_str = urllib.parse.urlencode(base_params)
        raw_url_to_sign = f"{POST_API_BASE}?{query_str}"

        # 3. Ký X-Bogus & X-Gnarly qua Signer Bridge (kèm payload body)
        try:
            x_bogus, x_gnarly = sign_tiktok_url(
                url=raw_url_to_sign,
                body=body_str,
                profile_path=self.profile_path,
                user_agent=self.user_agent,
            )
            self._log("  -> Ký số thành công (X-Bogus & X-Gnarly đã sẵn sàng)")
        except Exception as e:
            raise TikTokPublishError(f"Không thể ký chữ ký số X-Bogus/X-Gnarly: {e}") from e

        # 4. Gắn các chữ ký vào URL gửi đi
        final_params = list(base_params)
        if x_bogus:
            final_params.append(("X-Bogus", x_bogus))
        if x_gnarly:
            final_params.append(("X-Gnarly", x_gnarly))

        final_url = f"{POST_API_BASE}?{urllib.parse.urlencode(final_params)}"

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.tiktok.com/tiktokstudio/upload?from=creator_center&tab=video",
            "Origin": "https://www.tiktok.com",
            "Content-Type": "application/json",
            "Cookie": cookies_to_header_str(self.session_cookies),
        }

        # 5. Gửi request đăng bài qua Proxy
        try:
            resp = self.proxy_session.post(final_url, data=body_str.encode('utf-8'), headers=headers)
        except Exception as e:
            raise TikTokPublishError(f"Lỗi gửi request đăng bài tới TikTok Studio: {e}") from e

        if resp.status_code != 200:
            raise TikTokPublishError(f"TikTok Studio trả mã lỗi HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            data = resp.json()
        except Exception as e:
            raise TikTokPublishError(f"Phản hồi xuất bản không phải JSON hợp lệ: {e}") from e

        status_code = data.get("status_code", -1)
        status_msg = str(data.get("status_msg") or data.get("message") or "")

        # Kiểm tra phản hồi lỗi nghiệp vụ
        if status_code != 0:
            msg_lower = status_msg.lower()
            if any(term in msg_lower for term in ("prevented from posting", "temporarily blocked", "restricted")):
                raise TikTokAccountBlockedError(f"Tài khoản bị TikTok tạm khóa đăng bài: {status_msg}")
            if any(term in msg_lower for term in ("login", "not login", "session", "expired")):
                raise TikTokLoginRequiredError(f"Phiên đăng nhập hết hạn khi gửi bài đăng: {status_msg}")
            raise TikTokPublishError(f"TikTok từ chối xuất bản bài viết (status_code={status_code}): {status_msg} | data: {data}")

        # Trích xuất item_id
        resp_list = data.get("single_post_resp_list") or []
        if not resp_list:
            raise TikTokPublishError(f"Phản hồi thành công nhưng không tìm thấy single_post_resp_list: {data}")

        first_resp = resp_list[0]
        item_id = str(first_resp.get("item_id") or "")
        inner_status = first_resp.get("status_code", 0)
        inner_msg = str(first_resp.get("status_msg") or "")

        if inner_status != 0:
            raise TikTokPublishError(f"Bài viết bị lỗi nội bộ (status_code={inner_status}): {inner_msg}")

        if not item_id or item_id == "0":
            raise TikTokPublishError(f"Không nhận được item_id hợp lệ từ TikTok: {first_resp}")

        video_url = f"https://www.tiktok.com/video/{item_id}"
        self._log(f"Bước 5/5 (API): ĐÃ ĐĂNG THÀNH CÔNG! Link: {video_url}")

        return PublishResult(
            item_id=item_id,
            video_url=video_url,
            raw_response=data,
            status_code=status_code,
            status_msg=status_msg,
        )
