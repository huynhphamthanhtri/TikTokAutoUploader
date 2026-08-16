"""
tiktok_monetization_client.py - Module truy vấn dữ liệu tài chính & Payout từ máy chủ Webcast TikTok.

Đặc tính:
- Hỗ trợ định tuyến 3 vùng: US (.tiktokv.us), EU (.tiktokv.eu), Global (.tiktok.com).
- Truy vấn Read-Only an toàn qua Proxy riêng của Profile (HTTP/SOCKS5) và Session Cookies.
- Tự động che giấu số tài khoản ngân hàng và email (***1234).
- Phục vụ cho MonetizationWorkspace và MonetizationDetailModal.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from core_helpers import parse_proxy_string


# ==============================================================================
# 1. CONSTANTS & BASE HOST ROUTING
# ==============================================================================

HOST_US = "https://aggr16-normal.tiktokv.us"
HOST_EU = "https://webcast16-normal-no1a.tiktokv.eu"
HOST_GLOBAL = "https://webcast.tiktok.com"

EU_COUNTRY_CODES = {
    "GB", "UK", "DE", "FR", "IT", "ES", "NL", "BE", "AT", "CH", "SE", "PL",
    "PT", "IE", "NO", "DK", "FI", "CZ", "RO", "HU", "GR", "EU"
}

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.7559.96 Safari/537.36"
)


def resolve_webcast_base_host(region: str) -> str:
    """Xác định Base Host của TikTok Webcast dựa theo mã quốc gia của Profile."""
    if not region:
        return HOST_GLOBAL
    code = str(region).strip().upper()
    if code in ("US", "USA"):
        return HOST_US
    if code in EU_COUNTRY_CODES:
        return HOST_EU
    return HOST_GLOBAL


def mask_sensitive_payment_info(val: str) -> str:
    """Che giấu thông tin tài khoản ngân hàng / email nhạy cảm."""
    if not val or not isinstance(val, str):
        return ""
    text = val.strip()
    if "@" in text:
        parts = text.split("@")
        name = parts[0]
        domain = parts[1] if len(parts) > 1 else ""
        masked_name = (name[0] + "***") if len(name) > 0 else "***"
        return f"{masked_name}@{domain}"
    if len(text) > 4:
        return f"***{text[-4:]}"
    return "***"


def build_cookie_string(cookie_raw: Any) -> str:
    """Chuyển đổi cookie dict, list hoặc string thô thành định dạng Cookie Header."""
    if not cookie_raw:
        return ""
    if isinstance(cookie_raw, str):
        return cookie_raw.strip()
    if isinstance(cookie_raw, dict):
        return "; ".join(f"{k}={v}" for k, v in cookie_raw.items())
    if isinstance(cookie_raw, list):
        items = []
        for c in cookie_raw:
            if isinstance(c, dict) and "name" in c and "value" in c:
                items.append(f"{c['name']}={c['value']}")
        return "; ".join(items)
    return ""


# ==============================================================================
# 2. MONETIZATION CLIENT CLASS
# ==============================================================================

class TikTokMonetizationClient:
    """Client HTTP truy vấn các API Thu Nhập, Quỹ Tác Giả và PTTT từ TikTok."""

    def __init__(self, profile_name: str, profile_config: Dict[str, Any], timeout: float = 12.0):
        self.profile_name = profile_name
        self.config = profile_config or {}
        self.timeout = timeout
        
        # Determine Region and Base Host
        fp = self.config.get("fingerprint", {}) or {}
        region = fp.get("geo_country_code") or fp.get("geo_country") or self.config.get("region") or "US"
        self.region = str(region).strip().upper()
        self.base_host = resolve_webcast_base_host(self.region)

        # Setup requests Session with Proxy
        self.session = requests.Session()
        self._setup_proxy()

    def _setup_proxy(self) -> None:
        if not self.config.get("use_proxy"):
            return
        p_str = self.config.get("proxy_string", "").strip()
        p_type = self.config.get("proxy_type", "http").strip().lower()
        if not p_str:
            return

        parsed = parse_proxy_string(p_str)
        if not parsed or not parsed.get("ip") or not parsed.get("port"):
            return

        ip = parsed["ip"]
        port = parsed["port"]
        user = parsed.get("user")
        pwd = parsed.get("pass")

        if user and pwd:
            proxy_url = f"{p_type}://{user}:{pwd}@{ip}:{port}"
        else:
            proxy_url = f"{p_type}://{ip}:{port}"

        self.session.proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }

    def _get_headers(self) -> Dict[str, str]:
        cookie_str = build_cookie_string(self.config.get("cookie_str"))
        return {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://inapp.tiktokv.com/web-inapp/income-wallet/",
            "Cookie": cookie_str,
        }

    def fetch_all_monetization_data(self) -> Dict[str, Any]:
        """Truy vấn tổng hợp: Số dư, Quỹ tác giả, PTTT, và KYC."""
        now_ts = time.time()
        result: Dict[str, Any] = {
            "status": "SUCCESS",
            "profile_name": self.profile_name,
            "region": self.region,
            "balance": 0.0,
            "currency": "USD",
            "currency_symbol": "$",
            "available_balance": 0.0,
            "frozen_balance": 0.0,
            "next_payout_date": "N/A",
            "payout_status": "PAYOUT_NOT_LINKED",
            "kyc_status": "NOT_STARTED",
            "payment_method": "N/A",
            "rewards_estimated": "$0.00",
            "pending_earnings": [],
            "payout_breakdown": [],
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts)),
            "timestamp": now_ts,
            "freshness": "live",
            "errors": [],
        }

        # Check Cookie existence
        if not self.config.get("cookie_str"):
            result["status"] = "NO_AUTH"
            result["payout_status"] = "NO_AUTH"
            result["freshness"] = "unknown"
            return result

        headers = self._get_headers()

        # 1. Payout Summary (Số dư)
        try:
            url_balance = f"{self.base_host}/webcast/api/money/creator_earnings/v1/payout_summary"
            resp = self.session.get(url_balance, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                raw_json = resp.json()
                data = raw_json.get("data", {}) if isinstance(raw_json, dict) else {}
                
                # Handle total balance
                tot = data.get("total_balance")
                if tot is None:
                    tot = data.get("balance", 0.0)
                if isinstance(tot, dict):
                    try:
                        result["balance"] = float(tot.get("value", 0.0) or 0.0)
                        result["currency"] = str(tot.get("currency", "USD") or "USD")
                        result["currency_symbol"] = str(tot.get("currency_symbol", "$") or "$")
                    except (ValueError, TypeError):
                        pass
                elif isinstance(tot, (int, float, str)):
                    try:
                        result["balance"] = float(tot or 0.0)
                    except (ValueError, TypeError):
                        pass

                # Handle available & frozen balance
                avail = data.get("available_balance")
                if isinstance(avail, dict):
                    result["available_balance"] = float(avail.get("value", 0.0) or 0.0)
                elif isinstance(avail, (int, float)):
                    result["available_balance"] = float(avail)
                else:
                    result["available_balance"] = result["balance"]

                froz = data.get("frozen_balance")
                if isinstance(froz, dict):
                    result["frozen_balance"] = float(froz.get("value", 0.0) or 0.0)
                elif isinstance(froz, (int, float)):
                    result["frozen_balance"] = float(froz)

                p_date_raw = data.get("next_payout_date")
                if p_date_raw:
                    if isinstance(p_date_raw, (int, float)) or (isinstance(p_date_raw, str) and p_date_raw.isdigit()):
                        try:
                            d_obj = time.localtime(float(p_date_raw))
                            result["next_payout_date"] = time.strftime("%d/%m/%Y", d_obj)
                        except Exception:
                            result["next_payout_date"] = str(p_date_raw)
                    else:
                        result["next_payout_date"] = str(p_date_raw)
            elif resp.status_code in (401, 403):
                result["status"] = "NO_AUTH"
        except requests.RequestException as e:
            result["errors"].append(f"Balance error: {e}")
            result["status"] = "PROXY_ERROR"

        # 2. Business Rewards (Quỹ tác giả)
        try:
            url_rewards = f"{self.base_host}/webcast/api/money/one-wallet/v1/business/rewards"
            params = {
                "wallet_type": "MONTHLY_EARNING",
                "business_type": "CREATIVE_REWARDS",
            }
            resp_rew = self.session.get(url_rewards, params=params, headers=headers, timeout=self.timeout)
            if resp_rew.status_code == 200:
                raw_rew = resp_rew.json()
                d_rew = raw_rew.get("data", {}) if isinstance(raw_rew, dict) else {}
                summary_rew = d_rew.get("summary", {}) if isinstance(d_rew, dict) else {}
                est = summary_rew.get("estimated_amount", {}) if isinstance(summary_rew, dict) else {}
                if isinstance(est, dict) and est.get("currency_amount"):
                    result["rewards_estimated"] = str(est["currency_amount"])
                
                pending_list = []
                for p_item in d_rew.get("pending_earnings", []) or []:
                    if isinstance(p_item, dict):
                        p_amt = p_item.get("amount", {})
                        p_amt_str = p_amt.get("currency_amount", "$0.00") if isinstance(p_amt, dict) else str(p_amt)
                        p_ts = p_item.get("timestamp")
                        p_date_str = ""
                        if p_ts:
                            try:
                                p_date_str = time.strftime("%d/%m/%Y", time.localtime(float(p_ts)))
                            except Exception:
                                pass
                        pending_list.append({
                            "title": str(p_item.get("title", "")),
                            "amount": p_amt_str,
                            "date": p_date_str,
                            "bill_id": str(p_item.get("bill_id", "")),
                        })
                result["pending_earnings"] = pending_list

                breakdown_list = []
                for b_item in d_rew.get("payout_breakdown", []) or []:
                    if isinstance(b_item, dict):
                        b_amt = b_item.get("amount", {})
                        b_amt_str = b_amt.get("currency_amount", "$0.00") if isinstance(b_amt, dict) else str(b_amt)
                        breakdown_list.append({
                            "title": str(b_item.get("title", "")),
                            "amount": b_amt_str,
                        })
                
                # Check list for application eligibility (Followers/Views/Age)
                checklist = d_rew.get("apply_check_list", [])
                if checklist:
                    for chk in checklist:
                        if isinstance(chk, dict) and chk.get("threshold"):
                            k_name = chk.get("key", "req")
                            amt_val = chk.get("amount", 0)
                            th_val = chk.get("threshold", 0)
                            breakdown_list.append({
                                "title": f"Điều kiện {k_name}",
                                "amount": f"{amt_val:,}/{th_val:,}" if th_val else str(amt_val),
                            })
                
                result["payout_breakdown"] = breakdown_list
        except Exception as e:
            result["errors"].append(f"Rewards error: {e}")

        # 3. Payout Onboarding (PTTT)
        try:
            url_onboarding = f"{self.base_host}/webcast/api/money/payout_onboarding/v2/onboarding_detail"
            resp_onb = self.session.get(url_onboarding, headers=headers, timeout=self.timeout)
            if resp_onb.status_code == 200:
                raw_onb = resp_onb.json()
                d_onb = raw_onb.get("data", {}) if isinstance(raw_onb, dict) else {}
                
                masked_inst = d_onb.get("masked_instrument_identity", "")
                bind_status = d_onb.get("pi_bind_status")
                method = d_onb.get("payment_method") or d_onb.get("paymentMethod") or d_onb.get("bank_name") or d_onb.get("account_type")
                acc_num = d_onb.get("account_number") or d_onb.get("email") or ""
                
                if masked_inst:
                    result["payment_method"] = str(masked_inst)
                    result["payout_status"] = "PAYOUT_READY"
                elif method and method != "None":
                    masked = mask_sensitive_payment_info(acc_num)
                    result["payment_method"] = f"{method} ({masked})" if masked else str(method)
                    result["payout_status"] = "PAYOUT_READY"
                elif bind_status == 1:
                    result["payment_method"] = "Đã liên kết"
                    result["payout_status"] = "PAYOUT_READY"
                else:
                    result["payment_method"] = "Chưa liên kết (None)"
                    result["payout_status"] = "PAYOUT_NOT_LINKED"
        except Exception as e:
            result["errors"].append(f"Onboarding error: {e}")

        # 4. KYC Compliance Info
        try:
            url_kyc = f"{self.base_host}/webcast/api/compliance/kyc/v1/info/detail"
            resp_kyc = self.session.get(url_kyc, headers=headers, timeout=self.timeout)
            if resp_kyc.status_code == 200:
                raw_kyc = resp_kyc.json()
                d_kyc = raw_kyc.get("data", {}) if isinstance(raw_kyc, dict) else {}
                
                k_obj = d_kyc.get("kyc_status")
                if isinstance(k_obj, dict):
                    created = k_obj.get("created", False)
                    cdd = k_obj.get("cdd_status", 0)
                    if created or cdd == 1:
                        result["kyc_status"] = "APPROVED"
                    else:
                        result["kyc_status"] = "NOT_STARTED"
                else:
                    k_status = str(k_obj or d_kyc.get("status", "")).upper()
                    if any(x in k_status for x in ("APPROV", "PASS", "SUCCESS", "VERIFIED")):
                        result["kyc_status"] = "APPROVED"
                    elif any(x in k_status for x in ("PEND", "REVIEW", "WAIT")):
                        result["kyc_status"] = "PENDING"
                    elif any(x in k_status for x in ("REJECT", "FAIL", "DENI")):
                        result["kyc_status"] = "REJECTED"
                    else:
                        result["kyc_status"] = "NOT_STARTED"
        except Exception as e:
            result["errors"].append(f"KYC error: {e}")

        return result

        return result


def fetch_monetization_snapshot(
    profile_name: str,
    profile_config: Dict[str, Any],
    timeout: float = 12.0,
) -> Dict[str, Any]:
    """Hàm tiện ích cấp module để gọi nhanh truy vấn thu nhập của 1 profile."""
    client = TikTokMonetizationClient(profile_name, profile_config, timeout=timeout)
    return client.fetch_all_monetization_data()
