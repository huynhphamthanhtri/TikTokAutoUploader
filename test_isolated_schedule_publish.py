import datetime
import json
import logging
import math
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from tiktok_cookie_extractor import get_profile_tiktok_cookies
from tiktok_direct_uploader import run_direct_upload
from tiktok_publish_client import check_schedule_capability

def main():
    print("=== TEST CÔ LẬP LÊN LỊCH ĐĂNG QUA HTTP REST API (Profile: RT 1) ===")
    
    # 1. Đọc configs.json
    with open("configs.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    
    rt1_cfg = None
    for k, v in data.get("profiles", {}).items():
        if "RT 1" in k:
            rt1_cfg = v
            break
            
    if not rt1_cfg:
        print("LỖI: Không tìm thấy profile RT 1")
        sys.exit(1)
        
    cookies = get_profile_tiktok_cookies(rt1_cfg)
    proxy_cfg = {"use_proxy": True, "proxy_string": rt1_cfg.get("proxy_string"), "proxy_type": "http"}
    
    # 2. Check capability
    cap = check_schedule_capability(cookies, proxy_cfg=proxy_cfg)
    print(f"Server Time: {cap.server_time} ({datetime.datetime.fromtimestamp(cap.server_time, tz=datetime.timezone.utc)} UTC)")
    
    # 3. Tính toán target schedule epoch:
    # TikTok Studio yêu cầu:
    # - Tối thiểu 15 phút, tối đa 10 ngày
    # - BẮT BUỘC là bội số của 300 giây (5 phút: :00, :05, :10, :15, v.v.)
    server_now = cap.server_time
    # Chọn mốc cách hiện tại 45 phút, làm tròn đến mốc 5 phút tiếp theo
    raw_target = server_now + 2700  # +45 phút
    target_epoch = ((raw_target + 299) // 300) * 300
    
    tz_mexico = ZoneInfo("America/Mexico_City")
    dt_target = datetime.datetime.fromtimestamp(target_epoch, tz=tz_mexico)
    print(f"Target Schedule Epoch: {target_epoch}")
    print(f"Target Local Time (America/Mexico_City): {dt_target.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Cách server hiện tại: {(target_epoch - server_now) / 60:.1f} phút")
    print(f"Kiểm tra epoch % 300: {target_epoch % 300} (Phải bằng 0)")
    assert target_epoch % 300 == 0, "Epoch chưa chia hết cho 300!"

    # 4. Video test
    video_path = Path("tiny_test.mp4")
    if not video_path.exists():
        print("LỖI: Không tìm thấy tiny_test.mp4")
        sys.exit(1)
        
    print(f"\nBắt đầu tải lên & hẹn giờ video {video_path.name}...")
    
    res = run_direct_upload(
        profile_name="RT 1",
        profile_config=rt1_cfg,
        video_path=video_path,
        caption="Test automated schedule API #automation #test",
        status_callback=lambda msg: print(f"  [API] {msg}"),
        schedule_time=target_epoch,
        stop_before_post=False,
    )
    
    print("\n=== KẾT QUẢ RUN DIRECT UPLOAD ===")
    print(f"Outcome: {res.outcome}")
    print(f"Message: {res.message}")
    print(f"Details: {json.dumps(res.details, indent=2, ensure_ascii=False)}")

if __name__ == "__main__":
    main()
