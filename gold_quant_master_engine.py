"""
================================================================================
HIZEIRIS AUTO BOT — รองรับทั้ง GitHub Actions (ทำบน iPad 100%) และ Windows PC
================================================================================
- เมื่อรันบน GitHub Actions: ระบบจะดึง API Key & Telegram Token จาก GitHub Secrets อัตโนมัติ,
  เปิดเว็บ Hizeiris เก็บข้อมูล 1 รอบ, คำนวณแผน A/B/C + Recovery, ส่งเข้า Telegram แล้วจบการทำงาน
- เมื่อรันบน Windows PC: วนลูปรันทุก 30 หรือ 60 นาทีต่อเนื่อง
================================================================================
"""

import json
import os
import time
from datetime import datetime
from typing import Dict, Optional

import requests

from gold_quant_master_engine import (
    HizeirisSnapshot,
    run_master_prompt_engine,
)

# ==============================================================================
# 1. ตั้งค่าระบบ (ดึงจาก GitHub Secrets อัตโนมัติ หรือแก้ไขตรงนี้ถ้ารันบน PC)
# ==============================================================================
IS_GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS", "").lower() == "true"

CONFIG = {
    "HIZEIRIS_URL": "https://script.google.com/macros/s/AKfycbyHn6gN2wZfvMPTYiYrcIOPbyZMpYtB4cPRUYPh0dqu0ZbS_dYLQNyUsc5jxXzItS1X/exec?v=view-lqwjrh81ZsvZ",
    "CHROME_PROFILE_DIR": "./hizeiris_chrome_profile",
    "FIRST_TIME_LOGIN": False,

    "INTERVAL_MINUTES": 30,
    "STATE_FILE": "session_state.json",

    # ดึงรหัสจาก GitHub Secrets (หากรันบน PC สามารถวางรหัสแทนคำว่า YOUR_... ได้เลย)
    "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY_HERE"),
    "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE"),
    "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE"),

    "FALLBACK_DIFF": float(os.getenv("FALLBACK_DIFF", "7.5")),
    # บน GitHub Actions จะทำงานจริงอัตโนมัติ (DRY_RUN = False); บน PC เปลี่ยนเป็น False เมื่อพร้อม
    "DRY_RUN": False if IS_GITHUB_ACTIONS else True,
}


# ==============================================================================
# 2. ตัวเก็บภาพและข้อความจากเว็บ Google Apps Script (รองรับ IFRAME ซ้อน)
# ==============================================================================
def capture_hizeiris_web(url: str, profile_dir: str, first_login: bool = False) -> Dict[str, str]:
    from playwright.sync_api import sync_playwright

    os.makedirs(profile_dir, exist_ok=True)
    shot_path = f"hizeiris_snap_{datetime.now().strftime('%Y%m%d_%H%M')}.png"

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=not first_login,
            viewport={"width": 1440, "height": 2200},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        if first_login:
            print("🔑 กรุณาล็อกอิน (ถ้ามี) แล้วกด Enter...")
            input()
        else:
            page.wait_for_timeout(8000)

        page.screenshot(path=shot_path, full_page=True)

        all_texts = []
        for idx, frame in enumerate(page.frames):
            try:
                txt = frame.inner_text("body", timeout=3000)
                if txt and txt.strip():
                    all_texts.append(f"--- FRAME {idx} ({frame.url[:60]}) ---\n{txt.strip()}")
            except Exception:
                pass

        context.close()

    return {"screenshot_path": shot_path, "raw_text": "\n\n".join(all_texts)}


# ==============================================================================
# 3. ตัวถอดตัวเลขดิบจากภาพและข้อความ iframe เป็น JSON (VISION-TO-JSON ONLY)
# ==============================================================================
EXTRACTION_PROMPT = """
คุณคือระบบ OCR ถอดตัวเลขจากภาพหน้าจอและข้อความของเว็บ Intraday OI Monitor by Hizeiris เท่านั้น
ห้ามวิเคราะห์ ห้ามคำนวณ และห้ามวางแผนเทรด ให้ตอบกลับเป็น JSON ที่มีโครงสร้างดังนี้เท่านั้น:
{
  "contract_name": "string",
  "futures_price": 0.0,
  "diff": 0.0,
  "dte": 0.0,
  "atm_iv": 0.0,
  "vol_chg": 0.0,
  "pc_ratio": 0.0,
  "churn": 0.0,
  "put_wall": 0.0,
  "put_wall_pc": "0P/0C",
  "call_wall": 0.0,
  "call_wall_pc": "0P/0C",
  "max_pain": 0.0,
  "top_pump_strike": 0.0,
  "top_pump_pc": "0P/0C",
  "top_inflow_strike": 0.0,
  "top_inflow_pc": "+0C",
  "cme_sd_table": {
    "1SD_pts": 0.0, "2SD_pts": 0.0, "3SD_pts": 0.0,
    "+3SD": 0.0, "+2SD": 0.0, "+1SD": 0.0,
    "-1SD": 0.0, "-2SD": 0.0, "-3SD": 0.0
  }
}
"""


def extract_json_from_capture(capture: Dict[str, str], api_key: str, fallback_diff: float) -> HizeirisSnapshot:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    with open(capture["screenshot_path"], "rb") as f:
        img_bytes = f.read()

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
            f"{EXTRACTION_PROMPT}\n\nข้อความที่ดึงได้จาก iframe ของหน้าเว็บ:\n{capture['raw_text'][:6000]}",
        ],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = json.loads(response.text)

    return HizeirisSnapshot(
        contract_name=data.get("contract_name", "CME Gold 0DTE"),
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        futures_price=float(data["futures_price"]),
        diff=float(data.get("diff") or fallback_diff),
        dte=float(data["dte"]),
        atm_iv=float(data["atm_iv"]),
        vol_chg=float(data.get("vol_chg", 0.0)),
        pc_ratio=float(data.get("pc_ratio", 1.0)),
        churn=float(data.get("churn", 0.0)),
        put_wall=float(data["put_wall"]),
        put_wall_pc=str(data.get("put_wall_pc", "N/A")),
        call_wall=float(data["call_wall"]),
        call_wall_pc=str(data.get("call_wall_pc", "N/A")),
        max_pain=float(data["max_pain"]),
        top_pump_strike=float(data.get("top_pump_strike", data["max_pain"])),
        top_pump_pc=str(data.get("top_pump_pc", "N/A")),
        top_inflow_strike=float(data.get("top_inflow_strike", data["call_wall"])),
        top_inflow_pc=str(data.get("top_inflow_pc", "N/A")),
        cme_sd_table=data.get("cme_sd_table"),
    )


# ==============================================================================
# 4. จัดรูปแบบและส่งรายงานเข้า TELEGRAM
# ==============================================================================
def format_telegram_message(snap: HizeirisSnapshot, result: Dict, prev_state: Optional[Dict]) -> str:
    st = result["state"]
    coord_df = result["coordinates"]
    plans_df = result["plans"]

    delta_line = "🆕 รอบเริ่มต้นของวัน"
    if prev_state:
        d_fut = round(st["futures_price"] - prev_state["futures_price"], 1)
        d_iv = round(st["atm_iv"] - prev_state["atm_iv"], 2)
        delta_line = f"🔄 Δ จากรอบก่อน ({prev_state['timestamp']}): Fut `{d_fut:+.1f}` | IV `{d_iv:+.2f}%`"

    coord_lines = []
    for _, r in coord_df.iterrows():
        coord_lines.append(f"• {r['แนวสำคัญ']}: `{r['XAUUSD (หัก Diff)']}` (Fut `{r['Futures']}`)")

    plan_lines = []
    for _, p in plans_df.iterrows():
        plan_lines.append(
            f"*{p['แผน']}*: `{p['จุดเข้า (XAUUSD)']}`\n"
            f"  TP: `{p['TP1 / TP2']}`\n"
            f"  SL: `{p['SL (1 ไม้ / รวบ 2 ไม้)']}`\n"
            f"  Rec: `{p['Recovery (Supa888)']}`\n"
            f"  Edge: _{p['% ความได้เปรียบจากวิจัย']}_"
        )

    msg = (
        f"📊 *HIZEIRIS QUANT BOT* ({st['timestamp']})\n"
        f"{delta_line}\n"
        f"• *Futures*: `{st['futures_price']}` | *Diff*: `{st['diff']}` | *XAUUSD*: `{st['spot_price']}`\n"
        f"• *DTE*: `{snap.dte}` | *ATM IV*: `{st['atm_iv']}%` (`{snap.vol_chg:+.2f}`)\n\n"
        f"🧱 *พิกัด XAUUSD (หัก Diff {st['diff']})*\n" + "\n".join(coord_lines) + "\n\n"
        f"🎯 *สรุปแผน A / B / C & Recovery*\n" + "\n\n".join(plan_lines)
    )
    if snap.dte < 0.04:
        msg += "\n\n⛔ *คำเตือน 0DTE*: เหลือเวลา < 30-60 นาที ห้ามเปิดไม้สวนเทรนด์ใหม่!"
    return msg


def send_telegram(msg: str, token: str, chat_id: str) -> None:
    if token == "YOUR_BOT_TOKEN_HERE" or chat_id == "YOUR_CHAT_ID_HERE":
        print("\n📱 [TELEGRAM PREVIEW — ใส่ Token & Chat ID เพื่อส่งเข้ามือถือจริง]:\n")
        print(msg)
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}, timeout=15)
    if resp.status_code == 200:
        print(f"✅ ส่งรายงานเข้า Telegram สำเร็จ ({datetime.now().strftime('%H:%M:%S')})")
    else:
        print(f"⚠️ Telegram Error: {resp.text}")


# ==============================================================================
# 5. ลูปทำงานอัตโนมัติ (รองรับ GitHub Actions รอบเดียว และ PC วนลูป)
# ==============================================================================
def load_state(path: str) -> Optional[Dict]:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(path: str, state: Dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def run_once():
    prev_state = load_state(CONFIG["STATE_FILE"])

    if CONFIG["DRY_RUN"]:
        print("🧪 [DRY RUN MODE] ทดสอบระบบด้วยข้อมูลจำลอง...")
        snap = HizeirisSnapshot(
            contract_name="OG2X6 (0DTE)",
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            futures_price=4041.1,
            diff=CONFIG["FALLBACK_DIFF"],
            dte=0.45,
            atm_iv=24.50,
            vol_chg=-0.85,
            pc_ratio=1.18,
            churn=0.42,
            put_wall=4000.0, put_wall_pc="320P/75C",
            call_wall=4050.0, call_wall_pc="60P/290C",
            max_pain=4040.0,
            top_pump_strike=4040.0, top_pump_pc="55P/149C",
            top_inflow_strike=4050.0, top_inflow_pc="+45C",
        )
    else:
        cap = capture_hizeiris_web(CONFIG["HIZEIRIS_URL"], CONFIG["CHROME_PROFILE_DIR"], CONFIG["FIRST_TIME_LOGIN"])
        snap = extract_json_from_capture(cap, CONFIG["GEMINI_API_KEY"], CONFIG["FALLBACK_DIFF"])

    result = run_master_prompt_engine(snap, prev_state=prev_state)
    tg_msg = format_telegram_message(snap, result, prev_state)
    send_telegram(tg_msg, CONFIG["TELEGRAM_BOT_TOKEN"], CONFIG["TELEGRAM_CHAT_ID"])
    save_state(CONFIG["STATE_FILE"], result["state"])


def start_scheduler():
    print(f"🚀 เริ่มบอทเก็บข้อมูล Hizeiris (โหมด: {'GitHub Actions Cloud' if IS_GITHUB_ACTIONS else 'Local'})...")
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"❌ เกิดข้อผิดพลาดในรอบนี้: {e}")
            if IS_GITHUB_ACTIONS:
                raise

        if IS_GITHUB_ACTIONS or CONFIG["DRY_RUN"]:
            break
        time.sleep(CONFIG["INTERVAL_MINUTES"] * 60)


if __name__ == "__main__":
    start_scheduler()
