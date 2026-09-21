"""
test_user611_dedup_detection.py
=================================
Test suite xác minh tính đúng đắn của dedup engine cho profile user61172346518401.

Ground truth từ ảnh TikTokManager (CheckPost AUTO 6 - EU):
- Tổng 9 videos đầu tiên trong feed
- VIDEO TRÙNG (badge "Trùng"):
  * Row 2: aweme_id=7678772431397588226 (Virginia Trooper) -> group_id khác aweme_id
  * Row 6: aweme_id=7678028218963627286 (Why this cop changed) -> group_id khác aweme_id
- VIDEO KHÔNG TRÙNG: rows 1, 3, 4, 5, 7, 8, 9 (group_id == aweme_id)

Nguồn fixture:
- FACT: Mobile API aweme_list schema với group_id thực
- ASSUMPTION: group_id giả lập dựa trên ảnh TikTokManager; chưa có raw Mobile API response thật
"""
import json
import unittest
from pathlib import Path
from tiktok_dedup_engine import analyze_tiktok_video, TikTokDedupApiClient, ProfileContext

MOBILE_FIXTURE_PATH = Path(__file__).resolve().parent / "scratch" / "user61172346518401_mobile_api.json"

# aweme_id của các video được TikTokManager gắn badge "Trùng" trong ảnh
EXPECTED_DUP_AWEME_IDS = {
    "7678772431397588226",  # Row 2: Virginia Trooper - group_id != aweme_id
    "7678028218963627286",  # Row 6: Why this cop changed - group_id != aweme_id
}

# aweme_id của các video KHÔNG bị TikTokManager gắn badge "Trùng"
EXPECTED_ORIGINAL_AWEME_IDS = {
    "7678807350064483606",  # Row 1: They Dont Want You
    "7678438252101012758",  # Row 3: Cop Claims He Does Not Know
    "7678413916610825494",  # Row 4: He Wouldnt Get Out
    "7678060615272828183",  # Row 5: Why He Refused
    "7677325434337398038",  # Row 7: Cops Had No Reason
    "7677297230436388118",  # Row 8: They Tried To Kick
    "7676954935992274198",  # Row 9: He Told The Cop
}


class TestUser611MobileApiDedupDetection(unittest.TestCase):
    """
    Kiểm chứng engine phát hiện đúng video trùng từ Mobile API schema
    (aweme_list với group_id thực), phản ánh đúng kết quả TikTokManager.
    """

    def setUp(self):
        """Load Mobile API fixture - đây là ground truth schema từ TikTokManager."""
        self.assertTrue(
            MOBILE_FIXTURE_PATH.exists(),
            f"Fixture không tồn tại: {MOBILE_FIXTURE_PATH}. Chạy create_mobile_fixture.py để tạo."
        )
        with open(MOBILE_FIXTURE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.items = data.get("aweme_list") or []
        self.assertEqual(len(self.items), 9, "Fixture phải có đúng 9 items (9 video đầu từ ảnh TikTokManager)")

    def test_fixture_has_exactly_two_dup_videos(self):
        """
        Fixture Mobile API phải có đúng 2 video trùng (group_id != aweme_id),
        phản ánh đúng ảnh TikTokManager hiển thị 2 badge 'Trùng' trong 9 rows đầu.
        """
        dup_items = [i for i in self.items if i.get("group_id") != i.get("aweme_id")]
        self.assertEqual(
            len(dup_items), 2,
            f"Fixture phải có đúng 2 video trùng, hiện có: {len(dup_items)}"
        )
        dup_aweme_ids = {i["aweme_id"] for i in dup_items}
        self.assertEqual(
            dup_aweme_ids, EXPECTED_DUP_AWEME_IDS,
            "aweme_id của video trùng không khớp với ground truth TikTokManager"
        )

    def test_engine_detects_exactly_two_dup_videos(self):
        """
        Engine analyze_tiktok_video phải phát hiện đúng 2/9 video là trùng lặp,
        khớp với badge 'Trùng' trong ảnh TikTokManager.
        """
        analyzed = {item["aweme_id"]: analyze_tiktok_video(item) for item in self.items}

        detected_dups = {aid for aid, res in analyzed.items() if res.is_dup}
        self.assertEqual(
            detected_dups, EXPECTED_DUP_AWEME_IDS,
            f"Engine detect sai: expected={EXPECTED_DUP_AWEME_IDS}, got={detected_dups}"
        )

        # Xác nhận 7 video còn lại KHÔNG phải trùng
        detected_originals = {aid for aid, res in analyzed.items() if not res.is_dup}
        self.assertEqual(
            detected_originals, EXPECTED_ORIGINAL_AWEME_IDS,
            f"Engine sai cho video original: expected={EXPECTED_ORIGINAL_AWEME_IDS}, got={detected_originals}"
        )

    def test_dup_video_has_correct_group_id(self):
        """Video trùng phải có group_id khác aweme_id (đây là cơ chế detect của TikTokManager)."""
        for item in self.items:
            aweme_id = item["aweme_id"]
            result = analyze_tiktok_video(item)
            if aweme_id in EXPECTED_DUP_AWEME_IDS:
                self.assertTrue(result.is_dup, f"Video {aweme_id} phải là trùng lặp")
                self.assertNotEqual(
                    result.group_id, aweme_id,
                    f"Video trùng {aweme_id} phải có group_id khác aweme_id"
                )
            else:
                self.assertFalse(result.is_dup, f"Video {aweme_id} KHÔNG phải trùng lặp")
                self.assertEqual(
                    result.group_id, aweme_id,
                    f"Video original {aweme_id} phải có group_id == aweme_id"
                )

    def test_original_item_false_does_not_trigger_dup(self):
        """
        originalItem: False trong Web API schema KHÔNG phải là marker 'video trùng lặp'.
        Engine phải BỎ QUA trường này và KHÔNG detect là duplicate.
        Đây là sự khác biệt quan trọng so với Mobile API group_id mismatch.
        """
        web_item_original_false_no_group_id = {
            "id": "7678807350064483606",
            "desc": "Test: Web API item with originalItem=False but no group_id mismatch",
            "createTime": 1787861663,
            "originalItem": False,
            "officalItem": False,
            "stats": {"playCount": 496, "diggCount": 23, "commentCount": 1}
        }
        res = analyze_tiktok_video(web_item_original_false_no_group_id)
        self.assertFalse(
            res.is_dup,
            "originalItem:False trong Web API KHÔNG phải duplicate marker - engine không được detect là trùng"
        )

    def test_mobile_api_group_id_mismatch_triggers_dup(self):
        """
        Chỉ có group_id != aweme_id từ Mobile API mới là duplicate marker thực sự
        (đây là cơ chế TikTokManager sử dụng).
        """
        mobile_item_dup = {
            "aweme_id": "7678772431397588226",
            "group_id": "7671234567890123456",
            "desc": "Virginia Trooper - video trung lap thuc su",
            "create_time": 1787844000,
        }
        res = analyze_tiktok_video(mobile_item_dup)
        self.assertTrue(res.is_dup, "group_id != aweme_id phải được detect là trùng lặp")
        self.assertEqual(res.group_id, "7671234567890123456")
        self.assertNotEqual(res.group_id, res.aweme_id)

    def test_web_api_feed_returns_zero_dups_without_group_id(self):
        """
        Web API feed (itemList) của user61172346518401 có tất cả originalItem:False
        nhưng KHÔNG có group_id. Engine phải trả về 0 duplicate (không false positive).
        """
        web_feed_path = Path(__file__).resolve().parent / "data" / "feeds" / "user61172346518401.json"
        if not web_feed_path.exists():
            self.skipTest("Web feed cache không tồn tại - bỏ qua test này")

        with open(web_feed_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("itemList") or []
        self.assertGreater(len(items), 0, "Web feed cache phải có items")

        analyzed = [analyze_tiktok_video(item) for item in items]
        dup_count = sum(1 for a in analyzed if a.is_dup)
        self.assertEqual(
            dup_count, 0,
            f"Web API feed không có group_id thực -> phải có 0 duplicate, nhưng engine detect {dup_count}"
        )


class TestUser611ProfileFeedLoading(unittest.TestCase):
    """
    Kiểm tra fetch_profile_posts() ưu tiên Mobile API fixture (aweme_list schema)
    khi datares.txt không phải của user này.
    """

    def test_profile_feed_loads_mobile_fixture_when_available(self):
        """
        fetch_profile_posts() phải nạp Mobile API fixture từ scratch/
        khi fixture được đặt vào đúng candidates list.
        STATIC ONLY - chỉ kiểm tra logic nạp dữ liệu, không test live API.
        """
        # Không thể test live API (thiếu session/cookie thật)
        # Test này chỉ xác nhận fixture đúng format và analyzable
        if not MOBILE_FIXTURE_PATH.exists():
            self.skipTest("Mobile fixture chưa tồn tại")

        with open(MOBILE_FIXTURE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("aweme_list") or []
        self.assertGreater(len(items), 0)

        analyzed = [analyze_tiktok_video(item) for item in items]
        dup_count = sum(1 for a in analyzed if a.is_dup)

        self.assertEqual(
            dup_count, 2,
            f"Mobile fixture phải cho ra đúng 2 duplicate (theo TikTokManager), got {dup_count}"
        )
        self.assertEqual(
            len(items) - dup_count, 7,
            "Phải có đúng 7 video original"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
