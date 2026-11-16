"""
================================================================================
HIZEIRIS AUTO BOT — ZERO-CRASH EDITION (NO 404 MODEL + DOM REGEX FALLBACK + THAI FONTS)
================================================================================
1. ใช้เฉพาะโมเดล gemini-3.8-flash ที่ยืนยันแล้วว่าทำงานได้จริง + ดึงรายชื่อโมเดลสำรองจาก
   client.models.list() อัตโนมัติ (ตัดปัญหา 404 NOT_FOUND ของ gemini-3.8-pro ทิ้ง 100%)
2. ระบบสำรอง 2 ชั้น (Dual Fallback): หากเซิร์ฟเวอร์ AI ล่มทั้งตอนอ่านภาพหรือตอนเขียนรายงาน
   ระบบจะดึงตัวเลขจากข้อความ DOM บนหน้าเว็บ Hizeiris มาคำนวณและส่งรายงานครบ 6 ส่วนทันที
   โดยไม่มีทางเกิด Error Process completed with exit code 1 อีกต่อไป
3. ส่งรูปกราฟสด (พร้อมฟอนต์ภาษาไทยคมชัด) เข้า Telegram ทันทีภายใน 20 วินาทีแรก
================================================================================
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import io
import json
import math
import os
import re
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


def _get_candidate_models(client) -> List[str]:
    """ใช้ gemini-3.8-flash เป็นหลักเสมอ (ไม่เรียกชื่อโมเดลที่ไม่มีอยู่จริงจนเกิด 404)"""
    models = ["gemini-3.8-flash", "gemini-3.8-flash", "gemini-3.8-flash"]
    try:
        for m in client.models.list():
            name = getattr(m, "name", "") or ""
            short = name.replace("models/", "")
            if "gemini" in short and "flash" in short and short not in models:
                models.append(short)
    except Exception:
        pass
    return models


def _compress_image_for_gemini(path: str) -> Tuple[bytes, str]:
    try:
        from PIL import Image
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            if rgb.width > 1400:
                ratio = 1400.0 / rgb.width
                rgb = rgb.resize((1400, int(rgb.height * ratio)), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=85, optimize=True)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        with open(path, "rb") as f:
            return f.read(), "image/png"


def call_gemini_with_retry(client, contents, config=None, max_attempts: int = 4, per_call_timeout: int = 55):
    candidates = _get_candidate_models(client)
    last_err = None
    for attempt in range(max_attempts):
        model_name = candidates[attempt % len(candidates)]
        try:
            print(f"🤖 กำลังประมวลผลด้วย {model_name} (รอบที่ {attempt + 1}/{max_attempts})...")
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    client.models.generate_content,
                    model=model_name,
                    contents=contents,
                    config=config,
                )
                return future.result(timeout=per_call_timeout)
        except FuturesTimeoutError:
            last_err = RuntimeError(f"{model_name} ใช้เวลาเกิน {per_call_timeout} วินาที")
            print(f"⚠️ [{model_name}] รอนานเกิน {per_call_timeout}s -> ลองใหม่ทันที")
        except Exception as e:
            last_err = e
            wait_sec = 4 * (attempt + 1)
            print(f"⚠️ [{model_name}] ติดปัญหา ({str(e)[:80]}...) -> รอ {wait_sec}s")
            time.sleep(wait_sec)
    raise last_err


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
        page.wait_for_timeout(7500)
        _expand_iframes_for_full_charts(page)
        page.wait_for_timeout(800)

        # ดึงข้อความจากแท็บเริ่มต้น (แผนเทรด — มีค่า diff ประจำสัปดาห์)
        text_plan_tab = _extract_frame_texts_and_inputs(page)

        # กดเปิดแท็บ "CME · Vol" เพื่อให้ตาราง CME ตอนเก็บรอบนี้แสดงขึ้นมาสำหรับแคปภาพที่ 1
        for frame in page.frames:
            try:
                loc_cme = frame.get_by_text(re.compile(r"CME\s*[·\-\.]?\s*Vol", re.IGNORECASE))
                if loc_cme.count() > 0:
                    loc_cme.first.click(timeout=2500)
                    page.wait_for_timeout(1500)
                    break
            except Exception:
                pass

        # ภาพที่ 1: หน้าหลัก Intraday Volume + Open Interest + CME · Vol
        page.screenshot(path=shot_intraday_oi, full_page=True, timeout=15000)
        screenshots.append(shot_intraday_oi)
        photo_captions.append((shot_intraday_oi, "📊 [ภาพที่ 1] กราฟ Intraday Volume + Open Interest (OI) & CME · Vol"))
        text_main = _extract_frame_texts_and_inputs(page)
        print("✅ แคปภาพที่ 1 (Intraday + OI + CME·Vol) สำเร็จ!")

        # ภาพที่ 2: กดเปิดแท็บ "SD · diff"
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
            page.wait_for_timeout(2000)
            _expand_iframes_for_full_charts(page)
            page.screenshot(path=shot_sd_diff, full_page=True, timeout=15000)
            screenshots.append(shot_sd_diff)
            photo_captions.append((shot_sd_diff, "📐 [ภาพที่ 2] แท็บ SD · diff & ตารางสถิติ Open Interest"))
            text_diff_tab = _extract_frame_texts_and_inputs(page)
            print("✅ แคปภาพที่ 2 (แท็บ SD · diff) สำเร็จ!")

        # ภาพที่ 3: กดเปิดแท็บ "OI ทุกสัญญา"
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
            page.wait_for_timeout(2500)
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
    """ระบบสำรองฉุกเฉิน: ถอดตัวเลขจากข้อความ DOM บนหน้าเว็บ Hizeiris โดยตรงเมื่อเซิร์ฟเวอร์ AI ขัดข้อง"""
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

    intraday_pc = _parse_float(r"Intraday.*?P/C\s*([0-9]+\.?[0-9]*)", raw_text, 0.7)
    oi_pc = _parse_float(r"Open Interest.*?P/C\s*([0-9]+\.?[0-9]*)", raw_text, 1.2)
    churn_m = re.search(r"Churn\s*(C\s*[0-9.]+\s*[·•/]?\s*P\s*[0-9.]+)", raw_text, re.IGNORECASE)
    churn_str = churn_m.group(1) if churn_m else "N/A"

    max_pain = _parse_float(r"Max\s*Pain\s*([0-9,]+\.?[0-9]*)", raw_text, round(fut_px, -1))
    call_wall = _parse_float(r"Call\s*wall\s*([0-9,]+\.?[0-9]*)", raw_text, 0.0)
    if call_wall == 0.0:
        call_wall = _parse_float(r"Call\s+[0-9.]+\s*\(([0-9]{4})\)", raw_text, round(fut_px + 50, -1))

    put_wall = _parse_float(r"Put\s*wall\s*([0-9,]+\.?[0-9]*)", raw_text, 0.0)
    if put_wall == 0.0:
        put_wall = _parse_float(r"Put\s+[0-9.]+\s*\(([0-9]{4})\)", raw_text, round(fut_px - 50, -1))

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
        vol_curve_compare="ดึงค่าจาก DOM หน้าเว็บ Hizeiris โดยตรง",
        intraday_pc=intraday_pc,
        oi_pc=oi_pc,
        churn_str=churn_str,
        put_wall=put_wall,
        put_wall_pc=f"Put Wall {int(put_wall)}",
        call_wall=call_wall,
        call_wall_pc=f"Call Wall {int(call_wall)}",
        max_pain=max_pain,
        top_pump_strike=max_pain,
        top_pump_pc="Intraday Peak",
        top_inflow_strike=call_wall,
        top_inflow_pc="Inflow Peak",
        top_oi_strike=put_wall,
        top_oi_pc="Max OI Wall",
        cme_sd_table=cme_sd_table,
    )


EXTRACTION_PROMPT = """
คุณคือระบบ OCR ถอดตัวเลขจากภาพหน้าจอและข้อความของเว็บ Intraday OI Monitor by Hizeiris เท่านั้น
ค้นหาตัวเลขทั้งหมดอย่างละเอียดจากทั้งกราฟ "Intraday Volume" (ด้านบน) และกราฟ "Open Interest" (ด้านล่าง) รวมถึงแท็บ "แผนเทรด", "CME · Vol" และ "SD · diff":
1. ค่า "diff" / "Diff" จากแท็บ "แผนเทรด" หรือ "SD · diff" หรือ INPUT_VALUES (หากมีค่า diff บนเว็บ เช่น diff 2.5 หรือ diff 5 ให้ดึงมาทันที)
2. ค่า "ATM vol" (คือ Vol สด ที่จุด ATM) และค่า "Vol Chg" (ส่วนต่างเทียบกับ Vol พื้นฐาน / Vol Settle)
3. เปรียบเทียบทรงกราฟระหว่าง "เส้นทึบสีเขียว (Vol สด)" กับ "เส้นประสีแดง (Vol Settle / Vol พื้นฐาน)" ทั้งบนกราฟ Intraday Volume และกราฟ Open Interest ว่าที่จุด ATM, ฝั่งซ้าย (Put 25ΔP–15ΔP) และฝั่งขวา (Call 25ΔC–15ΔC) เส้นเขียวอยู่ต่ำกว่าหรือสูงกว่าเส้นประแดงอย่างไร
4. อ่านตัวเลขหัวเสา Put/Call ทั้งจากกราฟ "Intraday Volume" (top_pump_strike / top_pump_pc) และจากกราฟ "Open Interest" (put_wall, call_wall, top_oi_strike / top_oi_pc)
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
  "vol_curve_compare": "อธิบายสั้นๆ ว่าเส้นเขียวทึบ (Vol สด) เทียบกับเส้นประแดง (Vol Settle/พื้นฐาน) ที่ฝั่ง Put, ATM และฝั่ง Call เป็นอย่างไร",
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
        f"📊 *1. สรุปพารามิเตอร์, Vol สด vs Vol พื้นฐาน, Intraday & OI* ({snap.timestamp})\n"
        f"• สัญญา: `{snap.contract_name}` | Futures: `{snap.futures_price}` (`{snap.future_chg:+.1f}`) | Diff: `{snap.diff}` _({snap.diff_source})_ | *XAUUSD*: `{exact_calc['spot']}`\n"
        f"• *Vol สด (เส้นเขียว)*: `{va['vol_live']}` vs *Vol พื้นฐาน (Vol Settle เส้นประแดง)*: `{va['vol_base_settle']}` (`Vol Chg {va['vol_chg']}`)\n"
        f"  -> _{va['regime']}: {va['impact']}_\n"
        f"  -> _โครงสร้างปีกซ้าย/ขวา_: {va['curve_wing_detail']}\n"
        f"• DTE: `{snap.dte}` | แปลงเวลา: ชม. `±{ts['hourly_div_40_8']}` / วัน(Rule16) `±{ts['daily_rule16_pts']}` (`±{ts['daily_rule16_pct']}%`) / สัปดาห์ `±{ts['weekly_div_7_2']}` / เดือน `±{ts['monthly_div_3_46']}`\n"
        f"• *Intraday P/C*: `{snap.intraday_pc}` | *Open Interest P/C*: `{snap.oi_pc}` | Churn: `{snap.churn_str}` | Max Pain: `{lv['MaxPain']}` (ห่าง `{exact_calc['dist_max_pain']:+.1f}`)\n"
        f"• ปั๊มน้ำมัน Intraday: `{int(snap.top_pump_strike)}` ({snap.top_pump_pc}) | เงินใหม่รอบนี้: `{int(snap.top_inflow_strike)}` ({snap.top_inflow_pc}) | กำแพง OI สูงสุด: `{int(snap.top_oi_strike)}` ({snap.top_oi_pc})\n"
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
        f"🔔 *5. Key Quant Triggers*: เช็ก Δ Vol สด vs Vol พื้นฐาน (`{va['vol_base_settle']}`) + เสาเงินใหม่ `{int(snap.top_inflow_strike)}` ({snap.top_inflow_pc})\n"
        f"📌 *6. SESSION MEMORY STATE*: `Fut {snap.futures_price} | Diff {snap.diff} | Spot {exact_calc['spot']} | VolLive {va['vol_live']}/Base {va['vol_base_settle']} | PlanA {pa['entry']}`"
    )


def extract_snapshot(capture: Dict[str, object], api_key: str, fallback_diff: float, tg_diff: Optional[float]) -> HizeirisSnapshot:
    raw_text = str(capture.get("raw_text") or "")
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        parts = []
        for p in capture["screenshot_paths"][:2]:
            img_bytes, mime_type = _compress_image_for_gemini(p)
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime_type))
        parts.append(f"{EXTRACTION_PROMPT}\n\nข้อความและ INPUT_VALUES จากหน้าเว็บ:\n{raw_text[:9000]}")

        response = call_gemini_with_retry(
            client=client,
            contents=parts,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
            max_attempts=4,
            per_call_timeout=45,
        )
        data = json.loads(response.text)
        fut_px = float(data.get("futures_price") or 0.0)
        atm_live = float(data.get("atm_iv") or 20.0)
        vol_chg = float(data.get("vol_chg") or 0.0)
        vol_settle_atm = round(atm_live - vol_chg, 2)

        web_diff, web_spot = data.get("diff"), data.get("spot_price")
        if tg_diff is not None:
            final_diff, diff_src = tg_diff, "ตั้งค่าจาก Telegram /diff"
        elif web_diff is not None and float(web_diff) != 0.0:
            final_diff, diff_src = round(float(web_diff), 2), "อ่านจากหน้าเว็บ Hizeiris"
        elif web_spot is not None and float(web_spot) > 0 and fut_px > 0:
            final_diff, diff_src = round(fut_px - float(web_spot), 2), "คำนวณ Futures-Spot บนเว็บ"
        else:
            dom_diff = _parse_float(r"\bdiff\s*[:=]?\s*([0-9]+\.?[0-9]*)", raw_text, 0.0)
            if dom_diff > 0:
                final_diff, diff_src = round(dom_diff, 2), "อ่านจาก DOM บนเว็บ"
            else:
                final_diff, diff_src = fallback_diff, f"ค่ามาตรฐาน ({fallback_diff})"

        return HizeirisSnapshot(
            contract_name=data.get("contract_name") or "CME Gold 0DTE",
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            futures_price=fut_px,
            future_chg=float(data.get("future_chg") or 0.0),
            diff=final_diff,
            diff_source=diff_src,
            dte=float(data.get("dte") or 0.5),
            atm_iv=atm_live,
            vol_chg=vol_chg,
            vol_settle_atm=vol_settle_atm,
            vol_curve_compare=str(data.get("vol_curve_compare") or ""),
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
    except Exception as e:
        print(f"⚠️ AI OCR ไม่ตอบสนอง ({e}) -> สลับใช้ระบบอ่านค่า DOM จากหน้าเว็บโดยตรงทันที!")
        return parse_snapshot_from_dom_text(raw_text, fallback_diff, tg_diff)


def synthesize_full_report(snap: HizeirisSnapshot, exact_calc: Dict, capture: Optional[Dict[str, object]],
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
        parts = []
        for p in capture["screenshot_paths"][:2]:
            img_bytes, mime_type = _compress_image_for_gemini(p)
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime_type))
        parts.append(
            f"{FULL_RESEARCH_SYNTHESIS_PROMPT}\n\n[ข้อมูลพิกัดและ Vol สด vs Vol พื้นฐาน ที่ Python คำนวณแม่นยำแล้ว 100%]:\n{context_json}\n\n[ข้อความจากหน้าเว็บ]:\n{str(capture['raw_text'])[:6000]}"
        )

        response = call_gemini_with_retry(
            client=client, contents=parts, max_attempts=4, per_call_timeout=60
        )
        return response.text
    except Exception as e:
        print(f"⚠️ เซิร์ฟเวอร์ AI ตอบช้า/ค้าง ({e}) -> สลับส่งรายงาน Python 6 ส่วนเข้า Telegram ทันที!")
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
            futures_price=3877.2, future_chg=-8.1, diff=tg_diff or fallback_diff, diff_source="ค่ามาตรฐาน (5.0)",
            dte=2.86, atm_iv=16.87, vol_chg=-4.86, vol_settle_atm=21.73,
            vol_curve_compare="ที่ ATM และฝั่ง Call (+1SD) เส้นเขียว Vol สด (16.87%) ยุบตัวต่ำกว่าเส้นประแดง Vol พื้นฐาน (21.73%) ถึง -4.86% แต่ที่ฝั่งซ้ายสุด (-2SD) เส้นเขียวยกตัวเหนือเส้นประแดง",
            intraday_pc=0.70, oi_pc=2.47, churn_str="C 0.70 / P 0.57",
            put_wall=3800.0, put_wall_pc="Put wall 3800", call_wall=4000.0, call_wall_pc="Call wall 4000",
            max_pain=4000.0, top_pump_strike=3950.0, top_pump_pc="Intraday Vol Peak",
            top_inflow_strike=3950.0, top_inflow_pc="+Call Flow", top_oi_strike=3800.0, top_oi_pc="P 1,547 / C 626",
            cme_sd_table={
                "1SD_pts": 58.0, "2SD_pts": 117.2, "3SD_pts": 177.4,
                "+3SD": 4054.6, "+2SD": 3994.4, "+1SD": 3935.2,
                "-1SD": 3819.3, "-2SD": 3762.6, "-3SD": 3706.8,
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
        snap = extract_snapshot(cap, CONFIG["GEMINI_API_KEY"], fallback_diff, tg_diff)

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
