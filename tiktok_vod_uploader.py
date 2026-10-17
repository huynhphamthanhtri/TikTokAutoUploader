"""
tiktok_vod_uploader.py - Động cơ tải lên video VOD 4 bước qua HTTP REST API (Headless Bypass).

Quy trình 4 bước:
1. GET /api/v1/video/upload/auth/?aid=1988 (Auth & STS Credentials via Proxy)
2. GET /top/v1?Action=ApplyUploadInner (Ký AWS SigV4 -> Node VOD & StoreUri via Proxy)
3. POST /upload/v1/?uploadid={StoreUri} (Slice Chunks + CRC32 Checksum, hỗ trợ Bypass Proxy để tiết kiệm dung lượng)
4. POST /upload/v1/ Finalize (x-upload-with-postupload: 1 -> Nhận video_meta: Width, Height, Duration)
"""

from __future__ import annotations

import fnmatch
import json
import logging
import math
import os
import time
import uuid
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from tiktok_cookie_extractor import cookies_to_header_str
from tiktok_sigv4 import sign_vod_request
from tls_client_engine import TLSSessionWrapper

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB
MIN_CHUNK_SIZE = 5 * 1024 * 1024      # 5 MB
MAX_CHUNK_SIZE = 20 * 1024 * 1024     # 20 MB
DEFAULT_BYPASS_HOSTS_PATTERN = "*-up*.tiktokcdn*"


class VodUploadError(Exception):
    """Lỗi phát sinh trong quá trình tải lên VOD."""
    pass


class VodAuthExpiredError(VodUploadError):
    """Lỗi Cookie/Session hết hạn hoặc không có quyền tải lên."""
    pass


@dataclass
class VodUploadResult:
    vid: str
    upload_host: str
    store_uri: str
    video_meta: Dict[str, Any]
    request_id: str
    store_region: str
    auth_data: Dict[str, Any] = field(default_factory=dict)


class TikTokVodUploader:
    def __init__(
        self,
        session_cookies: Dict[str, str],
        proxy_cfg: Optional[Dict[str, Any]] = None,
        bypass_hosts_for_chunks: bool = True,
        bypass_hosts_pattern: str = DEFAULT_BYPASS_HOSTS_PATTERN,
        user_agent: Optional[str] = None,
        status_callback: Optional[Callable[[str], Any]] = None,
        cancellation_event: Any = None,
    ):
        self.session_cookies = session_cookies
        self.proxy_cfg = proxy_cfg
        self.bypass_hosts_for_chunks = bypass_hosts_for_chunks
        self.bypass_hosts_pattern = bypass_hosts_pattern
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        self.status_callback = status_callback or (lambda _: None)
        self.cancellation_event = cancellation_event

        # Session đi qua Proxy của profile (dùng cho Bước 1, Bước 2, Bước 4)
        self.proxy_session = TLSSessionWrapper(proxy_cfg=self.proxy_cfg, timeout=30.0)
        # Session Direct IP (dùng cho Bước 3 chunk upload nếu bypass proxy)
        self.direct_session = TLSSessionWrapper(proxy_cfg=None, timeout=60.0)

    def _log(self, message: str) -> None:
        try:
            self.status_callback(str(message))
        except Exception:
            pass
        logger.info(message)

    def _check_cancellation(self) -> None:
        if self.cancellation_event is not None and getattr(self.cancellation_event, "is_set", lambda: False)():
            raise VodUploadError("Tiến trình upload video đã bị hủy bỏ bởi người dùng.")

    def _get_common_headers(self) -> Dict[str, str]:
        raw = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.tiktok.com/tiktokstudio/upload?from=webapp",
            "Origin": "https://www.tiktok.com",
            "Cookie": cookies_to_header_str(self.session_cookies),
        }
        return {
            str(k).encode("latin-1", "ignore").decode("latin-1"): str(v).encode("latin-1", "ignore").decode("latin-1")
            for k, v in raw.items()
        }

    # =========================================================================
    # BƯỚC 1: XÁC THỰC & CẤP QUYỀN TẠM THỜI (Upload Auth & AWS STS)
    # =========================================================================
    def step1_upload_auth(self) -> Dict[str, Any]:
        self._check_cancellation()
        self._log("Bước 1/4 (API): Xác thực quyền tải lên VOD...")

        url = "https://www.tiktok.com/api/v1/video/upload/auth/?aid=1988"
        headers = self._get_common_headers()

        try:
            resp = self.proxy_session.get(url, headers=headers)
        except Exception as e:
            raise VodUploadError(f"Bước 1 thất bại (Lỗi kết nối proxy/mạng): {e}") from e

        if resp.status_code != 200:
            raise VodUploadError(f"Bước 1 thất bại: HTTP {resp.status_code} - {resp.text[:200]}")

        try:
            data = resp.json()
        except Exception as e:
            raise VodUploadError(f"Bước 1 thất bại: Server không trả về JSON hợp lệ ({e})") from e

        status_code = data.get("status_code", -1)
        if status_code != 0:
            msg = data.get("status_msg") or data.get("message") or f"status_code={status_code}"
            if "login" in msg.lower() or status_code in (10001, 10002, 10003, 10004):
                raise VodAuthExpiredError(f"Phiên đăng nhập hết hạn hoặc cần login lại: {msg}")
            raise VodUploadError(f"Bước 1 TikTok từ chối cấp quyền: {msg}")

        # V5 API: STS credentials nằm trong video_token_v5, fallback auth object
        token_v5 = data.get("video_token_v5") or {}
        if isinstance(token_v5, dict) and token_v5.get("access_key_id"):
            access_key = token_v5.get("access_key_id")
            secret_key = token_v5.get("secret_acess_key") or token_v5.get("secret_access_key")
            session_token = token_v5.get("session_token")
            space_name = token_v5.get("space_name") or "tiktok"
        else:
            auth = data.get("auth") or {}
            if isinstance(auth, str):
                try:
                    auth = json.loads(auth)
                except Exception:
                    auth = {}
            if not isinstance(auth, dict):
                auth = {}
            access_key = auth.get("access_key_id")
            secret_key = auth.get("secret_acess_key") or auth.get("secret_access_key")
            session_token = auth.get("session_token")
            space_name = auth.get("space_name") or "tiktok"

        store_region = data.get("store_region") or "us-east-1"
        video_token_v5 = token_v5 if isinstance(token_v5, dict) else {}
        auth_str = data.get("auth") if isinstance(data.get("auth"), str) else ""

        if not access_key or not secret_key or not session_token:
            raise VodUploadError("Bước 1 thất bại: Phản hồi thiếu STS Credentials (access_key/secret/session_token)")

        # Trích xuất user_id từ auth token nếu có
        user_id = data.get("user_id")
        if not user_id and auth_str:
            try:
                import base64
                import re
                parts = auth_str.split(":")
                decoded = base64.b64decode(parts[-1]).decode("utf-8", errors="ignore")
                m = re.search(r"UserId:(\d+)", decoded)
                if m:
                    user_id = m.group(1)
            except Exception:
                pass
        if not user_id:
            user_id = self.session_cookies.get("uid_tt") or self.session_cookies.get("odinId") or ""

        self._log(f"Bước 1/4 (API): Cấp quyền thành công (Region: {store_region}, User: {user_id or 'unknown'})")
        return {
            "access_key_id": access_key,
            "secret_access_key": secret_key,
            "session_token": session_token,
            "store_region": store_region,
            "space_name": space_name,
            "auth_str": auth_str,
            "user_id": user_id,
            "video_token_v5": video_token_v5,
            "raw": data,
        }

    # =========================================================================
    # BƯỚC 2: KHỞI TẠO PHIÊN TẢI LÊN VOD (ApplyUploadInner via SigV4)
    # =========================================================================
    def step2_apply_upload(self, auth_data: Dict[str, Any]) -> Dict[str, Any]:
        self._check_cancellation()
        self._log("Bước 2/4 (API): Khởi tạo phiên tải lên VOD (Ký SigV4)...")

        base_url = (
            "https://www.tiktok.com/top/v1"
            "?Action=ApplyUploadInner"
            "&Version=2020-11-19"
            "&SpaceName=tiktok"
            "&FileType=video"
            "&IsInner=1"
            "&X-Amz-Expires=604800"
            "&device_platform=web"
            "&business_tag=tiktok_video_submission_web"
        )

        sig_headers = sign_vod_request(
            url=base_url,
            access_key_id=auth_data["access_key_id"],
            secret_access_key=auth_data["secret_access_key"],
            session_token=auth_data["session_token"],
            region=auth_data.get("store_region", "us-east-1"),
            service="vod",
            method="GET",
        )

        headers = self._get_common_headers()
        headers.update(sig_headers)

        try:
            resp = self.proxy_session.get(base_url, headers=headers)
        except Exception as e:
            raise VodUploadError(f"Bước 2 thất bại (Lỗi kết nối proxy/mạng): {e}") from e

        if resp.status_code != 200:
            raise VodUploadError(f"Bước 2 thất bại: HTTP {resp.status_code} - {resp.text[:200]}")

        try:
            data = resp.json()
        except Exception as e:
            raise VodUploadError(f"Bước 2 thất bại: JSON không hợp lệ ({e})") from e

        result = data.get("Result") or {}
        inner_addr = result.get("InnerUploadAddress") or {}
        upload_nodes = inner_addr.get("UploadNodes") or []

        if not upload_nodes:
            raise VodUploadError(f"Bước 2 thất bại: Không tìm thấy UploadNodes trong phản hồi ({data})")

        first_node = upload_nodes[0]
        upload_host = first_node.get("UploadHost")
        store_infos = first_node.get("StoreInfos") or []
        if not store_infos or not upload_host:
            raise VodUploadError("Bước 2 thất bại: Thiếu UploadHost hoặc StoreInfos")

        store_info = store_infos[0]
        store_uri = store_info.get("StoreUri")
        store_auth = store_info.get("Auth")
        session_key = first_node.get("SessionKey") or result.get("SessionKey") or ""

        vid = first_node.get("Vid") or result.get("Vid") or ""
        sdk_param = result.get("SDKParam") or {}
        slice_count_web = sdk_param.get("slice_count_web") or 4

        self._log(f"Bước 2/4 (API): Phiên VOD sẵn sàng (Vid: {vid}, Host: {upload_host})")
        return {
            "upload_host": upload_host,
            "store_uri": store_uri,
            "store_auth": store_auth,
            "session_key": session_key,
            "vid": vid,
            "slice_count_web": slice_count_web,
            "request_id": (data.get("ResponseMetadata") or {}).get("RequestId", ""),
            "raw": data,
        }

    # =========================================================================
    # BƯỚC 3: PHÂN MẢNH & TẢI LÊN TỪNG PHẦN (Slice Chunk Upload)
    # =========================================================================
    def step3_upload_chunks(
        self,
        apply_data: Dict[str, Any],
        video_path: str | Path,
        auth_data: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        self._check_cancellation()

        vpath = Path(video_path)
        if not vpath.is_file():
            raise VodUploadError(f"Không tìm thấy file video tại: {vpath}")

        total_size = vpath.stat().st_size
        if total_size <= 0:
            raise VodUploadError("File video có dung lượng 0 byte.")

        upload_host = apply_data["upload_host"]
        store_uri = apply_data["store_uri"]
        store_auth = apply_data["store_auth"]

        # 1. Tính toán Adaptive Dynamic Slicing & số luồng tối ưu
        if total_size <= 15 * 1024 * 1024:
            # Video ngắn (< 15MB): 1 chunk duy nhất, giảm độ trễ tối đa
            chunk_size = total_size
            total_parts = 1
            max_workers = 1
        elif total_size <= 60 * 1024 * 1024:
            # Video vừa (15 - 60MB): 3 - 4 chunks song song
            slice_count = min(4, max(2, int(apply_data.get("slice_count_web") or 4)))
            calculated_chunk = math.ceil(total_size / slice_count)
            chunk_size = max(MIN_CHUNK_SIZE, min(MAX_CHUNK_SIZE, calculated_chunk))
            total_parts = math.ceil(total_size / chunk_size)
            max_workers = min(total_parts, 4)
        else:
            # Video dài / lớn (> 60MB): chunk size 15MB, tối đa 6 luồng song song
            chunk_size = 15 * 1024 * 1024
            total_parts = math.ceil(total_size / chunk_size)
            max_workers = min(total_parts, 6)

        # Quyết định có bypass proxy cho CDN host không
        use_direct = False
        if self.bypass_hosts_for_chunks:
            if fnmatch.fnmatch(upload_host.lower(), self.bypass_hosts_pattern.lower()) or "tiktokcdn" in upload_host.lower():
                use_direct = True

        uploader_session = self.direct_session if use_direct else self.proxy_session
        routing_desc = "Direct IP (Bypass Proxy)" if use_direct else "Qua Profile Proxy"
        mode_desc = f"Đa luồng song song ({max_workers} workers)" if max_workers > 1 else "1 stream duy nhất"
        self._log(f"Bước 3/4 (API): Tải {total_parts} phần video ({total_size / (1024*1024):.1f} MB) [{routing_desc} | {mode_desc}]...")

        upload_id = apply_data.get("upload_id") or str(uuid.uuid4())
        apply_data["upload_id"] = upload_id
        upload_url = f"https://{upload_host}/upload/v1/{store_uri}?uploadid={upload_id}&device_platform=web"

        user_id = (auth_data or {}).get("user_id") or self.session_cookies.get("uid_tt") or ""

        # Gửi pre_connect để khởi tạo phiên kết nối TOS
        pre_headers = {
            "Authorization": store_auth,
            "x-phase": "pre_connect",
            "User-Agent": self.user_agent,
            "Origin": "https://www.tiktok.com",
            "Referer": "https://www.tiktok.com/",
        }
        if user_id:
            pre_headers["x-storage-u"] = str(user_id)
        try:
            uploader_session.post(upload_url, data=b"--boundary\r\n", headers=pre_headers, timeout=30.0)
        except Exception:
            pass

        # Hàm worker tải lên 1 chunk riêng biệt (thread-safe)
        def _upload_single_part(part_num: int, offset: int, length: int) -> Tuple[int, str]:
            self._check_cancellation()
            with open(video_path, "rb") as vf:
                vf.seek(offset)
                chunk_bytes = vf.read(length)

            crc32_val = zlib.crc32(chunk_bytes) & 0xFFFFFFFF
            crc32_hex = f"{crc32_val:08x}"

            headers = {
                "Authorization": store_auth,
                "content-crc32": "Ignore",
                "content-disposition": "attachment; filename=undefined",
                "content-type": "application/octet-stream",
                "origin": "https://www.tiktok.com",
                "referer": "https://www.tiktok.com/",
                "user-agent": self.user_agent,
                "x-enable-omit-init-upload": "1",
                "x-enable-upload-mode": "stream",
                "x-offset": "0",
                "x-part-number": str(part_num),
                "x-part-offset": str(offset),
                "x-phase": "transfer",
                "x-size": str(length),
            }
            if user_id:
                headers["x-storage-u"] = str(user_id)

            max_retries = 3
            last_err = ""
            for attempt in range(1, max_retries + 1):
                self._check_cancellation()
                try:
                    self._log(f"  -> Đang tải phần {part_num}/{total_parts} ({length / (1024*1024):.1f} MB)...")
                    resp = uploader_session.post(upload_url, data=chunk_bytes, headers=headers, timeout=60.0)
                    if resp.status_code in (200, 201):
                        try:
                            rjson = resp.json()
                            if isinstance(rjson, dict):
                                if rjson.get("code") not in (None, 0, 2000):
                                    last_err = f"API error: {rjson}"
                                    time.sleep(1.0 * attempt)
                                    continue
                                server_crc = (rjson.get("data") or {}).get("crc32")
                                return part_num, (server_crc or crc32_hex)
                        except Exception:
                            pass
                        return part_num, crc32_hex
                    else:
                        last_err = f"HTTP {resp.status_code} ({resp.text[:100]})"
                except Exception as err:
                    last_err = str(err)

                if attempt < max_retries:
                    time.sleep(1.0 * attempt)

            raise VodUploadError(f"Upload chunk {part_num}/{total_parts} thất bại sau {max_retries} lần: {last_err}")

        # Chuẩn bị thông số các phần
        parts_info = []
        cur_offset = 0
        for p_idx in range(1, total_parts + 1):
            p_len = min(chunk_size, total_size - cur_offset)
            parts_info.append((p_idx, cur_offset, p_len))
            cur_offset += p_len

        parts_crc_map: Dict[int, str] = {}

        if max_workers == 1:
            # 1 chunk: chạy trực tiếp không cần tạo thread pool
            p_idx, c_hex = _upload_single_part(*parts_info[0])
            parts_crc_map[p_idx] = c_hex
        else:
            # Nhiều chunks: chạy song song qua ThreadPoolExecutor
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_part = {
                    executor.submit(_upload_single_part, p_idx, p_off, p_len): p_idx
                    for p_idx, p_off, p_len in parts_info
                }
                for fut in as_completed(future_to_part):
                    self._check_cancellation()
                    try:
                        p_idx, c_hex = fut.result()
                        parts_crc_map[p_idx] = c_hex
                    except Exception as exc:
                        for f in future_to_part:
                            f.cancel()
                        raise exc

        parts_crc = [parts_crc_map[i] for i in range(1, total_parts + 1)]
        self._log(f"Bước 3/4 (API): Tải lên toàn bộ {total_parts} phần hoàn tất song song!")
        return parts_crc

    # =========================================================================
    # BƯỚC 4: HOÀN TẤT & GHÉP MẢNH VIDEO (Post Upload Commit)
    # =========================================================================
    def step4_commit(
        self,
        apply_data: Dict[str, Any],
        parts_crc: List[str],
        auth_data: Dict[str, Any],
        total_size: int = 0,
    ) -> Dict[str, Any]:
        self._check_cancellation()
        self._log("Bước 4/4 (API): TikTok đang ghép mảnh video và xác thực...")

        upload_host = apply_data["upload_host"]
        store_uri = apply_data["store_uri"]
        store_auth = apply_data["store_auth"]
        upload_id = apply_data.get("upload_id") or str(uuid.uuid4())
        user_id = auth_data.get("user_id") or self.session_cookies.get("uid_tt") or ""

        url = f"https://{upload_host}/upload/v1/{store_uri}?uploadid={upload_id}&device_platform=web"
        headers = {
            "Authorization": store_auth,
            "Content-Type": "application/json",
            "origin": "https://www.tiktok.com",
            "referer": "https://www.tiktok.com/",
            "x-enable-upload-mode": "stream",
            "x-phase": "finish",
            "x-upload-with-postupload": "1",
            "User-Agent": self.user_agent,
        }
        if total_size > 0:
            headers["x-size"] = str(total_size)
        if user_id:
            headers["x-storage-u"] = str(user_id)

        parts_crc_str = ",".join(f"{idx}:{crc}" for idx, crc in enumerate(parts_crc, start=1))

        body = {
            "parts_crc": parts_crc_str,
            "post_upload_param": {
                "sts2_token": auth_data["session_token"],
                "sts2_secret": auth_data["secret_access_key"],
                "session_key": apply_data.get("session_key", ""),
                "functions": [],
            },
        }

        # Gọi qua Proxy để an toàn phiên
        try:
            resp = self.proxy_session.post(url, json=body, headers=headers)
        except Exception as e:
            raise VodUploadError(f"Bước 4 commit thất bại: {e}") from e

        if resp.status_code != 200:
            raise VodUploadError(f"Bước 4 commit thất bại: HTTP {resp.status_code} - {resp.text[:200]}")

        try:
            data = resp.json()
        except Exception as e:
            raise VodUploadError(f"Bước 4 JSON không hợp lệ: {e}") from e

        code = data.get("code", 0)
        if code not in (0, 2000):
            raise VodUploadError(f"Bước 4 commit bị từ chối: {data}")

        data_dict = data.get("data") or {}
        post_upload_resp = data_dict.get("post_upload_resp") or data.get("post_upload_resp") or {}
        results = post_upload_resp.get("results") or []
        first_res = results[0] if results else {}
        video_meta = first_res.get("video_meta") or post_upload_resp.get("video_meta") or {}
        req_id = post_upload_resp.get("request_id") or (data.get("ResponseMetadata") or {}).get("RequestId", "")
        vid = first_res.get("vid") or apply_data.get("vid") or ""

        width = video_meta.get("Width", 0)
        height = video_meta.get("Height", 0)
        duration = video_meta.get("Duration", 0)

        self._log(f"Bước 4/4 (API): Ghép mảnh hoàn tất (Vid: {vid}, {width}x{height}, {duration}s)")
        return {
            "video_meta": video_meta,
            "request_id": req_id,
            "vid": vid,
            "raw": data,
        }

    # =========================================================================
    # GIAO DIỆN CHÍNH: CHẠY TOÀN BỘ 4 BƯỚC VOD
    # =========================================================================
    def upload_video(self, video_path: str | Path) -> VodUploadResult:
        vpath = Path(video_path)
        total_size = vpath.stat().st_size if vpath.is_file() else 0
        # 1. Auth
        auth_data = self.step1_upload_auth()
        # 2. Apply
        apply_data = self.step2_apply_upload(auth_data)
        # 3. Chunks
        parts_crc = self.step3_upload_chunks(apply_data, vpath, auth_data=auth_data)
        # 4. Commit
        commit_data = self.step4_commit(apply_data, parts_crc, auth_data, total_size=total_size)

        return VodUploadResult(
            vid=commit_data.get("vid") or apply_data["vid"],
            upload_host=apply_data["upload_host"],
            store_uri=apply_data["store_uri"],
            video_meta=commit_data["video_meta"],
            request_id=commit_data["request_id"],
            store_region=auth_data.get("store_region", "us-east-1"),
            auth_data=auth_data,
        )

