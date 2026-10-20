# -*- coding: utf-8 -*-
"""
check_features.clients.vpcs_rewards_client - Client giao tiếp API Creator Rewards Program / VPCS.
Hỗ trợ F4 (checkVideoRank, submitAppeal không dùng AI).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import requests

from ..models import VPCSVideo
from core_helpers import format_requests_proxy
from ..services.vpcs_violation_decoder import decode_violation_text, calculate_penalty_expiry

logger = logging.getLogger("TikTokRewardsClient")


class TikTokRewardsClient:
    """Client giao tiếp API Creator Rewards Program (VPCS 2) của TikTok."""

    def __init__(self, profile_config: Dict[str, Any], timeout: int = 15):
        self.config = profile_config or {}
        self.timeout = timeout
        self.session = requests.Session()
        self._init_session()

    def _init_session(self) -> None:
        cookie_str = (
            self.config.get("cookie_str")
            or self.config.get("cookies")
            or self.config.get("cookie")
            or ""
        )
        if cookie_str:
            self.session.headers.update({"Cookie": str(cookie_str).strip()})

        self.session.headers.update({
            "User-Agent": (
                self.config.get("user_agent")
                or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.tiktok.com/creator-center",
            "Accept": "application/json, text/plain, */*",
        })

        proxy_str = (self.config.get("proxy_string") or self.config.get("proxy") or "").strip()
        if proxy_str and self.config.get("use_proxy", True):
            p_scheme = str(self.config.get("proxy_type", "http") or "http").strip().lower()
            proxies = format_requests_proxy(proxy_str, scheme=p_scheme)
            if proxies:
                self.session.proxies = proxies

    def fetch_vpcs_videos(
        self,
        start_epoch: int,
        end_epoch: int,
        max_pages: int = 10,
        delay_ms: int = 150,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        cancel_token_func: Optional[Callable[[], bool]] = None,
    ) -> Tuple[bool, List[VPCSVideo], str]:
        """
        Quét tuần tự tối đa 10 trang danh sách video VPCS theo đặc tả checkVideoRank.
        Có delay 150ms giữa mỗi trang để chống rate-limit.
        Trả về (success, List[VPCSVideo], message).
        """
        endpoint = "https://www.tiktok.com/creator_center/api/video/rank/"
        all_videos: List[VPCSVideo] = []
        seen_vids = set()

        for page in range(max_pages):
            if cancel_token_func and cancel_token_func():
                return False, all_videos, "Đã hủy quét video VPCS theo yêu cầu."

            if progress_cb:
                progress_cb(page + 1, max_pages)

            params = {
                "page": page,
                "start_time": start_epoch,
                "end_time": end_epoch,
            }

            try:
                resp = self.session.get(endpoint, params=params, timeout=self.timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    data_obj = data.get("data") or {}
                    raw_list = data_obj.get("video_info_list") or []
                    has_more = bool(data_obj.get("has_more", False))

                    if not raw_list:
                        # Hết video mới -> Dừng sớm
                        break

                    new_items_found = 0
                    for raw in raw_list:
                        vid = str(raw.get("vid") or "")
                        if vid and vid not in seen_vids:
                            seen_vids.add(vid)
                            vpcs_item = self._parse_raw_vpcs_video(raw)
                            all_videos.append(vpcs_item)
                            new_items_found += 1

                    if new_items_found == 0 or not has_more:
                        break

                elif resp.status_code == 429:
                    logger.warning("VPCS API rate limited (429)")
                    time.sleep(1.0)
                    break
                else:
                    logger.debug(f"VPCS API status {resp.status_code}")
                    break

            except Exception as e:
                logger.debug(f"fetch_vpcs_videos page {page} error: {e}")
                break

            # Delay an toàn giữa các trang theo đặc tả
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)

        msg = f"Đã quét hoàn tất {len(all_videos)} video trong khoảng thời gian đã chọn."
        return True, all_videos, msg

    def _parse_raw_vpcs_video(self, raw: Dict[str, Any]) -> VPCSVideo:
        """Chuyển đổi raw dict sang VPCSVideo DTO có bóc tách lỗi %s và hạn gậy."""
        vid = str(raw.get("vid") or "")
        posted_date = int(raw.get("video_posted_date") or raw.get("create_time") or 0)
        eligibility = bool(raw.get("eligibility", True))
        appeal_st = int(raw.get("appeal_status") or 0)
        reasons = raw.get("ineligible_reasons") or []

        v_title = ""
        v_desc = ""
        if reasons and isinstance(reasons[0], dict):
            first_r = reasons[0]
            v_title = str(first_r.get("title") or "Vi phạm chính sách thưởng")
            raw_desc = str(first_r.get("description") or "")
            args = first_r.get("punishment_description_args") or []
            v_desc = decode_violation_text(raw_desc, args)

        expiry_date, days_remaining = calculate_penalty_expiry(posted_date)

        return VPCSVideo(
            vid=vid,
            cover_url=str(raw.get("cover_url") or ""),
            description=str(raw.get("description") or "(không có tiêu đề)"),
            duration_sec=int(raw.get("durationSec") or raw.get("duration") or 0),
            video_posted_date=posted_date,
            eligibility=eligibility,
            appeal_status=appeal_st,
            ineligible_reasons=reasons,
            violation_title=v_title,
            violation_description=v_desc,
            expiry_date=expiry_date,
            days_remaining=days_remaining,
        )

    def submit_vpcs_appeal(self, video_id: str, description: str) -> Tuple[bool, str]:
        """Gửi kháng nghị vi phạm VPCS (submitAppeal) giới hạn <= 400 ký tự."""
        clean_text = description.strip()
        if not clean_text:
            return False, "Nội dung giải trình kháng nghị không được để trống."
        if len(clean_text) > 400:
            return False, "Nội dung giải trình kháng nghị không được vượt quá 400 ký tự."

        endpoint = "https://www.tiktok.com/creator_center/api/video/appeal/"
        payload = {
            "video_id": video_id,
            "description": clean_text,
        }

        try:
            resp = self.session.post(endpoint, json=payload, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status_code") == 0 or data.get("message") == "success":
                    return True, "Đã gửi đơn kháng nghị VPCS thành công lên TikTok!"
                err = data.get("status_msg") or data.get("message") or "TikTok từ chối đơn kháng nghị."
                return False, f"TikTok thông báo: {err}"
        except Exception as e:
            return False, f"Lỗi kết nối khi gửi kháng nghị VPCS: {e}"

        return False, "Không thể gửi kháng nghị lúc này."
