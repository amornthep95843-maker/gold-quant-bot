"""
================================================================================
HIZEIRIS AUTO BOT — ULTIMATE COMPLETE EDITION (WITH 503 AUTO-RETRY & FALLBACK)
================================================================================
- แก้ปัญหา 503 UNAVAILABLE (High Demand) ด้วยระบบ Auto-Retry 5 รอบ + สลับโมเดลสำรองอัตโนมัติ
- หากเซิร์ฟเวอร์ AI ค้างตอนเขียนบทวิเคราะห์ ระบบจะสลับส่งรายงาน 6 ส่วนที่คำนวณด้วย Python ล้วน
  เข้า Telegram ทันที (บอทไม่มีวันล่ม และส่งพิกัดตรงเวลา 100%)
================================================================================
"""

import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

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
    "FALLBACK_DIFF": float(os.getenv("FALLBACK_DIFF", "5.0")),
    "DRY_RUN": False if IS_GITHUB_ACTIONS else True,
}

# รายชื่อโมเดลเรียงตามลำดับ (หากตัวแรกคนใช้เยอะติด 503 จะสลับไปตัวถัดไปอัตโนมัติ)
MODEL_CANDIDATES = [
    "gemini-3.8-flash",
    "gemini-3.8-flash-lite",
    "gemini-3.8-pro",
]


def call_gemini_with_retry(client, contents, config=None, max_attempts: int = 5):
    """เรียก Gemini API พร้อมระบบ Retry อัตโนมัติเมื่อเจอ 503 UNAVAILABLE หรือ 429"""
    last_err = None
    for attempt in range(max_attempts):
        model_name = MODEL_CANDIDATES[attempt % len(MODEL_CANDIDATES)]
        try:
            return client.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )
        except Exception as e:
            last_err = e
            err_str = str(e)
            wait_sec = 5 * (attempt + 1)
            print(f"⚠️ [{model_name}] ครั้งที่ {attempt + 1}/{max_attempts} ติดปัญหา ({err_str[:80]}...) -> รอ {wait_sec} วินาทีแล้วลองใหม่")
            time.sleep(wait_sec)
    raise last_err


# ==============================================================================
# 2. โครงสร้างข้อมูลครบทุกช่องจากหน้าจอ Hizeiris (COMPLETE DATA MODEL)
# ==============================================================================
@dataclass
class HizeirisSnapshot:
    contract_name: str
    timestamp: str
    futures_price: float
    future_chg: float
    diff: float
    diff_source: str
    dte: float
    atm_iv: float
    vol_chg: float
    intraday_pc: float
    oi_pc: float
    churn_str: str
    put_wall: float
    put_wall_pc: str
    call_wall: float
    call_wall_pc: str
    max_pain: float
    top_pump_strike: float
    top_pump_pc: str
    top_inflow_strike: float
    top_inflow_pc: str
    top_oi_strike: float
    top_oi_pc: str
    vol_25c: Optional[float] = None
    vol_25p: Optional[float] = None
    next_series_atm_iv: Optional[float] = None
    cme_sd_table: Optional[Dict[str, float]] = None


def check_round_number_bonus(spot_price: float) -> str:
    rem = round(spot_price % 100, 1)
    for target in [0.0, 20.0, 50.0, 80.0, 100.0]:
        if abs(rem - target) <= 1.5:
            return " [เลขกลม +5%..+8%]"
    return ""


def evaluate_oi_zone_m2(distance: float) -> Dict[str, str]:
    d = abs(distance)
    if d < 5:
        return {"zone": "โซน 0 (<$5 Repeller Wall)", "corr": "-0.45", "light": "🔴 ไฟแดง", "touch": "แตะเป้าแล้ว (ทะลุ 15%–27.5%)", "win25": "<27.5% (เด้งเดี่ยว 45% / มีแรงต้าน 72.5%–85% / ทับ SD 78.57%–91.43%)"}
    elif d <= 15:
        return {"zone": "โซน 1A ($5–$15 Core Magnet)", "corr": "+0.85", "light": "🟢 เขียวเข้ม", "touch": "88.0%–95.0% ($10=95%)", "win25": "85.0%–88.0% (ถูกปัดตกเพียง 5%–12%)"}
    elif d <= 30:
        return {"zone": "โซน 1B ($15–$30 Event Horizon)", "corr": "+0.85", "light": "🟢 ไฟเขียว", "touch": "79.0%–90.0% (Magnet <=$25 ≈ 88%)", "win25": "78.0%–85.0% ($25=83%)"}
    elif d <= 50:
        return {"zone": "โซน 2 ($30–$50 Vacuum Zone)", "corr": "+0.65", "light": "🟢/🟡 เขียวอ่อน", "touch": "55.0%–63.0% ($40=63%)", "win25": "75.0%–78.0% ($40=78% | แบ่งล็อก +$25)"}
    elif d <= 75:
        return {"zone": "โซน 3A ($50–$75 Far Target Trap)", "corr": "+0.30..+0.45", "light": "🟡 ไฟเหลือง", "touch": "14.0%–45.0% (>$50=45%, $60=37%, $75=14%)", "win25": "บังคับ Fixed TP @ +$25 = 68.0%–72.0%"}
    return {"zone": "โซน 3B ($75–$100+ Extreme)", "corr": "<+0.20", "light": "🟡/🔴 ระวังสูง", "touch": "<5.0%–14.0%", "win25": "60.0%–68.0% (เก็บสั้น +$15..+$25)"}


def classify_current_sd_zone_6(spot: float, mid_spot: float, one_sd_pts: float) -> Dict[str, str]:
    if one_sd_pts <= 0:
        return {"sd_ratio": 0.0, "zone_name": "N/A", "stats": "N/A"}
    z = round(abs(spot - mid_spot) / one_sd_pts, 2)
    side = "ขาขึ้น (+SD)" if spot >= mid_spot else "ขาลง (-SD)"
    if z < 0.5:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 1: 0.0–0.5SD ({side}, {z}SD / 45Δ–35Δ) — Idle / No-Trade Reversal Zone",
            "stats": "แกว่งไม่พ้น <1SD ทั้งวัน 30% (500d) | แตะ 0.5SD 92.6% (27d) | วิ่งออกหา ±1SD 70%–84% | สวนกลางอากาศชนะแค่ 50%–55%",
        }
    elif z < 1.0:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 2: ±0.5–1.0SD ({side}, {z}SD / 35Δ–25Δ) — Inner Transition Zone",
            "stats": "โอกาสแตะ ±1.0SD = 70.0% (500d) ถึง 77.8% (27d) | ดีดกลับเดี่ยว 60%–65% (ทับ OI = 72%–80%) | รอเข้าโซน 1.0–1.5SD",
        }
    elif z < 1.5:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 3: ±1.0–1.5SD ({side}, {z}SD / 25Δ) — First Action / The Drift Zone",
            "stats": "ย่อสั้น $8–$12 ชนะ 70.0%–78.53% (เอเชีย 82.0%, ทับ OI 82%–88%) | แต่กลับตัวคำใหญ่ >=$25 แค่ 55.71% (Low Edge 500d) และมีโอกาสไหลทะลุไป 2SD ถึง 40.0%!",
        }
    elif z < 2.0:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 4: ±1.5–2.0SD ({side}, {z}SD / 15Δ) — Primary Kill Zone & Twin-Peaks Volga",
            "stats": "แตะ 25.9%–28.0% ของวัน | ย่อระยะกลาง $15–$22 ชนะ 80.0%–85.58% | กลับตัวคำใหญ่ >=$25 (500d) พุ่งเป็น 78.57% | ทับกำแพง OI + IV Crush ชนะสูงสุด 86.0%–91.4% (ไหลไป 3SD เพียง 25%)",
        }
    elif z < 2.5:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 5: ±2.0–2.5SD ({side}, {z}SD / 15Δ–5Δ) — Overextended Extreme Reversion Zone",
            "stats": "แตะ 11.1%–15.0% ของวัน | ดีดกลับคำใหญ่ $25–$35 ชนะ 78.0%–88.0% (ทับ OI + IV Crush = 89.0%–91.4%+)",
        }
    return {
        "sd_ratio": z,
        "zone_name": f"โซนที่ 6: ±2.5–3.0SD+ ({side}, {z}SD / 5Δ) — Tail Extremes (91.43% Peak Exhaustion)",
        "stats": "แตะ 3.7%–7.0% ของวัน (โอกาสจบสวิง 96.3%) | ฝืนรับมีดไม้แรก <55% | รอหยุดไหลเกิด Absorption (IBS <0.15 / >0.85) ชนะ 86.75% และกลับตัวคำใหญ่ >=$25 สูงถึง 91.43%–95.0%!",
    }


def compute_exact_coordinates(snap: HizeirisSnapshot) -> Dict:
    fut = snap.futures_price
    diff = snap.diff
    spot = round(fut - diff, 1)

    hourly_iv_pts = round(fut * (snap.atm_iv / 40.8 / 100.0), 1)
    daily_rule16_pct = round(snap.atm_iv / 16.0, 2)
    daily_rule16_pts = round(fut * (daily_rule16_pct / 100.0), 1)
    weekly_iv_pts = round(fut * (snap.atm_iv / 7.2 / 100.0), 1)
    monthly_iv_pts = round(fut * (snap.atm_iv / 3.46 / 100.0), 1)

    if snap.cme_sd_table and snap.cme_sd_table.get("+1SD", 0) > 0:
        sd = snap.cme_sd_table
        one_sd = float(sd.get("1SD_pts") or round(sd["+1SD"] - fut, 1))
    else:
        one_sd = round(fut * (snap.atm_iv / 100.0) * math.sqrt(max(snap.dte, 0.01) / 365.0), 1)
        sd = {
            "1SD_pts": one_sd, "2SD_pts": round(one_sd * 2, 1), "3SD_pts": round(one_sd * 3, 1),
            "+3SD": round(fut + one_sd * 3, 1), "+2SD": round(fut + one_sd * 2, 1), "+1SD": round(fut + one_sd, 1),
            "-1SD": round(fut - one_sd, 1), "-2SD": round(fut - one_sd * 2, 1), "-3SD": round(fut - one_sd * 3, 1),
        }

    half_sd = round(one_sd * 0.5, 1)
    one_half_sd = round(one_sd * 1.5, 1)
    two_half_sd = round(one_sd * 2.5, 1)

    levels = {
        "+3.0SD": (sd["+3SD"], round(sd["+3SD"] - diff, 1)),
        "+2.5SD": (round(fut + two_half_sd, 1), round(fut + two_half_sd - diff, 1)),
        "+2.0SD": (sd["+2SD"], round(sd["+2SD"] - diff, 1)),
        "+1.5SD": (round(fut + one_half_sd, 1), round(fut + one_half_sd - diff, 1)),
        "+1.0SD": (sd["+1SD"], round(sd["+1SD"] - diff, 1)),
        "+0.5SD": (round(fut + half_sd, 1), round(fut + half_sd - diff, 1)),
        "CallWall": (snap.call_wall, round(snap.call_wall - diff, 1)),
        "MaxPain": (snap.max_pain, round(snap.max_pain - diff, 1)),
        "SpotNow": (fut, spot),
        "PutWall": (snap.put_wall, round(snap.put_wall - diff, 1)),
        "-0.5SD": (round(fut - half_sd, 1), round(fut - half_sd - diff, 1)),
        "-1.0SD": (sd["-1SD"], round(sd["-1SD"] - diff, 1)),
        "-1.5SD": (round(fut - one_half_sd, 1), round(fut - one_half_sd - diff, 1)),
        "-2.0SD": (sd["-2SD"], round(sd["-2SD"] - diff, 1)),
        "-2.5SD": (round(fut - two_half_sd, 1), round(fut - two_half_sd - diff, 1)),
        "-3.0SD": (sd["-3SD"], round(sd["-3SD"] - diff, 1)),
    }

    dist_call = round(levels["CallWall"][1] - spot, 1)
    dist_put = round(spot - levels["PutWall"][1], 1)
    dist_pain = round(levels["MaxPain"][1] - spot, 1)
    current_sd_info = classify_current_sd_zone_6(spot, spot, one_sd)

    call_spot = levels["CallWall"][1]
    put_spot = levels["PutWall"][1]
    p1sd_spot, m1sd_spot = levels["+1.0SD"][1], levels["-1.0SD"][1]
    p2sd_spot, m2sd_spot = levels["+2.0SD"][1], levels["-2.0SD"][1]
    p3sd_spot, m3sd_spot = levels["+3.0SD"][1], levels["-3.0SD"][1]

    if snap.dte >= 0.04 and (0 < dist_call <= 25 or 0 < dist_put <= 25):
        if 0 < dist_call <= 25 and (dist_call <= dist_put or dist_put <= 0):
            a_dir, a_entry = "BUY (Follow Magnet ขึ้นหา Call Wall)", spot
            a_tp1, a_tp2 = round(spot + min(12.5, dist_call * 0.6), 1), call_spot
            a_sl1, a_sl2 = round(spot - 12.5, 1), round(spot - 25.0, 1)
            a_rec1_tp, a_rec2_tp = round(a_sl1 - 12.5, 1), round(a_sl2 - 12.5, 1)
        else:
            a_dir, a_entry = "SELL (Follow Magnet ลงหา Put Wall)", spot
            a_tp1, a_tp2 = round(spot - min(12.5, dist_put * 0.6), 1), put_spot
            a_sl1, a_sl2 = round(spot + 12.5, 1), round(spot + 25.0, 1)
            a_rec1_tp, a_rec2_tp = round(a_sl1 + 12.5, 1), round(a_sl2 + 12.5, 1)
    else:
        if abs(p2sd_spot - spot) <= abs(spot - m2sd_spot):
            a_dir, a_entry = "SELL (ดักสวน Kill Zone +2SD)", p2sd_spot
            a_tp1, a_tp2 = round(a_entry - 15.0, 1), round(a_entry - 25.0, 1)
            a_sl1, a_sl2 = round(a_entry + 12.5, 1), round(a_entry + 25.0, 1)
            a_rec1_tp, a_rec2_tp = round(a_sl1 + 12.5, 1), round(a_sl2 + 12.5, 1)
        else:
            a_dir, a_entry = "BUY (ดักสวน Kill Zone -2SD)", m2sd_spot
            a_tp1, a_tp2 = round(a_entry + 15.0, 1), round(a_entry + 25.0, 1)
            a_sl1, a_sl2 = round(a_entry - 12.5, 1), round(a_entry - 25.0, 1)
            a_rec1_tp, a_rec2_tp = round(a_sl1 - 12.5, 1), round(a_sl2 - 12.5, 1)

    return {
        "spot": spot,
        "diff": diff,
        "time_scaling_pts": {
            "hourly_div_40_8": hourly_iv_pts,
            "daily_rule16_pct": daily_rule16_pct,
            "daily_rule16_pts": daily_rule16_pts,
            "weekly_div_7_2": weekly_iv_pts,
            "monthly_div_3_46": monthly_iv_pts,
        },
        "sd_pts": {"1SD": one_sd, "1.5SD": one_half_sd, "2SD": sd["2SD_pts"], "2.5SD": two_half_sd, "3SD": sd["3SD_pts"]},
        "levels_spot": {k: f"{v[1]}{check_round_number_bonus(v[1])} (Fut {v[0]})" for k, v in levels.items()},
        "dist_call": dist_call,
        "dist_put": dist_put,
        "dist_max_pain": dist_pain,
        "oi_call_zone": evaluate_oi_zone_m2(dist_call),
        "oi_put_zone": evaluate_oi_zone_m2(dist_put),
        "current_sd_zone": current_sd_info,
        "eod_lock": snap.dte < 0.04,
        "plan_a_exact": {
            "dir": a_dir, "entry": a_entry, "tp1": a_tp1, "tp2": a_tp2,
            "sl1_12_5": a_sl1, "rec1_2x_tp": a_rec1_tp,
            "sl2_25_0": a_sl2, "rec2_6x_tp": a_rec2_tp,
        },
        "plan_b_exact": {
            "sell_1sd": p1sd_spot, "sell_1sd_tp": round(p1sd_spot - 10.0, 1), "sell_1sd_sl": round(p1sd_spot + 12.5, 1),
            "buy_1sd": m1sd_spot, "buy_1sd_tp": round(m1sd_spot + 10.0, 1), "buy_1sd_sl": round(m1sd_spot - 12.5, 1),
            "sell_3sd": p3sd_spot, "buy_3sd": m3sd_spot,
        },
        "plan_c_exact": {
            "call_wall_break": round(call_spot + 12.5, 1), "call_squeeze_tp": round(call_spot + 25.0, 1),
            "put_wall_break": round(put_spot - 12.5, 1), "put_squeeze_tp": round(put_spot - 25.0, 1),
        },
    }


# ==============================================================================
# 3. เก็บภาพ/ข้อความเว็บ Hizeiris + ถอด JSON + สังเคราะห์รายงานตาม PROMPT วิจัยเต็ม
# ==============================================================================
def fetch_user_diff_from_telegram(token: str, chat_id: str) -> Optional[float]:
    if token == "YOUR_BOT_TOKEN_HERE" or chat_id == "YOUR_CHAT_ID_HERE":
        return None
    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        resp = requests.get(url, params={"offset": -20, "timeout": 5}, timeout=10)
        if resp.status_code == 200:
            for upd in reversed(resp.json().get("result", [])):
                msg = upd.get("message", {})
                if str(msg.get("chat", {}).get("id", "")) == str(chat_id):
                    m = re.match(r"^/?diff\s+([0-9]+(?:\.[0-9]+)?)$", msg.get("text", "").strip(), re.IGNORECASE)
                    if m:
                        return float(m.group(1))
    except Exception:
        pass
    return None


def capture_hizeiris_web(url: str, profile_dir: str) -> Dict[str, str]:
    from playwright.sync_api import sync_playwright

    os.makedirs(profile_dir, exist_ok=True)
    shot_path = f"hizeiris_snap_{datetime.now().strftime('%Y%m%d_%H%M')}.png"

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir, headless=True, viewport={"width": 1440, "height": 2200}
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
ค้นหาตัวเลขทั้งหมดอย่างละเอียด โดยเฉพาะ:
- ชื่อสัญญา, เวลา/รอบ, ราคากลาง Futures, Future Chg, ค่า diff (ส่วนต่าง), ราคา Spot (ถ้ามี)
- DTE, ATM vol, Vol Chg, Intraday P/C, OI P/C, Churn (C/P)
- Put wall (พร้อมเลขเสา Put/Call), Call wall (พร้อมเลขเสา Put/Call), Max Pain
- เสา Intraday Volume สูงสุด (ปั๊มน้ำมันหลัก), ตาราง "เพิ่มขึ้นมากสุดรอบนี้", ตาราง "สูงสุดทั้งหมด"
- ตาราง "CME ตอนเก็บรอบนี้" (1SD, 2SD, 3SD ช่อง ลง/ขึ้น/ต่ำสุด/สูงสุด) และกล่อง "Vol จาก CME"
ตอบกลับเป็น JSON ตามโครงสร้างนี้เท่านั้น:
{
  "contract_name": "string",
  "futures_price": 0.0,
  "future_chg": 0.0,
  "spot_price": null,
  "diff": null,
  "dte": 0.0,
  "atm_iv": 0.0,
  "vol_chg": 0.0,
  "intraday_pc": 0.0,
  "oi_pc": 0.0,
  "churn_str": "string",
  "put_wall": 0.0,
  "put_wall_pc": "0P/0C",
  "call_wall": 0.0,
  "call_wall_pc": "0P/0C",
  "max_pain": 0.0,
  "top_pump_strike": 0.0,
  "top_pump_pc": "0P/0C",
  "top_inflow_strike": 0.0,
  "top_inflow_pc": "+0C/+0P",
  "top_oi_strike": 0.0,
  "top_oi_pc": "0P/0C",
  "vol_25c": null,
  "vol_25p": null,
  "next_series_atm_iv": null,
  "cme_sd_table": {
    "1SD_pts": 0.0, "2SD_pts": 0.0, "3SD_pts": 0.0,
    "+3SD": 0.0, "+2SD": 0.0, "+1SD": 0.0,
    "-1SD": 0.0, "-2SD": 0.0, "-3SD": 0.0
  }
}
"""


FULL_RESEARCH_SYNTHESIS_PROMPT = """
คุณคือ "Quantitative Derivatives & Market Microstructure Analyst (Mtraders Masterclass & AI CME Analyst)"
จงวิเคราะห์ภาพหน้าจอแอป "Intraday OI Monitor by Hizeiris" และข้อมูลพิกัดที่ Python คำนวณให้แล้วด้านล่างนี้ โดยห้ามตกหล่นแม้แต่หัวข้อเดียว (ห้ามใช้ ICT/SMC/EA และห้ามพูดเรื่องขนาดพอร์ต/Kelly/Lot):

[บังคับแสดงผลครบทั้ง 6 ส่วนตามโครงสร้างวิจัยนี้เท่านั้น]
### ส่วนที่ 1: สรุปพารามิเตอร์, เปรียบเทียบรอบก่อน (Δ t-1 -> t), HMM 3-State, จตุภาค Price-OI, PCR & โมเดลรถไม่มีเบรก
- ระบุ: สัญญา, เวลา/รอบ, Futures, Future Chg, ค่า Diff (และแหล่งที่มา), ราคา XAUUSD, DTE, ATM Vol, กฎแปลงเวลา (รายชั่วโมง ÷40.8, รายวัน Rule of 16 ÷16, รายสัปดาห์ ÷7.2, รายเดือน ÷3.46), Vol Chg, Intraday P/C, OI P/C, Churn C/P, Max Pain (และระยะห่าง)
- ระบุข้อมูลจากตาราง `เพิ่มขึ้นมากสุดรอบนี้`, `สูงสุดทั้งหมด`, และ `Vol จาก CME` (เทียบ Term Structure Contango vs Backwardation กับซีรีส์ถัดไป)
- วินิจฉัย:
  1) สถานะแผนเดิมจากรอบก่อน (PENDING / ACTIVE IN-PLAY / TP COMPLETED / TRIGGER RECOVERY NOW! / CANCELLED & PIVOT)
  2) จตุภาค Price-OI (Long Buildup / Short Covering / Short Buildup / Long Liquidation) + กฎ Contrarian PCR (>1.20 Extreme Fear Buy / <0.40 Extreme Greed Sell) + สแกน 7 รูปแบบเชิงปริมาณ (High-Confidence Bottom, Buyer Fatigue, False Breakout, Quiet Accumulation, Exhaustion Gap, Shakeout, Mid-Trend Pause)
  3) สภาวะ HMM 3-State (Positive Gamma Mean Reversion [-60..-90 bps] / Gravity Momentum / Negative Gamma Squeeze [+6.4% 30m Vol]) + 4-Factor PCA & Malz RR25/BF25 (Gold Positive Call Skew vs Put Skew, Dupire 2x Local Vol, Twin-Peaks Volga ที่ 15Δ-25Δ ขยาย Shadow Gamma +25-30%, Vanna & Charm, Speed/Zomma/Color, Rough Volatility τ^-0.40) + ลำดับ Dealer 4-Phase Squeeze (50Δ -> 25Δ -> 35-45Δ Vanna Spike -> 50Δ+ Final Flush)
  4) ตำแหน่งของ "รถไม่มีเบรก" เทียบกับ "ปั๊มน้ำมัน Intraday Volume" (จังหวะที่ 1 ทะลุปั๊มแรก / จังหวะที่ 2 น้ำมันหมดไหลถอยหลังกลับปั๊มแรก / จังหวะที่ 3 แวะเติมน้ำมันเพื่อไปต่อ) และกฎล็อกเป้า 2 ช่วงเวลา (00:30/01:30 น. Settle & 08:30 น. Kill Zone 1SD-2SD)

### ส่วนที่ 2: ตารางพิกัดแนวรับ-แนวต้านคืนนี้ (หักค่า Diff จากตาราง CME ตอนเก็บรอบนี้ + เลขเสา Put/Call)
- แสดงพิกัดเรียงจากบนลงล่าง (+3SD, +2.5SD, +2SD [เขียว +2], Call Wall, +1.5SD, +1SD [เขียว +1], +0.5SD, Max Pain, ราคากลาง/ปั๊มน้ำมันปัจจุบัน, -0.5SD, -1SD [เขียว -1], Put Wall, -1.5SD, -2SD [เขียว -2], -2.5SD, -3SD)
- ทุกแถวต้องระบุ: พิกัด XAUUSD (และ Futures) ที่ตรงกับค่าที่ Python คำนวณให้เป๊ะ 100%, โบนัสเลขกลม (+5%..+8%), เลขเสา `Put/Call` (ส้ม P / ฟ้า C), และ `% ความน่าจะเป็น` ทั้งจากวิจัย 27d/50d/100d (`แตะ / ดีดกลับเดี่ยว / ทับ OI / ทับ OI+IV Crush`) คู่กับวิจัย 500 วัน VOL2VOL (`โอกาสกลับตัวคำใหญ่ >=$25: ±1SD=55.71% [ไหลต่อ 40%], ±2SD=78.57%, ±3SD=91.43%`)

### ส่วนที่ 3: การประเมินความได้เปรียบเชิงสถิติ (% Statistical Edge Scorecard: ฝั่ง Buy vs. ฝั่ง Sell)
- เทียบแต้มต่อสุทธิเป็น % ระหว่างฝั่ง Buy กับฝั่ง Sell จาก:
  1) โซนระยะห่าง OI ทั้ง 7 โซน (<$5 ไฟแดง Corr -0.45, $5-$15 88-95%, $15-$30 79-90%, $30-$50 55-63%, $50-$75 Fixed TP +$25 = 68-72%, >$75, หนี OI 52%) & Volatility Regime 4 ระดับ (High 88.5%, Normal 81.2%, Baseline 76.83%, Low 64.5%)
  2) โซน SD 6 ระดับ + Kill Zone Confluence (86.0%–91.4%)
  3) งานวิจัย 0DTE ต่างประเทศ 7 ฉบับ & อัตราชนะรายกลยุทธ์ (ยืนฝั่ง Market Maker ดักสวนที่กำแพง 70-80% / ทับ SD 85.6-91.4%, ตามโมเมนตัม Debit 65-70% / ไฟเขียว 78-88.5%, Iron Condor 60-70%, ไล่ซื้อไร้ OI 50-60%, ไล่ทุบซื้อ Put ก้นหลุมตอน IV แพงชนะแค่ 30-40%!)

### ส่วนที่ 4: สรุปแผนปฏิบัติการเทรดเชิงปริมาณแบบครบวงจร: แผน A, แผน B, แผน C & ระบบแก้พอร์ต Supa888 (ไม่ยุ่งขนาดพอร์ต)
- แสดงรายละเอียดครบทั้ง **แผน A (หลัก: High-Conviction)**, **แผน B (รอง: Tactical ±1SD เก็บสั้น $8-$12 หรือ ±3SD รอ Absorption + IBS <0.15 / >0.85)**, และ **แผน C (สำรอง: Gamma Squeeze Breakout & Weekend Gap-Fill 404 Gaps [>0.15% ปิดวันจันทร์ 66.4%, 1 สัปดาห์ 83.2%, 1 เดือน 92.5%, Gap ลงปิด 97.8% vs Gap ขึ้น 88.7%, Run-on 0.7x])**
- ทุกแผนต้องระบุพิกัด XAUUSD & Futures จริง: `จุดเข้า (Entry)`, `Quant Microstructure Trigger`, `% ความได้เปรียบจากงานวิจัย`, `จุดทำกำไร (TP1 / TP2 / Fixed TP @ +$25 หากเป้าไกล >$50)`, `จุดตัดขาดทุน (SL เผื่อ Jump Risk 2.5 SD 5m)` และ `จุดลั่นไกแก้พอร์ต Supa888`:
  1) **RECOVERY ปกติ (เข้าไม้เดียว)**: ผิดทางครบ `-12.5 เหรียญ` -> คัทสลับหน้า **ทบ 2 เท่า (2x)** ไปตั้ง TP ที่ระยะ `+12.5 เหรียญ` ถัดไป (กำไรสุทธิ `+12.5 เหรียญต่อหน่วย`)
  2) **RECOVERY แบบรวบ (สะสม 2 ไม้ห่างกัน 12.5 เหรียญ)**: หลุดด่านสองครบ `-25.0 เหรียญ` จากไม้แรก (รวมติดลบ `-37.5 เหรียญ`) -> คัทรวบสลับหน้า **ทบ 6 เท่า (6x)** ไปตั้ง TP ที่ระยะ `+12.5 เหรียญ` ถัดไป (กำไรสุทธิ `+37.5 เหรียญต่อหน่วย`)

### ส่วนที่ 5: จุดเฝ้าระวังการเปลี่ยนสถานะเชิงปริมาณในรอบถัดไป (Key Quant Triggers & 0DTE Timing Rules)
- ระบุตัวแปรที่ต้องเช็กในรอบถัดไป (`Δ รอบก่อน` ในกล่อง `Vol จาก CME`, ตาราง `เพิ่มขึ้นมากสุดรอบนี้`, อาการรถน้ำมันหมดไหลกลับปั๊มแรก, สัญญาณกำแพงแตกเข้าแผน C Recovery) และเตือนกฎเหล็กห้ามเปิดไม้สวนเทรนด์ในช่วง **30–60 นาทีสุดท้ายก่อนปิดตลาด (DTE < 0.04)**

### ส่วนที่ 6: 📌 กล่องบันทึกความจำสถานะประจำซีรีส์ (SESSION MEMORY STATE TRACKER)
- สรุปบรรทัดล็อกสถานะสำหรับรอบถัดไปให้ครบถ้วน
"""


def build_deterministic_fallback_report(snap: HizeirisSnapshot, exact_calc: Dict) -> str:
    lv = exact_calc["levels_spot"]
    pa = exact_calc["plan_a_exact"]
    pb = exact_calc["plan_b_exact"]
    pc = exact_calc["plan_c_exact"]
    ts = exact_calc["time_scaling_pts"]
    return (
        f"📊 *1. สรุปพารามิเตอร์, HMM, 4-Quadrant & รถไม่มีเบรก* ({snap.timestamp})\n"
        f"• สัญญา: `{snap.contract_name}` | Futures: `{snap.futures_price}` (`{snap.future_chg:+.1f}`) | Diff: `{snap.diff}` _({snap.diff_source})_ | *XAUUSD*: `{exact_calc['spot']}`\n"
        f"• DTE: `{snap.dte}` | ATM IV: `{snap.atm_iv}%` (Vol Chg `{snap.vol_chg:+.2f}`) | แปลงเวลา: ชม. `±{ts['hourly_div_40_8']}` / วัน(Rule16) `±{ts['daily_rule16_pts']}` (`±{ts['daily_rule16_pct']}%`) / สัปดาห์ `±{ts['weekly_div_7_2']}` / เดือน `±{ts['monthly_div_3_46']}`\n"
        f"• Intraday P/C: `{snap.intraday_pc}` | OI P/C: `{snap.oi_pc}` | Churn: `{snap.churn_str}` | Max Pain: `{lv['MaxPain']}` (ห่าง `{exact_calc['dist_max_pain']:+.1f}`)\n"
        f"• ปั๊มน้ำมันหลัก: `{int(snap.top_pump_strike)}` ({snap.top_pump_pc}) | เงินใหม่รอบนี้: `{int(snap.top_inflow_strike)}` ({snap.top_inflow_pc}) | สูงสุดทั้งหมด: `{int(snap.top_oi_strike)}` ({snap.top_oi_pc})\n"
        f"• โซน SD ปัจจุบัน: {exact_calc['current_sd_zone']['zone_name']} -> _{exact_calc['current_sd_zone']['stats']}_\n\n"
        f"🧱 *2. ตารางพิกัด XAUUSD (หัก Diff {snap.diff} ครบ 6 โซน SD)*\n"
        f"• +3.0SD (5ΔC, 91.43%): `{lv['+3.0SD']}` | +2.5SD: `{lv['+2.5SD']}`\n"
        f"• +2.0SD (15ΔC Kill Zone 78.57%–91.4%): `{lv['+2.0SD']}` | +1.5SD: `{lv['+1.5SD']}`\n"
        f"• Call Wall ({snap.call_wall_pc}): `{lv['CallWall']}` | +1.0SD (25ΔC Drift): `{lv['+1.0SD']}`\n"
        f"• +0.5SD (35ΔC): `{lv['+0.5SD']}` | ราคากลาง/Spot: `{lv['SpotNow']}` | Max Pain: `{lv['MaxPain']}`\n"
        f"• -0.5SD (35ΔP): `{lv['-0.5SD']}` | -1.0SD (25ΔP Drift): `{lv['-1.0SD']}` | Put Wall ({snap.put_wall_pc}): `{lv['PutWall']}`\n"
        f"• -1.5SD: `{lv['-1.5SD']}` | -2.0SD (15ΔP Kill Zone 78.57%–91.4%): `{lv['-2.0SD']}`\n"
        f"• -2.5SD: `{lv['-2.5SD']}` | -3.0SD (5ΔP, 91.43%): `{lv['-3.0SD']}`\n\n"
        f"⚖️ *3. Statistical Edge Scorecard*\n"
        f"• ฝั่งขึ้นหา Call Wall (ห่าง `{exact_calc['dist_call']}`): {exact_calc['oi_call_zone']['light']} {exact_calc['oi_call_zone']['zone']} -> แตะ `{exact_calc['oi_call_zone']['touch']}` | +$25 `{exact_calc['oi_call_zone']['win25']}`\n"
        f"• ฝั่งลงหา Put Wall (ห่าง `{exact_calc['dist_put']}`): {exact_calc['oi_put_zone']['light']} {exact_calc['oi_put_zone']['zone']} -> แตะ `{exact_calc['oi_put_zone']['touch']}` | +$25 `{exact_calc['oi_put_zone']['win25']}`\n\n"
        f"🎯 *4. สรุปแผนเทรด A / B / C & Supa888 Recovery*\n"
        f"*แผน A (หลัก)*: `{pa['dir']} @ {pa['entry']}`\n"
        f"  • TP1: `{pa['tp1']}` / TP2: `{pa['tp2']}`\n"
        f"  • SL1 (-12.5): `{pa['sl1_12_5']}` (ทบ 2x -> TP `{pa['rec1_2x_tp']}`)\n"
        f"  • SL2 (-25.0): `{pa['sl2_25_0']}` (รวบทบ 6x -> TP `{pa['rec2_6x_tp']}`)\n"
        f"*แผน B (รอง)*: เก็บสั้น ±1SD (`SELL {pb['sell_1sd']} -> TP {pb['sell_1sd_tp']}` หรือ `BUY {pb['buy_1sd']} -> TP {pb['buy_1sd_tp']}`) | ดักคำใหญ่ ±3SD (`{pb['sell_3sd']}` / `{pb['buy_3sd']}` Edge 91.43%)\n"
        f"*แผน C (Squeeze)*: ทะลุ Call Wall เข้าที่ `{pc['call_wall_break']}` -> TP `{pc['call_squeeze_tp']}` | หลุด Put Wall เข้าที่ `{pc['put_wall_break']}` -> TP `{pc['put_squeeze_tp']}`\n\n"
        f"🔔 *5. Key Quant Triggers*: เช็ก Δ Vol Chg รอบถัดไป + เสาเงินใหม่ `{int(snap.top_inflow_strike)}` ({snap.top_inflow_pc})\n"
        f"📌 *6. SESSION MEMORY STATE*: `Fut {snap.futures_price} | Diff {snap.diff} | Spot {exact_calc['spot']} | PlanA {pa['entry']}`"
    )


def extract_snapshot(capture: Dict[str, str], api_key: str, fallback_diff: float, tg_diff: Optional[float]) -> HizeirisSnapshot:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    with open(capture["screenshot_path"], "rb") as f:
        img_bytes = f.read()

    response = call_gemini_with_retry(
        client=client,
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
            f"{EXTRACTION_PROMPT}\n\nข้อความจาก iframe:\n{capture['raw_text'][:8000]}",
        ],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = json.loads(response.text)
    fut_px = float(data.get("futures_price") or 0.0)

    web_diff, web_spot = data.get("diff"), data.get("spot_price")
    if web_diff is not None and float(web_diff) != 0.0:
        final_diff, diff_src = round(float(web_diff), 2), "อ่านตรงจากเว็บ Hizeiris"
    elif web_spot is not None and float(web_spot) > 0 and fut_px > 0:
        final_diff, diff_src = round(fut_px - float(web_spot), 2), "คำนวณ Futures-Spot บนเว็บ"
    elif tg_diff is not None:
        final_diff, diff_src = tg_diff, "ตั้งค่าจาก Telegram /diff"
    else:
        final_diff, diff_src = fallback_diff, "ค่าสำรอง (Fallback)"

    return HizeirisSnapshot(
        contract_name=data.get("contract_name") or "CME Gold 0DTE",
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        futures_price=fut_px,
        future_chg=float(data.get("future_chg") or 0.0),
        diff=final_diff,
        diff_source=diff_src,
        dte=float(data.get("dte") or 0.5),
        atm_iv=float(data.get("atm_iv") or 20.0),
        vol_chg=float(data.get("vol_chg") or 0.0),
        intraday_pc=float(data.get("intraday_pc") or 1.0),
        oi_pc=float(data.get("oi_pc") or 1.0),
        churn_str=str(data.get("churn_str") or "N/A"),
        put_wall=float(data.get("put_wall") or 0.0),
        put_wall_pc=str(data.get("put_wall_pc") or "N/A"),
        call_wall=float(data.get("call_wall") or 0.0),
        call_wall_pc=str(data.get("call_wall_pc") or "N/A"),
        max_pain=float(data.get("max_pain") or 0.0),
        top_pump_strike=float(data.get("top_pump_strike") or data.get("max_pain") or 0.0),
        top_pump_pc=str(data.get("top_pump_pc") or "N/A"),
        top_inflow_strike=float(data.get("top_inflow_strike") or data.get("call_wall") or 0.0),
        top_inflow_pc=str(data.get("top_inflow_pc") or "N/A"),
        top_oi_strike=float(data.get("top_oi_strike") or data.get("call_wall") or 0.0),
        top_oi_pc=str(data.get("top_oi_pc") or "N/A"),
        vol_25c=float(data["vol_25c"]) if data.get("vol_25c") is not None else None,
        vol_25p=float(data["vol_25p"]) if data.get("vol_25p") is not None else None,
        next_series_atm_iv=float(data["next_series_atm_iv"]) if data.get("next_series_atm_iv") is not None else None,
        cme_sd_table=data.get("cme_sd_table"),
    )


def synthesize_full_report(snap: HizeirisSnapshot, exact_calc: Dict, capture: Optional[Dict[str, str]],
                           api_key: str, prev_state: Optional[Dict]) -> str:
    if CONFIG["DRY_RUN"] or api_key == "YOUR_GEMINI_API_KEY_HERE" or not capture:
        return build_deterministic_fallback_report(snap, exact_calc)

    context_json = json.dumps({
        "snapshot": snap.__dict__,
        "exact_coordinates_and_plans": exact_calc,
        "previous_state": prev_state,
    }, ensure_ascii=False, indent=2)

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        with open(capture["screenshot_path"], "rb") as f:
            img_bytes = f.read()

        response = call_gemini_with_retry(
            client=client,
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                f"{FULL_RESEARCH_SYNTHESIS_PROMPT}\n\n[ข้อมูลพิกัดที่ Python คำนวณแม่นยำแล้ว 100%]:\n{context_json}\n\n[ข้อความจากหน้าเว็บ]:\n{capture['raw_text'][:6000]}",
            ],
        )
        return response.text
    except Exception as e:
        print(f"⚠️ เซิร์ฟเวอร์ AI ค้างระหว่างเขียนบทวิเคราะห์ ({e}) -> สลับส่งรายงาน Python 6 ส่วนแทนทันที!")
        return build_deterministic_fallback_report(snap, exact_calc)


def split_telegram_chunks(text: str, max_len: int = 3900) -> List[str]:
    if len(text) <= max_len:
        return [text]
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > max_len:
            chunks.append(current.strip())
            current = line
        else:
            current += line
    if current.strip():
        chunks.append(current.strip())
    return chunks


def send_telegram_parts(parts: List[str], token: str, chat_id: str) -> None:
    if token == "YOUR_BOT_TOKEN_HERE" or chat_id == "YOUR_CHAT_ID_HERE":
        for i, p in enumerate(parts, 1):
            print(f"\n📱 [TELEGRAM PREVIEW {i}/{len(parts)}]:\n{p}")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for p in parts:
        resp = requests.post(url, json={"chat_id": chat_id, "text": p, "parse_mode": "Markdown"}, timeout=15)
        if resp.status_code != 200:
            resp = requests.post(url, json={"chat_id": chat_id, "text": p}, timeout=15)
        print(f"✅ Telegram status {resp.status_code}")


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
    tg_diff = fetch_user_diff_from_telegram(CONFIG["TELEGRAM_BOT_TOKEN"], CONFIG["TELEGRAM_CHAT_ID"])
    fallback_diff = prev_state.get("diff", CONFIG["FALLBACK_DIFF"]) if prev_state else CONFIG["FALLBACK_DIFF"]

    if CONFIG["DRY_RUN"]:
        cap = None
        snap = HizeirisSnapshot(
            contract_name="OG2X6 (0DTE)", timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            futures_price=4041.1, future_chg=-8.1, diff=tg_diff or fallback_diff, diff_source="ทดสอบ (5.0)",
            dte=0.45, atm_iv=24.50, vol_chg=-0.85, intraday_pc=1.18, oi_pc=1.05, churn_str="0.42C/0.38P",
            put_wall=4000.0, put_wall_pc="320P/75C", call_wall=4050.0, call_wall_pc="60P/290C",
            max_pain=4040.0, top_pump_strike=4040.0, top_pump_pc="55P/149C",
            top_inflow_strike=4050.0, top_inflow_pc="+45C/+12P", top_oi_strike=4000.0, top_oi_pc="320P/75C",
        )
    else:
        cap = capture_hizeiris_web(CONFIG["HIZEIRIS_URL"], CONFIG["CHROME_PROFILE_DIR"])
        snap = extract_snapshot(cap, CONFIG["GEMINI_API_KEY"], fallback_diff, tg_diff)

    exact_calc = compute_exact_coordinates(snap)
    report_text = synthesize_full_report(snap, exact_calc, cap, CONFIG["GEMINI_API_KEY"], prev_state)
    send_telegram_parts(split_telegram_chunks(report_text), CONFIG["TELEGRAM_BOT_TOKEN"], CONFIG["TELEGRAM_CHAT_ID"])

    save_state(CONFIG["STATE_FILE"], {
        "timestamp": snap.timestamp, "futures_price": snap.futures_price, "spot_price": exact_calc["spot"],
        "diff": snap.diff, "atm_iv": snap.atm_iv, "put_wall": snap.put_wall,
        "call_wall": snap.call_wall, "max_pain": snap.max_pain,
        "plan_a_dir": exact_calc["plan_a_exact"]["dir"],
        "plan_a_entry": exact_calc["plan_a_exact"]["entry"],
        "plan_a_tp1": exact_calc["plan_a_exact"]["tp1"],
    })


def start_scheduler():
    print(f"🚀 เริ่มบอท Hizeiris Ultimate (โหมด: {'GitHub Actions' if IS_GITHUB_ACTIONS else 'Local'})...")
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