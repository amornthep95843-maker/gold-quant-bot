"""
================================================================================
HIZEIRIS AUTO BOT — EXACT "SD · diff" MORNING LOCK EDITION (DTE = 0.8 UNTIL SERIES END)
================================================================================
1. กดเปิดแท็บ "SD · diff" บนเว็บ Hizeiris ตั้งค่า:
   - เปิดปุ่ม "SD: เปิด" และ "diff: ใช้"
   - เลือก "IV ตอนเปิด" และกรอกช่อง DTE = 0.8 เพื่อให้หน้าเว็บคำนวณตาราง
     (range / buy spot / sell spot ของ 1sd, 2sd, 3sd) และตีเส้นปะสีม่วงลงบนกราฟทันที
2. ดึงค่าจากตาราง "SD · diff" บนเว็บโดยตรง (ทศนิยม 2 ตำแหน่งเป๊ะตามตารางเว็บ):
   - ราคาเปิด (Futures), diff, ราคา spot ตอนเปิด, IV ตอนเปิด 06:30
   - 1sd (range, buy spot, sell spot)
   - 2sd (range, buy spot, sell spot)
   - 3sd (range, buy spot, sell spot)
   และล็อกกรอบนี้ไว้ใช้ตลอดทั้งวันจนจบซีรีส์เพื่อวางกรอบการเล่นตามงานวิจัย
3. วิเคราะห์การเล่นครบทั้ง 6 โซน (1SD SELL, 2SD SELL, 3SD SELL, 1SD BUY, 2SD BUY, 3SD BUY)
   ร่วมกับกำแพง OI (Put Wall / Call Wall), ปั๊มน้ำมัน Intraday Volume, และ Vol สด vs Vol Settle
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
    "HIZEIRIS_URL": "https://script.google.com/macros/s/AKfycbyHn6gN2wZfvMPTYiYrcIOPbyZMpYtB4cPRUYPh0dqu0ZbS_dYLQNyUsc5jxXzXzItS1X/exec?v=view-lqwjrh81ZsvZ".replace("XzXz", "Xz"),
    "CHROME_PROFILE_DIR": "./hizeiris_chrome_profile",
    "FIRST_TIME_LOGIN": False,
    "INTERVAL_MINUTES": 30,
    "STATE_FILE": "session_state.json",
    "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY_HERE"),
    "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE"),
    "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE"),
    "FALLBACK_DIFF": float(os.getenv("FALLBACK_DIFF", "2.5")),
    "FIXED_DTE": float(os.getenv("FIXED_DTE", "0.8")),
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
            if rgb.width > 1024:
                ratio = 1024.0 / rgb.width
                rgb = rgb.resize((1024, int(rgb.height * ratio)), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=78, optimize=True)
            return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
    except Exception:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii"), "image/png"


def call_gemini_rest_fast(api_key: str, prompt_text: str, image_paths: List[str],
                          timeout_sec: int = 32) -> str:
    parts = []
    if image_paths and os.path.exists(image_paths[0]):
        b64_data, mime = _compress_image_b64(image_paths[0])
        parts.append({"inline_data": {"mime_type": mime, "data": b64_data}})
    parts.append({"text": prompt_text})

    payload: Dict[str, object] = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.2,
            "thinkingConfig": {"thinkingBudget": 512},
        },
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={api_key}"

    try:
        print(f"🤖 เรียก Gemini REST API (จำกัดเวลา {timeout_sec}s)...")
        if hasattr(signal, "SIGALRM"):
            signal.signal(signal.SIGALRM, _alarm_handler)
            signal.alarm(timeout_sec + 5)
        try:
            resp = requests.post(url, json=payload, timeout=(8, timeout_sec))
        finally:
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)

        if resp.status_code == 200:
            data = resp.json()
            candidates = data.get("candidates", [])
            if candidates:
                for part in candidates[0].get("content", {}).get("parts", []):
                    if "text" in part and not part.get("thought", False):
                        if part["text"].strip():
                            return part["text"]
    except Exception as e:
        print(f"⚡ ส่งรายงานวิเคราะห์เชิงปริมาณ 6 ส่วนฉบับเต็มจากระบบ Python ทันที ({str(e)[:80]})")
    raise RuntimeError("Fallback to full deterministic quantitative report")


# ==============================================================================
# 2. โครงสร้างข้อมูลตาราง SD · diff (ล็อกค่าตอนเช้า 06:30 ใช้จนจบซีรีส์ของวัน)
# ==============================================================================
@dataclass
class HizeirisSnapshot:
    contract_name: str
    timestamp: str
    round_str: str
    futures_price: float           # ราคา Futures รอบปัจจุบัน (เช่น 3864.8)
    future_chg: float
    daily_open_fut: float          # ราคาเปิด Futures ตอนเช้า (เช่น 3869.70)
    daily_open_spot: float         # ราคา Spot ตอนเปิด (เช่น 3867.20 = 3869.70 - 2.50)
    open_iv: float                 # IV ตอนเปิด 06:30 (เช่น 17.3%)
    open_source: str
    diff: float                    # ค่า diff ที่ปัดแล้ว (เช่น 2.50)
    raw_market_diff: Optional[float]
    diff_source: str
    dte: float                     # DTE จริงของสัญญาในรอบนั้น (เช่น 0.38)
    fixed_dte: float               # DTE ที่ล็อกไว้คำนวณกรอบวัน (0.8)
    atm_iv: float                  # Vol สด รอบปัจจุบัน (%)
    vol_chg: float
    vol_settle_atm: float          # Vol พื้นฐาน / Settle (%)
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
    sd_diff_table: Optional[Dict[str, float]] = None


def _is_valid_gold_price(val: float, ref_fut: float = 0.0) -> bool:
    if not (2500.0 <= val <= 6000.0):
        return False
    if ref_fut > 2500.0 and abs(val - ref_fut) > 250.0:
        return False
    return True


def check_round_number_bonus(spot_price: float) -> str:
    rem = round(spot_price % 100, 2)
    for target in [0.0, 20.0, 50.0, 80.0, 100.0]:
        if abs(rem - target) <= 1.8:
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


def evaluate_vol_live_vs_base(atm_live: float, vol_chg: float, vol_settle: float, open_iv: float) -> Dict[str, str]:
    diff_vs_open = round(atm_live - open_iv, 2)
    if vol_chg <= -1.0:
        regime = f"IV Crush รุนแรง (Vol สด {atm_live:.2f}% ต่ำกว่า Vol Settle {vol_settle:.2f}% อยู่ {vol_chg:+.2f}% | เทียบ IV ตอนเปิด {open_iv:.2f}% = {diff_vs_open:+.2f}%)"
        impact = "เส้นเขียว (Vol สด) ยุบตัวต่ำกว่าเส้นประแดง (Vol Settle) -> ดูดซับแรงกระแทกและเร่งแรงดีดกลับเข้าหากรอบ SD ตอนเปิด (Vanna Bounce / Mean Reversion แข็งแกร่ง)"
    elif vol_chg < 0:
        regime = f"IV Crush เบาบาง (Vol สด {atm_live:.2f}% ต่ำกว่า Vol Settle {vol_settle:.2f}% อยู่ {vol_chg:+.2f}% | IV ตอนเปิด {open_iv:.2f}%)"
        impact = "ความผันผวนสดลดต่ำกว่าฐาน Settle -> สนับสนุนการดักเล่นตามกรอบ Fixed SD ตอนเปิด (1SD/2SD/3SD)"
    elif vol_chg >= 1.0:
        regime = f"Volatility Expansion รุนแรง (Vol สด {atm_live:.2f}% พุ่งสูงกว่า Vol Settle {vol_settle:.2f}% อยู่ {vol_chg:+.2f}% | IV ตอนเปิด {open_iv:.2f}%)"
        impact = "เส้นเขียว (Vol สด) ยกตัวเหนือเส้นประแดง -> ระวังการฉีกกรอบทะลุ ±1SD ไปหา ±2SD (เน้นดักที่ ±2SD / ±3SD เป็นหลัก)"
    else:
        regime = f"Vol สมดุลเกาะฐาน (Vol สด {atm_live:.2f}% vs Vol Settle {vol_settle:.2f}% | IV ตอนเปิด {open_iv:.2f}%)"
        impact = "เส้นเขียวเกาะใกล้เส้นประแดง -> ตลาดเคารพกรอบ Fixed SD ตอนเช้า (DTE 0.8) และกำแพง OI อย่างมีนัยสำคัญ"

    return {
        "vol_live": f"{atm_live:.2f}%",
        "vol_base_settle": f"{vol_settle:.2f}%",
        "open_iv": f"{open_iv:.2f}%",
        "vol_chg": f"{vol_chg:+.2f}%",
        "regime": regime,
        "impact": impact,
    }


def classify_current_sd_zone_6(spot: float, open_spot: float, one_sd_pts: float) -> Dict[str, str]:
    if one_sd_pts <= 0:
        return {"sd_ratio": 0.0, "zone_name": "N/A", "stats": "N/A"}
    z = round((spot - open_spot) / one_sd_pts, 2)
    az = abs(z)
    side = "เหนือราคาเปิด (+SD)" if z >= 0 else "ต่ำกว่าราคาเปิด (-SD)"
    if az < 0.5:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 1: 0.0–0.5SD ({side}, {z:+.2f}SD / 45Δ–35Δ) — โซนสะสมพลังใกล้ราคาเปิด (Idle Zone)",
            "stats": "แกว่งไม่พ้น <1SD ทั้งวัน 30% (500d) | แตะ 0.5SD 92.6% (27d) | วิ่งออกหา ±1SD 70%–84% (รอเข้าที่กรอบ ±1SD / ±2SD)",
        }
    elif az < 1.0:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 2: ±0.5–1.0SD ({side}, {z:+.2f}SD / 35Δ–25Δ) — โซนเดินทางเข้าหา ±1SD (Transition Zone)",
            "stats": "โอกาสแตะ ±1.0SD = 70.0% (500d) ถึง 77.8% (27d) | เตรียมดักย่อ/เด้งสั้นที่ขอบ ±1SD หรือถือตามแรงดูด OI หากห่าง <$25",
        }
    elif az < 1.5:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 3: ±1.0–1.5SD ({side}, {z:+.2f}SD / 25Δ) — โซนปะทะด่านแรก (The Drift Zone)",
            "stats": "ย่อ/เด้งสั้น $8–$12 ชนะ 70.0%–78.53% (เอเชีย 82.0%) | แต่กลับตัวคำใหญ่ >=$25 แค่ 55.71% และมีโอกาสไหลต่อไป ±2SD ถึง 40.0%!",
        }
    elif az < 2.0:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 4: ±1.5–2.0SD ({side}, {z:+.2f}SD / 15Δ) — โซนสังหารหลัก (Primary Kill Zone & Twin-Peaks Volga)",
            "stats": "กลับตัวคำใหญ่ >=$25 (500d) = 78.57% | ทับกำแพง OI + IV Crush ชนะสูงสุด 86.0%–91.4% (ไหลไป 3SD เพียง 25%)",
        }
    elif az < 2.5:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 5: ±2.0–2.5SD ({side}, {z:+.2f}SD / 15Δ–5Δ) — โซนตึงตัวขั้นสุด (Overextended Reversion Zone)",
            "stats": "แตะ 11.1%–15.0% ของวัน | ดีดกลับคำใหญ่ $25–$35 ชนะ 78.0%–88.0% (ทับ OI + IV Crush = 89.0%–91.4%+)",
        }
    return {
        "sd_ratio": z,
        "zone_name": f"โซนที่ 6: ±2.5–3.0SD+ ({side}, {z:+.2f}SD / 5Δ) — โซนปลายหาง (Tail Extremes 91.43% Peak Exhaustion)",
        "stats": "แตะ 3.7%–7.0% ของวัน (จบสวิง 96.3%) | รอหยุดไหลเกิด Absorption ชนะ 86.75% และกลับตัวคำใหญ่ >=$25 สูงถึง 91.43%–95.0%!",
    }


def _build_6_sd_zone_plans(
    sell_1sd: float, sell_2sd: float, sell_3sd: float,
    buy_1sd: float, buy_2sd: float, buy_3sd: float,
    diff: float, snap: HizeirisSnapshot,
) -> List[Dict[str, object]]:
    call_spot = round(snap.call_wall - diff, 2)
    put_spot = round(snap.put_wall - diff, 2)
    iv_crush = snap.vol_chg < 0

    def _conf_note(px: float, wall_px: float, wall_name: str, wall_fut: float) -> str:
        if abs(px - wall_px) <= 15.0:
            return f" 🔥 [ทับ {wall_name} Spot {wall_px:.2f} (Fut {wall_fut:.0f}) -> Confluence Win Rate พุ่งเป็น 86.0%–91.4%!]"
        return f" (ห่าง {wall_name} Spot {wall_px:.2f} อยู่ {abs(px - wall_px):.1f} จุด)"

    return [
        {
            "zone": "1) Zone 1SD SELL (sell spot 1sd — 25ΔC Drift Zone)",
            "entry_spot": f"{sell_1sd:,.2f}",
            "entry_fut": f"{sell_1sd + diff:,.2f}",
            "tp1": f"{sell_1sd - 10.0:,.2f}",
            "tp2": f"{sell_1sd - 15.0:,.2f}",
            "sl1": f"{sell_1sd + 12.5:,.2f}",
            "rec1_2x_tp": f"{sell_1sd + 25.0:,.2f}",
            "win_rate": f"ย่อสั้น $8–$12 ชนะ 70.0%–78.53% (เอเชีย 82.0%) | กลับตัว >=$25 แค่ 55.71%{_conf_note(sell_1sd, call_spot, 'Call Wall', snap.call_wall)}",
            "research_play": (
                "ดัก SELL เก็บสั้น +$8 ถึง +$12 จุด (TP1/TP2) แล้วล็อกกำไรทันที เพราะตามงานวิจัย 500 วัน VOL2VOL "
                "ราคาแตะ 1SD สูงถึง 70% ของวัน แต่มีโอกาสถึง 40% ที่จะไหลทะลุไปหา 2SD SELL "
                + ("(รอบนี้ Vol สดต่ำกว่าฐาน ช่วยหนุนแรงย่อ)" if iv_crush else "(ห้ามถือแช่หากเสา Call Intraday ดันราคาขึ้นมาจ่อ)")
            ),
        },
        {
            "zone": "2) Zone 2SD SELL (sell spot 2sd — 15ΔC Primary Kill Zone โซนหลัก)",
            "entry_spot": f"{sell_2sd:,.2f}",
            "entry_fut": f"{sell_2sd + diff:,.2f}",
            "tp1": f"{sell_2sd - 15.0:,.2f}",
            "tp2": f"{sell_2sd - 25.0:,.2f}",
            "sl1": f"{sell_2sd + 12.5:,.2f}",
            "rec1_2x_tp": f"{sell_2sd + 25.0:,.2f}",
            "sl2": f"{sell_2sd + 25.0:,.2f}",
            "rec2_6x_tp": f"{sell_2sd + 37.5:,.2f}",
            "win_rate": f"กลับตัวคำใหญ่ >=$25 ชนะ 78.57% (500d) ถึง 85.58% (50d){_conf_note(sell_2sd, call_spot, 'Call Wall', snap.call_wall)}",
            "research_play": (
                "โซนดัก SELL หลักประจำวัน! ราคาขยายตัวมาถึง 2SD เพียง 28% ของวัน และมีโอกาสหลุดไป 3SD เพียง 25% "
                "เป็นจุดพีคของ Twin-Peaks Volga (15Δ) ที่ขยาย Shadow Gamma +25%–30% บังคับให้ Dealer เทขาย Futures กดราคาสวิงกลับ +$15 ถึง +$25 จุด"
            ),
        },
        {
            "zone": "3) Zone 3SD SELL (sell spot 3sd — 5ΔC Tail Extreme โซนปลายหาง)",
            "entry_spot": f"{sell_3sd:,.2f}",
            "entry_fut": f"{sell_3sd + diff:,.2f}",
            "tp1": f"{sell_3sd - 20.0:,.2f}",
            "tp2": f"{sell_3sd - 31.34:,.2f}",
            "sl1": f"{sell_3sd + 12.5:,.2f}",
            "rec1_2x_tp": f"{sell_3sd + 25.0:,.2f}",
            "sl2": f"{sell_3sd + 25.0:,.2f}",
            "rec2_6x_tp": f"{sell_3sd + 37.5:,.2f}",
            "win_rate": "กลับตัวคำใหญ่ >=$25 สูงสุด 91.43% (500d) ถึง 95.0% หลังเกิด Absorption (จบสวิง 96.3% ของ 27d)",
            "research_play": (
                "โซนดักทุบคำใหญ่ +$25 ถึง +$35 จุด! โอกาสเกิดเพียง 3.7%–7.0% ของวัน "
                "กฎเหล็กจากวิจัย 50d: ห้ามหลับตาสวนไม้แรกทันทีที่แตะ (เพราะสวนเร็วชนะ <55%) ต้องรอให้ราคาหยุดไหลและเส้นเขียว Vol สดเริ่มหักหัวลง (IV Exhaustion) แล้วค่อยกด SELL รันกำไรกลับหา 2SD"
            ),
        },
        {
            "zone": "4) Zone 1SD BUY (buy spot 1sd — 25ΔP Drift Zone)",
            "entry_spot": f"{buy_1sd:,.2f}",
            "entry_fut": f"{buy_1sd + diff:,.2f}",
            "tp1": f"{buy_1sd + 10.0:,.2f}",
            "tp2": f"{buy_1sd + 15.0:,.2f}",
            "sl1": f"{buy_1sd - 12.5:,.2f}",
            "rec1_2x_tp": f"{buy_1sd - 25.0:,.2f}",
            "win_rate": f"เด้งสั้น $8–$12 ชนะ 70.0%–78.53% (เอเชีย 82.0%) | กลับตัว >=$25 แค่ 55.71%{_conf_note(buy_1sd, put_spot, 'Put Wall', snap.put_wall)}",
            "research_play": (
                "ดัก BUY เก็บเด้งสั้น +$8 ถึง +$12 จุด (TP1/TP2) หากจุดนี้ทับกับ Put Wall จะช่วยเพิ่มแรงรับของ Dealer "
                "แต่หากหลุด 1SD BUY ลงไปและเส้นเขียว Vol สดฝั่งซ้ายพุ่งชัน ให้ระวังการไหลต่อลงไปหา 2SD BUY (โอกาสไหลต่อ 40%)"
            ),
        },
        {
            "zone": "5) Zone 2SD BUY (buy spot 2sd — 15ΔP Primary Kill Zone โซนหลัก)",
            "entry_spot": f"{buy_2sd:,.2f}",
            "entry_fut": f"{buy_2sd + diff:,.2f}",
            "tp1": f"{buy_2sd + 15.0:,.2f}",
            "tp2": f"{buy_2sd + 25.0:,.2f}",
            "sl1": f"{buy_2sd - 12.5:,.2f}",
            "rec1_2x_tp": f"{buy_2sd - 25.0:,.2f}",
            "sl2": f"{buy_2sd - 25.0:,.2f}",
            "rec2_6x_tp": f"{buy_2sd - 37.5:,.2f}",
            "win_rate": f"กลับตัวคำใหญ่ >=$25 ชนะ 78.57% (500d) ถึง 85.58% (50d){_conf_note(buy_2sd, put_spot, 'Put Wall', snap.put_wall)}",
            "research_play": (
                "โซนดัก BUY หลักประจำวัน! ราคาลงมาถึงเพียง 28% ของวัน และมีโอกาสไหลไป 3SD เพียง 25% "
                "เมื่อราคาปะทะโซนนี้แล้วเสา Put ปิดทำกำไร จะเกิด IV Crush + Vanna Bounce บังคับให้ Dealer ซื้อ Futures คืนลากราคาดีดกลับ +$15 ถึง +$25 จุด"
            ),
        },
        {
            "zone": "6) Zone 3SD BUY (buy spot 3sd — 5ΔP Tail Extreme โซนปลายหาง)",
            "entry_spot": f"{buy_3sd:,.2f}",
            "entry_fut": f"{buy_3sd + diff:,.2f}",
            "tp1": f"{buy_3sd + 20.0:,.2f}",
            "tp2": f"{buy_3sd + 31.34:,.2f}",
            "sl1": f"{buy_3sd - 12.5:,.2f}",
            "rec1_2x_tp": f"{buy_3sd - 25.0:,.2f}",
            "sl2": f"{buy_3sd - 25.0:,.2f}",
            "rec2_6x_tp": f"{buy_3sd - 37.5:,.2f}",
            "win_rate": "กลับตัวคำใหญ่ >=$25 สูงสุด 91.43% (500d) ถึง 95.0% หลังเกิด Absorption (จบสวิง 96.3% ของ 27d)",
            "research_play": (
                "โซนดักช้อนคำใหญ่ +$25 ถึง +$35 จุด! โอกาสเกิดเพียง 3.7%–7.0% ของวัน "
                "รอจังหวะตื่นตระหนกจบลง (เกิดแท่งดูดซับแรงขาย Absorption + Put IV พีคแล้วหักหัวลง) แล้วเข้า BUY ถือรันกำไรกลับขึ้นหา 2SD BUY"
            ),
        },
    ]


def compute_exact_coordinates(snap: HizeirisSnapshot) -> Dict:
    fut = snap.futures_price
    open_fut = round(snap.daily_open_fut if _is_valid_gold_price(snap.daily_open_fut, fut) else fut, 2)
    diff = round(snap.diff, 2)
    spot = round(fut - diff, 2)
    open_spot = round(open_fut - diff, 2)
    fixed_dte = snap.fixed_dte
    open_iv = snap.open_iv

    # หากหน้าเว็บคำนวณตาราง SD · diff มาให้แล้ว ใช้ตัวเลขทศนิยม 2 ตำแหน่งจากตารางเว็บโดยตรง
    if snap.sd_diff_table and snap.sd_diff_table.get("range_1sd", 0) > 0:
        t = snap.sd_diff_table
        r1 = round(float(t["range_1sd"]), 2)
        r2 = round(float(t["range_2sd"]), 2)
        r3 = round(float(t["range_3sd"]), 2)
        buy_1sd = round(float(t["buy_1sd"]), 2)
        sell_1sd = round(float(t["sell_1sd"]), 2)
        buy_2sd = round(float(t["buy_2sd"]), 2)
        sell_2sd = round(float(t["sell_2sd"]), 2)
        buy_3sd = round(float(t["buy_3sd"]), 2)
        sell_3sd = round(float(t["sell_3sd"]), 2)
    else:
        # สูตรเดียวกับหน้าเว็บ Hizeiris เป๊ะ: 1SD = Open_Fut * (IV_ตอนเปิด / 100) * sqrt(0.8 / 365)
        r1 = round(open_fut * (open_iv / 100.0) * math.sqrt(fixed_dte / 365.0), 2)
        r2 = round(r1 * 2.0, 2)
        r3 = round(r1 * 3.0, 2)
        buy_1sd = round(open_spot - r1, 2)
        sell_1sd = round(open_spot + r1, 2)
        buy_2sd = round(open_spot - r2, 2)
        sell_2sd = round(open_spot + r2, 2)
        buy_3sd = round(open_spot - r3, 2)
        sell_3sd = round(open_spot + r3, 2)

    r05 = round(r1 * 0.5, 2)
    r15 = round(r1 * 1.5, 2)
    r25 = round(r1 * 2.5, 2)

    hourly_iv_pts = round(open_fut * (open_iv / 40.8 / 100.0), 2)
    daily_rule16_pct = round(open_iv / 16.0, 2)
    daily_rule16_pts = round(open_fut * (daily_rule16_pct / 100.0), 2)
    weekly_iv_pts = round(open_fut * (open_iv / 7.2 / 100.0), 2)

    vol_analysis = evaluate_vol_live_vs_base(
        snap.atm_iv, snap.vol_chg, snap.vol_settle_atm, open_iv
    )

    call_spot = round(snap.call_wall - diff, 2)
    put_spot = round(snap.put_wall - diff, 2)
    pain_spot = round(snap.max_pain - diff, 2)

    dist_call = round(call_spot - spot, 2)
    dist_put = round(spot - put_spot, 2)
    dist_pain = round(pain_spot - spot, 2)
    current_sd_info = classify_current_sd_zone_6(spot, open_spot, r1)
    six_zone_plans = _build_6_sd_zone_plans(
        sell_1sd, sell_2sd, sell_3sd, buy_1sd, buy_2sd, buy_3sd, diff, snap
    )

    return {
        "spot": spot,
        "open_fut": open_fut,
        "open_spot": open_spot,
        "open_iv": open_iv,
        "fixed_dte": fixed_dte,
        "diff": diff,
        "vol_live_vs_base": vol_analysis,
        "time_scaling_pts": {
            "hourly_div_40_8": hourly_iv_pts,
            "daily_rule16_pct": daily_rule16_pct,
            "daily_rule16_pts": daily_rule16_pts,
            "weekly_div_7_2": weekly_iv_pts,
        },
        "sd_table_exact": {
            "range_1sd": r1, "buy_1sd": buy_1sd, "sell_1sd": sell_1sd,
            "range_2sd": r2, "buy_2sd": buy_2sd, "sell_2sd": sell_2sd,
            "range_3sd": r3, "buy_3sd": buy_3sd, "sell_3sd": sell_3sd,
            "buy_1sd_fut": round(buy_1sd + diff, 2), "sell_1sd_fut": round(sell_1sd + diff, 2),
            "buy_2sd_fut": round(buy_2sd + diff, 2), "sell_2sd_fut": round(sell_2sd + diff, 2),
            "buy_3sd_fut": round(buy_3sd + diff, 2), "sell_3sd_fut": round(sell_3sd + diff, 2),
            "buy_05sd": round(open_spot - r05, 2), "sell_05sd": round(open_spot + r05, 2),
            "buy_15sd": round(open_spot - r15, 2), "sell_15sd": round(open_spot + r15, 2),
            "buy_25sd": round(open_spot - r25, 2), "sell_25sd": round(open_spot + r25, 2),
        },
        "walls_spot": {
            "call_wall_spot": call_spot, "call_wall_fut": snap.call_wall,
            "put_wall_spot": put_spot, "put_wall_fut": snap.put_wall,
            "max_pain_spot": pain_spot, "max_pain_fut": snap.max_pain,
        },
        "dist_call": dist_call,
        "dist_put": dist_put,
        "dist_max_pain": dist_pain,
        "oi_call_zone": evaluate_oi_zone_m2(dist_call),
        "oi_put_zone": evaluate_oi_zone_m2(dist_put),
        "current_sd_zone": current_sd_info,
        "six_zone_plans": six_zone_plans,
        "plan_c_exact": {
            "call_wall_break": round(call_spot + 12.5, 2), "call_squeeze_tp": round(call_spot + 25.0, 2),
            "put_wall_break": round(put_spot - 12.5, 2), "put_squeeze_tp": round(put_spot - 25.0, 2),
        },
    }


# ==============================================================================
# 3. เปิดแท็บ "SD · diff" ตั้งค่า DTE = 0.8 + SD:เปิด + diff:ใช้ และแคปภาพกราฟ
# ==============================================================================
def fetch_telegram_commands(token: str, chat_id: str) -> Tuple[Optional[float], Optional[float]]:
    if token == "YOUR_BOT_TOKEN_HERE" or chat_id == "YOUR_CHAT_ID_HERE":
        return None, None
    tg_diff, tg_open = None, None
    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        resp = requests.get(url, params={"offset": -20, "timeout": 5}, timeout=10)
        if resp.status_code == 200:
            for upd in reversed(resp.json().get("result", [])):
                msg = upd.get("message", {})
                if str(msg.get("chat", {}).get("id", "")) == str(chat_id):
                    txt = msg.get("text", "").strip()
                    if tg_diff is None:
                        m = re.match(r"^/?diff\s+([0-9]+(?:\.[0-9]+)?)$", txt, re.IGNORECASE)
                        if m:
                            tg_diff = float(m.group(1))
                    if tg_open is None:
                        m2 = re.match(r"^/?open\s+([0-9]+(?:\.[0-9]+)?)$", txt, re.IGNORECASE)
                        if m2 and _is_valid_gold_price(float(m2.group(1))):
                            tg_open = float(m2.group(1))
    except Exception:
        pass
    return tg_diff, tg_open


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
                    text: (el.parentElement ? el.parentElement.innerText : '').slice(0, 100)
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
                        el.style.height = '2300px';
                        el.style.maxHeight = 'none';
                    });
                }""",
                timeout=2000,
            )
        except Exception:
            pass


def _configure_sd_diff_tab_on_page(page, fixed_dte: float) -> None:
    """
    ตั้งค่าหน้าแท็บ SD · diff บนเว็บ Hizeiris ให้ตรงกับภาพต้นแบบของผู้ใช้:
    1) เปิดปุ่ม 'SD: เปิด' และ 'diff: ใช้'
    2) กรอกช่อง DTE = 0.8 แล้วสั่งคำนวณตาราง 1sd/2sd/3sd + ตีเส้นม่วงบนกราฟทันที
    """
    for frame in page.frames:
        try:
            frame.locator("body").evaluate(
                f"""body => {{
                    // 1. เปิดปุ่ม SD: เปิด และ diff: ใช้ หากยังปิดอยู่
                    Array.from(document.querySelectorAll('button, span, div')).forEach(el => {{
                        const t = (el.innerText || '').trim();
                        if (t === 'SD: ปิด') el.click();
                        if (t === 'diff: ปิด' || t === 'diff: ไม่ใช้') el.click();
                    }});
                    // 2. ค้นหาช่อง input DTE แล้วใส่ค่า {fixed_dte}
                    Array.from(document.querySelectorAll('input')).forEach(inp => {{
                        const parentTxt = (inp.parentElement ? inp.parentElement.innerText : '') + ' ' +
                                          (inp.previousElementSibling ? inp.previousElementSibling.innerText : '');
                        const val = parseFloat(inp.value);
                        if (/DTE/i.test(parentTxt) || (!isNaN(val) && val > 0.01 && val < 5.0 && inp.value !== '2.5')) {{
                            if (/DTE/i.test(parentTxt)) {{
                                inp.value = '{fixed_dte}';
                                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }}
                        }}
                    }});
                }}""",
                timeout=2500,
            )
        except Exception:
            pass


def capture_hizeiris_web(url: str, profile_dir: str, fixed_dte: float = 0.8) -> Dict[str, object]:
    from playwright.sync_api import sync_playwright

    os.makedirs(profile_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    shot_sd_diff = f"hizeiris_1_sddiff_dte08_{ts}.png"
    shot_cme_vol = f"hizeiris_2_cme_vol_{ts}.png"
    shot_oi_all = f"hizeiris_3_oi_all_{ts}.png"
    screenshots: List[str] = []
    photo_captions: List[Tuple[str, str]] = []

    print("🌐 กำลังเปิดหน้าเว็บ Hizeiris...")
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=True,
            viewport={"width": 1600, "height": 2300},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(15000)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(7000)
        _expand_iframes_for_full_charts(page)
        page.wait_for_timeout(800)

        text_plan_tab = _extract_frame_texts_and_inputs(page)

        # 1) กดเปิดแท็บ "SD · diff" ก่อนเป็นอันดับแรก เพื่อล็อก DTE = 0.8 และให้เส้น SD สีม่วงขึ้นบนกราฟ!
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
            page.wait_for_timeout(1500)
            _configure_sd_diff_tab_on_page(page, fixed_dte)
            page.wait_for_timeout(1500)
            _expand_iframes_for_full_charts(page)
            page.screenshot(path=shot_sd_diff, full_page=True, timeout=15000)
            screenshots.append(shot_sd_diff)
            photo_captions.append((shot_sd_diff, f"📐 [ภาพที่ 1] แท็บ SD · diff (Fix ราคาเปิด + DTE {fixed_dte}) & กราฟ Intraday + OI"))
            text_diff_tab = _extract_frame_texts_and_inputs(page)
            print(f"✅ แคปภาพที่ 1 (แท็บ SD · diff ล็อก DTE = {fixed_dte}) สำเร็จ!")

        # 2) กดเปิดแท็บ "CME · Vol" เพื่อดู ATM vol สด, Vol Chg และตาราง Vol จาก CME (โดยเส้น SD สีม่วงยังค้างบนกราฟ)
        for frame in page.frames:
            try:
                loc_cme = frame.get_by_text(re.compile(r"CME\s*[·\-\.]?\s*Vol", re.IGNORECASE))
                if loc_cme.count() > 0:
                    loc_cme.first.click(timeout=2500)
                    page.wait_for_timeout(1500)
                    break
            except Exception:
                pass

        _expand_iframes_for_full_charts(page)
        page.screenshot(path=shot_cme_vol, full_page=True, timeout=15000)
        screenshots.append(shot_cme_vol)
        photo_captions.append((shot_cme_vol, "📊 [ภาพที่ 2] กราฟ Intraday Volume + Open Interest (OI) & ตาราง CME · Vol"))
        text_main = _extract_frame_texts_and_inputs(page)
        print("✅ แคปภาพที่ 2 (Intraday + OI + CME·Vol) สำเร็จ!")

        # 3) กดเปิดแท็บ "OI ทุกสัญญา"
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
            f"[VIEW 1: SD · diff (Fixed DTE {fixed_dte})]\n{text_diff_tab}\n\n"
            f"[VIEW 2: CME · Vol]\n{text_main}\n\n"
            f"[VIEW 0: แผนเทรด]\n{text_plan_tab}\n\n"
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


def parse_snapshot_from_dom_text(
    raw_text: str,
    fallback_diff: float,
    tg_diff: Optional[float],
    tg_open: Optional[float],
    prev_state: Optional[Dict],
) -> HizeirisSnapshot:
    contract_m = re.search(r"\b(OG[A-Z0-9]{2,5}|G[0-9][A-Z0-9]{2,5}|GC[A-Z0-9]{2,5})\b", raw_text)
    contract_name = contract_m.group(1) if contract_m else "G3MX6"

    round_m = re.search(r"(รอบ\s*[0-9]+/[0-9]+)", raw_text)
    round_str = round_m.group(1) if round_m else "รอบล่าสุด"

    fut_px = _parse_float(r"Futures\s*([2-5][0-9]{3}(?:\.[0-9]+)?)", raw_text, 0.0)
    if fut_px == 0.0:
        fut_px = _parse_float(r"ราคากลาง\s*([2-5],?[0-9]{3}(?:\.[0-9]+)?)", raw_text, 3864.8)

    dte = _parse_float(r"DTE\s*([0-9]+\.?[0-9]*)", raw_text, 0.38)

    # 1. ดึงข้อมูลจากตารางในแท็บ "SD · diff" ตามภาพต้นแบบของผู้ใช้:
    # "ราคาเปิด 3869.7 · GCZ26.CMX" หรือ "ราคาเปิด 3,869.70"
    sd_diff_open_fut = _parse_float(r"ราคาเปิด\s*([2-5],?[0-9]{3}\.[0-9]+)", raw_text, 0.0)
    # "1SD = 3869.7 × 17.3% × √(0.8/365) = 31.34 · IV ตอนเปิด 06:30"
    formula_m = re.search(
        r"1SD\s*=\s*([2-5],?[0-9]{3}(?:\.[0-9]+)?)\s*[×x*]\s*([0-9]+\.[0-9]+)%?\s*[×x*]\s*√\(([0-9.]+)/365\)\s*=\s*([0-9]+\.[0-9]+)",
        raw_text,
    )
    open_iv = 0.0
    if formula_m:
        if sd_diff_open_fut == 0.0:
            sd_diff_open_fut = float(formula_m.group(1).replace(",", ""))
        open_iv = float(formula_m.group(2))

    # ดึงค่า diff ตลาดจริง และค่าปัด: "diff ตลาดจริง 2.97 -> ปัด 2.5"
    raw_mkt_diff = _parse_float(r"diff\s*ตลาดจริง\s*([0-9]+\.[0-9]+)", raw_text, 0.0)
    rounded_diff = _parse_float(r"ปัด\s*([0-9]+\.?[0-9]*)", raw_text, 0.0)
    if rounded_diff == 0.0:
        rounded_diff = _parse_float(r"\bdiff\s+([0-9]+\.[0-9]+)\s+ราคา\s*spot", raw_text, 0.0)
    if rounded_diff == 0.0:
        rounded_diff = _parse_float(r"\bdiff\s*[:=]?\s*([0-9]+\.?[0-9]*)", raw_text, 0.0)

    if tg_diff is not None:
        final_diff, diff_src = round(tg_diff, 2), "ตั้งค่าจาก Telegram /diff"
    elif 0.1 <= rounded_diff <= 50.0:
        final_diff = round(rounded_diff, 2)
        diff_src = f"แท็บ SD·diff (ตลาดจริง {raw_mkt_diff:.2f} -> ปัด {final_diff:.2f})" if raw_mkt_diff > 0 else "อ่านจากแท็บ SD·diff บนเว็บ"
    else:
        final_diff, diff_src = fallback_diff, f"ค่ามาตรฐาน ({fallback_diff})"

    # ดึงตาราง 1sd / 2sd / 3sd (range | buy spot | sell spot) จากแท็บ SD · diff
    sd_diff_table = None
    row1_m = re.search(r"1sd\s+([0-9]+\.[0-9]+)\s+([2-5],?[0-9]{3}\.[0-9]+)\s+([2-5],?[0-9]{3}\.[0-9]+)", raw_text, re.IGNORECASE)
    row2_m = re.search(r"2sd\s+([0-9]+\.[0-9]+)\s+([2-5],?[0-9]{3}\.[0-9]+)\s+([2-5],?[0-9]{3}\.[0-9]+)", raw_text, re.IGNORECASE)
    row3_m = re.search(r"3sd\s+([0-9]+\.[0-9]+)\s+([2-5],?[0-9]{3}\.[0-9]+)\s+([2-5],?[0-9]{3}\.[0-9]+)", raw_text, re.IGNORECASE)
    if row1_m and row2_m and row3_m:
        sd_diff_table = {
            "range_1sd": float(row1_m.group(1)),
            "buy_1sd": float(row1_m.group(2).replace(",", "")),
            "sell_1sd": float(row1_m.group(3).replace(",", "")),
            "range_2sd": float(row2_m.group(1)),
            "buy_2sd": float(row2_m.group(2).replace(",", "")),
            "sell_2sd": float(row2_m.group(3).replace(",", "")),
            "range_3sd": float(row3_m.group(1)),
            "buy_3sd": float(row3_m.group(2).replace(",", "")),
            "sell_3sd": float(row3_m.group(3).replace(",", "")),
        }

    # ดึง ATM vol สด และ Vol Chg จากแท็บ CME · Vol
    atm_live = _parse_float(r"ATM\s*vol\s*([0-9]+\.?[0-9]*)", raw_text, 0.0)
    if atm_live == 0.0:
        atm_live = open_iv if open_iv > 0 else _parse_float(r"\bIV%?\s*([0-9]+\.[0-9]+)", raw_text, 17.3)
    if open_iv == 0.0:
        open_iv = atm_live

    vol_chg = _parse_float(r"Vol\s*Chg\s*([+\-]?[0-9]+\.?[0-9]*)", raw_text, 0.0)
    future_chg = _parse_float(r"Future\s*Chg\s*([+\-]?[0-9]+\.?[0-9]*)", raw_text, 0.0)
    vol_settle_atm = round(atm_live - vol_chg, 2)

    # ล็อกราคาเปิดของวัน (ใช้ตาราง SD · diff ตอนเช้า 06:30 เป็นอันดับ 1 และล็อกจนจบซีรีส์ของวัน)
    today_str = datetime.now().strftime("%Y-%m-%d")
    prev_open = float(prev_state.get("daily_open_fut", 0.0)) if prev_state else 0.0
    prev_open_iv = float(prev_state.get("open_iv", 0.0)) if prev_state else 0.0

    if tg_open is not None and _is_valid_gold_price(tg_open, fut_px):
        daily_open_fut, open_src = round(tg_open, 2), "ตั้งค่าจาก Telegram /open"
    elif _is_valid_gold_price(sd_diff_open_fut, fut_px):
        daily_open_fut, open_src = round(sd_diff_open_fut, 2), "ตาราง SD·diff (IV ตอนเปิด 06:30)"
    elif prev_state and prev_state.get("trading_day") == today_str and _is_valid_gold_price(prev_open, fut_px):
        daily_open_fut, open_src = round(prev_open, 2), f"ล็อกราคาเปิดของวัน ({prev_open:.2f})"
        if prev_open_iv > 5.0:
            open_iv = prev_open_iv
    elif future_chg != 0.0 and _is_valid_gold_price(fut_px - future_chg, fut_px):
        daily_open_fut, open_src = round(fut_px - future_chg, 2), "คำนวณจาก Futures - Future Chg"
    else:
        daily_open_fut, open_src = round(fut_px, 2), "ล็อกราคาเปิดรอบแรกของวัน"

    daily_open_spot = round(daily_open_fut - final_diff, 2)

    intra_m = re.search(r"Intraday\s+C\s*([0-9,]+)\s*[·•]?\s*P\s*([0-9,]+)\s*[·•]?\s*P/C\s*([0-9.]+)", raw_text, re.IGNORECASE)
    if intra_m:
        intraday_c = int(intra_m.group(1).replace(",", ""))
        intraday_p = int(intra_m.group(2).replace(",", ""))
        intraday_pc = float(intra_m.group(3))
    else:
        intraday_c, intraday_p = 0, 0
        intraday_pc = _parse_float(r"Intraday.*?P/C\s*([0-9]+\.?[0-9]*)", raw_text, 0.58)

    oi_m = re.search(r"Open Interest\s+C\s*([0-9,]+)\s*[·•]?\s*P\s*([0-9,]+)\s*[·•]?\s*P/C\s*([0-9.]+)", raw_text, re.IGNORECASE)
    if oi_m:
        oi_c = int(oi_m.group(1).replace(",", ""))
        oi_p = int(oi_m.group(2).replace(",", ""))
        oi_pc = float(oi_m.group(3))
    else:
        oi_c, oi_p = 0, 0
        oi_pc = _parse_float(r"Open Interest.*?P/C\s*([0-9]+\.?[0-9]*)", raw_text, 1.70)

    churn_m = re.search(r"Churn\s*(C\s*[0-9.]+\s*[·•/]?\s*P\s*[0-9.]+)", raw_text, re.IGNORECASE)
    churn_str = churn_m.group(1) if churn_m else "N/A"

    max_pain = _parse_float(r"Max\s*Pain\s*([2-5][0-9]{3})", raw_text, round(fut_px, -1))

    call_wall = _parse_float(r"Call\s*wall\s*([2-5][0-9]{3})", raw_text, 0.0)
    call_oi_cnt = ""
    cw_plan_m = re.search(r"Call\s+[0-9.]+\s*\(([2-5][0-9]{3})\)[^\n]*?OI\s*([0-9,]+)", raw_text, re.IGNORECASE)
    if cw_plan_m:
        if call_wall == 0.0:
            call_wall = float(cw_plan_m.group(1))
        call_oi_cnt = f"OI {cw_plan_m.group(2)} สัญญา"
    if call_wall == 0.0:
        call_wall = round(fut_px + 60, -1)

    put_wall = _parse_float(r"Put\s*wall\s*([2-5][0-9]{3})", raw_text, 0.0)
    put_oi_cnt = ""
    pw_plan_m = re.search(r"Put\s+[0-9.]+\s*\(([2-5][0-9]{3})\)[^\n]*?OI\s*([0-9,]+)", raw_text, re.IGNORECASE)
    if pw_plan_m:
        if put_wall == 0.0:
            put_wall = float(pw_plan_m.group(1))
        put_oi_cnt = f"OI {pw_plan_m.group(2)} สัญญา"
    if put_wall == 0.0:
        put_wall = round(fut_px - 15, -1)

    return HizeirisSnapshot(
        contract_name=contract_name,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        round_str=round_str,
        futures_price=fut_px,
        future_chg=future_chg,
        daily_open_fut=daily_open_fut,
        daily_open_spot=daily_open_spot,
        open_iv=open_iv,
        open_source=open_src,
        diff=final_diff,
        raw_market_diff=raw_mkt_diff if raw_mkt_diff > 0 else None,
        diff_source=diff_src,
        dte=dte,
        fixed_dte=CONFIG["FIXED_DTE"],
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
        put_wall_pc=put_oi_cnt or f"Put wall {int(put_wall)}",
        call_wall=call_wall,
        call_wall_pc=call_oi_cnt or f"Call wall {int(call_wall)}",
        max_pain=max_pain,
        sd_diff_table=sd_diff_table,
    )


FULL_RESEARCH_SYNTHESIS_PROMPT = """
คุณคือ "Quantitative Derivatives & Market Microstructure Analyst (Mtraders Masterclass & AI CME Analyst)"
จงวิเคราะห์ข้อมูล **กรอบ Fixed SD ตอนเช้าจากแท็บ SD · diff (ราคาเปิด + IV ตอนเปิด 06:30 + DTE = 0.8 ใช้จนจบซีรีส์ของวัน)** ร่วมกับข้อมูล Intraday Volume, Open Interest และ Vol สด vs Vol Settle ที่ Python คำนวณให้แล้วด้านล่างนี้ โดยห้ามตกหล่นแม้แต่หัวข้อเดียว (ห้ามใช้ ICT/SMC/EA และห้ามพูดเรื่องขนาดพอร์ต/Kelly/Lot/1.0 Unit):

[บังคับแสดงผลครบทั้ง 6 ส่วนตามโครงสร้างวิจัยนี้เท่านั้น]
### ส่วนที่ 1: สรุปพารามิเตอร์, ตาราง Fixed SD ตอนเช้า (DTE 0.8), Vol สด vs Vol Settle, Intraday & OI
### ส่วนที่ 2: ตารางพิกัด Fixed SD ประจำวัน (range | buy spot | sell spot ของ 1sd, 2sd, 3sd) เทียบกับ Call Wall / Put Wall / Max Pain
### ส่วนที่ 3: การประเมินความได้เปรียบเชิงสถิติ (% Statistical Edge Scorecard: ฝั่ง Buy vs ฝั่ง Sell)
### ส่วนที่ 4: แผนปฏิบัติการเทรดเชิงปริมาณครบทั้ง 6 โซน Fixed SD (1SD SELL, 2SD SELL, 3SD SELL, 1SD BUY, 2SD BUY, 3SD BUY) & วิธีเล่นตามงานวิจัย + Supa888 Recovery
### ส่วนที่ 5: จุดเฝ้าระวังการเปลี่ยนสถานะเชิงปริมาณในรอบถัดไป (Key Quant Triggers)
### ส่วนที่ 6: 📌 กล่องบันทึกความจำสถานะประจำซีรีส์ (SESSION MEMORY STATE TRACKER)
"""


def build_deterministic_fallback_report(snap: HizeirisSnapshot, exact_calc: Dict) -> str:
    tb = exact_calc["sd_table_exact"]
    ws = exact_calc["walls_spot"]
    zones = exact_calc["six_zone_plans"]
    pc = exact_calc["plan_c_exact"]
    ts = exact_calc["time_scaling_pts"]
    va = exact_calc["vol_live_vs_base"]

    zone_lines = []
    for z in zones:
        rec2_line = f" | SL2 (-25): `{z['sl2']}` (รวบทบ 6x -> `{z['rec2_6x_tp']}`)" if "sl2" in z else ""
        zone_lines.append(
            f"• *{z['zone']}*\n"
            f"  พิกัดเข้า Spot: `{z['entry_spot']}` (Fut `{z['entry_fut']}`)\n"
            f"  เป้าทำกำไร: TP1 `{z['tp1']}` / TP2 `{z['tp2']}`\n"
            f"  แก้พอร์ต Supa888: SL1 (-12.5) `{z['sl1']}` (ทบ 2x -> TP `{z['rec1_2x_tp']}`){rec2_line}\n"
            f"  สถิติวิจัย: _{z['win_rate']}_\n"
            f"  การเล่นตามวิจัย: _{z['research_play']}_"
        )

    return (
        f"📊 *ส่วนที่ 1: สรุปพารามิเตอร์ & กรอบวิจัย Fixed SD ตอนเช้า (ใช้จนจบซีรีส์)* ({snap.timestamp})\n"
        f"• สัญญา: `{snap.contract_name}` ({snap.round_str}) | DTE จริง: `{snap.dte}` | *ล็อก DTE วิจัย*: `{snap.fixed_dte}`\n"
        f"• *ราคาเปิด Futures (06:30)*: `{exact_calc['open_fut']:,.2f}` | *diff*: `{snap.diff:.2f}` _({snap.diff_source})_ -> *ราคาเปิด Spot*: `{exact_calc['open_spot']:,.2f}`\n"
        f"• *สมการ 1SD ประจำวัน*: `{exact_calc['open_fut']} × {exact_calc['open_iv']}% × √({snap.fixed_dte}/365) = {tb['range_1sd']:.2f}` จุด\n"
        f"• *ราคาปัจจุบัน*: Futures `{snap.futures_price:,.1f}` -> *XAUUSD (Spot)* `{exact_calc['spot']:,.2f}`\n"
        f"• *Vol สด (เส้นเขียว)*: `{va['vol_live']}` vs *Vol Settle (เส้นประแดง)*: `{va['vol_base_settle']}` (`Vol Chg {va['vol_chg']}` | IV ตอนเปิด `{va['open_iv']}`)\n"
        f"  -> _{va['regime']}: {va['impact']}_\n"
        f"• *Intraday Volume*: C `{snap.intraday_c:,}` / P `{snap.intraday_p:,}` (`P/C {snap.intraday_pc:.2f}`) | *Open Interest*: C `{snap.oi_c:,}` / P `{snap.oi_p:,}` (`P/C {snap.oi_pc:.2f}`) | Churn: `{snap.churn_str}`\n"
        f"• ตำแหน่งราคาปัจจุบันในกรอบวัน: {exact_calc['current_sd_zone']['zone_name']}\n"
        f"  -> _{exact_calc['current_sd_zone']['stats']}_\n\n"
        f"📐 *ส่วนที่ 2: ตารางกรอบ Fixed SD ประจำวัน (แท็บ SD · diff — วัดจากราคา Spot)*\n"
        f"• `ราคาเปิด`: Fut `{exact_calc['open_fut']:,.2f}` | diff `{snap.diff:.2f}` | *ราคา Spot ตอนเปิด*: `{exact_calc['open_spot']:,.2f}`\n"
        f"• *1SD* (range `{tb['range_1sd']:.2f}`): *buy spot* = `{tb['buy_1sd']:,.2f}` (Fut `{tb['buy_1sd_fut']:,.2f}`) | *sell spot* = `{tb['sell_1sd']:,.2f}` (Fut `{tb['sell_1sd_fut']:,.2f}`)\n"
        f"• *2SD* (range `{tb['range_2sd']:.2f}`): *buy spot* = `{tb['buy_2sd']:,.2f}` (Fut `{tb['buy_2sd_fut']:,.2f}`) | *sell spot* = `{tb['sell_2sd']:,.2f}` (Fut `{tb['sell_2sd_fut']:,.2f}`)\n"
        f"• *3SD* (range `{tb['range_3sd']:.2f}`): *buy spot* = `{tb['buy_3sd']:,.2f}` (Fut `{tb['buy_3sd_fut']:,.2f}`) | *sell spot* = `{tb['sell_3sd']:,.2f}` (Fut `{tb['sell_3sd_fut']:,.2f}`)\n"
        f"• *กำแพง OI & Max Pain ปัจจุบัน (หัก diff {snap.diff:.2f})*:\n"
        f"  - Call Wall ({snap.call_wall_pc}): Spot `{ws['call_wall_spot']:,.2f}` (Fut `{ws['call_wall_fut']:,.0f}`)\n"
        f"  - Max Pain: Spot `{ws['max_pain_spot']:,.2f}` (Fut `{ws['max_pain_fut']:,.0f}`)\n"
        f"  - Put Wall ({snap.put_wall_pc}): Spot `{ws['put_wall_spot']:,.2f}` (Fut `{ws['put_wall_fut']:,.0f}`)\n\n"
        f"⚖️ *ส่วนที่ 3: Statistical Edge Scorecard (เทียบระยะ Spot ปัจจุบันหากำแพง OI)*\n"
        f"• ฝั่งขึ้นหา Call Wall (ห่าง `{exact_calc['dist_call']:+.2f}`): {exact_calc['oi_call_zone']['light']} {exact_calc['oi_call_zone']['zone']} -> แตะ `{exact_calc['oi_call_zone']['touch']}` | +$25 `{exact_calc['oi_call_zone']['win25']}`\n"
        f"• ฝั่งลงหา Put Wall (ห่าง `{-exact_calc['dist_put']:+.2f}`): {exact_calc['oi_put_zone']['light']} {exact_calc['oi_put_zone']['zone']} -> แตะ `{exact_calc['oi_put_zone']['touch']}` | +$25 `{exact_calc['oi_put_zone']['win25']}`\n\n"
        f"🎯 *ส่วนที่ 4: แผนเทรด 6 โซน Fixed SD (1SD/2SD/3SD SELL & BUY) + การเล่นตามงานวิจัย*\n"
        + "\n\n".join(zone_lines)
        + f"\n\n• *แผน C (สำรอง — Breakout / Gamma Squeeze)*:\n"
        f"  ทะลุ Call Wall ยืนเหนือ `{pc['call_wall_break']:,.2f}` -> Follow Buy เป้า `{pc['call_squeeze_tp']:,.2f}` | หลุด Put Wall ยืนต่ำกว่า `{pc['put_wall_break']:,.2f}` -> Follow Sell เป้า `{pc['put_squeeze_tp']:,.2f}`\n\n"
        f"🔔 *ส่วนที่ 5: Key Quant Triggers รอบถัดไป*: เช็กการเคลื่อนที่ของเสา Intraday (`P/C {snap.intraday_pc:.2f}`) เมื่อราคาเข้าใกล้ `1SD buy ({tb['buy_1sd']:,.2f})` / `1SD sell ({tb['sell_1sd']:,.2f})` และการกดตัวของเส้นเขียว Vol สดเทียบเส้นประแดง\n"
        f"📌 *ส่วนที่ 6: SESSION MEMORY STATE*: `OpenFut {exact_calc['open_fut']:.2f} | OpenSpot {exact_calc['open_spot']:.2f} | IV {exact_calc['open_iv']}% | DTE {snap.fixed_dte} | 1SD {tb['range_1sd']:.2f} | Buy1/2/3: {tb['buy_1sd']:.2f}/{tb['buy_2sd']:.2f}/{tb['buy_3sd']:.2f} | Sell1/2/3: {tb['sell_1sd']:.2f}/{tb['sell_2sd']:.2f}/{tb['sell_3sd']:.2f}`"
    )


def synthesize_full_report(snap: HizeirisSnapshot, exact_calc: Dict, capture: Optional[Dict[str, object]],
                           api_key: str, prev_state: Optional[Dict]) -> str:
    if CONFIG["DRY_RUN"] or api_key == "YOUR_GEMINI_API_KEY_HERE" or not capture:
        return build_deterministic_fallback_report(snap, exact_calc)

    context_json = json.dumps({
        "snapshot": snap.__dict__,
        "sd_diff_morning_lock_and_6_zones": exact_calc,
        "previous_state": prev_state,
    }, ensure_ascii=False, indent=2)

    prompt = (
        f"{FULL_RESEARCH_SYNTHESIS_PROMPT}\n\n"
        f"[ข้อมูลตาราง SD·diff ตอนเช้า (DTE 0.8) และแผน 6 โซนที่ Python คำนวณตรงกับหน้าเว็บ 100%]:\n{context_json}"
    )

    try:
        return call_gemini_rest_fast(
            api_key=api_key,
            prompt_text=prompt,
            image_paths=capture["screenshot_paths"][:1],
            timeout_sec=30,
        )
    except Exception:
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
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_state(path: str, state: Dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def run_once():
    prev_state = load_state(CONFIG["STATE_FILE"])
    tg_diff, tg_open = fetch_telegram_commands(CONFIG["TELEGRAM_BOT_TOKEN"], CONFIG["TELEGRAM_CHAT_ID"])
    fallback_diff = CONFIG["FALLBACK_DIFF"]

    if CONFIG["DRY_RUN"]:
        cap = None
        # ทดสอบด้วยข้อมูลจริงจากภาพหน้าจอแท็บ SD · diff ของผู้ใช้ (3869.7, IV 17.3%, DTE 0.8, diff 2.5)
        snap = HizeirisSnapshot(
            contract_name="G3MX6",
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            round_str="รอบ 121/121",
            futures_price=3864.8,
            future_chg=-4.9,
            daily_open_fut=3869.70,
            daily_open_spot=3867.20,
            open_iv=17.30,
            open_source="ตาราง SD·diff (IV ตอนเปิด 06:30)",
            diff=tg_diff or 2.50,
            raw_market_diff=2.97,
            diff_source="แท็บ SD·diff (ตลาดจริง 2.97 -> ปัด 2.50)",
            dte=0.38,
            fixed_dte=CONFIG["FIXED_DTE"],
            atm_iv=17.30,
            vol_chg=0.00,
            vol_settle_atm=17.30,
            vol_curve_compare="เส้นเขียว Vol สด เกาะสมดุลกับเส้นประแดง Vol Settle",
            intraday_c=634,
            intraday_p=370,
            intraday_pc=0.58,
            oi_c=622,
            oi_p=1058,
            oi_pc=1.70,
            churn_str="C 0.59 / P 0.45",
            put_wall=3850.0,
            put_wall_pc="Put wall 3850",
            call_wall=3925.0,
            call_wall_pc="Call wall 3925",
            max_pain=3900.0,
            sd_diff_table={
                "range_1sd": 31.34, "buy_1sd": 3835.86, "sell_1sd": 3898.54,
                "range_2sd": 62.68, "buy_2sd": 3804.52, "sell_2sd": 3929.88,
                "range_3sd": 94.02, "buy_3sd": 3773.18, "sell_3sd": 3961.22,
            },
        )
    else:
        cap = capture_hizeiris_web(CONFIG["HIZEIRIS_URL"], CONFIG["CHROME_PROFILE_DIR"], CONFIG["FIXED_DTE"])
        if cap and cap.get("photo_captions"):
            send_telegram_photos(
                cap["photo_captions"],
                CONFIG["TELEGRAM_BOT_TOKEN"],
                CONFIG["TELEGRAM_CHAT_ID"],
            )
        snap = parse_snapshot_from_dom_text(
            str(cap.get("raw_text") or ""), fallback_diff, tg_diff, tg_open, prev_state
        )
        print(
            f"✅ ดึงตาราง SD·diff สำเร็จ: {snap.contract_name} | OpenFut {snap.daily_open_fut:.2f} | "
            f"OpenSpot {snap.daily_open_spot:.2f} | IV {snap.open_iv}% | DTE {snap.fixed_dte} | Diff {snap.diff:.2f}"
        )

    exact_calc = compute_exact_coordinates(snap)
    report_text = synthesize_full_report(snap, exact_calc, cap, CONFIG["GEMINI_API_KEY"], prev_state)
    send_telegram_parts(split_telegram_chunks(report_text), CONFIG["TELEGRAM_BOT_TOKEN"], CONFIG["TELEGRAM_CHAT_ID"])

    tb = exact_calc["sd_table_exact"]
    save_state(CONFIG["STATE_FILE"], {
        "trading_day": datetime.now().strftime("%Y-%m-%d"),
        "timestamp": snap.timestamp,
        "daily_open_fut": exact_calc["open_fut"],
        "daily_open_spot": exact_calc["open_spot"],
        "open_iv": exact_calc["open_iv"],
        "fixed_dte": snap.fixed_dte,
        "range_1sd": tb["range_1sd"],
        "buy_1sd": tb["buy_1sd"], "sell_1sd": tb["sell_1sd"],
        "buy_2sd": tb["buy_2sd"], "sell_2sd": tb["sell_2sd"],
        "buy_3sd": tb["buy_3sd"], "sell_3sd": tb["sell_3sd"],
        "futures_price": snap.futures_price,
        "spot_price": exact_calc["spot"],
        "diff": snap.diff,
        "put_wall": snap.put_wall,
        "call_wall": snap.call_wall,
        "max_pain": snap.max_pain,
    })
    print("🎉 ส่งรูปกราฟและตารางกรอบวิจัย SD·diff (DTE 0.8) ครบ 6 โซนเข้า Telegram เสร็จสมบูรณ์!")


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
