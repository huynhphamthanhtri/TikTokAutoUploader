# -*- coding: utf-8 -*-
"""
test_web_signer_adapter.py: Kiểm thử WebSignerAdapter cho Creator Studio Web API.
Tuân thủ quy chuẩn AGENTS.md:
- Kiểm tra tính hợp lệ của chữ ký X-Bogus trong URL query.
- Kiểm tra tính hợp lệ của chữ ký X-Gnarly trong Request Headers.
- Kiểm tra tính nhất quán của User-Agent giữa lúc ký và headers.
- Kiểm tra compact JSON serialization cho POST body.
- Kiểm tra hợp nhất query parameters có sẵn trong URL ban đầu (không bị mất mát tham số).
- Kiểm tra truyền tải đồng nhất payload vào bộ ký X-Bogus trong cả 2 trường hợp có query và không có query.
"""

import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
import pytest

from check_features.services.web_signer import WebSignerAdapter, DEFAULT_WEB_UA
from check_features.services.web_signer.bogus import Signer as BogusSigner


def test_signer_adapter_get_request():
    """Kiểm tra ký GET request với query params thông thường."""
    adapter = WebSignerAdapter()
    base_url = "https://www.tiktok.com/tiktok/creator/manage/item_list/v1/"
    params = {
        "count": "30",
        "cursor": "0",
        "aid": "1988",
        "locale": "en",
    }

    signed_url, signed_headers, payload_str = adapter.sign_request(base_url, params=params)

    # 1. URL phải chứa base ban đầu
    assert signed_url.startswith(base_url)
    assert payload_str is None

    # 2. Query string phải có X-Bogus
    parsed = urlparse(signed_url)
    query_params = parse_qs(parsed.query)
    assert "X-Bogus" in query_params
    assert len(query_params["X-Bogus"][0]) > 20
    assert query_params["aid"][0] == "1988"

    # 3. Headers phải có X-Gnarly và User-Agent nhất quán
    assert "X-Gnarly" in signed_headers
    assert len(signed_headers["X-Gnarly"]) > 50
    assert signed_headers["User-Agent"] == DEFAULT_WEB_UA


def test_signer_adapter_post_request_with_payload():
    """Kiểm tra ký POST request với JSON payload."""
    custom_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36"
    adapter = WebSignerAdapter(user_agent=custom_ua)
    base_url = "https://www.tiktok.com/tiktok/creator/manage/item_list/v1/"
    params = {"locale": "en", "aid": "1988"}
    payload = {
        "cursor": 0,
        "size": 30,
        "query": {
            "sort_orders": [{"field_name": "post_time", "order": 2}],
            "conditions": [],
            "is_recent_posts": False
        }
    }

    signed_url, signed_headers, payload_str = adapter.sign_request(
        base_url,
        params=params,
        payload=payload
    )

    # 1. Body phải là compact JSON string (không có khoảng trắng sau dấu hai chấm và dấu phẩy)
    assert payload_str is not None
    assert " " not in payload_str
    assert signed_headers["Content-Type"] == "application/json"

    # 2. Headers User-Agent phải đúng custom UA
    assert signed_headers["User-Agent"] == custom_ua
    assert "X-Gnarly" in signed_headers

    # 3. URL có X-Bogus
    assert "X-Bogus=" in signed_url


def test_signer_adapter_merges_existing_url_queries():
    """Kiểm tra adapter bảo toàn và hợp nhất query parameters có sẵn trong URL ban đầu."""
    adapter = WebSignerAdapter()
    url_with_queries = "https://www.tiktok.com/tiktok/creator/manage/item_list/v1/?aid=1988&region=US"
    params = {"count": "30", "locale": "en"}

    signed_url, _, _ = adapter.sign_request(url_with_queries, params=params)

    parsed = urlparse(signed_url)
    query_params = parse_qs(parsed.query)

    # Đảm bảo toàn bộ tham số từ URL cũ và params mới đều hiện diện đầy đủ
    assert query_params["aid"][0] == "1988"
    assert query_params["region"][0] == "US"
    assert query_params["count"][0] == "30"
    assert query_params["locale"][0] == "en"
    assert "X-Bogus" in query_params


def test_signer_adapter_purges_existing_xbogus():
    """Kiểm tra adapter tự động lọc bỏ X-Bogus cũ để tránh lặp tham số."""
    adapter = WebSignerAdapter()
    base_url = "https://www.tiktok.com/api/test/?aid=1988&X-Bogus=OLD_STALE_BOGUS_TOKEN_123"
    params = {
        "locale": "en",
        "X-Bogus": "ANOTHER_OLD_TOKEN"
    }

    signed_url, _, _ = adapter.sign_request(base_url, params=params)

    # Đảm bảo không chứa X-Bogus cũ
    assert "OLD_STALE_BOGUS_TOKEN_123" not in signed_url
    assert "ANOTHER_OLD_TOKEN" not in signed_url
    assert "X-Bogus=" in signed_url
    assert "aid=1988" in signed_url
    assert "locale=en" in signed_url


def test_signer_adapter_both_query_and_payload_passed_to_signer():
    """Kiểm tra adapter truyền đầy đủ cả query_raw và payload_str vào BogusSigner.sign."""
    adapter = WebSignerAdapter()
    base_url = "https://www.tiktok.com/tiktok/creator/manage/item_list/v1/"
    params = {"aid": "1988", "count": "20"}
    payload = {"cursor": 0, "filter": "active"}

    with patch.object(BogusSigner, "sign", wraps=BogusSigner.sign) as mock_sign:
        signed_url, headers, payload_str = adapter.sign_request(
            base_url,
            params=params,
            payload=payload
        )
        assert mock_sign.called
        args, kwargs = mock_sign.call_args
        # args[0] = query_raw ('aid=1988&count=20')
        # args[1] = user_agent
        # args[2] = payload_str ('{"cursor":0,"filter":"active"}')
        assert "aid=1988" in args[0]
        assert "count=20" in args[0]
        assert args[1] == DEFAULT_WEB_UA
        assert args[2] == '{"cursor":0,"filter":"active"}'
        assert "X-Bogus=" in signed_url
