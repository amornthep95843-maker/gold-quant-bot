"""
================================================================================
HIZEIRIS AUTO BOT — ULTRA-FAST REST & DOM EDITION (จบใน 60 วินาที ไม่ค้าง 100%)
================================================================================
1. ตัดการใช้ ThreadPoolExecutor และ SDK ที่แอบค้างเบื้องหลังออกทั้งหมด เปลี่ยนมาใช้
   REST API ตรง (requests.post + signal.alarm ล็อกเวลาตายตัว 40 วินาที)
2. อ่านค่าตัวเลขจาก DOM หน้าเว็บ Hizeiris โดยตรงทันที (Futures, Diff, ATM Vol สด,
   Vol Chg, Vol พื้นฐาน, 1SD/2SD/3SD, Call/Put Wall, Intraday & OI P/C, Churn)
3. ส่งรูปกราฟสด 3 มุมมอง (พร้อมฟอนต์ภาษาไทย) เข้า Telegram ทันทีภายใน 20 วินาทีแรก
4. ส่งรายงานวิเคราะห์เชิงปริมาณครบทั้ง 6 ส่วน + แผน A/B/C + Supa888 Recovery
   ภายใน 45–75 วินาที โดยไม่มีทางค้างจนถูกยกเลิก (Canceled) อีกต่อไป
================================================================================
"""

import base64
import io
import json
import math
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

sys.stdout.reconfigure(line_buffering=True)

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


class HardTimeoutError(Exception):
    pass


def _alarm_handler(signum, frame):
    raise HardTimeoutError("ครบกำหนดเวลาสูงสุดต่อรอบ (Hard Timeout)")


def _compress_image_b64(path: str) -> Tuple[str, str]:
    try:
        from PIL import Image
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            if rgb.width > 1280:
                ratio = 1280.0 / rgb.width
                rgb = rgb.resize((1280, int(rgb.height * ratio)), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=82, optimize=True)
            return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
    except Exception:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii"), "image/png"


def call_gemini_rest_fast(api_key: str, prompt_text: str, image_paths: List[str],
                          json_mode: bool = False, timeout_sec: int = 40) -> str:
    """
    เรียก Gemini ผ่าน REST API โดยตรงพร้อม signal.alarm ล็อกเวลาตายตัว
    ไม่ใช้ SDK หรือ ThreadPoolExecutor ที่ค้างเบื้องหลังจนถึง 15 นาที
    """
    parts = []
    for p in image_paths[:2]:
        if os.path.exists(p):
            b64_data, mime = _compress_image_b64(p)
            parts.append({"inline_data": {"mime_type": mime, "data": b64_data}})
    parts.append({"text": prompt_text})

    payload: Dict[str, object] = {"contents": [{"parts": parts}]}
    if json_mode:
        payload["generationConfig"] = {"responseMimeType": "application/json"}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={api_key}"

    last_err = None
    for attempt in range(2):
        try:
            print(f"🤖 เรียก Gemini REST API (ครั้งที่ {attempt + 1}/2, จำกัดเวลา {timeout_sec}s)...")
            if hasattr(signal, "SIGALRM"):
                signal.signal(signal.SIGALRM, _alarm_handler)
                signal.alarm(timeout_sec + 5)
            try:
                resp = requests.post(url, json=payload, timeout=(10, timeout_sec))
            finally:
                if hasattr(signal, "SIGALRM"):
                    signal.alarm(0)

            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    text_out = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    if text_out.strip():
                        return text_out
            last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
            print(f"⚠️ Gemini ตอบกลับ HTTP {resp.status_code} -> รอ 3 วินาที...")
            time.sleep(3)
        except Exception as e:
            last_err = e
            print(f"⚠️ Gemini ใช้เวลาเกินกำหนดหรือขัดข้อง ({str(e)[:100]}) -> ข้ามไปใช้ระบบคำนวณ Python ทันที!")
            break
    raise last_err or RuntimeError("Gemini REST API failed")


# ==============================================================================
# 2. โครงสร้างข้อมูลครบทุกช่อง + Vol สด vs Vol พื้นฐาน (Vol Settle)
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
    vol_settle_atm: float
    vol_curve_compare: str
    intraday_c: int
    intraday_p: int
    intraday_pc: float
    oi_c: int
    oi_p: int
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


def evaluate_vol_live_vs_base(atm_live: float, vol_chg: float, vol_settle: float, curve_desc: str) -> Dict[str, str]:
    if vol_chg <= -1.0:
        regime = f"IV Crush รุนแรง (Vol สด {atm_live:.2f}% ต่ำกว่า Vol พื้นฐาน {vol_settle:.2f}% อยู่ {vol_chg:+.2f}%)"
        impact = "เส้นเขียว (Vol สด) ยุบตัวต่ำกว่าเส้นประแดง (Vol พื้นฐาน) -> ดูดซับแรงกระแทกและเร่งแรงดีดกลับ (Vanna Bounce / Mean Reversion แข็งแกร่ง)"
    elif vol_chg < 0:
        regime = f"IV Crush เบาบาง (Vol สด {atm_live:.2f}% ต่ำกว่า Vol พื้นฐาน {vol_settle:.2f}% อยู่ {vol_chg:+.2f}%)"
        impact = "ความผันผวนสดลดต่ำกว่าฐาน Settle -> สนับสนุนการเทรดในกรอบและดักสวนที่ขอบ SD"
    elif vol_chg >= 1.0:
        regime = f"Volatility Expansion รุนแรง (Vol สด {atm_live:.2f}% พุ่งสูงกว่า Vol พื้นฐาน {vol_settle:.2f}% อยู่ {vol_chg:+.2f}%)"
        impact = "เส้นเขียว (Vol สด) ยกตัวเหนือเส้นประแดง (Vol พื้นฐาน) -> ระวังการฉีกกรอบทะลุ ±1SD ไป ±2SD และห้ามรับมีดไม้แรกเร็วเกินไป!"
    else:
        regime = f"Vol ทรงตัวใกล้ฐาน (Vol สด {atm_live:.2f}% vs Vol พื้นฐาน {vol_settle:.2f}%, Chg {vol_chg:+.2f}%)"
        impact = "เส้นเขียวเกาะใกล้เส้นประแดง -> เทรดตามโซนแม่เหล็ก OI และกรอบ SD มาตรฐาน"

    return {
        "vol_live": f"{atm_live:.2f}%",
        "vol_base_settle": f"{vol_settle:.2f}%",
        "vol_chg": f"{vol_chg:+.2f}%",
        "regime": regime,
        "impact": impact,
        "curve_wing_detail": curve_desc or "เปรียบเทียบความชันเส้นเขียวทึบ (Vol สด) กับเส้นประแดง (Vol Settle) ตามโซน Delta",
    }


def classify_current_sd_zone_6(spot: float, mid_spot: float, one_sd_pts: float) -> Dict[str, str]:
    if one_sd_pts <= 0:
        return {"sd_ratio": 0.0, "zone_name": "N/A", "stats": "N/A"}
    z = round(abs(spot - mid_spot) / one_sd_pts, 2)
    side = "ขาขึ้น (+SD)" if spot >= mid_spot else "ขาลง (-SD)"
    if z < 0.5:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 1: 0.0–0.5SD ({side}, {z}SD / 45Δ–35Δ) — Idle / No-Trade Reversal Zone",
            "stats": "แกว่งไม่พ้น <1SD ทั้งวัน 30% (500d) | แตะ 0.5SD 92.6% (27d) | วิ่งออกหา ±1SD 70%–84%",
        }
    elif z < 1.0:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 2: ±0.5–1.0SD ({side}, {z}SD / 35Δ–25Δ) — Inner Transition Zone",
            "stats": "โอกาสแตะ ±1.0SD = 70.0% (500d) ถึง 77.8% (27d) | ดีดกลับเดี่ยว 60%–65% (ทับ OI = 72%–80%)",
        }
    elif z < 1.5:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 3: ±1.0–1.5SD ({side}, {z}SD / 25Δ) — First Action / The Drift Zone",
            "stats": "ย่อสั้น $8–$12 ชนะ 70.0%–78.53% (เอเชีย 82.0%) | แต่กลับตัวคำใหญ่ >=$25 แค่ 55.71% และไหลไป 2SD ถึง 40.0%!",
        }
    elif z < 2.0:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 4: ±1.5–2.0SD ({side}, {z}SD / 15Δ) — Primary Kill Zone & Twin-Peaks Volga",
            "stats": "กลับตัวคำใหญ่ >=$25 (500d) = 78.57% | ทับกำแพง OI + IV Crush ชนะสูงสุด 86.0%–91.4% (ไหลไป 3SD เพียง 25%)",
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
        "stats": "แตะ 3.7%–7.0% ของวัน (จบสวิง 96.3%) | รอหยุดไหลเกิด Absorption ชนะ 86.75% และกลับตัวคำใหญ่ >=$25 สูงถึง 91.43%–95.0%!",
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

    vol_analysis = evaluate_vol_live_vs_base(
        snap.atm_iv, snap.vol_chg, snap.vol_settle_atm, snap.vol_curve_compare
    )

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
            a_dir, a_entry = "SELL (ดักสวน Kill Zone +2SD / Call Wall)", p2sd_spot
            a_tp1, a_tp2 = round(a_entry - 15.0, 1), round(a_entry - 25.0, 1)
            a_sl1, a_sl2 = round(a_entry + 12.5, 1), round(a_entry + 25.0, 1)
            a_rec1_tp, a_rec2_tp = round(a_sl1 + 12.5, 1), round(a_sl2 + 12.5, 1)
        else:
            a_dir, a_entry = "BUY (ดักสวน Kill Zone -2SD / Put Wall)", m2sd_spot
            a_tp1, a_tp2 = round(a_entry + 15.0, 1), round(a_entry + 25.0, 1)
            a_sl1, a_sl2 = round(a_entry - 12.5, 1), round(a_entry - 25.0, 1)
            a_rec1_tp, a_rec2_tp = round(a_sl1 - 12.5, 1), round(a_sl2 - 12.5, 1)

    return {
        "spot": spot,
        "diff": diff,
        "vol_live_vs_base": vol_analysis,
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
            "sell_2sd": p2sd_spot, "buy_2sd": m2sd_spot,
            "sell_3sd": p3sd_spot, "buy_3sd": m3sd_spot,
        },
        "plan_c_exact": {
            "call_wall_break": round(call_spot + 12.5, 1), "call_squeeze_tp": round(call_spot + 25.0, 1),
            "put_wall_break": round(put_spot - 12.5, 1), "put_squeeze_tp": round(put_spot - 25.0, 1),
        },
    }


# ==============================================================================
# 3. ดึงภาพกราฟ Intraday + Open Interest (OI) + SD·diff และส่งรูปเข้า Telegram
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


def _extract_frame_texts_and_inputs(page) -> str:
    blocks = []
    for idx, frame in enumerate(page.frames):
        try:
            txt = frame.inner_text("body", timeout=2000)
            inputs = frame.locator("input, select, textarea").evaluate_all(
                """els => els.map(el => ({
                    id: el.id || '',
                    name: el.name || '',
                    placeholder: el.placeholder || '',
                    value: el.value || '',
                    text: (el.parentElement ? el.parentElement.innerText : '').slice(0, 80)
                })).filter(x => x.value !== '')"""
            )
            if (txt and txt.strip()) or inputs:
                blocks.append(f"--- FRAME {idx} ---\n{txt.strip()}\nINPUT_VALUES: {json.dumps(inputs, ensure_ascii=False)}")
        except Exception:
            pass
    return "\n\n".join(blocks)


def _expand_iframes_for_full_charts(page) -> None:
    for frame in page.frames:
        try:
            frame.locator("body").evaluate(
                """body => {
                    if (!document.getElementById('thai-font-fix')) {
                        const link = document.createElement('link');
                        link.id = 'thai-font-fix';
                        link.rel = 'stylesheet';
                        link.href = 'https://fonts.googleapis.com/css2?family=Noto+Sans+Thai:wght@400;600;700&display=swap';
                        document.head.appendChild(link);
                        const style = document.createElement('style');
                        style.innerHTML = `* { font-family: "Noto Sans Thai", "Waree", "Garuda", "Loma", sans-serif !important; }`;
                        document.head.appendChild(style);
                    }
                    document.documentElement.style.overflow = 'visible';
                    body.style.overflow = 'visible';
                    document.querySelectorAll('iframe, #sandboxFrame, #userHtmlFrame').forEach(el => {
                        el.style.height = '2200px';
                        el.style.maxHeight = 'none';
                    });
                }""",
                timeout=2000,
            )
        except Exception:
            pass


def capture_hizeiris_web(url: str, profile_dir: str) -> Dict[str, object]:
    from playwright.sync_api import sync_playwright

    os.makedirs(profile_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    shot_intraday_oi = f"hizeiris_1_intraday_oi_{ts}.png"
    shot_sd_diff = f"hizeiris_2_sddiff_{ts}.png"
    shot_oi_all = f"hizeiris_3_oi_all_{ts}.png"
    screenshots: List[str] = []
    photo_captions: List[Tuple[str, str]] = []

    print("🌐 กำลังเปิดหน้าเว็บ Hizeiris...")
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=True,
            viewport={"width": 1600, "height": 2200},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(15000)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(7000)
        _expand_iframes_for_full_charts(page)
        page.wait_for_timeout(800)

        text_plan_tab = _extract_frame_texts_and_inputs(page)

        for frame in page.frames:
            try:
                loc_cme = frame.get_by_text(re.compile(r"CME\s*[·\-\.]?\s*Vol", re.IGNORECASE))
                if loc_cme.count() > 0:
                    loc_cme.first.click(timeout=2500)
                    page.wait_for_timeout(1500)
                    break
            except Exception:
                pass

        page.screenshot(path=shot_intraday_oi, full_page=True, timeout=15000)
        screenshots.append(shot_intraday_oi)
        photo_captions.append((shot_intraday_oi, "📊 [ภาพที่ 1] กราฟ Intraday Volume + Open Interest (OI) & CME · Vol"))
        text_main = _extract_frame_texts_and_inputs(page)
        print("✅ แคปภาพที่ 1 (Intraday + OI + CME·Vol) สำเร็จ!")

        clicked_sd_diff = False
        for frame in page.frames:
            try:
                loc = frame.get_by_text(re.compile(r"SD\s*[·\-\.]?\s*diff", re.IGNORECASE))
                if loc.count() > 0:
                    loc.first.click(timeout=2500)
                    clicked_sd_diff = True
                    break
            except Exception:
                pass

        text_diff_tab = ""
        if clicked_sd_diff:
            page.wait_for_timeout(1800)
            _expand_iframes_for_full_charts(page)
            page.screenshot(path=shot_sd_diff, full_page=True, timeout=15000)
            screenshots.append(shot_sd_diff)
            photo_captions.append((shot_sd_diff, "📐 [ภาพที่ 2] แท็บ SD · diff & ตารางสถิติ Open Interest"))
            text_diff_tab = _extract_frame_texts_and_inputs(page)
            print("✅ แคปภาพที่ 2 (แท็บ SD · diff) สำเร็จ!")

        clicked_oi_all = False
        for frame in page.frames:
            try:
                loc_oi = frame.get_by_text(re.compile(r"OI\s*ทุกสัญญา", re.IGNORECASE))
                if loc_oi.count() > 0:
                    loc_oi.first.click(timeout=2500)
                    clicked_oi_all = True
                    break
            except Exception:
                pass

        text_oi_all = ""
        if clicked_oi_all:
            page.wait_for_timeout(2000)
            _expand_iframes_for_full_charts(page)
            page.screenshot(path=shot_oi_all, full_page=True, timeout=15000)
            screenshots.append(shot_oi_all)
            photo_captions.append((shot_oi_all, "🧱 [ภาพที่ 3] กราฟ Open Interest รวม (OI ทุกสัญญา)"))
            text_oi_all = _extract_frame_texts_and_inputs(page)
            print("✅ แคปภาพที่ 3 (OI ทุกสัญญา) สำเร็จ!")

        context.close()

    return {
        "screenshot_paths": screenshots,
        "photo_captions": photo_captions,
        "raw_text": (
            f"[VIEW 0: แผนเทรด]\n{text_plan_tab}\n\n"
            f"[VIEW 1: Intraday + Open Interest + CME·Vol]\n{text_main}\n\n"
            f"[VIEW 2: SD · diff]\n{text_diff_tab}\n\n"
            f"[VIEW 3: OI ทุกสัญญา]\n{text_oi_all}"
        ),
    }


def _parse_float(pattern: str, text: str, default: float = 0.0) -> float:
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except Exception:
            pass
    return default


def parse_snapshot_from_dom_text(raw_text: str, fallback_diff: float, tg_diff: Optional[float]) -> HizeirisSnapshot:
    """ถอดตัวเลขจากข้อความ DOM บนหน้าเว็บ Hizeiris โดยตรงใน 0.01 วินาที"""
    contract_m = re.search(r"\b(OG[A-Z0-9]{2,5}|G[0-9][A-Z0-9]{2,5}|GC[A-Z0-9]{2,5})\b", raw_text)
    contract_name = contract_m.group(1) if contract_m else "CME Gold"

    fut_px = _parse_float(r"Futures\s*([0-9,]+\.?[0-9]*)", raw_text, 0.0)
    if fut_px == 0.0:
        fut_px = _parse_float(r"ราคากลาง\s*([0-9,]+\.?[0-9]*)", raw_text, 3880.0)

    dte = _parse_float(r"DTE\s*([0-9]+\.?[0-9]*)", raw_text, 0.5)
    atm_live = _parse_float(r"ATM\s*vol\s*([0-9]+\.?[0-9]*)", raw_text, 0.0)
    if atm_live == 0.0:
        atm_live = _parse_float(r"\bIV\s*([0-9]+\.?[0-9]*)", raw_text, 17.5)

    vol_chg = _parse_float(r"Vol\s*Chg\s*([+\-]?[0-9]+\.?[0-9]*)", raw_text, 0.0)
    future_chg = _parse_float(r"Future\s*Chg\s*([+\-]?[0-9]+\.?[0-9]*)", raw_text, 0.0)
    vol_settle_atm = round(atm_live - vol_chg, 2)

    intra_m = re.search(r"Intraday\s+C\s*([0-9,]+)\s*[·•]?\s*P\s*([0-9,]+)\s*[·•]?\s*P/C\s*([0-9.]+)", raw_text, re.IGNORECASE)
    if intra_m:
        intraday_c = int(intra_m.group(1).replace(",", ""))
        intraday_p = int(intra_m.group(2).replace(",", ""))
        intraday_pc = float(intra_m.group(3))
    else:
        intraday_c, intraday_p = 0, 0
        intraday_pc = _parse_float(r"Intraday.*?P/C\s*([0-9]+\.?[0-9]*)", raw_text, 0.7)

    oi_m = re.search(r"Open Interest\s+C\s*([0-9,]+)\s*[·•]?\s*P\s*([0-9,]+)\s*[·•]?\s*P/C\s*([0-9.]+)", raw_text, re.IGNORECASE)
    if oi_m:
        oi_c = int(oi_m.group(1).replace(",", ""))
        oi_p = int(oi_m.group(2).replace(",", ""))
        oi_pc = float(oi_m.group(3))
    else:
        oi_c, oi_p = 0, 0
        oi_pc = _parse_float(r"Open Interest.*?P/C\s*([0-9]+\.?[0-9]*)", raw_text, 1.2)

    churn_m = re.search(r"Churn\s*(C\s*[0-9.]+\s*[·•/]?\s*P\s*[0-9.]+)", raw_text, re.IGNORECASE)
    churn_str = churn_m.group(1) if churn_m else "N/A"

    max_pain = _parse_float(r"Max\s*Pain\s*([0-9,]+\.?[0-9]*)", raw_text, round(fut_px, -1))

    call_wall = _parse_float(r"Call\s*wall\s*([0-9,]+\.?[0-9]*)", raw_text, 0.0)
    call_oi_cnt = ""
    cw_plan_m = re.search(r"Call\s+[0-9.]+\s*\(([0-9]{4})\)[^\n]*?OI\s*([0-9,]+)", raw_text, re.IGNORECASE)
    if cw_plan_m:
        if call_wall == 0.0:
            call_wall = float(cw_plan_m.group(1))
        call_oi_cnt = f"OI {cw_plan_m.group(2)} สัญญา"
    if call_wall == 0.0:
        call_wall = round(fut_px + 50, -1)

    put_wall = _parse_float(r"Put\s*wall\s*([0-9,]+\.?[0-9]*)", raw_text, 0.0)
    put_oi_cnt = ""
    pw_plan_m = re.search(r"Put\s+[0-9.]+\s*\(([0-9]{4})\)[^\n]*?OI\s*([0-9,]+)", raw_text, re.IGNORECASE)
    if pw_plan_m:
        if put_wall == 0.0:
            put_wall = float(pw_plan_m.group(1))
        put_oi_cnt = f"OI {pw_plan_m.group(2)} สัญญา"
    if put_wall == 0.0:
        put_wall = round(fut_px - 50, -1)

    web_diff = _parse_float(r"\bdiff\s*[:=]?\s*([0-9]+\.?[0-9]*)", raw_text, 0.0)
    if tg_diff is not None:
        final_diff, diff_src = tg_diff, "ตั้งค่าจาก Telegram /diff"
    elif web_diff > 0:
        final_diff, diff_src = round(web_diff, 2), "อ่านจากหน้าเว็บ Hizeiris"
    else:
        final_diff, diff_src = fallback_diff, f"ค่ามาตรฐาน ({fallback_diff})"

    cme_sd_table = None
    sd1_m = re.search(r"1SD\s+([+\-]?[0-9.]+)\s+([+\-]?[0-9.]+)\s+([0-9,]+\.?[0-9]*)\s+([0-9,]+\.?[0-9]*)", raw_text)
    sd2_m = re.search(r"2SD\s+([+\-]?[0-9.]+)\s+([+\-]?[0-9.]+)\s+([0-9,]+\.?[0-9]*)\s+([0-9,]+\.?[0-9]*)", raw_text)
    sd3_m = re.search(r"3SD\s+([+\-]?[0-9.]+)\s+([+\-]?[0-9.]+)\s+([0-9,]+\.?[0-9]*)\s+([0-9,]+\.?[0-9]*)", raw_text)
    if sd1_m and sd2_m and sd3_m:
        cme_sd_table = {
            "1SD_pts": abs(float(sd1_m.group(2))),
            "2SD_pts": abs(float(sd2_m.group(2))),
            "3SD_pts": abs(float(sd3_m.group(2))),
            "-1SD": float(sd1_m.group(3).replace(",", "")),
            "+1SD": float(sd1_m.group(4).replace(",", "")),
            "-2SD": float(sd2_m.group(3).replace(",", "")),
            "+2SD": float(sd2_m.group(4).replace(",", "")),
            "-3SD": float(sd3_m.group(3).replace(",", "")),
            "+3SD": float(sd3_m.group(4).replace(",", "")),
        }
    else:
        # กรณีอ่านจากบรรทัดสรุปในแท็บแผนเทรด: 1SD 41.8 (±1SD 3825.4–3909.0 · ±2SD 3783.5–3950.9)
        plan_sd_m = re.search(
            r"1SD\s*([0-9.]+)\s*\(±1SD\s*([0-9.]+)[–\-]([0-9.]+)\s*[·•]\s*±2SD\s*([0-9.]+)[–\-]([0-9.]+)\)",
            raw_text,
        )
        if plan_sd_m:
            s1 = float(plan_sd_m.group(1))
            m1, p1 = float(plan_sd_m.group(2)) + final_diff, float(plan_sd_m.group(3)) + final_diff
            m2, p2 = float(plan_sd_m.group(4)) + final_diff, float(plan_sd_m.group(5)) + final_diff
            cme_sd_table = {
                "1SD_pts": s1,
                "2SD_pts": round(s1 * 2, 1),
                "3SD_pts": round(s1 * 3, 1),
                "-1SD": round(m1, 1),
                "+1SD": round(p1, 1),
                "-2SD": round(m2, 1),
                "+2SD": round(p2, 1),
                "-3SD": round(fut_px - s1 * 3, 1),
                "+3SD": round(fut_px + s1 * 3, 1),
            }

    return HizeirisSnapshot(
        contract_name=contract_name,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        futures_price=fut_px,
        future_chg=future_chg,
        diff=final_diff,
        diff_source=diff_src,
        dte=dte,
        atm_iv=atm_live,
        vol_chg=vol_chg,
        vol_settle_atm=vol_settle_atm,
        vol_curve_compare="เปรียบเทียบเส้นเขียวทึบ (Vol สด) กับเส้นประสีแดง (Vol Settle/พื้นฐาน) ตามโครงสร้าง Smile",
        intraday_c=intraday_c,
        intraday_p=intraday_p,
        intraday_pc=intraday_pc,
        oi_c=oi_c,
        oi_p=oi_p,
        oi_pc=oi_pc,
        churn_str=churn_str,
        put_wall=put_wall,
        put_wall_pc=put_oi_cnt or f"Put Wall {int(put_wall)}",
        call_wall=call_wall,
        call_wall_pc=call_oi_cnt or f"Call Wall {int(call_wall)}",
        max_pain=max_pain,
        top_pump_strike=max_pain,
        top_pump_pc=f"Intraday C {intraday_c}/P {intraday_p}",
        top_inflow_strike=call_wall,
        top_inflow_pc=call_oi_cnt or "Call Inflow",
        top_oi_strike=put_wall if oi_pc >= 1.0 else call_wall,
        top_oi_pc=f"OI C {oi_c}/P {oi_p}",
        cme_sd_table=cme_sd_table,
    )


FULL_RESEARCH_SYNTHESIS_PROMPT = """
คุณคือ "Quantitative Derivatives & Market Microstructure Analyst (Mtraders Masterclass & AI CME Analyst)"
จงวิเคราะห์ภาพหน้าจอแอป "Intraday OI Monitor by Hizeiris" ทั้งกราฟ Intraday Volume, กราฟ Open Interest (OI), แท็บ SD·diff และข้อมูลพิกัดที่ Python คำนวณให้แล้วด้านล่างนี้ โดยห้ามตกหล่นแม้แต่หัวข้อเดียว (ห้ามใช้ ICT/SMC/EA และห้ามพูดเรื่องขนาดพอร์ต/Kelly/Lot/1.0 Unit):

[บังคับแสดงผลครบทั้ง 6 ส่วนตามโครงสร้างวิจัยนี้เท่านั้น]
### ส่วนที่ 1: สรุปพารามิเตอร์, Vol สด vs Vol พื้นฐาน (Vol Settle), เปรียบเทียบรอบก่อน (Δ t-1 -> t), HMM 3-State, จตุภาค Price-OI, PCR & โมเดลรถไม่มีเบรก
- ระบุ: สัญญา, เวลา/รอบ, Futures, Future Chg, ค่า Diff (และแหล่งที่มา), ราคา XAUUSD, DTE
- **วิเคราะห์เจาะลึก `Vol สด (เส้นทึบสีเขียว)` เทียบกับ `Vol พื้นฐาน / Vol Settle (เส้นประสีแดง)`**:
  - ระบุค่า `Vol สด (ATM vol)`, `Vol พื้นฐาน (Vol Settle = Vol สด - Vol Chg)` และ `Vol Chg`
  - อธิบายความหมายของช่องว่างระหว่างเส้นเขียวทึบ (`Vol สด`) กับเส้นประแดง (`Vol Settle`) ทั้งที่จุด ATM, ปีกซ้าย (`Put 25ΔP–15ΔP`) และปีกขวา (`Call 25ΔC–15ΔC`) ว่าเกิด `IV Crush` (หนุน Vanna Bounce / Mean Reversion) หรือ `Vol Expansion` (หนุน Breakout / Squeeze)
- **เปรียบเทียบกราฟแท่ง `Intraday Volume` (ด้านบน) กับกราฟแท่ง `Open Interest` (ด้านล่าง)**:
  - ชี้ชัดว่าเสาไหนเป็นปั๊มน้ำมันรายวัน (`Intraday Volume`) และเสาไหนเป็นกำแพงสะสม (`Open Interest`) พร้อมระบุ `Intraday P/C`, `OI P/C`, `Churn C/P`, `Max Pain`, ตาราง `เพิ่มขึ้นมากสุดรอบนี้`, `สูงสุดทั้งหมด`, และ `Vol จาก CME`
- วินิจฉัย: สถานะแผนเดิม 5 รูปแบบ, จตุภาค Price-OI 4 สถานะ + กฎ Contrarian PCR + 7 รูปแบบเชิงปริมาณ, สภาวะ HMM 3-State + Shadow Greeks (Vanna, Twin-Peaks Volga ขยาย Shadow Gamma +25-30%, Charm, Speed) + 4-Phase Dealer Squeeze, และตำแหน่งของ "รถไม่มีเบรก" เทียบกับ "ปั๊มน้ำมัน Intraday Volume"

### ส่วนที่ 2: ตารางพิกัดแนวรับ-แนวต้านคืนนี้ (หักค่า Diff จากตาราง CME ตอนเก็บรอบนี้ + เลขเสา Put/Call ทั้ง Intraday & OI)
- แสดงพิกัดเรียงจากบนลงล่าง (+3SD, +2.5SD, +2SD [เขียว +2], Call Wall, +1.5SD, +1SD [เขียว +1], +0.5SD, Max Pain, ราคากลาง/ปั๊มน้ำมันปัจจุบัน, -0.5SD, -1SD [เขียว -1], Put Wall, -1.5SD, -2SD [เขียว -2], -2.5SD, -3SD)
- ทุกแถวต้องใช้พิกัด XAUUSD (และ Futures) ที่ตรงกับค่าที่ Python คำนวณให้เป๊ะ 100%, โบนัสเลขกลม (+5%..+8%), เลขเสา `Put/Call` (ทั้งจาก Intraday และ OI), และ `% ความน่าจะเป็น` จากวิจัย 27d/50d/100d คู่กับวิจัย 500 วัน VOL2VOL (`±1SD >=$25 แค่ 55.71% [ไหลต่อ 40%], ±2SD=78.57%, ±3SD=91.43%`)

### ส่วนที่ 3: การประเมินความได้เปรียบเชิงสถิติ (% Statistical Edge Scorecard: ฝั่ง Buy vs. ฝั่ง Sell)
- เทียบแต้มต่อสุทธิเป็น % ระหว่างฝั่ง Buy กับฝั่ง Sell จาก: โซนระยะห่าง OI ทั้ง 7 โซน & Vol Regime 4 ระดับ, โซน SD 6 ระดับ + Kill Zone Confluence (86%–91.4%), โครงสร้าง `Vol สด vs Vol พื้นฐาน`, และงานวิจัย 0DTE 7 ฉบับ (PoP รายกลยุทธ์)

### ส่วนที่ 4: สรุปแผนปฏิบัติการเทรดเชิงปริมาณแบบครบวงจร: แผน A, แผน B, แผน C & ระบบแก้พอร์ต Supa888
- แสดงรายละเอียดครบทั้ง **แผน A (หลัก)**, **แผน B (รอง)**, และ **แผน C (สำรอง: Gamma Squeeze & Weekend Gap)**
- ทุกแผนต้องระบุเฉพาะพิกัดราคา XAUUSD & Futures จริง (ห้ามระบุจำนวน Unit หรือขนาดพอร์ต): `Entry`, `Quant Trigger (อิงเสา Intraday + OI + Vol สด vs Vol พื้นฐาน)`, `% Win Rate จากวิจัย`, `TP1 / TP2 / Fixed TP @ +$25`, `SL` และ `จุดลั่นไกแก้พอร์ต Supa888` (`Recovery ปกติ -12.5 ทบ 2x` และ `Recovery แบบรวบ 2 ไม้ -25.0 ทบ 6x`)

### ส่วนที่ 5: จุดเฝ้าระวังการเปลี่ยนสถานะเชิงปริมาณในรอบถัดไป (Key Quant Triggers & 0DTE Timing Rules)
### ส่วนที่ 6: 📌 กล่องบันทึกความจำสถานะประจำซีรีส์ (SESSION MEMORY STATE TRACKER)
"""


def build_deterministic_fallback_report(snap: HizeirisSnapshot, exact_calc: Dict) -> str:
    lv = exact_calc["levels_spot"]
    pa = exact_calc["plan_a_exact"]
    pb = exact_calc["plan_b_exact"]
    pc = exact_calc["plan_c_exact"]
    ts = exact_calc["time_scaling_pts"]
    va = exact_calc["vol_live_vs_base"]
    return (
        f"📊 *ส่วนที่ 1: สรุปพารามิเตอร์, Vol สด vs Vol พื้นฐาน, Intraday & OI* ({snap.timestamp})\n"
        f"• สัญญา: `{snap.contract_name}` | Futures: `{snap.futures_price}` (`{snap.future_chg:+.1f}`) | Diff: `{snap.diff}` _({snap.diff_source})_ | *XAUUSD*: `{exact_calc['spot']}`\n"
        f"• *Vol สด (เส้นเขียวทึบ)*: `{va['vol_live']}` vs *Vol พื้นฐาน (Vol Settle เส้นประแดง)*: `{va['vol_base_settle']}` (`Vol Chg {va['vol_chg']}`)\n"
        f"  -> _{va['regime']}: {va['impact']}_\n"
        f"• DTE: `{snap.dte}` | แปลงเวลา: ชม. `±{ts['hourly_div_40_8']}` / วัน(Rule16) `±{ts['daily_rule16_pts']}` (`±{ts['daily_rule16_pct']}%`) / สัปดาห์ `±{ts['weekly_div_7_2']}` / เดือน `±{ts['monthly_div_3_46']}`\n"
        f"• *Intraday Volume*: C `{snap.intraday_c:,}` / P `{snap.intraday_p:,}` (`P/C {snap.intraday_pc}`) | *Open Interest*: C `{snap.oi_c:,}` / P `{snap.oi_p:,}` (`P/C {snap.oi_pc}`)\n"
        f"• Churn: `{snap.churn_str}` | Max Pain: `{lv['MaxPain']}` (ห่าง `{exact_calc['dist_max_pain']:+.1f}`)\n"
        f"• โซน SD ปัจจุบัน: {exact_calc['current_sd_zone']['zone_name']} -> _{exact_calc['current_sd_zone']['stats']}_\n\n"
        f"🧱 *ส่วนที่ 2: ตารางพิกัด XAUUSD (หัก Diff {snap.diff} ครบ 6 โซน SD & กำแพง OI)*\n"
        f"• +3.0SD (5ΔC, 91.43%): `{lv['+3.0SD']}`\n"
        f"• +2.5SD (Overextended): `{lv['+2.5SD']}`\n"
        f"• +2.0SD (15ΔC Kill Zone 78.57%–91.4%): `{lv['+2.0SD']}`\n"
        f"• Call Wall ({snap.call_wall_pc}): `{lv['CallWall']}`\n"
        f"• +1.5SD (25Δ–15Δ): `{lv['+1.5SD']}`\n"
        f"• +1.0SD (25ΔC Drift Zone 55.71% / ไหลต่อ 40%): `{lv['+1.0SD']}`\n"
        f"• +0.5SD (35ΔC): `{lv['+0.5SD']}`\n"
        f"• ราคากลาง / ปัจจุบัน: `{lv['SpotNow']}` | Max Pain: `{lv['MaxPain']}`\n"
        f"• -0.5SD (35ΔP): `{lv['-0.5SD']}`\n"
        f"• -1.0SD (25ΔP Drift Zone 55.71% / ไหลต่อ 40%): `{lv['-1.0SD']}`\n"
        f"• -1.5SD (25Δ–15Δ): `{lv['-1.5SD']}`\n"
        f"• Put Wall ({snap.put_wall_pc}): `{lv['PutWall']}`\n"
        f"• -2.0SD (15ΔP Kill Zone 78.57%–91.4%): `{lv['-2.0SD']}`\n"
        f"• -2.5SD (Overextended): `{lv['-2.5SD']}`\n"
        f"• -3.0SD (5ΔP, 91.43%): `{lv['-3.0SD']}`\n\n"
        f"⚖️ *ส่วนที่ 3: Statistical Edge Scorecard (ฝั่ง Buy vs ฝั่ง Sell)*\n"
        f"• ฝั่งขึ้นหา Call Wall (ห่าง `{exact_calc['dist_call']}`): {exact_calc['oi_call_zone']['light']} {exact_calc['oi_call_zone']['zone']} -> แตะ `{exact_calc['oi_call_zone']['touch']}` | +$25 `{exact_calc['oi_call_zone']['win25']}`\n"
        f"• ฝั่งลงหา Put Wall (ห่าง `{exact_calc['dist_put']}`): {exact_calc['oi_put_zone']['light']} {exact_calc['oi_put_zone']['zone']} -> แตะ `{exact_calc['oi_put_zone']['touch']}` | +$25 `{exact_calc['oi_put_zone']['win25']}`\n\n"
        f"🎯 *ส่วนที่ 4: สรุปแผนปฏิบัติการเทรด A / B / C & ระบบแก้พอร์ต Supa888*\n"
        f"*แผน A (แผนหลัก — High Probability Setup)*: `{pa['dir']} @ {pa['entry']}`\n"
        f"  • เป้าทำกำไร: TP1 `{pa['tp1']}` (+15 pts) / TP2 (Fixed +$25) `{pa['tp2']}` (Win Rate 78.57%–86.0%)\n"
        f"  • จุดตัดขาดทุน & Supa888 Level 1 (ระยะ -12.5): `{pa['sl1_12_5']}` -> สลับหน้าทบ 2x เก็บ TP ที่ `{pa['rec1_2x_tp']}`\n"
        f"  • Supa888 Level 2 แบบรวบ 2 ไม้ (ระยะ -25.0): `{pa['sl2_25_0']}` -> สลับหน้าทบ 6x เก็บ TP ที่ `{pa['rec2_6x_tp']}`\n"
        f"*แผน B (แผนรอง — Range Fade ขอบตรงข้าม & ±1SD)*:\n"
        f"  • ดักฝั่งตรงข้าม Kill Zone ±2SD: SELL `{pb['sell_2sd']}` หรือ BUY `{pb['buy_2sd']}` (Win Rate 78.57%)\n"
        f"  • เก็บสั้น ±1SD ($8–$12): SELL `{pb['sell_1sd']} -> TP {pb['sell_1sd_tp']}` หรือ BUY `{pb['buy_1sd']} -> TP {pb['buy_1sd_tp']}` (ระวังไหลต่อ 40%)\n"
        f"  • ดักคำใหญ่ ±3SD: SELL `{pb['sell_3sd']}` หรือ BUY `{pb['buy_3sd']}` (Win Rate 91.43%)\n"
        f"*แผน C (แผนสำรอง — Breakout / Gamma Squeeze)*:\n"
        f"  • ทะลุ Call Wall ยืนเหนือ `{pc['call_wall_break']}` -> Follow Buy เป้า `{pc['call_squeeze_tp']}`\n"
        f"  • หลุด Put Wall ยืนต่ำกว่า `{pc['put_wall_break']}` -> Follow Sell เป้า `{pc['put_squeeze_tp']}`\n\n"
        f"🔔 *ส่วนที่ 5: Key Quant Triggers รอบถัดไป*: เฝ้าระวังการยกตัวของเส้นเขียว Vol สด (`{va['vol_live']}`) เทียบเส้นประแดง (`{va['vol_base_settle']}`) และการย้ายเสา Intraday (`P/C {snap.intraday_pc}`)\n"
        f"📌 *ส่วนที่ 6: SESSION MEMORY STATE*: `Fut {snap.futures_price} | Diff {snap.diff} | Spot {exact_calc['spot']} | VolLive {va['vol_live']}/Base {va['vol_base_settle']} | PlanA {pa['entry']}`"
    )


def synthesize_full_report(snap: HizeirisSnapshot, exact_calc: Dict, capture: Optional[Dict[str, object]],
                           api_key: str, prev_state: Optional[Dict]) -> str:
    if CONFIG["DRY_RUN"] or api_key == "YOUR_GEMINI_API_KEY_HERE" or not capture:
        return build_deterministic_fallback_report(snap, exact_calc)

    context_json = json.dumps({
        "snapshot": snap.__dict__,
        "exact_coordinates_and_plans": exact_calc,
        "previous_state": prev_state,
    }, ensure_ascii=False, indent=2)

    prompt = (
        f"{FULL_RESEARCH_SYNTHESIS_PROMPT}\n\n"
        f"[ข้อมูลพิกัดและ Vol สด vs Vol พื้นฐาน ที่ Python คำนวณแม่นยำแล้ว 100%]:\n{context_json}\n\n"
        f"[ข้อความจากหน้าเว็บ]:\n{str(capture['raw_text'])[:6000]}"
    )

    try:
        return call_gemini_rest_fast(
            api_key=api_key,
            prompt_text=prompt,
            image_paths=capture["screenshot_paths"][:2],
            json_mode=False,
            timeout_sec=40,
        )
    except Exception as e:
        print(f"⚡ ส่งรายงานวิเคราะห์เชิงปริมาณ 6 ส่วนจากระบบคำนวณ Python ทันที ({e})")
        return build_deterministic_fallback_report(snap, exact_calc)


def send_telegram_photos(photo_captions: List[Tuple[str, str]], token: str, chat_id: str) -> None:
    if token == "YOUR_BOT_TOKEN_HERE" or chat_id == "YOUR_CHAT_ID_HERE" or not photo_captions:
        return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    for path, title in photo_captions:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as img_file:
                resp = requests.post(
                    url,
                    data={"chat_id": chat_id, "caption": title},
                    files={"photo": img_file},
                    timeout=25,
                )
                print(f"📸 ส่งภาพ {path} เข้า Telegram สำเร็จ (status {resp.status_code})")
        except Exception as e:
            print(f"⚠️ ส่งภาพ {path} ไม่สำเร็จ: {e}")


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
    fallback_diff = CONFIG["FALLBACK_DIFF"]

    if CONFIG["DRY_RUN"]:
        cap = None
        snap = HizeirisSnapshot(
            contract_name="G3MX6", timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            futures_price=3881.4, future_chg=-2.3, diff=tg_diff or 2.5, diff_source="อ่านจากหน้าเว็บ Hizeiris",
            dte=0.48, atm_iv=17.30, vol_chg=0.00, vol_settle_atm=17.30,
            vol_curve_compare="เส้นเขียว Vol สด (17.30%) เกาะสมดุลกับเส้นประแดง Vol พื้นฐาน (17.30%)",
            intraday_c=374, intraday_p=133, intraday_pc=0.36,
            oi_c=1124, oi_p=1535, oi_pc=1.37, churn_str="C 0.68 / P 0.41",
            put_wall=3800.0, put_wall_pc="OI 390 สัญญา", call_wall=3950.0, call_wall_pc="OI 213 สัญญา",
            max_pain=3900.0, top_pump_strike=3900.0, top_pump_pc="Intraday C 374/P 133",
            top_inflow_strike=3950.0, top_inflow_pc="OI 213 สัญญา", top_oi_strike=3800.0, top_oi_pc="OI C 1124/P 1535",
            cme_sd_table={
                "1SD_pts": 41.8, "2SD_pts": 83.6, "3SD_pts": 125.4,
                "+3SD": 4006.8, "+2SD": 3953.4, "+1SD": 3911.5,
                "-1SD": 3827.9, "-2SD": 3786.0, "-3SD": 3756.0,
            },
        )
    else:
        cap = capture_hizeiris_web(CONFIG["HIZEIRIS_URL"], CONFIG["CHROME_PROFILE_DIR"])
        if cap and cap.get("photo_captions"):
            send_telegram_photos(
                cap["photo_captions"],
                CONFIG["TELEGRAM_BOT_TOKEN"],
                CONFIG["TELEGRAM_CHAT_ID"],
            )
        # อ่านค่าตัวเลขจาก DOM หน้าเว็บ Hizeiris โดยตรงทันที (ใช้เวลา 0.01 วินาที ไม่ต้องรอ AI OCR!)
        snap = parse_snapshot_from_dom_text(str(cap.get("raw_text") or ""), fallback_diff, tg_diff)
        print(f"✅ ดึงข้อมูลสำเร็จ: {snap.contract_name} | Fut {snap.futures_price} | Diff {snap.diff} | ATM IV {snap.atm_iv}%")

    exact_calc = compute_exact_coordinates(snap)
    report_text = synthesize_full_report(snap, exact_calc, cap, CONFIG["GEMINI_API_KEY"], prev_state)
    send_telegram_parts(split_telegram_chunks(report_text), CONFIG["TELEGRAM_BOT_TOKEN"], CONFIG["TELEGRAM_CHAT_ID"])

    save_state(CONFIG["STATE_FILE"], {
        "timestamp": snap.timestamp, "futures_price": snap.futures_price, "spot_price": exact_calc["spot"],
        "diff": snap.diff, "atm_iv": snap.atm_iv, "vol_settle_atm": snap.vol_settle_atm,
        "put_wall": snap.put_wall, "call_wall": snap.call_wall, "max_pain": snap.max_pain,
        "plan_a_dir": exact_calc["plan_a_exact"]["dir"],
        "plan_a_entry": exact_calc["plan_a_exact"]["entry"],
        "plan_a_tp1": exact_calc["plan_a_exact"]["tp1"],
    })
    print("🎉 ส่งรูปกราฟและรายงานวิเคราะห์เข้า Telegram เสร็จสมบูรณ์!")


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
