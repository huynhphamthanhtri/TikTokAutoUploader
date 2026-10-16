"""
tiktok_direct_uploader.py - Bộ điều phối toàn trình Direct API Upload cho TikTok.

Tích hợp toàn diện:
1. Trích xuất cookie Live (SQLite DPAPI) / Config fallback.
2. Đọc vân tay profile (data.huynhthang) lấy User-Agent và Timezone.
3. Chạy 4 bước VOD Upload (Auth, SigV4 Apply, Chunks với proxy bypass, Commit).
4. Hỗ trợ stop_before_post (Pre-Post Dry-run mode: upload video nhưng dừng trước khi Post).
5. Chạy Bước 5 Web Project Post API (Ký X-Bogus, X-Gnarly qua Node Worker).
6. Trả về đối tượng UploadResult chuẩn tương thích 100% với main.py và patchright_upload.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fast_mp4_meta import get_fast_mp4_metadata
from patchright_upload import (
    REJECTION_SCOPE_ACCOUNT_BLOCKED,
    REJECTION_SCOPE_VIDEO,
    UploadResult,
)
from tiktok_cookie_extractor import (
    extract_user_id_from_cookies,
    get_profile_tiktok_cookies,
)
from tiktok_publish_client import (
    TikTokAccountBlockedError,
    TikTokLoginRequiredError,
    TikTokPublishClient,
    TikTokPublishError,
)
from tiktok_signer_bridge import read_profile_fingerprint
from tiktok_vod_uploader import (
    TikTokVodUploader,
    VodAuthExpiredError,
    VodUploadError,
)

logger = logging.getLogger(__name__)


def run_direct_upload(
    profile_name: str,
    profile_config: Dict[str, Any],
    video_path: str | Path,
    caption: str = "",
    visibility_type: int = 0,
    allow_comment: bool = True,
    allow_duet: bool = True,
    allow_stitch: bool = True,
    allow_content_reuse: bool = True,
    poster_delay_ms: int = 1000,
    playlist_id: str = "",
    status_callback: Optional[Callable[[str], Any]] = None,
    stop_before_post: bool = False,
    cancellation_event: Any = None,
    workspace_dir: Optional[Path] = None,
) -> UploadResult:
    """Thực thi quy trình đăng bài trực tiếp qua API (Engine 1).
    
    Trả về UploadResult tương thích hoàn toàn với main.py:
    - outcome: 'posted' | 'prepared' | 'rejected' | 'login_required' | 'failed'
    - post_dispatched: bool
    - details: dict chứa video_url, item_id, vid, rejection_scope...
    """
    log = status_callback or (lambda _: None)

    def _log_status(msg: str) -> None:
        try:
            log(f"[{profile_name}] {msg}")
        except Exception:
            pass

    # 1. Trích xuất Cookies
    _log_status("Đang đọc Cookie phiên đăng nhập TikTok...")
    cookies = get_profile_tiktok_cookies(profile_config, workspace_dir=workspace_dir)
    if not cookies.get("sessionid"):
        return UploadResult(
            outcome="login_required",
            message="Không tìm thấy cookie sessionid hợp lệ của TikTok trong profile",
            post_dispatched=False,
            details={"reason": "missing_sessionid"},
        )

    # 2. Đọc cấu hình Proxy
    proxy_cfg = {
        "use_proxy": profile_config.get("use_proxy", False) or bool(profile_config.get("proxy_string")),
        "proxy_string": profile_config.get("proxy_string", ""),
        "proxy_type": profile_config.get("proxy_type", "http"),
    }

    # 3. Đọc vân tay Profile (data.huynhthang)
    profile_path_str = str(profile_config.get("browser_profile_path") or "")
    pdir = Path(profile_path_str) if profile_path_str else None
    if not pdir and workspace_dir:
        pdir = workspace_dir / "profiles" / profile_name
    fingerprint = read_profile_fingerprint(pdir)

    user_agent = None
    tz_name = "America/New_York"
    if fingerprint:
        user_agent = (fingerprint.get("navigator") or {}).get("userAgent")
        tz_name = (fingerprint.get("timezone") or {}).get("name") or tz_name

    # Cấu hình Bypass Proxy Hosts cho Chunks
    bypass_chunks = bool(profile_config.get("upload_bypass_hosts", True))

    try:
        # 4. Tải video lên VOD (Bước 1 -> Bước 4)
        vod_uploader = TikTokVodUploader(
            session_cookies=cookies,
            proxy_cfg=proxy_cfg,
            bypass_hosts_for_chunks=bypass_chunks,
            user_agent=user_agent,
            status_callback=_log_status,
            cancellation_event=cancellation_event,
        )

        vod_res = vod_uploader.upload_video(video_path)

        # 5. Nếu bật chế độ Dry-run (dừng trước khi Post)
        if stop_before_post:
            _log_status("Pre-Post dry-run hoàn tất: Video đã sẵn sàng trên VOD, KHÔNG bấm Post.")
            return UploadResult(
                outcome="prepared",
                message="Video đã upload hoàn tất lên TikTok VOD (Pre-post dry-run)",
                post_dispatched=False,
                details={
                    "vid": vod_res.vid,
                    "video_meta": vod_res.video_meta,
                    "upload_host": vod_res.upload_host,
                },
            )

        # 6. Xuất bản bài viết (Bước 5)
        publish_client = TikTokPublishClient(
            session_cookies=cookies,
            proxy_cfg=proxy_cfg,
            profile_path=pdir,
            user_agent=user_agent,
            tz_name=tz_name,
            status_callback=_log_status,
        )

        # Đọc bổ trợ metadata MP4 để tối ưu và tránh thiếu thông số
        fast_meta = get_fast_mp4_metadata(video_path)
        final_meta = dict(vod_res.video_meta or {})
        if not final_meta.get("Width"):
            final_meta["Width"] = fast_meta.get("width", 1080)
        if not final_meta.get("Height"):
            final_meta["Height"] = fast_meta.get("height", 1920)
        if not final_meta.get("Duration"):
            final_meta["Duration"] = fast_meta.get("duration", 15.0)

        pub_res = publish_client.publish_video(
            vid=vod_res.vid,
            video_meta=final_meta,
            caption=caption,
            visibility_type=visibility_type,
            allow_comment=allow_comment,
            allow_duet=allow_duet,
            allow_stitch=allow_stitch,
            allow_content_reuse=allow_content_reuse,
            poster_delay_ms=poster_delay_ms,
            playlist_id=playlist_id,
        )

        return UploadResult(
            outcome="posted",
            message=f"Đã đăng thành công lên TikTok: {pub_res.video_url}",
            post_dispatched=True,
            confirmation="api",
            details={
                "item_id": pub_res.item_id,
                "video_url": pub_res.video_url,
                "vid": vod_res.vid,
                "engine": "api_direct",
            },
        )

    except VodAuthExpiredError as e:
        _log_status(f"Phiên đăng nhập hết hạn: {e}")
        return UploadResult(
            outcome="login_required",
            message=str(e),
            post_dispatched=False,
            details={"error_type": "auth_expired"},
        )
    except TikTokAccountBlockedError as e:
        _log_status(f"Tài khoản bị tạm khóa đăng: {e}")
        return UploadResult(
            outcome="rejected",
            message=str(e),
            post_dispatched=False,
            details={"rejection_scope": REJECTION_SCOPE_ACCOUNT_BLOCKED},
        )
    except TikTokLoginRequiredError as e:
        _log_status(f"Cần đăng nhập lại: {e}")
        return UploadResult(
            outcome="login_required",
            message=str(e),
            post_dispatched=False,
            details={"error_type": "login_required"},
        )
    except TikTokPublishError as e:
        _log_status(f"TikTok từ chối xuất bản: {e}")
        return UploadResult(
            outcome="rejected",
            message=str(e),
            post_dispatched=True,
            details={"rejection_scope": REJECTION_SCOPE_VIDEO},
        )
    except VodUploadError as e:
        _log_status(f"Lỗi tải lên VOD: {e}")
        return UploadResult(
            outcome="failed",
            message=str(e),
            post_dispatched=False,
            details={"error_type": "vod_upload_error"},
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Direct API unknown exception: {tb}")
        _log_status(f"Lỗi ngoại lệ trong Direct API Uploader: {e}\n{tb}")
        return UploadResult(
            outcome="failed",
            message=f"Direct API error: {e}",
            post_dispatched=False,
            details={"error_type": "unknown_exception"},
        )
