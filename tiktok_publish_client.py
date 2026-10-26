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
import secrets
import string
import time
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
    is_scheduled: bool = False
    schedule_time: Optional[int] = None


@dataclass
class ScheduleCapabilityResult:
    can_schedule: bool
    check_status: str  # "allowed", "disabled", "unknown"
    server_time: int  # in seconds
    server_time_source: str = "server"  # "server" | "local"
    reason: str = ""
    ab_value: str = ""
    logid: str = ""
    raw_response: Dict[str, Any] = field(default_factory=dict)


def check_schedule_capability(
    session_cookies: Dict[str, str],
    proxy_cfg: Optional[Dict[str, Any]] = None,
    user_agent: Optional[str] = None,
    timeout: float = 20.0,
) -> ScheduleCapabilityResult:
    """Kiểm tra tài khoản có được cấp quyền lên lịch đăng bài (Schedule Posting) trên TikTok hay không.
    
    Gọi endpoint AB test chính thức:
    GET https://www.tiktok.com/api/v1/web/project/get/ab/?browser=chrome&ab_param=[{"first_key":"tiktok_web","second_key":"web_creation_schedule_posting","ab_type":1}]
    
    Phân loại rõ ràng check_status:
    - 'allowed': TikTok server xác nhận cờ ab_value == "1"
    - 'disabled': TikTok server xác nhận cờ ab_value == "0"
    - 'unknown': Lỗi mạng, session hết hạn hoặc phản hồi không xác định (không đánh đồng là account bị block).
    """
    ua = user_agent or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    ab_param_val = '[{"first_key":"tiktok_web","second_key":"web_creation_schedule_posting","ab_type":1}]'
    url = f"https://www.tiktok.com/api/v1/web/project/get/ab/?browser=chrome&ab_param={urllib.parse.quote(ab_param_val)}"
    
    headers = {
        "User-Agent": str(ua).encode("latin-1", "ignore").decode("latin-1"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.tiktok.com/tiktokstudio/upload?from=creator_center&tab=video",
        "Origin": "https://www.tiktok.com",
        "Cookie": cookies_to_header_str(session_cookies),
    }

    try:
        session = TLSSessionWrapper(proxy_cfg=proxy_cfg, timeout=timeout)
        resp = session.get(url, headers=headers)
    except Exception as e:
        logger.warning(f"Lỗi kết nối khi kiểm tra quyền lên lịch TikTok: {e}")
        return ScheduleCapabilityResult(
            can_schedule=False,
            check_status="unknown",
            server_time=int(time.time()),
            server_time_source="local",
            reason=f"Lỗi kết nối mạng/proxy khi kiểm tra quyền lên lịch: {e}",
        )

    if resp.status_code != 200:
        return ScheduleCapabilityResult(
            can_schedule=False,
            check_status="unknown",
            server_time=int(time.time()),
            server_time_source="local",
            reason=f"TikTok trả mã lỗi HTTP {resp.status_code} khi kiểm tra quyền lên lịch",
        )

    try:
        data = resp.json()
    except Exception as e:
        return ScheduleCapabilityResult(
            can_schedule=False,
            check_status="unknown",
            server_time=int(time.time()),
            server_time_source="local",
            reason=f"TikTok trả dữ liệu không phải JSON: {e}",
        )

    if not isinstance(data, dict):
        return ScheduleCapabilityResult(
            can_schedule=False,
            check_status="unknown",
            server_time=int(time.time()),
            server_time_source="local",
            reason="Phản hồi từ TikTok không phải JSON Object",
        )

    status_code = data.get("status_code")
    if type(status_code) is not int or status_code != 0:
        msg = str(data.get("status_msg") or data.get("message") or f"status_code={status_code}")
        return ScheduleCapabilityResult(
            can_schedule=False,
            check_status="unknown",
            server_time=int(time.time()),
            server_time_source="local",
            reason=f"TikTok từ chối truy vấn quyền lên lịch: {msg}",
            raw_response=data,
        )

    extra = data.get("extra")
    extra = extra if isinstance(extra, dict) else {}
    now_ms = extra.get("now")
    if type(now_ms) is int and now_ms > 0:
        server_time = now_ms // 1000
        server_time_source = "server"
    else:
        server_time = int(time.time())
        server_time_source = "local"

    log_pb = data.get("log_pb")
    log_pb = log_pb if isinstance(log_pb, dict) else {}
    logid = str(extra.get("logid") or log_pb.get("impr_id") or "")

    entries = data.get("ab_param")
    if not isinstance(entries, list):
        return ScheduleCapabilityResult(
            can_schedule=False,
            check_status="unknown",
            server_time=server_time,
            server_time_source=server_time_source,
            reason="Trường ab_param không hợp lệ",
            logid=logid,
            raw_response=data,
        )

    target_ab = next(
        (
            item for item in entries
            if isinstance(item, dict)
            and item.get("first_key") == "tiktok_web"
            and item.get("second_key") == "web_creation_schedule_posting"
            and item.get("ab_type") == 1
        ),
        None,
    )

    if not target_ab:
        return ScheduleCapabilityResult(
            can_schedule=False,
            check_status="unknown",
            server_time=server_time,
            server_time_source=server_time_source,
            reason="Không tìm thấy cờ web_creation_schedule_posting (ab_type=1) trong phản hồi từ TikTok",
            logid=logid,
            raw_response=data,
        )

    ab_val = target_ab.get("ab_value")
    if ab_val == "1":
        return ScheduleCapabilityResult(
            can_schedule=True,
            check_status="allowed",
            server_time=server_time,
            server_time_source=server_time_source,
            reason="",
            ab_value="1",
            logid=logid,
            raw_response=data,
        )
    elif ab_val == "0":
        return ScheduleCapabilityResult(
            can_schedule=False,
            check_status="disabled",
            server_time=server_time,
            server_time_source=server_time_source,
            reason="Tính năng lên lịch đăng bài bị vô hiệu hóa cho tài khoản này (ab_value='0')",
            ab_value="0",
            logid=logid,
            raw_response=data,
        )
    else:
        return ScheduleCapabilityResult(
            can_schedule=False,
            check_status="unknown",
            server_time=server_time,
            server_time_source=server_time_source,
            reason=f"Giá trị cờ web_creation_schedule_posting không xác định: {ab_val!r}",
            ab_value=str(ab_val or ""),
            logid=logid,
            raw_response=data,
        )


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

    def check_schedule_capability(self) -> ScheduleCapabilityResult:
        """Kiểm tra tài khoản có quyền lên lịch đăng bài trên TikTok hay không."""
        return check_schedule_capability(
            session_cookies=self.session_cookies,
            proxy_cfg=self.proxy_cfg,
            user_agent=self.user_agent,
        )

    def _log(self, message: str) -> None:
        try:
            self.status_callback(str(message))
        except Exception:
            pass
        logger.info(message)

    def acknowledge_schedule_terms(self) -> bool:
        """Xác nhận điều khoản lưu trữ video hẹn giờ (POST /api/v1/post_schedule/ack/?ack_type=2&aid=1988)."""
        url = "https://www.tiktok.com/api/v1/post_schedule/ack/?ack_type=2&aid=1988"
        headers = {
            "User-Agent": str(self.user_agent).encode("latin-1", "ignore").decode("latin-1"),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.tiktok.com/tiktokstudio/upload?from=creator_center&tab=video",
            "Origin": "https://www.tiktok.com",
            "Cookie": cookies_to_header_str(self.session_cookies),
        }
        try:
            resp = self.proxy_session.post(url, headers=headers)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    st = data.get("status_code")
                    if st == 0 or st is None:
                        self._log("  -> Đã xác nhận điều khoản lưu trữ hẹn giờ (post_schedule/ack status=200/0)")
                        return True
                except Exception:
                    self._log("  -> Đã gửi ACK điều khoản lưu trữ hẹn giờ (post_schedule/ack HTTP 200)")
                    return True
        except Exception as e:
            logger.warning(f"Acknowledge schedule terms failed: {e}")
        return False

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
        schedule_time: Optional[int] = None,
        server_time_ref: Optional[int] = None,
        server_time_mono: Optional[float] = None,
    ) -> PublishResult:
        """Gửi request Bước 5 xuất bản bài đăng chính thức lên trang cá nhân TikTok (hỗ trợ hẹn giờ trực tiếp)."""
        action_desc = "lên lịch đăng bài" if schedule_time else "xuất bản bài đăng"
        self._log(f"Bước 5/5 (API): Đang ký chữ ký số và {action_desc}...")

        width = int(video_meta.get("Width") or 1080)
        height = int(video_meta.get("Height") or 1920)
        duration_sec = float(video_meta.get("Duration") or 15.0)
        duration_ms = max(1000, int(duration_sec * 1000))

        clean_caption = (caption or "").strip()
        markup_text, text_extra = format_caption_and_markup(clean_caption)

        # 1. Chuẩn bị payload JSON theo đúng chuẩn Ground Truth TikTok Studio
        creation_id = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(21))

        feature_item: Dict[str, Any] = {
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
        if schedule_time is not None:
            if type(schedule_time) is not int or schedule_time <= 0:
                raise TikTokPublishError(f"Giá trị schedule_time không hợp lệ (phải là Unix epoch int dương): {schedule_time!r}")
            # Căn chỉnh schedule_time về mốc 5 phút (300s) chuẩn TikTok Studio
            if schedule_time % 300 != 0:
                aligned_epoch = ((schedule_time + 299) // 300) * 300
                self._log(f"  -> Tự động căn chỉnh schedule_time về mốc 5 phút: {schedule_time} -> {aligned_epoch}")
                schedule_time = aligned_epoch

            # Sử dụng đồng hồ server nếu được truyền vào, có bù trôi monotonic
            if server_time_ref is not None and server_time_ref > 0:
                if server_time_mono is not None and server_time_mono > 0:
                    now_sec = server_time_ref + max(0, int(time.monotonic() - server_time_mono))
                else:
                    now_sec = server_time_ref
            else:
                now_sec = int(time.time())

            diff = schedule_time - now_sec
            if diff < 900:
                raise TikTokPublishError(
                    f"Thời gian lên lịch không hợp lệ: schedule_time={schedule_time} "
                    f"cách hiện tại {diff}s (TikTok yêu cầu tối thiểu 15 phút = 900s)."
                )
            if diff > 864000:
                raise TikTokPublishError(
                    f"Thời gian lên lịch vượt quá giới hạn TikTok: schedule_time={schedule_time} "
                    f"cách hiện tại {diff}s (TikTok tối đa 10 ngày = 864,000s)."
                )
            # Tự động gửi ACK điều khoản lưu trữ video hẹn giờ lên TikTok
            self.acknowledge_schedule_terms()
            feature_item["schedule_time"] = schedule_time
            # Theo chuẩn TikTok Studio (Ground Truth), video hẹn giờ thiết lập duet & stitch thành None (null)
            feature_item["privacy_setting_info"]["allow_duet"] = None
            feature_item["privacy_setting_info"]["allow_stitch"] = None
            self._log(f"  -> Đã gán tham số schedule_time vào TikTok API: {schedule_time} (cách hiện tại {diff // 60} phút)")

        payload = {
            "post_common_info": {
                "creation_id": creation_id,
                "enter_post_page_from": 2,
                "post_type": 3,
            },
            "feature_common_info_list": [feature_item],
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

        if not x_bogus or not x_gnarly:
            raise TikTokPublishError("Signer bridge không tạo đủ cặp chữ ký X-Bogus và X-Gnarly")

        # 4. Gắn các chữ ký vào URL gửi đi
        final_params = list(base_params)
        if x_bogus:
            final_params.append(("X-Bogus", x_bogus))
        if x_gnarly:
            final_params.append(("_signature", x_gnarly))
            final_params.append(("X-Gnarly", x_gnarly))

        final_url = f"{POST_API_BASE}?{urllib.parse.urlencode(final_params)}"

        headers = {
            "User-Agent": str(self.user_agent).encode("latin-1", "ignore").decode("latin-1"),
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

        status_code = data.get("status_code")
        status_msg = str(data.get("status_msg") or data.get("message") or "")

        # Kiểm tra phản hồi lỗi nghiệp vụ
        if type(status_code) is not int or status_code != 0:
            msg_lower = status_msg.lower()
            if any(term in msg_lower for term in ("prevented from posting", "temporarily blocked", "restricted")):
                raise TikTokAccountBlockedError(f"Tài khoản bị TikTok tạm khóa đăng bài: {status_msg}")
            if any(term in msg_lower for term in ("login", "not login", "session", "expired")):
                raise TikTokLoginRequiredError(f"Phiên đăng nhập hết hạn khi gửi bài đăng: {status_msg}")
            raise TikTokPublishError(f"TikTok từ chối xuất bản bài viết (status_code={status_code}): {status_msg} | data: {data}")

        # Kiểm tra fatal_item_ids trong extra nếu có
        extra = data.get("extra")
        if isinstance(extra, dict) and extra.get("fatal_item_ids"):
            raise TikTokPublishError(f"TikTok báo lỗi bài đăng chứa fatal_item_ids: {extra.get('fatal_item_ids')}")

        # Trích xuất item_id
        resp_list = data.get("single_post_resp_list") or []
        if not resp_list or not isinstance(resp_list, list):
            raise TikTokPublishError(f"Phản hồi thành công nhưng không tìm thấy single_post_resp_list: {data}")

        first_resp = resp_list[0]
        if not isinstance(first_resp, dict):
            raise TikTokPublishError(f"Mục single_post_resp_list không phải object: {first_resp}")

        inner_status = first_resp.get("status_code")
        if type(inner_status) is not int or inner_status != 0:
            inner_msg = str(first_resp.get("status_msg") or "")
            raise TikTokPublishError(f"Bài viết bị lỗi nội bộ (status_code={inner_status}): {inner_msg}")

        item_id = str(first_resp.get("item_id") or "")
        if not item_id or item_id == "0":
            raise TikTokPublishError(f"Không nhận được item_id hợp lệ từ TikTok: {first_resp}")

        video_url = f"https://www.tiktok.com/video/{item_id}"
        if schedule_time:
            self._log(f"Bước 5/5 (API): ĐÃ LÊN LỊCH ĐĂNG THÀNH CÔNG TRÊN SERVER TIKTOK! (Epoch: {schedule_time}) Link: {video_url}")
        else:
            self._log(f"Bước 5/5 (API): ĐÃ ĐĂNG THÀNH CÔNG! Link: {video_url}")

        return PublishResult(
            item_id=item_id,
            video_url=video_url,
            raw_response=data,
            status_code=status_code,
            status_msg=status_msg,
            is_scheduled=bool(schedule_time),
            schedule_time=schedule_time,
        )
