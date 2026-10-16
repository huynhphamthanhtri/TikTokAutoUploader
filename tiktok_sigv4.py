"""
tiktok_sigv4.py - AWS Signature Version 4 Signer cho TikTok VOD Gateway.

Thực thi thuật toán chuẩn AWS SigV4 thuần Python (không phụ thuộc boto3 hay thư viện ngoài).
Dùng để ký request Bước 2: GET https://www.tiktok.com/top/v1?Action=ApplyUploadInner...
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
from typing import Dict, Mapping, Optional, Tuple
from urllib.parse import parse_qsl, quote, urlencode, urlparse


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def get_signature_key(key: str, date_stamp: str, region_name: str, service_name: str) -> bytes:
    k_date = _sign(("AWS4" + key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region_name)
    k_service = _sign(k_region, service_name)
    k_signing = _sign(k_service, "aws4_request")
    return k_signing


def sign_vod_request(
    url: str,
    access_key_id: str,
    secret_access_key: str,
    session_token: str,
    region: str = "us-east-1",
    service: str = "vod",
    method: str = "GET",
    body: bytes = b"",
    request_datetime: Optional[datetime.datetime] = None,
) -> Dict[str, str]:
    """Ký request AWS SigV4 cho dịch vụ VOD của TikTok.
    
    Trả về dict các headers bổ sung cần gửi kèm:
    - Authorization
    - x-amz-date
    - x-amz-security-token
    """
    if request_datetime is None:
        request_datetime = datetime.datetime.now(datetime.timezone.utc)
    elif request_datetime.tzinfo is None:
        request_datetime = request_datetime.replace(tzinfo=datetime.timezone.utc)

    amz_date = request_datetime.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = request_datetime.strftime("%Y%m%d")

    parsed_url = urlparse(url)
    host = parsed_url.hostname or "www.tiktok.com"
    canonical_uri = parsed_url.path or "/"

    # Canonical Query String: sắp xếp theo key, encode chuẩn
    query_params = parse_qsl(parsed_url.query, keep_blank_values=True)
    query_params_sorted = sorted(query_params, key=lambda x: (x[0], x[1]))
    canonical_querystring = urlencode(query_params_sorted, quote_via=quote)

    # Canonical Headers
    headers_to_sign = {
        "host": host.lower().strip(),
        "x-amz-date": amz_date,
        "x-amz-security-token": session_token.strip(),
    }
    sorted_header_keys = sorted(headers_to_sign.keys())
    canonical_headers = "".join(f"{k}:{headers_to_sign[k]}\n" for k in sorted_header_keys)
    signed_headers = ";".join(sorted_header_keys)

    # Payload hash
    payload_hash = hashlib.sha256(body).hexdigest()

    # Canonical Request
    canonical_request = (
        f"{method.upper()}\n"
        f"{canonical_uri}\n"
        f"{canonical_querystring}\n"
        f"{canonical_headers}\n"
        f"{signed_headers}\n"
        f"{payload_hash}"
    )

    # String to Sign
    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = (
        f"{algorithm}\n"
        f"{amz_date}\n"
        f"{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )

    # Tính Signature
    signing_key = get_signature_key(secret_access_key, date_stamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    # Authorization Header
    authorization_header = (
        f"{algorithm} "
        f"Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    return {
        "Authorization": authorization_header,
        "x-amz-date": amz_date,
        "x-amz-security-token": session_token.strip(),
    }
