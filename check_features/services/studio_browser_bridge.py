# -*- coding: utf-8 -*-
"""
check_features.services.studio_browser_bridge - Cầu nối trình duyệt trích xuất dữ liệu Creator Studio & Channel an toàn.
Giải quyết triệt để lỗi TikTok WAF chặn HTTP requests bằng Mã 1 (internal server error) hoặc HTML challenge.

Tuân thủ kiến trúc:
- Tận dụng singleton Browser Service & Profile-Patchright của profile qua browser_patchright_glue.
- Phát hiện và cách ly ProfileInUseError / ProfileBusyError, không can thiệp phá khóa profile đang bận.
- Đảm bảo giải phóng hoàn toàn SessionToken trong khối finally, không để rò rỉ process Chromium.
- Trích xuất dữ liệu đa tầng với Exception Isolation:
  1. SSR Bootstrap Context (__Creator_Center_Context__ -> firstBatchQueryItems).
  2. Bổ sung các bài đăng còn lại từ trang cá nhân (@username) để đạt đủ số lượng bài đăng (được cô lập lỗi riêng).
  3. Ghi cache atomic vào data/feeds/{uname}_mobile.json để nạp nhanh lần sau.
- Trả về DTO minh bạch với data_source, completeness và error_code.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("StudioBrowserBridge")


class StudioBrowserBridge:
    """Bộ cầu nối trình duyệt Patchright lấy dữ liệu bài đăng Creator Studio & Channel."""

    @classmethod
    def fetch_studio_posts(
        cls,
        profile_config: Dict[str, Any],
        profile_name: str = "",
        count: int = 25,
        timeout: int = 50,
    ) -> Dict[str, Any]:
        from browser_patchright_glue import (
            open_session,
            run_operation,
            ProfileBusyError,
            SessionSetupError,
        )

        p_name = profile_name or str(profile_config.get("profile_name") or profile_config.get("name") or "Unknown")
        cfg_copy = dict(profile_config)
        cfg_copy["headless"] = True

        token = None
        try:
            logger.info(f"[{p_name}] Initializing safe studio browser bridge session...")
            token = open_session(cfg_copy, profile_name=p_name, timeout=min(timeout, 30))
        except ProfileBusyError as e:
            logger.warning(f"[{p_name}] Profile busy / in-use: {e}")
            return {
                "success": False,
                "items": [],
                "source": "browser_bridge",
                "completeness": "empty",
                "cursor": 0,
                "has_more": False,
                "message": "Trình duyệt của profile đang bận (được sử dụng bởi tiến trình khác). Vui lòng thử lại sau.",
                "error_code": "PROFILE_BUSY",
            }
        except Exception as e:
            logger.error(f"[{p_name}] Không thể mở browser bridge: {e}")
            return {
                "success": False,
                "items": [],
                "source": "browser_bridge",
                "completeness": "empty",
                "cursor": 0,
                "has_more": False,
                "message": f"Không thể mở trình duyệt profile: {e}",
                "error_code": "SESSION_SETUP_ERROR",
            }

        try:
            async def _extract_operation(page) -> Dict[str, Any]:
                # BƯỚC 1: Lấy các bài từ Creator Studio SSR Context
                studio_items: List[Dict[str, Any]] = []
                seen_ids: set[str] = set()

                logger.info(f"[{p_name}] Navigating to https://www.tiktok.com/tiktokstudio/content ...")
                await page.goto("https://www.tiktok.com/tiktokstudio/content", wait_until="domcontentloaded", timeout=35000)
                await asyncio.sleep(2.5)

                curr_url = str(getattr(page, "url", "") or "").lower()
                if "login" in curr_url:
                    return {
                        "success": False,
                        "items": [],
                        "source": "browser_studio_login_check",
                        "completeness": "empty",
                        "cursor": 0,
                        "has_more": False,
                        "message": "Phiên đăng nhập TikTok trên trình duyệt đã hết hạn. Vui lòng mở Chrome đăng nhập lại.",
                        "error_code": "LOGIN_REQUIRED",
                    }

                ssr_json_str = await page.evaluate('''() => {
                    const el = document.getElementById("__Creator_Center_Context__");
                    return el ? el.innerText : null;
                }''')

                if ssr_json_str:
                    parsed = None
                    # Parse JSON gốc trước, chỉ unescape HTML khi parse raw thất bại
                    try:
                        parsed = json.loads(ssr_json_str)
                    except Exception:
                        try:
                            decoded = html.unescape(ssr_json_str)
                            parsed = json.loads(decoded)
                        except Exception as e:
                            logger.warning(f"[{p_name}] Lỗi giải mã __Creator_Center_Context__: {e}")

                    if parsed and isinstance(parsed, dict):
                        batch = parsed.get("firstBatchQueryItems") or {}
                        raw_list = batch.get("item_list") or []
                        for it in raw_list:
                            iid = str(it.get("item_id") or it.get("id") or "")
                            if iid and iid not in seen_ids:
                                it["data_source"] = "creator_studio"
                                studio_items.append(it)
                                seen_ids.add(iid)
                        logger.info(f"[{p_name}] Extracted {len(studio_items)} items from Creator Studio SSR.")

                # BƯỚC 2: Bổ sung từ trang cá nhân (@username) với Exception Isolation
                uname = str(profile_config.get("tiktok_id") or profile_config.get("tiktok_account") or "").lstrip("@")
                if len(studio_items) < count and uname:
                    try:
                        logger.info(f"[{p_name}] Số bài hiện tại ({len(studio_items)}) < {count}. Bổ sung từ trang @{uname}...")
                        chan_url = f"https://www.tiktok.com/@{uname}"
                        await page.goto(chan_url, wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(1.5)

                        for _ in range(3):
                            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            await asyncio.sleep(1.2)

                        chan_items = await page.evaluate('''() => {
                            const results = [];
                            const links = document.querySelectorAll("a[href*='/video/']");
                            const seen = new Set();
                            for (const a of links) {
                                const href = a.href || "";
                                const m = href.match(/\\/video\\/(\\d+)/);
                                if (m && !seen.has(m[1])) {
                                    seen.add(m[1]);
                                    results.push({
                                        item_id: m[1],
                                        desc: (a.innerText || "").trim(),
                                        share_url: href
                                    });
                                }
                            }
                            return results;
                        }''')

                        added_from_chan = 0
                        for cit in chan_items:
                            cid = cit.get("item_id")
                            if cid and cid not in seen_ids:
                                enriched_item = {
                                    "item_id": cid,
                                    "id": cid,
                                    "desc": cit.get("desc", ""),
                                    "share_url": cit.get("share_url", f"https://www.tiktok.com/@{uname}/video/{cid}"),
                                    "data_source": "channel_feed",
                                    "visibility": 1,
                                    "status": 102,
                                }
                                studio_items.append(enriched_item)
                                seen_ids.add(cid)
                                added_from_chan += 1
                        logger.info(f"[{p_name}] Đã bổ sung thêm {added_from_chan} bài từ trang cá nhân. Tổng: {len(studio_items)}")
                    except Exception as e_chan:
                        # Cách ly lỗi: Thất bại ở trang cá nhân không làm mất dữ liệu Studio đã lấy thành công
                        logger.warning(f"[{p_name}] Không thể bổ sung từ trang cá nhân: {e_chan}. Giữ nguyên {len(studio_items)} bài Studio.")

                # BƯỚC 3: Lưu cache offline atomic vào data/feeds/{uname}_mobile.json
                if studio_items and uname:
                    try:
                        cls._save_offline_feed_cache(uname, studio_items, profile_config)
                    except Exception as e:
                        logger.warning(f"[{p_name}] Không thể ghi cache offline: {e}")

                if studio_items:
                    return {
                        "success": True,
                        "items": studio_items,
                        "source": "browser_bridge",
                        "completeness": "complete" if len(studio_items) >= count else "partial",
                        "cursor": len(studio_items),
                        "has_more": False,
                        "message": f"Đã lấy {len(studio_items)} bài đăng từ Creator Studio qua Trình Duyệt.",
                        "error_code": None,
                    }

                return {
                    "success": False,
                    "items": [],
                    "source": "browser_bridge",
                    "completeness": "empty",
                    "cursor": 0,
                    "has_more": False,
                    "message": "Không tìm thấy bài đăng nào trên Creator Studio qua Trình Duyệt.",
                    "error_code": "NO_POSTS_FOUND",
                }

            result = run_operation(token, _extract_operation, timeout=timeout)
            return result

        except Exception as e:
            logger.error(f"[{p_name}] Lỗi thực thi browser bridge operation: {e}", exc_info=True)
            return {
                "success": False,
                "items": [],
                "source": "browser_bridge",
                "completeness": "empty",
                "cursor": 0,
                "has_more": False,
                "message": f"Lỗi trích xuất trình duyệt: {e}",
                "error_code": "OPERATION_ERROR",
            }
        finally:
            if token is not None:
                try:
                    logger.info(f"[{p_name}] Closing browser bridge session...")
                    token.quit(timeout=10)
                except Exception as ex:
                    logger.warning(f"[{p_name}] Ngoại lệ khi đóng session browser bridge: {ex}")

    @classmethod
    def _save_offline_feed_cache(cls, username: str, items: List[Dict[str, Any]], profile_config: Dict[str, Any]) -> None:
        """Lưu cache danh sách bài đăng nguyên tử (atomic write) vào data/feeds/{username}_mobile.json."""
        if not username or not items:
            return
        clean_uname = username.lstrip("@").strip()
        feed_dir = Path("data") / "feeds"
        feed_dir.mkdir(parents=True, exist_ok=True)
        cache_path = feed_dir / f"{clean_uname}_mobile.json"
        tmp_path = feed_dir / f"{clean_uname}_mobile.json.tmp"

        payload = {
            "schema_version": 1,
            "source": "studio_browser_bridge",
            "account_id": str(profile_config.get("tiktok_user_id") or profile_config.get("account_uuid") or ""),
            "username": clean_uname,
            "aweme_list": [
                {
                    "aweme_id": str(it.get("item_id") or it.get("id") or ""),
                    "desc": str(it.get("desc") or ""),
                    "group_id": str(it.get("group_id") or it.get("groupId") or ""),
                    "statistics": {
                        "play_count": int(it.get("play_count") or 0),
                        "digg_count": int(it.get("like_count") or 0),
                        "comment_count": int(it.get("comment_count") or 0),
                    },
                    "create_time": int(it.get("create_time") or it.get("post_time") or 0),
                }
                for it in items
            ]
        }

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        tmp_path.replace(cache_path)
        logger.info(f"Saved {len(items)} items to offline cache: {cache_path}")
