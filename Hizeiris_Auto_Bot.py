"""
================================================================================
HIZEIRIS AUTO BOT (ALL-IN-ONE SELF-CONTAINED EDITION)
================================================================================
รวมเครื่องยนต์คำนวณเชิงปริมาณจาก Master Prompt + ตัวเก็บข้อมูลเว็บ Hizeiris (iframe)
+ ตัวถอดตัวเลข JSON + ตัวส่งรายงานเข้า Telegram ครบจบในไฟล์เดียว!
================================================================================
"""

import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

import pandas as pd
import requests

# ==============================================================================
# 1. ตั้งค่าระบบ (ดึงจาก GitHub Secrets อัตโนมัติ)
# ==============================================================================
IS_GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS", "").lower() == "true"

CONFIG = {
    "HIZEIRIS_URL": "https://script.google.com/macros/s/AKfycbyHn6gN2wZfvMPTYiYrcIOPbyZMpYtB4cPRUYPh0dqu0ZbS_dYLQNyUsc5jxXzItS1X/exec?v=view-lqwjrh81ZsvZ",
    "CHROME_PROFILE_DIR": "./hizeiris_chrome_profile",
    "FIRST_TIME_LOGIN": False,
    "INTERVAL_MINUTES": 30,
    "STATE_FILE": "session_state.json",
    "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY_HERE"),
    "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE"),
    "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE"),
    "FALLBACK_DIFF": float(os.getenv("FALLBACK_DIFF", "7.5")),
    "DRY_RUN": False if IS_GITHUB_ACTIONS else True,
}


# ==============================================================================
# 2. โครงสร้างข้อมูล & ค่าคงที่จากงานวิจัย (MASTER PROMPT ENGINE)
# ==============================================================================
@dataclass
class HizeirisSnapshot:
    contract_name: str
    timestamp: str
    futures_price: float
    diff: float
    dte: float
    atm_iv: float
    vol_chg: float
    pc_ratio: float
    churn: float
    put_wall: float
    put_wall_pc: str
    call_wall: float
    call_wall_pc: str
    max_pain: float
    top_pump_strike: float
    top_pump_pc: str
    top_inflow_strike: float
    top_inflow_pc: str
    cme_sd_table: Optional[Dict[str, float]] = None
    candle_high: float = 0.0
    candle_low: float = 0.0
    candle_close: float = 0.0
    prev_friday_close: Optional[float] = None
    monday_open: Optional[float] = None
    vol_25c: Optional[float] = None
    vol_25p: Optional[float] = None
    next_series_atm_iv: Optional[float] = None
    vol_regime: str = "NORMAL"


VOL_REGIME_RATES = {
    "HIGH": ("ความผันผวนสูง (High Vol)", "88.50% (+11.67% จากค่าฐาน)"),
    "NORMAL": ("ความผันผวนปกติ (Normal Vol)", "81.20% (+4.37% จากค่าฐาน)"),
    "BASELINE": ("ค่าเฉลี่ยตลาดจริง 2 ปี", "76.83% (หัก Friction -5.67%)"),
    "LOW": ("ความผันผวนต่ำ (Low Vol)", "64.50% (-12.33% เน้นเก็บสั้น)"),
}


def check_round_number_bonus(spot_price: float) -> str:
    rem = round(spot_price % 100, 1)
    for target in [0.0, 20.0, 50.0, 80.0, 100.0]:
        if abs(rem - target) <= 1.5:
            return " [เลขกลม +5% ถึง +8%]"
    return ""


def evaluate_oi_zone_7(distance: float, moving_away: bool = False) -> Dict[str, str]:
    if moving_away:
        return {"zone": "โซน 4: Anti-Gravity", "light": "🟡 ไฟเหลือง", "touch_pct": "52.00%"}
    d = abs(distance)
    if d < 5:
        return {"zone": "โซน 0: <$5 Repeller Wall", "light": "🔴 ไฟแดง", "touch_pct": "แตะแล้ว (ทะลุ <27.5%)"}
    elif d <= 15:
        return {"zone": "โซน 1A: $5–$15 Core Magnet", "light": "🟢 เขียวเข้ม", "touch_pct": "88.0%–95.0% ($10 = 95%)"}
    elif d <= 25:
        return {"zone": "โซน 1B: $15–$25 Event Horizon", "light": "🟢 ไฟเขียว", "touch_pct": "≈ 88.0%"}
    elif d <= 50:
        return {"zone": "โซน 2: $30–$50 Vacuum Zone", "light": "🟢/🟡 เขียวอ่อน", "touch_pct": "55.0%–63.0%"}
    elif d <= 75:
        return {"zone": "โซน 3A: $50–$75 Far Trap", "light": "🟡 ไฟเหลือง", "touch_pct": "14.0%–45.0% (Fixed TP +$25 = 72%)"}
    return {"zone": "โซน 3B: >$75 Extreme", "light": "🟡/🔴 ระวังสูง", "touch_pct": "< 5.0%–14.0%"}


def run_master_prompt_engine(snap: HizeirisSnapshot, prev_state: Optional[Dict] = None) -> Dict:
    fut = snap.futures_price
    diff = snap.diff
    spot = round(fut - diff, 1)
    eod_lock = snap.dte < 0.04

    if snap.cme_sd_table and snap.cme_sd_table.get("+1SD", 0) > 0:
        sd = snap.cme_sd_table
    else:
        one_sd = round(fut * (snap.atm_iv / 100.0) * math.sqrt(max(snap.dte, 0.01) / 365.0), 1)
        sd = {
            "1SD_pts": one_sd, "2SD_pts": round(one_sd * 2, 1), "3SD_pts": round(one_sd * 3, 1),
            "+3SD": round(fut + one_sd * 3, 1), "+2SD": round(fut + one_sd * 2, 1), "+1SD": round(fut + one_sd, 1),
            "-1SD": round(fut - one_sd, 1), "-2SD": round(fut - one_sd * 2, 1), "-3SD": round(fut - one_sd * 3, 1),
        }

    call_spot = round(snap.call_wall - diff, 1)
    put_spot = round(snap.put_wall - diff, 1)
    pain_spot = round(snap.max_pain - diff, 1)
    dist_call = round(call_spot - spot, 1)
    dist_put = round(spot - put_spot, 1)

    coord_specs = [
        ("ขอบบน +3SD", f"+{sd['3SD_pts']} จุด", sd["+3SD"]),
        ("ขอบบน +2SD", f"+{sd['2SD_pts']} จุด", sd["+2SD"]),
        ("Call Wall", snap.call_wall_pc, snap.call_wall),
        ("ขอบบน +1SD", f"+{sd['1SD_pts']} จุด", sd["+1SD"]),
        ("ราคากลาง", f"ปั๊ม {int(snap.top_pump_strike)}", fut),
        ("Max Pain", f"Max Pain {int(snap.max_pain)}", snap.max_pain),
        ("ขอบล่าง -1SD", f"-{sd['1SD_pts']} จุด", sd["-1SD"]),
        ("Put Wall", snap.put_wall_pc, snap.put_wall),
        ("ขอบล่าง -2SD", f"-{sd['2SD_pts']} จุด", sd["-2SD"]),
        ("ขอบล่าง -3SD", f"-{sd['3SD_pts']} จุด", sd["-3SD"]),
    ]

    coord_rows = []
    for label, info, fut_lv in coord_specs:
        spot_lv = round(fut_lv - diff, 1)
        coord_rows.append({
            "แนวสำคัญ": label,
            "ข้อมูล": info,
            "Futures": fut_lv,
            "XAUUSD (หัก Diff)": spot_lv,
            "โบนัส": check_round_number_bonus(spot_lv),
        })
    coord_df = pd.DataFrame(coord_rows).sort_values("Futures", ascending=False).reset_index(drop=True)

    p1sd_spot, m1sd_spot = round(sd["+1SD"] - diff, 1), round(sd["-1SD"] - diff, 1)
    p2sd_spot, m2sd_spot = round(sd["+2SD"] - diff, 1), round(sd["-2SD"] - diff, 1)
    p3sd_spot, m3sd_spot = round(sd["+3SD"] - diff, 1), round(sd["-3SD"] - diff, 1)

    oi_call_zone = evaluate_oi_zone_7(dist_call)
    oi_put_zone = evaluate_oi_zone_7(dist_put)

    if not eod_lock and (0 < dist_call <= 25 or 0 < dist_put <= 25):
        if 0 < dist_call <= 25 and (dist_call <= dist_put or dist_put <= 0):
            a_dir, a_entry = "BUY", spot
            a_tp1 = round(a_entry + min(12.5, dist_call * 0.6), 1)
            a_tp2 = call_spot
            a_sl1, a_sl2 = round(a_entry - 12.5, 1), round(a_entry - 25.0, 1)
            a_rec1_tp, a_rec2_tp = round(a_sl1 - 12.5, 1), round(a_sl2 - 12.5, 1)
            a_edge = f"{oi_call_zone['zone']} -> แตะเป้า {oi_call_zone['touch_pct']} | กฎ $25: {VOL_REGIME_RATES[snap.vol_regime][1]}"
        else:
            a_dir, a_entry = "SELL", spot
            a_tp1 = round(a_entry - min(12.5, dist_put * 0.6), 1)
            a_tp2 = put_spot
            a_sl1, a_sl2 = round(a_entry + 12.5, 1), round(a_entry + 25.0, 1)
            a_rec1_tp, a_rec2_tp = round(a_sl1 + 12.5, 1), round(a_sl2 + 12.5, 1)
            a_edge = f"{oi_put_zone['zone']} -> แตะเป้า {oi_put_zone['touch_pct']} | กฎ $25: {VOL_REGIME_RATES[snap.vol_regime][1]}"
    else:
        if abs(p2sd_spot - spot) <= abs(spot - m2sd_spot):
            a_dir, a_entry = "SELL", p2sd_spot
            a_tp1, a_tp2 = round(a_entry - 15.0, 1), round(a_entry - 25.0, 1)
            a_sl1, a_sl2 = round(a_entry + 12.5, 1), round(a_entry + 25.0, 1)
            a_rec1_tp, a_rec2_tp = round(a_sl1 + 12.5, 1), round(a_sl2 + 12.5, 1)
        else:
            a_dir, a_entry = "BUY", m2sd_spot
            a_tp1, a_tp2 = round(a_entry + 15.0, 1), round(a_entry + 25.0, 1)
            a_sl1, a_sl2 = round(a_entry - 12.5, 1), round(a_entry - 25.0, 1)
            a_rec1_tp, a_rec2_tp = round(a_sl1 - 12.5, 1), round(a_sl2 - 12.5, 1)
        a_edge = "VOL2VOL 500d ±2SD >=$25 = 78.57% (ทับ OI + IV Crush = 86.0%–91.4%)"

    plans_df = pd.DataFrame([
        {
            "แผน": "แผน A (หลัก)",
            "จุดเข้า (XAUUSD)": f"{a_dir} @ {a_entry}",
            "% ความได้เปรียบจากวิจัย": a_edge,
            "TP1 / TP2": f"{a_tp1} / {a_tp2}",
            "SL (1 ไม้ / รวบ 2 ไม้)": f"SL1 (-12.5): {a_sl1} | SL2 (-25.0): {a_sl2}",
            "Recovery (Supa888)": f"SL1 สลับทบ 2x -> TP {a_rec1_tp} | SL2 รวบทบ 6x -> TP {a_rec2_tp}",
        },
        {
            "แผน": "แผน B (รอง)",
            "จุดเข้า (XAUUSD)": f"SELL ±1SD @ {p1sd_spot} / BUY @ {m1sd_spot} (หรือ ±3SD: {p3sd_spot} / {m3sd_spot})",
            "% ความได้เปรียบจากวิจัย": "±1SD สั้น $8–$12 = 70%–78.53% (เป้า >=$25 แค่ 55.71% / ไหลต่อ 40%) | ±3SD = 91.43%",
            "TP1 / TP2": f"±1SD เก็บสั้น: {round(p1sd_spot - 10, 1)} / {round(m1sd_spot + 10, 1)} | ±3SD เก็บ >=$25",
            "SL (1 ไม้ / รวบ 2 ไม้)": f"SL1 (-12.5): {round(p1sd_spot + 12.5, 1)} / {round(m1sd_spot - 12.5, 1)}",
            "Recovery (Supa888)": f"หลุด ±1SD ครบ -12.5 สลับทบ 2x ตามโอกาส 40% ไป ±2SD ({p2sd_spot} / {m2sd_spot})",
        },
        {
            "แผน": "แผน C (สำรอง)",
            "จุดเข้า (XAUUSD)": f"Breakout ทะลุ Call Wall ({call_spot}) หรือ Put Wall ({put_spot}) เกิน 12.5 เหรียญ",
            "% ความได้เปรียบจากวิจัย": "Negative Gamma ขยาย Vol 30m +6.4% | กฎ $25 High Vol = 88.50%",
            "TP1 / TP2": f"ขึ้น TP: {round(call_spot + 25, 1)} | ลง TP: {round(put_spot - 25, 1)}",
            "SL (1 ไม้ / รวบ 2 ไม้)": "คัทเมื่อดึงกลับเข้ากำแพงเดิมเกิน 12.5 เหรียญ",
            "Recovery (Supa888)": "ทบ 2x (แก้ไม้เดียว -12.5 -> +12.5) หรือ ทบ 6x (แก้รวบ -25.0 -> +12.5)",
        },
    ])

    new_state = {
        "timestamp": snap.timestamp,
        "futures_price": fut,
        "spot_price": spot,
        "diff": diff,
        "atm_iv": snap.atm_iv,
        "put_wall": snap.put_wall,
        "call_wall": snap.call_wall,
        "max_pain": snap.max_pain,
        "plan_a_dir": a_dir,
        "plan_a_entry": a_entry,
        "plan_a_tp1": a_tp1,
    }
    return {"state": new_state, "coordinates": coord_df, "plans": plans_df}


# ==============================================================================
# 3. ตัวเก็บภาพ/ข้อความจากเว็บ Hizeiris + ถอด JSON + ส่ง Telegram
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
        page.wait_for_timeout(8000)
        page.screenshot(path=shot_path, full_page=True)

        all_texts = []
        for idx, frame in enumerate(page.frames):
            try:
                txt = frame.inner_text("body", timeout=3000)
                if txt and txt.strip():
                    all_texts.append(f"--- FRAME {idx} ---\n{txt.strip()}")
            except Exception:
                pass
        context.close()

    return {"screenshot_path": shot_path, "raw_text": "\n\n".join(all_texts)}


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
        model="gemini-3.8-flash",
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
            f"{EXTRACTION_PROMPT}\n\nข้อความจาก iframe:\n{capture['raw_text'][:6000]}",
        ],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = json.loads(response.text)

    return HizeirisSnapshot(
        contract_name=data.get("contract_name") or "CME Gold 0DTE",
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        futures_price=float(data.get("futures_price") or 0.0),
        diff=float(data.get("diff") or fallback_diff),
        dte=float(data.get("dte") or 0.5),
        atm_iv=float(data.get("atm_iv") or 20.0),
        vol_chg=float(data.get("vol_chg") or 0.0),
        pc_ratio=float(data.get("pc_ratio") or 1.0),
        churn=float(data.get("churn") or 0.0),
        put_wall=float(data.get("put_wall") or 0.0),
        put_wall_pc=str(data.get("put_wall_pc") or "N/A"),
        call_wall=float(data.get("call_wall") or 0.0),
        call_wall_pc=str(data.get("call_wall_pc") or "N/A"),
        max_pain=float(data.get("max_pain") or 0.0),
        top_pump_strike=float(data.get("top_pump_strike") or data.get("max_pain") or 0.0),
        top_pump_pc=str(data.get("top_pump_pc") or "N/A"),
        top_inflow_strike=float(data.get("top_inflow_strike") or data.get("call_wall") or 0.0),
        top_inflow_pc=str(data.get("top_inflow_pc") or "N/A"),
        cme_sd_table=data.get("cme_sd_table"),
    )


def format_telegram_message(snap: HizeirisSnapshot, result: Dict, prev_state: Optional[Dict]) -> str:
    st = result["state"]
    coord_df = result["coordinates"]
    plans_df = result["plans"]

    delta_line = "🆕 รอบเริ่มต้นของวัน"
    if prev_state:
        d_fut = round(st["futures_price"] - prev_state["futures_price"], 1)
        d_iv = round(st["atm_iv"] - prev_state["atm_iv"], 2)
        delta_line = f"🔄 Δ จากรอบก่อน ({prev_state['timestamp']}): Fut `{d_fut:+.1f}` | IV `{d_iv:+.2f}%`"

    coord_lines = [f"• {r['แนวสำคัญ']}: `{r['XAUUSD (หัก Diff)']}` (Fut `{r['Futures']}`){r['โบนัส']}" for _, r in coord_df.iterrows()]
    plan_lines = [
        f"*{p['แผน']}*: `{p['จุดเข้า (XAUUSD)']}`\n"
        f"  TP: `{p['TP1 / TP2']}`\n"
        f"  SL: `{p['SL (1 ไม้ / รวบ 2 ไม้)']}`\n"
        f"  Rec: `{p['Recovery (Supa888)']}`\n"
        f"  Edge: _{p['% ความได้เปรียบจากวิจัย']}_"
        for _, p in plans_df.iterrows()
    ]

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
        print("\n📱 [TELEGRAM PREVIEW]:\n" + msg)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}, timeout=15)
    print(f"✅ Telegram status {resp.status_code}: {resp.text[:120]}")


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
        print("🧪 [DRY RUN MODE] ทดสอบระบบ...")
        snap = HizeirisSnapshot(
            contract_name="OG2X6 (0DTE)", timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            futures_price=4041.1, diff=CONFIG["FALLBACK_DIFF"], dte=0.45, atm_iv=24.50,
            vol_chg=-0.85, pc_ratio=1.18, churn=0.42,
            put_wall=4000.0, put_wall_pc="320P/75C", call_wall=4050.0, call_wall_pc="60P/290C",
            max_pain=4040.0, top_pump_strike=4040.0, top_pump_pc="55P/149C",
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
    print(f"🚀 เริ่มบอท Hizeiris (โหมด: {'GitHub Actions' if IS_GITHUB_ACTIONS else 'Local'})...")
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"❌ Error: {e}")
            if IS_GITHUB_ACTIONS:
                raise
        if IS_GITHUB_ACTIONS or CONFIG["DRY_RUN"]:
            break
        time.sleep(CONFIG["INTERVAL_MINUTES"] * 60)


if __name__ == "__main__":
    start_scheduler()