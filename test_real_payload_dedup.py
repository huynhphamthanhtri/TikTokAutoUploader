import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, r"e:\BK_TOOL_VIBE_AUTO_UPLOAD\VIBE_AUTO_UPLOAD-LP")
sys.stdout.reconfigure(encoding='utf-8')

from tiktok_dedup_engine import analyze_tiktok_video, VideoItemAnalysis

class TestRealPayloadDedup(unittest.TestCase):
    """Kiểm tra nhận diện trên dữ liệu thực tế trích xuất từ TikTokManager."""

    def test_detect_real_duplicates_from_ttm_payload(self):
        payload_path = r"C:\Users\huynh\AppData\Local\Programs\tiktokmanager\_decompiled\datares.txt"
        with open(payload_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        aweme_list = data.get("aweme_list", [])
        self.assertEqual(len(aweme_list), 9, "Phải có đúng 9 video mẫu từ live payload")

        analyzed = [analyze_tiktok_video(item) for item in aweme_list]

        # Video 2 và Video 4 trong payload thực tế có group_id khác aweme_id:
        # Video 2: aweme_id=7681997517537594646, group_id=7681997048069115149
        # Video 4: aweme_id=7679460450093845782, group_id=7679277164268768534
        dup_items = [v for v in analyzed if v.is_dup]

        print("\n--- KẾT QUẢ PHÂN TÍCH THỰC TẾ ---")
        for v in dup_items:
            print(f"TRÙNG: aweme_id={v.aweme_id} -> gốc={v.group_id} ({v.original_url})")

        self.assertEqual(len(dup_items), 2, "Phải phát hiện chính xác 2 video trùng lặp!")
        self.assertEqual(dup_items[0].aweme_id, "7681997517537594646")
        self.assertEqual(dup_items[0].group_id, "7681997048069115149")
        self.assertEqual(dup_items[1].aweme_id, "7679460450093845782")
        self.assertEqual(dup_items[1].group_id, "7679277164268768534")

if __name__ == "__main__":
    unittest.main()
