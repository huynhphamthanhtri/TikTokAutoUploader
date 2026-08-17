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

HOST_US = "https://webcast.tiktok.com"
HOST_EU = "https://webcast.tiktok.com"
HOST_GLOBAL = "https://webcast.tiktok.com"

CREATOR_HOST_EU = "https://api16-normal-no1a.tiktokv.eu"
CREATOR_HOST_US = "https://api16-normal-useast8.tiktokv.us"
CREATOR_HOST_GLOBAL = "https://api16-normal-c-useast1a.tiktokv.com"
CREATOR_HOST_VA = "https://api16-va.tiktokv.com"

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


def resolve_creator_base_hosts(region: str) -> List[str]:
    """Trả về danh sách host Creator Rewards theo thứ tự ưu tiên dựa trên region."""
    code = str(region or "").strip().upper()
    if code in EU_COUNTRY_CODES:
        return [CREATOR_HOST_EU, CREATOR_HOST_US, CREATOR_HOST_GLOBAL, CREATOR_HOST_VA]
    elif code in ("US", "USA"):
        return [CREATOR_HOST_US, CREATOR_HOST_EU, CREATOR_HOST_GLOBAL, CREATOR_HOST_VA]
    else:
        return [CREATOR_HOST_GLOBAL, CREATOR_HOST_EU, CREATOR_HOST_US, CREATOR_HOST_VA]


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
        text = cookie_raw.strip()
        if text.startswith("[") or text.startswith("{"):
            try:
                parsed = json.loads(text)
                return build_cookie_string(parsed)
            except Exception:
                return text
        return text
    if isinstance(cookie_raw, dict):
        return "; ".join(f"{k}={v}" for k, v in cookie_raw.items() if k and v is not None)
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
            "Referer": "https://www.tiktok.com/",
            "Origin": "https://www.tiktok.com",
            "Cookie": cookie_str,
        }

    def fetch_all_monetization_data(self) -> Dict[str, Any]:
        """Truy vấn tổng hợp: Số dư, Quỹ tác giả, PTTT, KYC và CRP."""
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
            "tax_status": "NOT_STARTED",
            "tiktok_user_id": "",
            "unique_id": "",
            "nickname": "",
            "sec_uid": "",
            "kyc_full_name": "",
            "kyc_id_type": "",
            "kyc_id_country": "",
            "kyc_birthday": "",
            "kyc_nationality": "",
            "payment_method": "Chưa liên kết (None)",
            "rewards_estimated": "$0.00",
            "pending_earnings": [],
            "payout_breakdown": [],
            "crp_status": "NOT_STARTED",
            "crp_display": "Chưa check",
            "crp_punishment": "",
            "crp_reapply_date": "",
            "crp_can_reapply": False,
            "crp_followers": 0,
            "crp_followers_threshold": 10000,
            "crp_views": 0,
            "crp_views_threshold": 100000,
            "crp_all_met": False,
            "crp_rpm": 0.0,
            "crp_qualified_views": 0,
            "crp_estimated_revenue": 0.0,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts)),
            "timestamp": now_ts,
            "freshness": "live",
            "errors": [],
        }

        # 0. Check Cookie existence
        cookie_header = build_cookie_string(self.config.get("cookie_str"))
        if not cookie_header:
            result["status"] = "NO_AUTH"
            result["payout_status"] = "Chưa có Cookie"
            result["kyc_status"] = "Chưa có Cookie"
            result["tax_status"] = "Chưa có Cookie"
            result["crp_status"] = "NO_AUTH"
            result["crp_display"] = "⚪ Chưa có Cookie"
            result["payment_method"] = "Chưa có Cookie (Cần đăng nhập)"
            result["checked_at"] = "Chưa có Cookie"
            result["freshness"] = "unknown"
            return result

        headers = self._get_headers()

        # 0.5. TikTok Passport Account & UID Info
        try:
            url_pass = "https://www.tiktok.com/passport/web/account/info/"
            resp_pass = self.session.get(url_pass, headers=headers, timeout=self.timeout)
            if resp_pass.status_code == 200:
                raw_pass = resp_pass.json() if resp_pass.text else {}
                if isinstance(raw_pass, dict):
                    p_data = raw_pass.get("data", {})
                    if isinstance(p_data, dict):
                        u_id = p_data.get("user_id_str") or p_data.get("user_id")
                        if u_id:
                            result["tiktok_user_id"] = str(u_id)
                        if p_data.get("screen_name") or p_data.get("username"):
                            result["unique_id"] = str(p_data.get("screen_name") or p_data.get("username"))
                        if p_data.get("sec_user_id"):
                            result["sec_uid"] = str(p_data.get("sec_user_id"))
        except Exception as e:
            result["errors"].append(f"Account info error: {e}")

        # 0.6. Fallback Account Extraction via /setting if UID or unique_id is missing
        if not result["tiktok_user_id"] or not result["unique_id"]:
            try:
                url_setting = "https://www.tiktok.com/setting"
                headers_html = dict(headers)
                headers_html["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                resp_setting = self.session.get(url_setting, headers=headers_html, timeout=self.timeout)
                if resp_setting.status_code == 200 and resp_setting.text:
                    body_text = resp_setting.text
                    if not result["tiktok_user_id"]:
                        m_uid = (
                            re.search(r'"uid":"(\d+)"', body_text)
                            or re.search(r'"user_id":"(\d+)"', body_text)
                            or re.search(r'"userId":"(\d+)"', body_text)
                            or re.search(r'"id":"(\d{15,22})"', body_text)
                        )
                        if m_uid:
                            result["tiktok_user_id"] = m_uid.group(1)
                    if not result["unique_id"]:
                        m_uniq = (
                            re.search(r'"uniqueId":"([^"]+)"', body_text)
                            or re.search(r'"unique_id":"([^"]+)"', body_text)
                        )
                        if m_uniq:
                            result["unique_id"] = m_uniq.group(1)
                    if not result["sec_uid"]:
                        m_sec = re.search(r'"secUid":"([^"]+)"', body_text)
                        if m_sec:
                            result["sec_uid"] = m_sec.group(1)
            except Exception:
                pass

        # 1. Payout Summary (Số dư & Kiểm tra Cookie Die)
        try:
            url_balance = f"{self.base_host}/webcast/api/money/creator_earnings/v1/payout_summary"
            resp = self.session.get(url_balance, headers=headers, timeout=self.timeout)

            # Check HTTP 401/403 (Cookie Die)
            if resp.status_code in (401, 403):
                result["status"] = "COOKIE_EXPIRED"
                result["payout_status"] = "Cookie Die"
                result["kyc_status"] = "Cookie Die"
                result["crp_status"] = "COOKIE_EXPIRED"
                result["crp_display"] = "🔴 Cookie Die"
                result["payment_method"] = "Cookie die - Không check được"
                result["checked_at"] = "Cookie hết hạn"
                return result

            if resp.status_code == 200:
                raw_json = resp.json() if resp.text else {}
                if isinstance(raw_json, dict):
                    status_code = raw_json.get("status_code", 0)
                    status_msg = str(raw_json.get("status_message", "") or "").lower()

                    # Detect TikTok status_code 20003 / 10003 (Cookie expired / not logged in)
                    if status_code in (20003, 10003) or "not logged in" in status_msg or "login" in status_msg:
                        result["status"] = "COOKIE_EXPIRED"
                        result["payout_status"] = "Cookie Die"
                        result["kyc_status"] = "Cookie Die"
                        result["crp_status"] = "COOKIE_EXPIRED"
                        result["crp_display"] = "🔴 Cookie Die"
                        result["payment_method"] = "Cookie die - Không check được"
                        result["checked_at"] = "Cookie hết hạn"
                        return result

                    data = raw_json.get("data", {}) if isinstance(raw_json.get("data"), dict) else {}

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
            else:
                result["errors"].append(f"HTTP {resp.status_code}: {resp.text[:100]}")
        except requests.exceptions.ProxyError as e:
            result["status"] = "PROXY_ERROR"
            result["payout_status"] = "Lỗi Proxy"
            result["kyc_status"] = "Lỗi Proxy"
            result["crp_status"] = "PROXY_ERROR"
            result["crp_display"] = "🟡 Lỗi Proxy"
            result["payment_method"] = "Không kết nối được Proxy"
            result["checked_at"] = "Lỗi Proxy"
            result["errors"].append(f"Proxy error: {e}")
            return result
        except requests.RequestException as e:
            err_str = str(e).lower()
            if "proxy" in err_str:
                result["status"] = "PROXY_ERROR"
                result["payout_status"] = "Lỗi Proxy"
                result["kyc_status"] = "Lỗi Proxy"
                result["crp_status"] = "PROXY_ERROR"
                result["crp_display"] = "🟡 Lỗi Proxy"
                result["payment_method"] = "Không kết nối được Proxy"
                result["checked_at"] = "Lỗi Proxy"
                result["errors"].append(f"Proxy error: {e}")
                return result
            result["errors"].append(f"Balance error: {e}")

        # 2. Business Rewards (Quỹ tác giả)
        try:
            url_rewards = f"{self.base_host}/webcast/api/money/one-wallet/v1/business/rewards"
            params = {
                "wallet_type": "MONTHLY_EARNING",
                "business_type": "CREATIVE_REWARDS",
            }
            resp_rew = self.session.get(url_rewards, params=params, headers=headers, timeout=self.timeout)
            if resp_rew.status_code == 200:
                raw_rew = resp_rew.json() if resp_rew.text else {}
                d_rew = raw_rew.get("data", {}) if isinstance(raw_rew, dict) and isinstance(raw_rew.get("data"), dict) else {}
                summary_rew = d_rew.get("summary", {}) if isinstance(d_rew.get("summary"), dict) else {}
                est = summary_rew.get("estimated_amount", {}) if isinstance(summary_rew.get("estimated_amount"), dict) else {}
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

        # 3. Payout Onboarding (PTTT & Thuế / Tax Status)
        try:
            url_onboarding = f"{self.base_host}/webcast/api/money/payout_onboarding/v2/onboarding_detail"
            resp_onb = self.session.get(url_onboarding, headers=headers, timeout=self.timeout)
            if resp_onb.status_code in (401, 403):
                result["payout_status"] = "Cookie Die"
                result["tax_status"] = "Cookie Die"
            elif resp_onb.status_code == 200:
                raw_onb = resp_onb.json() if resp_onb.text else {}
                d_onb = raw_onb.get("data", {}) if isinstance(raw_onb, dict) and isinstance(raw_onb.get("data"), dict) else {}

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
                elif bind_status == 2:
                    result["payment_method"] = "🟡 Đang xác minh PTTT (Pending)"
                    result["payout_status"] = "PAYOUT_PENDING"
                else:
                    result["payment_method"] = "Chưa liên kết (None)"
                    result["payout_status"] = "PAYOUT_NOT_LINKED"

                # Parse Tax Status separately from KYC
                u_tax = d_onb.get("user_tax_status")
                if u_tax == 1:
                    result["tax_status"] = "TAX_VERIFIED"
                elif u_tax == 2:
                    result["tax_status"] = "TAX_PENDING"
                else:
                    result["tax_status"] = "TAX_NOT_STARTED"
        except Exception as e:
            result["errors"].append(f"Onboarding error: {e}")

        # 4. KYC Identity Compliance Info (Xác minh danh tính)
        try:
            url_kyc = f"{self.base_host}/webcast/api/compliance/kyc/v1/info/detail"
            resp_kyc = self.session.get(url_kyc, headers=headers, timeout=self.timeout)
            if resp_kyc.status_code in (401, 403):
                result["kyc_status"] = "Cookie Die"
            elif resp_kyc.status_code == 200:
                raw_kyc = resp_kyc.json() if resp_kyc.text else {}
                k_obj = raw_kyc.get("kyc_status") or (raw_kyc.get("data", {}) if isinstance(raw_kyc.get("data"), dict) else {}).get("kyc_status")
                last_sub = raw_kyc.get("last_submitted_data", {}) or (raw_kyc.get("data", {}) if isinstance(raw_kyc.get("data"), dict) else {}).get("last_submitted_data", {})

                full_name = ""
                if isinstance(last_sub, dict):
                    full_name = str(last_sub.get("full_name", "") or "")
                    result["kyc_full_name"] = full_name
                    result["kyc_id_type"] = str(last_sub.get("id_type", "") or "")
                    result["kyc_id_country"] = str(last_sub.get("id_issue_country_region", "") or "")
                    result["kyc_birthday"] = str(last_sub.get("birthday", "") or "")
                    result["kyc_nationality"] = str(last_sub.get("nationality", "") or "")

                if isinstance(k_obj, dict):
                    created = bool(k_obj.get("created", False))
                    cdd = int(k_obj.get("cdd_status", 0) or 0)
                    fail_poa = bool(k_obj.get("fail_dynamic_poa", False))
                    id_resubmit = bool(k_obj.get("id_doc_resubmit", False))
                    poa_resubmit = bool(k_obj.get("poa_doc_resubmit", False))

                    if fail_poa or id_resubmit or poa_resubmit:
                        result["kyc_status"] = "RESUBMIT"
                    elif created and cdd >= 7:
                        result["kyc_status"] = "APPROVED"
                    elif cdd in (1, 2) or (created and cdd < 7):
                        result["kyc_status"] = "PENDING"
                    elif not created and not full_name:
                        result["kyc_status"] = "NOT_STARTED"
                    else:
                        result["kyc_status"] = "WARNING" if full_name else "NOT_STARTED"

                    kyc_uid = (
                        k_obj.get("user_id") if isinstance(k_obj, dict) else None
                    ) or raw_kyc.get("user_id") or raw_kyc.get("uid") or (raw_kyc.get("data", {}) if isinstance(raw_kyc.get("data"), dict) else {}).get("user_id")
                    if kyc_uid and (not result["tiktok_user_id"] or result["tiktok_user_id"] == "0"):
                        result["tiktok_user_id"] = str(kyc_uid)
                else:
                    k_status = str(k_obj or raw_kyc.get("status", "")).upper()
                    if any(x in k_status for x in ("APPROV", "PASS", "SUCCESS", "VERIFIED")):
                        result["kyc_status"] = "APPROVED"
                    elif any(x in k_status for x in ("PEND", "REVIEW", "WAIT")):
                        result["kyc_status"] = "PENDING"
                    elif any(x in k_status for x in ("RESUBMIT", "POA", "RE_SUBMIT")):
                        result["kyc_status"] = "RESUBMIT"
                    elif any(x in k_status for x in ("REJECT", "FAIL", "DENI")):
                        result["kyc_status"] = "REJECTED"
                    else:
                        result["kyc_status"] = "NOT_STARTED"
                    
                    kyc_uid = raw_kyc.get("user_id") or raw_kyc.get("uid") or (raw_kyc.get("data", {}) if isinstance(raw_kyc.get("data"), dict) else {}).get("user_id")
                    if kyc_uid and (not result["tiktok_user_id"] or result["tiktok_user_id"] == "0"):
                        result["tiktok_user_id"] = str(kyc_uid)
        except Exception as e:
            result["errors"].append(f"KYC error: {e}")

        # Fallback UID and unique_id from profile config if still missing
        if not result["tiktok_user_id"]:
            cfg_id = self.config.get("tiktok_id") or self.config.get("tiktok_account")
            if cfg_id:
                result["tiktok_user_id"] = str(cfg_id)
        if not result["unique_id"]:
            cfg_user = self.config.get("tiktok_account") or self.config.get("tiktok_id")
            if cfg_user:
                result["unique_id"] = str(cfg_user).lstrip("@")

        # 5. CRP Creator Rewards Profile & Eligibility (Multi-host failover)
        creator_hosts = resolve_creator_base_hosts(self.region)
        crp_found = False

        for chost in creator_hosts:
            try:
                url_crp = f"{chost}/tiktok/v1/creator/incentives/profile"
                resp_crp = self.session.get(url_crp, headers=headers, timeout=self.timeout)
                if resp_crp.status_code == 200:
                    raw_crp = resp_crp.json() if resp_crp.text else {}
                    if isinstance(raw_crp, dict) and raw_crp.get("status_code") == 0:
                        crp_found = True
                        p_status = str(raw_crp.get("profile_status", "")).strip()
                        check_list = raw_crp.get("apply_check_list", []) or []
                        raw_data = raw_crp.get("raw", {}) if isinstance(raw_crp.get("raw"), dict) else raw_crp
                        p_data = raw_data.get("profile", {}) if isinstance(raw_data.get("profile"), dict) else {}
                        appeal_info = p_data.get("appeal_info", {}) if isinstance(p_data.get("appeal_info"), dict) else {}
                        punishments = raw_data.get("punishment_infos", []) or raw_crp.get("punishment_infos", []) or []

                        f_item = next((c for c in check_list if c.get("key") == "follower_count"), {})
                        v_item = next((c for c in check_list if c.get("key") == "video_view"), {})
                        result["crp_followers"] = int(f_item.get("amount", 0) or 0)
                        result["crp_followers_threshold"] = int(f_item.get("threshold", 10000) or 10000)
                        result["crp_views"] = int(v_item.get("amount", 0) or 0)
                        result["crp_views_threshold"] = int(v_item.get("threshold", 100000) or 100000)

                        f_ok = (f_item.get("status") == 1) or (result["crp_followers"] >= result["crp_followers_threshold"] > 0)
                        v_ok = (v_item.get("status") == 1) or (result["crp_views"] >= result["crp_views_threshold"] > 0)
                        result["crp_all_met"] = bool(raw_crp.get("all_requirements_met") or (f_ok and v_ok))

                        # Extract exact store region from TikTok
                        st_reg = p_data.get("store_region") or raw_crp.get("store_region")
                        if st_reg:
                            result["region"] = str(st_reg).strip().upper()
                            result["store_region"] = str(st_reg).strip().upper()

                        punishment_title = ""
                        if punishments:
                            p_first = punishments[0]
                            punishment_title = p_first.get("title", "")
                            p_desc = p_first.get("description", "")
                            result["crp_punishment"] = punishment_title
                            result["crp_punishment_desc"] = p_desc
                            result["crp_punishments_all"] = punishments

                        # Punishment label normalization
                        if punishment_title:
                            p_t_lower = punishment_title.lower()
                            if "security" in p_t_lower:
                                punishment_label = "Bảo mật (TKTBM)"
                            elif "unoriginal" in p_t_lower or "copy" in p_t_lower:
                                punishment_label = "Nội dung copy"
                            else:
                                punishment_label = punishment_title
                        else:
                            punishment_label = ""

                        reapply_ts = p_data.get("reapply_starting_date", 0) or 0
                        reapply_date_str = ""
                        can_reapply = False
                        if reapply_ts and reapply_ts > 0:
                            try:
                                reapply_date_str = time.strftime("%d/%m/%Y", time.localtime(float(reapply_ts)))
                                if float(reapply_ts) <= now_ts:
                                    can_reapply = True
                            except Exception:
                                reapply_date_str = str(reapply_ts)

                        result["crp_reapply_date"] = reapply_date_str
                        result["crp_can_reapply"] = can_reapply
                        result["crp_could_appeal"] = bool(p_data.get("could_appeal", False))
                        result["crp_could_reapply"] = bool(p_data.get("could_reapply", False))

                        appeal_submit_ts = appeal_info.get("appeal_submit_time", 0) or 0
                        appeal_deadline_ts = appeal_info.get("appeal_review_deadline", 0) or 0

                        f_k = result["crp_followers"] / 1000.0 if result["crp_followers"] >= 1000 else result["crp_followers"]
                        f_th = result["crp_followers_threshold"] / 1000.0
                        f_unit = "k" if result["crp_followers"] >= 1000 else ""

                        # Evaluate lifecycle state
                        if p_status.lower() in ("enabled", "active") or raw_crp.get("enabled") is True:
                            result["crp_status"] = "ACTIVE"
                            result["crp_display"] = "🟢 KIẾM TIỀN"
                        elif appeal_submit_ts > 0 and (appeal_deadline_ts * 1000 > now_ts * 1000 or appeal_deadline_ts == 0):
                            result["crp_status"] = "APPEAL"
                            d_str = ""
                            if appeal_deadline_ts:
                                try:
                                    d_str = f" - Hạn: {time.strftime('%d/%m/%Y', time.localtime(float(appeal_deadline_ts)))}"
                                except Exception:
                                    pass
                            result["crp_display"] = f"🟡 ĐANG KHÁNG{d_str}"
                        elif p_status.lower() in ("in review", "review") or raw_crp.get("formatted") == "In Review":
                            result["crp_status"] = "REVIEW"
                            result["crp_display"] = "🟡 ĐANG DUYỆT"
                        elif reapply_ts and reapply_ts > 0:
                            if can_reapply:
                                # Reapply date has passed! Evaluate current stats
                                if result["crp_all_met"]:
                                    result["crp_status"] = "ELIGIBLE"
                                    result["crp_display"] = "🟢 ĐỦ ĐK (Hết hạn phạt - Sẵn sàng ĐK lại)"
                                else:
                                    result["crp_status"] = "INELIGIBLE"
                                    result["crp_display"] = f"⚪ CHƯA ĐỦ ({f_k:.1f}{f_unit}/{f_th:.0f}k - Hết hạn phạt)"
                            else:
                                # Reapply date is in the future (penalty still active)
                                if "bảo mật" in punishment_label.lower():
                                    result["crp_status"] = "TKTBM"
                                    result["crp_display"] = f"🔴 TKTBM (ĐK lại: {reapply_date_str})"
                                else:
                                    result["crp_status"] = "REJECTED"
                                    result["crp_display"] = f"🔴 BỊ LOẠI ({punishment_label} - ĐK lại: {reapply_date_str})"
                        elif punishment_title:
                            if "bảo mật" in punishment_label.lower():
                                result["crp_status"] = "TKTBM"
                                result["crp_display"] = "🔴 TKTBM (Bảo mật)"
                            else:
                                result["crp_status"] = "REJECTED"
                                result["crp_display"] = f"🔴 BỊ LOẠI ({punishment_label})"
                        elif result["crp_all_met"]:
                            result["crp_status"] = "ELIGIBLE"
                            result["crp_display"] = "🟢 ĐỦ ĐIỀU KIỆN"
                        else:
                            result["crp_status"] = "INELIGIBLE"
                            result["crp_display"] = f"⚪ CHƯA ĐỦ ({f_k:.1f}{f_unit}/{f_th:.0f}k)"
                        break
            except Exception as e:
                result["errors"].append(f"CRP profile error ({chost}): {e}")

        # Fallback if profile status was still not determined
        if not crp_found and result["crp_status"] == "NOT_STARTED" and result["status"] == "SUCCESS":
            result["crp_status"] = "INELIGIBLE"
            result["crp_display"] = "⚪ CHƯA ĐỦ ĐK"

        # 6. CRP Dashboard Analytics (RPM & Qualified Views - Multi-host failover)
        today = time.strftime("%Y-%m-%d")
        for chost in creator_hosts:
            try:
                url_dash = f"{chost}/tiktok/v1/creator/incentives/analytics/dashboard_overview"
                resp_dash = self.session.get(url_dash, params={"start_date": "2026-01-01", "end_date": today}, headers=headers, timeout=self.timeout)
                if resp_dash.status_code == 200:
                    raw_dash = resp_dash.json() if resp_dash.text else {}
                    if isinstance(raw_dash, dict) and raw_dash.get("status_code") == 0:
                        result["crp_rpm"] = float(raw_dash.get("rpm", 0.0) or 0.0)
                        result["crp_qualified_views"] = int(raw_dash.get("qualified_views", 0) or 0)
                        result["crp_estimated_revenue"] = float(raw_dash.get("estimated_revenue", 0.0) or 0.0)
                        break
            except Exception as e:
                result["errors"].append(f"CRP dash error ({chost}): {e}")

        # 7. Channel Public Stats (Followers, Likes, Videos) via TikTok Profile Page
        if result.get("unique_id"):
            try:
                url_prof = f"https://www.tiktok.com/@{result['unique_id']}"
                headers_html = dict(headers)
                headers_html["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                resp_prof = self.session.get(url_prof, headers=headers_html, timeout=min(self.timeout, 8.0))
                if resp_prof.status_code == 200 and resp_prof.text:
                    body_p = resp_prof.text
                    m_f = re.search(r'"followerCount":(\d+)', body_p)
                    m_h = re.search(r'"heartCount":(\d+)', body_p) or re.search(r'"heart":(\d+)', body_p)
                    m_v = re.search(r'"videoCount":(\d+)', body_p)
                    m_nick = re.search(r'"nickname":"([^"]+)"', body_p)
                    m_avatar = re.search(r'"avatarLarger":"([^"]+)"', body_p) or re.search(r'"avatarUri":\["([^"]+)"', body_p)

                    if m_f:
                        result["follower_count"] = int(m_f.group(1))
                        if not result.get("crp_followers"):
                            result["crp_followers"] = int(m_f.group(1))
                    if m_h:
                        result["heart_count"] = int(m_h.group(1))
                    if m_v:
                        result["video_count"] = int(m_v.group(1))
                    if m_nick and not result.get("nickname"):
                        result["nickname"] = m_nick.group(1)
                    if m_avatar and not result.get("avatar_url"):
                        result["avatar_url"] = m_avatar.group(1).replace(r"\u002F", "/")
            except Exception:
                pass

        return result

    def apply_creative_rewards(self) -> Dict[str, Any]:
        """Gửi đơn đăng ký tham gia Quỹ Kiếm Tiền (Creative Rewards) qua API."""
        headers = self._get_headers()
        creator_hosts = resolve_creator_base_hosts(self.region)
        last_msg = "Không có kết nối"
        for chost in creator_hosts:
            url_apply = f"{chost}/tiktok/v1/creator/incentives/apply"
            try:
                resp = self.session.post(url_apply, headers=headers, json={}, timeout=self.timeout)
                if resp.status_code == 200:
                    raw = resp.json() if resp.text else {}
                    status_code = raw.get("status_code", 0)
                    status_msg = raw.get("status_msg") or raw.get("message") or "Success"
                    if status_code == 0:
                        return {"success": True, "message": "Gửi đơn đăng ký kiếm tiền thành công!"}
                    return {"success": False, "message": f"TikTok phản hồi: {status_msg}"}
                last_msg = f"HTTP {resp.status_code}: {resp.text[:120]}"
            except Exception as e:
                last_msg = f"Lỗi gửi đơn: {e}"
        return {"success": False, "message": last_msg}


def fetch_monetization_snapshot(
    profile_name: str,
    profile_config: Dict[str, Any],
    timeout: float = 12.0,
) -> Dict[str, Any]:
    """Hàm tiện ích cấp module để gọi nhanh truy vấn thu nhập của 1 profile."""
    client = TikTokMonetizationClient(profile_name, profile_config, timeout=timeout)
    return client.fetch_all_monetization_data()


def apply_creative_rewards_for_profile(
    profile_name: str,
    profile_config: Dict[str, Any],
    timeout: float = 12.0,
) -> Dict[str, Any]:
    """Hàm tiện ích cấp module để gửi đơn duyệt kiếm tiền CRP của 1 profile."""
    client = TikTokMonetizationClient(profile_name, profile_config, timeout=timeout)
    return client.apply_creative_rewards()
