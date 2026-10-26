# -*- coding: utf-8 -*-
"""
WebSignerAdapter: Adapter đóng gói chữ ký X-Bogus và X-Gnarly cho các Web/Creator Studio endpoints của TikTok.
Áp dụng thuật toán chữ ký mổ xẻ từ repo armxe/tiktok-api:
- X-Bogus (Double-MD5 + RC4 biến thể) gắn vào Query string.
- X-Gnarly (ChaCha20 biến thể) gắn vào Request Header.
Đảm bảo tính nhất quán tuyệt đối giữa User-Agent lúc ký và lúc gửi HTTP.
Bảo toàn và merge đầy đủ các query parameters có sẵn trong URL ban đầu.
"""

import json
import time
from typing import Any, Dict, Optional, Tuple, Union
from urllib.parse import parse_qs, urlencode, urlparse

from .bogus import Signer as BogusSigner
from .gnarly import get_X_Gnarly


DEFAULT_WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class WebSignerAdapter:
    """
    Adapter ký số cho các HTTP request Web TikTok.
    """

    def __init__(self, user_agent: str = DEFAULT_WEB_UA):
        self.user_agent = user_agent.strip() if user_agent else DEFAULT_WEB_UA

    def sign_request(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Union[Dict[str, Any], str]] = None,
        headers: Optional[Dict[str, str]] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[str, Dict[str, str], Optional[str]]:
        """
        Ký request với X-Bogus và X-Gnarly.
        
        Args:
            url: Base URL của endpoint (có thể chứa sẵn query parameters)
            params: Dictionary query parameters bổ sung hoặc ghi đè
            payload: Payload JSON dạng dict hoặc chuỗi (dùng cho POST request)
            headers: Headers hiện tại cần bổ sung chữ ký
            user_agent: Custom UA nếu muốn override UA của instance

        Returns:
            Tuple[signed_url, signed_headers, payload_str]:
            - signed_url: Full URL kèm query string đã có X-Bogus
            - signed_headers: Dictionary headers đã được gán X-Gnarly, User-Agent, Content-Type
            - payload_str: Chuỗi JSON chuẩn hóa (compact separators) nếu có payload
        """
        ua = (user_agent.strip() if user_agent else self.user_agent) or DEFAULT_WEB_UA

        # 1. Trích xuất và hợp nhất (merge) query params có sẵn trong URL với params truyền vào
        parsed_url = urlparse(url)
        merged_params: Dict[str, Any] = {}
        if parsed_url.query:
            for k, v_list in parse_qs(parsed_url.query, keep_blank_values=True).items():
                merged_params[k] = v_list[-1] if len(v_list) == 1 else v_list

        if params:
            merged_params.update(params)

        # 2. Lọc bỏ X-Bogus cũ để tránh lặp token
        merged_params.pop("X-Bogus", None)
        query_raw = urlencode(merged_params, doseq=True)

        # 3. Chuẩn hóa payload (nếu có)
        payload_str = ""
        if payload is not None:
            if isinstance(payload, dict):
                # Chuẩn hóa compact format để chữ ký khớp tuyệt đối với body gửi đi
                payload_str = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
            else:
                payload_str = str(payload)

        # 4. Ký X-Bogus (Double-MD5 + RC4) - Đồng nhất ký cả query_raw và payload_str
        query_signed = BogusSigner.sign(query_raw, ua, payload_str)

        # 5. Ký X-Gnarly (ChaCha20)
        gnarly_token = get_X_Gnarly(query_raw, payload_str, ua)

        # 6. Xây dựng full URL sạch, bảo toàn scheme và host
        if parsed_url.scheme and parsed_url.netloc:
            base_clean = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
        else:
            base_clean = url.split("?")[0].rstrip("?")
        full_signed_url = f"{base_clean}?{query_signed}"

        # 7. Chuẩn hóa headers
        req_headers = dict(headers or {})
        req_headers["User-Agent"] = ua
        req_headers["X-Gnarly"] = gnarly_token
        if payload is not None and "Content-Type" not in req_headers:
            req_headers["Content-Type"] = "application/json"

        return full_signed_url, req_headers, (payload_str if payload is not None else None)
