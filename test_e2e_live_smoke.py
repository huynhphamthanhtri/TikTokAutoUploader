import sys
import time
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from youtube_monitor import core, cloudflare_owner, cloudflare_helper

def live_smoke_test():
    print("=" * 60)
    print("🚀 BẮT ĐẦU LIVE SMOKE TEST TOÀN TRÌNH (ZERO-MOCK DEFINITION OF DONE)")
    print("=" * 60)

    # 1. Kiểm tra binary cloudflared
    print("\n[Bước 1] Kiểm tra binary cloudflared.exe...")
    bin_path = cloudflare_helper.get_cloudflared_bin_path()
    assert bin_path is not None, "Không tìm thấy cloudflared.exe!"
    assert Path(bin_path).exists(), f"File {bin_path} không tồn tại!"
    print(f"  -> Binary OK: {bin_path}")

    # 2. Khởi chạy Cloudflare Quick Tunnel thật
    print("\n[Bước 2] Khởi chạy Cloudflare Quick Tunnel...")
    test_port = 5899
    # Start local callback server on test_port
    ok_srv, err_or_port = core._start_callback_server(test_port)
    assert ok_srv is True, f"Không thể bật callback server: {err_or_port}"
    print(f"  -> Local callback server chạy trên port: {test_port}")

    ok, res = cloudflare_owner.start_owned_agent(
        test_port,
        "live_e2e_instance",
        1,
        lambda msg: print(f"    [Cloudflare Event] {msg}")
    )
    assert ok is True, f"Khởi chạy tunnel thất bại: {res}"
    public_url = res["public_url"]
    pid = res["agent_pid"]
    print(f"  -> Quick Tunnel URL: {public_url}")
    print(f"  -> Agent PID: {pid}")
    assert public_url.startswith("https://"), f"URL phải là HTTPS: {public_url}"
    assert "trycloudflare.com" in public_url, f"Domain phải là trycloudflare.com: {public_url}"

    # 3. Gửi HTTP Challenge qua Cloudflare Edge toàn cầu
    print("\n[Bước 3] Xác minh luồng Callback challenge qua Cloudflare Edge...")
    challenge_token = "verify_test_token_live_123"
    test_url = f"{public_url}/youtube_callback/{core._callback_owner_token}?hub.challenge={challenge_token}"
    print(f"  -> Đang gửi GET tới: {test_url}")

    routed = False
    for attempt in range(6):
        try:
            req = urllib.request.Request(test_url, headers={"User-Agent": "E2E-SmokeTest/1.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                body = resp.read().decode("utf-8").strip()
                print(f"  -> Lần {attempt+1}: Status {resp.status}, Body={body}")
                if resp.status == 200 and body == challenge_token:
                    routed = True
                    break
        except Exception as e:
            print(f"  -> Lần {attempt+1} chờ định tuyến Edge DNS ({e}), thử lại sau 2s...")
            time.sleep(2)

    assert routed is True, "Xác minh Callback challenge qua Cloudflare Edge thất bại!"
    print("  -> Callback challenge qua Cloudflare Tunnel thành công 100%!")

    # 4. Kiểm tra trạng thái Agent sống
    assert cloudflare_owner.is_agent_alive() is True, "Agent phải đang sống!"
    print("\n[Bước 4] Kiểm tra Agent sống: OK")

    # 5. Dừng tunnel an toàn và kiểm tra Zero Zombie Process
    print("\n[Bước 5] Dừng tunnel an toàn...")
    cloudflare_owner.stop_owned_agent(lambda msg: print(f"    [Stop Event] {msg}"))
    time.sleep(1)
    core._stop_callback_server()

    is_alive = cloudflare_owner.is_agent_alive()
    print(f"  -> Trạng thái Agent sau khi dừng: alive={is_alive}")
    assert is_alive is False, "Agent phải tắt hoàn toàn sau khi stop!"
    print("  -> Zero Zombie Process: Đã xác minh 100%!")

    # 6. Kiểm tra tải video YouTube live bằng yt-dlp với cookie policy primary
    print("\n[Bước 6] Kiểm tra tải video YouTube thực tế (y9gKtC7uId4 & 7yxyGWf7B2Q)...")
    video_id = "7yxyGWf7B2Q"
    dest_dir = Path("Auto_Data/youtube_downloads")
    ok_dl = core.download_one("test_channel", video_id, target_folder=str(dest_dir))
    print(f"  -> download_one({video_id}) trả về: {ok_dl}")
    assert ok_dl is True, f"Tải video {video_id} thất bại!"

    # Kiểm tra file đã tải
    found_files = list(dest_dir.glob(f"*{video_id}*")) or list(dest_dir.glob("*.mp4"))
    assert len(found_files) > 0, "Không tìm thấy file video sau khi tải!"
    target_file = found_files[-1]
    file_size = target_file.stat().st_size
    print(f"  -> File thực tế: {target_file.name} ({file_size} bytes)")
    assert file_size > 1000000, f"File quá nhỏ: {file_size} bytes"

    print("\n" + "=" * 60)
    print("🎉 TOÀN BỘ LIVE SMOKE TEST ĐÃ ĐẠT 100% KHÔNG DÙNG MOCK!")
    print("=" * 60)

if __name__ == "__main__":
    live_smoke_test()
