import unittest

from tiktok_account_discovery import (
    EndpointRecord,
    extract_payload_keys,
    is_allowed_host,
    is_allowed_path,
    is_json_content_type,
    is_readonly_allowed,
    is_sensitive_query,
    payload_shape,
    redact_query,
    request_path,
    classify_endpoint,
)
from tiktok_account_inspection import (
    AccountInspectionResult,
    AnalyticsInfo,
    InspectionState,
    PayoutInfo,
    classify_account,
    mask_email,
    mask_identifier,
)


class RedactionTests(unittest.TestCase):
    def test_mask_email_hides_local_and_domain(self):
        masked = mask_email("thanh.tri@gmail.com")
        self.assertNotIn("thanh.tri", masked)
        self.assertNotIn("gmail", masked)
        self.assertIn("***", masked)

    def test_mask_email_empty(self):
        self.assertEqual(mask_email(""), "")

    def test_mask_email_short_local(self):
        masked = mask_email("ab@x.com")
        self.assertNotIn("x.com", masked)

    def test_mask_identifier_keeps_tail(self):
        masked = mask_identifier("TX12345678", keep=4)
        self.assertEqual(masked[-4:], "5678")
        self.assertNotIn("TX123", masked)

    def test_mask_identifier_short_fully_masked(self):
        self.assertEqual(mask_identifier("abc", keep=4), "***")

    def test_redact_query_drops_sensitive(self):
        url = "https://www.tiktok.com/api?device_id=123&locale=en&msToken=SECRET"
        safe, dropped = redact_query(url)
        self.assertNotIn("msToken", safe)
        self.assertNotIn("SECRET", safe)
        self.assertIn("device_id", dropped)

    def test_redact_query_no_query(self):
        url = "https://www.tiktok.com/tiktokstudio/api/web/user"
        self.assertEqual(redact_query(url), (url, ()))


class ReadOnlyGuardTests(unittest.TestCase):
    def test_allowed_host(self):
        self.assertTrue(is_allowed_host("https://www.tiktok.com/api"))
        self.assertFalse(is_allowed_host("https://example.com/api"))

    def test_allowed_path(self):
        self.assertTrue(is_allowed_path("https://www.tiktok.com/tiktokstudio/api/web/user"))
        self.assertTrue(
            is_allowed_path("https://www.tiktok.com/tiktok/v1/creator/m10n_center/reward_analytics")
        )
        self.assertFalse(is_allowed_path("https://www.tiktok.com/api/v1/video/upload/auth/"))

    def test_sensitive_query_detected(self):
        self.assertTrue(is_sensitive_query("https://www.tiktok.com/api?msToken=abc"))
        self.assertTrue(is_sensitive_query("https://www.tiktok.com/api?X-Bogus=abc"))
        self.assertFalse(is_sensitive_query("https://www.tiktok.com/api?locale=en"))

    def test_readonly_requires_get_and_allowed(self):
        self.assertTrue(
            is_readonly_allowed("https://www.tiktok.com/tiktokstudio/api/web/user", "GET")
        )
        self.assertFalse(
            is_readonly_allowed("https://www.tiktok.com/tiktokstudio/api/web/user", "POST")
        )
        self.assertFalse(
            is_readonly_allowed("https://www.tiktok.com/api/v1/video/upload/auth/", "GET")
        )
        self.assertFalse(
            is_readonly_allowed("https://www.tiktok.com/tiktokstudio/api/web/user?msToken=x", "GET")
        )

    def test_json_content_type(self):
        self.assertTrue(is_json_content_type("application/json; charset=utf-8"))
        self.assertFalse(is_json_content_type("text/html"))

    def test_request_path(self):
        self.assertEqual(
            request_path("https://www.tiktok.com/tiktok/v1/creator/x?locale=en"),
            "/tiktok/v1/creator/x",
        )


class PayloadShapeTests(unittest.TestCase):
    def test_payload_shape_scalars(self):
        shape = payload_shape({"id": 1, "name": "x", "ok": True, "none": None})
        self.assertEqual(
            shape, {"id": "int", "name": "str", "ok": "bool", "none": "null"}
        )

    def test_payload_shape_nested(self):
        shape = payload_shape({"data": {"views": 5}})
        self.assertEqual(shape, {"data": {"views": "int"}})

    def test_extract_payload_keys_dotted(self):
        keys = extract_payload_keys({"data": {"views": 5, "list": [{"a": 1}]}})
        self.assertIn("data.views", keys)
        self.assertIn("data.list[].a", keys)

    def test_extract_payload_keys_depth_limited(self):
        keys = extract_payload_keys({"a": {"b": {"c": {"d": {"e": 1}}}}}, max_depth=2)
        self.assertTrue(any(key.endswith("<nested>") for key in keys))


class ClassifyEndpointTests(unittest.TestCase):
    def test_healthy_endpoint_stays(self):
        record = EndpointRecord(
            path="/tiktok/v1/creator/x",
            status=200,
            content_type="application/json",
            payload_keys=("a",),
        )
        self.assertIsNone(classify_endpoint(record))

    def test_unauthorized_downgraded(self):
        record = EndpointRecord(path="/x", status=401, content_type="application/json", payload_keys=("a",))
        downgraded = classify_endpoint(record)
        self.assertIsNotNone(downgraded)
        self.assertFalse(downgraded.safe_get)

    def test_non_json_downgraded(self):
        record = EndpointRecord(
            path="/x", status=200, content_type="text/html", payload_keys=("a",)
        )
        self.assertIsNotNone(classify_endpoint(record))

    def test_empty_payload_downgraded(self):
        record = EndpointRecord(path="/x", status=200, content_type="application/json")
        self.assertIsNotNone(classify_endpoint(record))


class ClassificationTests(unittest.TestCase):
    def test_login_required_summary(self):
        result = AccountInspectionResult(state=InspectionState.LOGIN_REQUIRED)
        self.assertEqual(classify_account(result), "Cần đăng nhập lại")

    def test_full_success_summary(self):
        result = AccountInspectionResult(
            state=InspectionState.SUCCESS,
            identity=IdentityInfoAlias(unique_id="user"),
            analytics=AnalyticsInfo(views_30d=100),
            monetization=MonetizationAlias(balance_amount="10"),
            payout=PayoutInfo(payout_linked=True),
        )
        summary = classify_account(result)
        self.assertIn("Identity OK", summary)
        self.assertIn("Analytics OK", summary)
        self.assertIn("Monetized", summary)
        self.assertIn("Payout Ready", summary)

    def test_missing_groups_marked(self):
        result = AccountInspectionResult(state=InspectionState.SUCCESS)
        summary = classify_account(result)
        self.assertIn("Identity thiếu", summary)
        self.assertIn("Analytics N/A", summary)


class ParseTests(unittest.TestCase):
    def test_parse_web_user_identity(self):
        from tiktok_account_inspection import parse_identity_from_web_user

        payload = {
            "userId": "7436260438268888097",
            "statusCode": 0,
            "userBaseInfo": {
                "UserProfile": {
                    "UserBase": {
                        "UniqId": "sughemxtpds",
                        "NickName": "sughemxtpds",
                        "Region": {"Region": "US"},
                        "Id": "7436260438268888097",
                        "CertInfo": {"HasCert": {"value": True}},
                    },
                    "UserStatus": {"UserStatus": "normal"},
                }
            },
        }
        identity = parse_identity_from_web_user(payload)
        self.assertEqual(identity.numeric_user_id, "7436260438268888097")
        self.assertEqual(identity.unique_id, "sughemxtpds")
        self.assertEqual(identity.region, "US")
        self.assertIs(identity.verified, True)
        self.assertEqual(identity.account_status, "normal")

    def test_parse_app_context_identity(self):
        from tiktok_account_inspection import parse_identity_from_app_context

        payload = {"region": "US", "user": {"uid": "123", "uniqueId": "handle", "nickName": "Name"}}
        identity = parse_identity_from_app_context(payload)
        self.assertEqual(identity.numeric_user_id, "123")
        self.assertEqual(identity.unique_id, "handle")
        self.assertEqual(identity.nickname, "Name")

    def test_merge_identity_fills_gaps(self):
        from tiktok_account_inspection import (
            IdentityInfo,
            merge_identity,
        )

        merged = merge_identity(
            IdentityInfo(numeric_user_id="1"),
            IdentityInfo(unique_id="u", region="DE"),
        )
        self.assertEqual(merged.numeric_user_id, "1")
        self.assertEqual(merged.unique_id, "u")
        self.assertEqual(merged.region, "DE")

    def test_build_result_success_when_identity_found(self):
        from tiktok_account_inspection import build_inspection_result

        result = build_inspection_result(
            "AUTO 6",
            {
                "/tiktokstudio/api/web/user": {
                    "userId": "7436260438268888097",
                    "userBaseInfo": {
                        "UserProfile": {"UserBase": {"UniqId": "sughemxtpds"}}
                    },
                }
            },
            checked_at="2026-08-14T00:00:00Z",
        )
        self.assertEqual(result.state, InspectionState.PARTIAL)
        self.assertEqual(result.identity.numeric_user_id, "7436260438268888097")
        self.assertGreaterEqual(len(result.warnings), 1)

    def test_build_result_login_required_without_identity(self):
        from tiktok_account_inspection import build_inspection_result

        result = build_inspection_result("AUTO 6", {}, checked_at="x")
        self.assertEqual(result.state, InspectionState.LOGIN_REQUIRED)

    def test_reward_error_is_not_treated_as_balance(self):
        from tiktok_account_inspection import parse_monetization_from_reward

        payload = {
            "log_pb": {"impr_id": "x"},
            "status_code": 3016030,
            "status_msg": "Invalid parameters",
        }
        info, ok = parse_monetization_from_reward(payload)
        self.assertFalse(ok)
        self.assertEqual(info.balance_amount, "")

    def test_to_plain_converts_mappingproxy(self):
        from types import MappingProxyType

        from tiktok_account_inspection import to_plain

        raw = MappingProxyType({"user": MappingProxyType({"uid": "1"}), "items": (1, 2)})
        plain = to_plain(raw)
        self.assertEqual(plain, {"user": {"uid": "1"}, "items": [1, 2]})
        self.assertIs(type(plain["user"]), dict)

    def test_money_str_formats(self):
        from tiktok_account_inspection import _money_str

        self.assertEqual(_money_str(10), "10.00")
        self.assertEqual(_money_str("3.5"), "3.5")
        self.assertEqual(_money_str(None), "")


class IdentityInfoAlias:
    def __init__(self, unique_id=""):
        self.unique_id = unique_id
        self.numeric_user_id = ""


class MonetizationAlias:
    def __init__(self, balance_amount=""):
        self.balance_amount = balance_amount
        self.currency = ""
        self.available_amount = ""
        self.pending_amount = ""
        self.earnings_30d = ""
        self.earnings_current_month = ""
        self.earnings_lifetime = ""
        self.last_updated_at = ""


if __name__ == "__main__":
    unittest.main()
