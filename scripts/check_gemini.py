"""Kiểm tra danh sách, tốc độ phản hồi và quota của toàn bộ Gemini API Keys & Models:
    python scripts/check_gemini.py
"""

import re
import sys
import time
from pathlib import Path

# Thêm thư mục gốc dự án vào sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google import genai
from google.genai import types

from app.ai.key_rotator import mask_key, parse_keys
from app.config import get_settings


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    settings = get_settings()
    keys = parse_keys(settings.gemini_api_keys or settings.gemini_api_key)
    models = [m.strip() for m in (settings.gemini_extract_models or settings.gemini_models).split(",") if m.strip()]
    
    print("=" * 75, flush=True)
    print("     BÁO CÁO TÌNH TRẠNG, TỐC ĐỘ & QUOTA GEMINI API KEYS THEO TỪNG MODEL", flush=True)
    print("=" * 75, flush=True)
    print(f"Tổng số API Key: {len(keys)} | Các model ưu tiên: {', '.join(models)}\n", flush=True)
    
    opts = types.HttpOptions(timeout=15_000, retry_options=types.HttpRetryOptions(attempts=1))
    
    total_ready_pairs = 0
    total_pairs = len(keys) * len(models)
    
    for idx, key in enumerate(keys, 1):
        masked = mask_key(key)
        print(f"🔑 Key #{idx} [{masked}]:", flush=True)
        client = genai.Client(api_key=key, http_options=opts)
        
        for model in models:
            t0 = time.time()
            try:
                client.models.generate_content(model=model, contents="OK")
                dt = time.time() - t0
                print(f"   ✅ {model:<24} : SẴN SÀNG ({dt:.2f}s)", flush=True)
                total_ready_pairs += 1
            except Exception as e:
                dt = time.time() - t0
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    is_daily = "perday" in err.replace(" ", "").lower()
                    retry_match = re.search(r"Please retry in ([^.]+)", err)
                    retry_time = retry_match.group(1).strip() if retry_match else ""
                    kind = f"Hết quota ngày ({retry_time})" if is_daily else "Chạm RPM phút (chờ 15s)"
                    print(f"   ⏳ {model:<24} : 429 - {kind}", flush=True)
                elif "503" in err or "UNAVAILABLE" in err:
                    print(f"   ⚠️ {model:<24} : 503 - Server Google đang bận ({dt:.2f}s)", flush=True)
                else:
                    print(f"   ❌ {model:<24} : LỖI ({err[:60]}...)", flush=True)
        print(flush=True)

    print("-" * 75)
    print(f"TỔNG KẾT: {total_ready_pairs}/{total_pairs} cặp (Key × Model) đang sẵn sàng chạy ngay lập tức!")
    print("-" * 75)
    print("💡 TRANG QUẢN TRỊ GOOGLE CHÍNH THỨC:")
    print("   👉 Xem Rate Limit Realtime : https://aistudio.google.com/rate-limit")
    print("   👉 Quản lý API Keys        : https://aistudio.google.com/app/apikey")
    print("=" * 75)

if __name__ == "__main__":
    main()
