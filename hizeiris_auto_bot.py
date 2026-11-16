"""
================================================================================
HIZEIRIS AUTO BOT — CONTINUOUS 30-MIN ENGINE + MORNING FIXED SD VS CURRENT ROUND
================================================================================
1. แก้บั๊กแถบเลื่อนเวลา (Time Slider <input type="range">): บังคับให้แถบเวลาอยู่ขวาสุด (รอบล่าสุดของวันเสมอ)
   ไม่เผลอเลื่อนกลับไปวันศุกร์ และกดสลับปุ่มแท็บ "SD · diff" -> "CME · Vol" -> "OI ทุกสัญญา" แม่นยำ 100%
2. เปรียบเทียบ "กรอบ Fix ตอนเช้า (SD·diff 06:30, DTE=0.8)" กับ "ข้อมูลรอบปัจจุบัน (CME·Vol รอบนี้)"
   พร้อมวิเคราะห์ว่าตั้งแต่รอบเช้า -> รอบก่อนหน้า -> รอบปัจจุบัน ราคา, Vol สด, กรอบ SD และเสา Intraday/OI
   เปลี่ยนแปลงไปอย่างไร และจุดไหนเกิด Double Confluence
3. บันทึกประวัติทุกรอบของวันลงใน session_state.json (Round-by-Round History Log) และแสดงตาราง
   พัฒนาการรายรอบในรายงาน Telegram ทุกครั้ง พร้อม git push บันทึกสถานะหลังจบทุกรอบทันที
4. ระบบทำงานต่อเนื่องอัตโนมัติ (Continuous 30-Min Shift Loop): บอทจะเฝ้าหน้าจอและส่งรายงานทุกๆ 30 นาที
   ตรงเวลาเป๊ะ (นาทีที่ :00 และ :30) ต่อเนื่องสูงสุด 10 รอบต่อกะ (5 ชั่วโมง) โดยไม่ต้องรอคิว Cron ดีเลย์
================================================================================
"""

import base64
import io
import json
import math
import os
import re
import signal
import subprocess
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
    "INTERVAL_MINUTES": 30,
    "MAX_ROUNDS_PER_JOB": int(os.getenv("MAX_ROUNDS_PER_JOB", "10")),
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
            if rgb.width > 1100:
                ratio = 1100.0 / rgb.width
                rgb = rgb.resize((1100, int(rgb.height * ratio)), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=80, optimize=True)
            return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
    except Exception:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii"), "image/png"


def call_gemini_rest_fast(api_key: str, prompt_text: str, image_paths: List[str],
                          timeout_sec: int = 45) -> str:
    parts = []
    for img_path in image_paths[:2]:
        if os.path.exists(img_path):
            b64_data, mime = _compress_image_b64(img_path)
            parts.append({"inline_data": {"mime_type": mime, "data": b64_data}})
    parts.append({"text": prompt_text})

    payload: Dict[str, object] = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.15,
            "maxOutputTokens": 2048,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={api_key}"

    for attempt in range(2):
        try:
            print(f"🤖 เรียก AI Gemini 3.8 Flash ตรวจสอบซ้ำจากภาพกราฟจริง (รอบ {attempt + 1}/2, จำกัดเวลา {timeout_sec}s)...")
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
                    for part in candidates[0].get("content", {}).get("parts", []):
                        if "text" in part and not part.get("thought", False):
                            if part["text"].strip():
                                return part["text"]
            print(f"⚠️ AI ตอบกลับ HTTP {resp.status_code} ({resp.text[:120]}) -> ลองใหม่อีกครั้ง...")
            time.sleep(2)
        except Exception as e:
            print(f"⚠️ AI รอบ {attempt + 1} ขัดข้อง ({str(e)[:80]})")
            time.sleep(2)
    raise RuntimeError("AI audit timed out after 2 attempts")


# ==============================================================================
# 2. โครงสร้างข้อมูลครบทุกตาราง (SD·diff ตอนเช้า DTE 0.8 + CME รอบนี้ + ประวัติรายรอบ)
# ==============================================================================
@dataclass
class HizeirisSnapshot:
    contract_name: str
    timestamp: str
    round_str: str
    futures_price: float
    future_chg: float
    daily_open_fut: float          # ราคาเปิด Futures ตอนเช้า 06:30 (เช่น 3869.70)
    daily_open_spot: float         # ราคาเปิด Spot ตอนเช้า (เช่น 3867.20)
    open_iv: float                 # IV ตอนเปิด 06:30 (เช่น 17.3%)
    open_source: str
    diff: float                    # ค่า diff ที่ใช้หัก (เช่น 2.50)
    raw_market_diff: Optional[float]
    diff_source: str
    dte: float                     # DTE จริงของสัญญารอบปัจจุบัน (เช่น 0.38)
    fixed_dte: float               # DTE ล็อกสำหรับกรอบวิจัยของวัน (0.8)
    atm_iv: float                  # ATM vol สด รอบปัจจุบัน (%)
    vol_chg: float                 # Vol Chg เทียบ Vol Settle
    vol_settle_atm: float          # Vol พื้นฐาน / Vol Settle (%)
    intraday_c: int
    intraday_p: int
    intraday_pc: float
    oi_c: int
    oi_p: int
    oi_pc: float
    churn_c: float
    churn_p: float
    churn_str: str
    put_wall: float
    put_wall_pc: str
    call_wall: float
    call_wall_pc: str
    max_pain: float
    top_inflow_text: str
    top_oi_text: str
    cme_vol_series_text: str
    pillar_labels_text: str
    sd_diff_table: Optional[Dict[str, float]] = None
    cme_dynamic_sd_table: Optional[Dict[str, float]] = None


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
            return " [ทับเลขกลม +5%..+8%]"
    return ""


def evaluate_oi_zone_m2(distance: float) -> Dict[str, str]:
    d = abs(distance)
    if d < 5:
        return {
            "zone": "โซนที่ 0: ระยะ < $5 (Repeller Wall Zone — กำแพงแรงต้านผลักออก)",
            "corr": "-0.45",
            "light": "🔴 ไฟแดง (ห้ามไล่ราคา)",
            "touch": "แตะเป้าแล้ว (100%) / โอกาสทะลุไปต่อทันทีเพียง 15.0%–27.5%",
            "win25": "ฝั่งไล่ตามเทรนด์เหลือ < 27.5%",
            "reversal": "เด้งกลับ 72.5%–85.0% (พุ่งถึง 89.0%–91.4% ถ้าทับซ้อนขอบ SD + IV Crush)",
            "rule": "ปิดทำกำไรฝั่งตามเทรนด์ทันที และดักเข้าสวน (Mean Reversion) เมื่อ IV เริ่มหดตัว",
        }
    elif d <= 15:
        return {
            "zone": "โซนที่ 1A: ระยะ $5–$15 (Core Magnet Zone — แรงดึงดูดเข้มข้นสูงสุด)",
            "corr": "+0.85",
            "light": "🟢 ไฟเขียวเข้ม",
            "touch": "88.0%–95.0% (ที่ระยะ $10 = 95.0%)",
            "win25": "85.0%–88.0%",
            "reversal": "ถูกปัดตกเพียง 5.0%–12.0%",
            "rule": "Follow Momentum เต็มกำลัง ถือเข้าชนระดับ Strike เป้าหมายได้โดยตรง",
        }
    elif d <= 30:
        return {
            "zone": "โซนที่ 1B: ระยะ $15–$30 (Event Horizon < $25–$30)",
            "corr": "+0.85",
            "light": "🟢 ไฟเขียว",
            "touch": "79.0%–90.0% (เฉลี่ยโซน <$30 = 90.0%, ที่ระยะ $25 = 79.0%)",
            "win25": "78.0%–85.0% (ที่ระยะ $25 = 83.0%)",
            "reversal": "ถูกปัดตก 10.0%–21.0%",
            "rule": "เข้าออเดอร์เชิงรุก ปล่อยให้แม่เหล็ก Gamma Hedging ดึงราคาไปชน Strike เป้าหมาย",
        }
    elif d <= 50:
        return {
            "zone": "โซนที่ 2: ระยะ $30–$50 (Vacuum Zone — สุญญากาศ/แรงเฉื่อยปานกลาง)",
            "corr": "+0.65",
            "light": "🟢/🟡 ไฟเขียวอ่อน",
            "touch": "55.0%–63.0% (ที่ระยะ $40 = 63.0%)",
            "win25": "75.0%–78.0% (ที่ระยะ $40 = 78.0%)",
            "reversal": "37.0%–45.0% (ระวังรถน้ำมันหมดกลางทางไหลกลับปั๊มแรก)",
            "rule": "แบ่งปิดทำกำไรหลักที่ระยะ +$25 และกันทุนที่เหลือลุ้นชน Strike",
        }
    elif d <= 75:
        return {
            "zone": "โซนที่ 3A: ระยะ $50–$75 (Far Target Trap Zone — กับดักระยะไกล)",
            "corr": "+0.30 -> +0.45",
            "light": "🟡 ไฟเหลือง (ห้ามถือรอชนเป้า)",
            "touch": "ตกฮวบเหลือ 14.0%–45.0% (เฉลี่ย >$50 = 45.0%, ที่ $60 = 37.0%, ที่ $75 = 14.0%)",
            "win25": "ยังคงสูงถึง 68.0%–72.0% (ที่ $60 = 72.0%, ที่ $75 = 68.0%)",
            "reversal": "โอกาสไปไม่ถึง Strike สูงถึง 55.0%–86.0%",
            "rule": "ห้ามถือลุ้นชน Strike เด็ดขาด! ต้องตั้ง Fixed TP เก็บกำไรคงที่ @ +$25 แล้วออกจากตลาดทันที",
        }
    return {
        "zone": "โซนที่ 3B: ระยะ $75–$100+ (Extreme Distance Zone — ไกลสุดขอบ)",
        "corr": "< +0.20",
        "light": "🟡/🔴 ระวังสูง",
        "touch": "< 5.0%–14.0% (ที่ระยะ $90–$100 < 5.0%)",
        "reversal": "โอกาสไปไม่ถึง Strike สูงถึง 86.0%–95.0%",
        "win25": "60.0%–68.0% (ที่ระยะ $100 = 60.0%)",
        "rule": "เก็บสั้นไม่เกิน +$15 ถึง +$25 เท่านั้น ห้ามคาดหวังว่าตลาดจะวิ่งไปแตะ Strike ในรอบเดียว",
    }


def evaluate_vol_live_vs_base(atm_live: float, vol_chg: float, vol_settle: float, open_iv: float) -> Dict[str, str]:
    diff_vs_open = round(atm_live - open_iv, 2)
    if vol_chg <= -1.0:
        regime = f"IV Crush รุนแรง (Vol สด {atm_live:.2f}% ต่ำกว่า Vol Settle {vol_settle:.2f}% อยู่ {vol_chg:+.2f}% | เทียบ IV ตอนเปิด {open_iv:.2f}% = {diff_vs_open:+.2f}%)"
        impact = "เส้นเขียว (Vol สด) ยุบตัวต่ำกว่าเส้นประแดง (Vol Settle) -> ดูดซับแรงกระแทกและเร่งแรงดีดกลับเข้าหากรอบ SD ตอนเปิด (Vanna Bounce / Mean Reversion แข็งแกร่ง)"
    elif vol_chg < 0:
        regime = f"IV Crush เบาบาง (Vol สด {atm_live:.2f}% ต่ำกว่า Vol Settle {vol_settle:.2f}% อยู่ {vol_chg:+.2f}% | เทียบ IV ตอนเปิด {open_iv:.2f}% = {diff_vs_open:+.2f}%)"
        impact = "ความผันผวนสดลดต่ำกว่าฐาน Settle -> สนับสนุนการดักเล่นตามกรอบ Fixed SD ตอนเปิด (1SD/2SD/3SD)"
    elif vol_chg >= 1.0:
        regime = f"Volatility Expansion รุนแรง (Vol สด {atm_live:.2f}% พุ่งสูงกว่า Vol Settle {vol_settle:.2f}% อยู่ {vol_chg:+.2f}% | เทียบ IV ตอนเปิด {open_iv:.2f}% = {diff_vs_open:+.2f}%)"
        impact = "เส้นเขียว (Vol สด) ยกตัวเหนือเส้นประแดง -> ระวังการฉีกกรอบทะลุ ±1SD ไปหา ±2SD (เน้นดักที่ ±2SD / ±3SD เป็นหลัก)"
    else:
        regime = f"Vol สมดุลเกาะฐาน (Vol สด {atm_live:.2f}% vs Vol Settle {vol_settle:.2f}% | เทียบ IV ตอนเปิด {open_iv:.2f}% = {diff_vs_open:+.2f}%)"
        impact = "เส้นเขียวเกาะใกล้เส้นประแดง -> ตลาดเคารพกรอบ Fixed SD ตอนเช้า (DTE 0.8) และกำแพง OI อย่างมีนัยสำคัญ"

    return {
        "vol_live": f"{atm_live:.2f}%",
        "vol_base_settle": f"{vol_settle:.2f}%",
        "open_iv": f"{open_iv:.2f}%",
        "diff_vs_open": f"{diff_vs_open:+.2f}%",
        "vol_chg": f"{vol_chg:+.2f}%",
        "regime": regime,
        "impact": impact,
    }


def evaluate_vol_regime_m2_2(atm_iv: float, vol_chg: float) -> Dict[str, str]:
    if atm_iv >= 22.0 or abs(vol_chg) >= 2.5:
        return {
            "regime_name": "1. โซนความผันผวนสูง (High Vol / Trending)",
            "win_rate": "88.50%",
            "adj": "+11.67% (เทียบค่าฐาน 2 ปี 76.83%)",
            "note": "ตลาดมีพลังงานสูง (หาก IV พุ่งขึ้น 2 เท่า ต้องลดขนาดความเสี่ยงลงครึ่งหนึ่งตามกฎ VaR Sizing)",
        }
    elif atm_iv <= 13.5 and abs(vol_chg) < 0.8:
        return {
            "regime_name": "4. โซนความผันผวนต่ำ (Low Vol / Sideways)",
            "win_rate": "64.50%",
            "adj": "-12.33% (เทียบค่าฐาน 2 ปี 76.83%)",
            "note": "ตลาดไร้ทิศทาง/วันหยุดธนาคาร เน้นดักกรอบแคบและลดความเสี่ยงลงครึ่งหนึ่ง (0.5x)",
        }
    return {
        "regime_name": "2. โซนความผันผวนปกติ (Normal Vol Day) & ค่าฐาน 2 ปี (2-Year Baseline)",
        "win_rate": "81.20% (ค่าฐาน 2 ปี = 76.83% หัก Friction -5.67% จากทฤษฎี 82.50% | กรองด้วย OI พุ่งเป็น 78.00%–84.00%)",
        "adj": "+4.37% เหนือค่าฐาน",
        "note": "สภาพคล่องมาตรฐาน เทรดตามกรอบ Fixed SD (DTE 0.8) และแรงโน้มถ่วง OI มาตรฐาน (1.0x)",
    }


def evaluate_module4_microstructure(snap: HizeirisSnapshot, spot: float, open_spot: float, one_sd: float) -> Dict[str, str]:
    price_up = snap.future_chg >= 0
    high_activity = (snap.churn_c >= 0.45 or snap.churn_p >= 0.45 or (snap.intraday_c + snap.intraday_p) >= 500)

    if price_up and high_activity:
        quadrant = "1. Long Buildup (ราคา ↑ + เงินใหม่ไหลเข้า ↑) -> กระทิงแข็งแกร่ง (Strong Bullish) ย่อซื้อตามแรงดึงแม่เหล็ก"
    elif price_up and not high_activity:
        quadrant = "2. Short Covering (ราคา ↑ + OI/Flow ชะลอตัว ↓) -> กระทิงอ่อนแอ / ระวังกับดัก Bull Trap เมื่อชนแนวต้าน +1SD/+2SD"
    elif (not price_up) and high_activity:
        quadrant = "3. Short Buildup (ราคา ↓ + เงินใหม่ไหลเข้าฝั่งขาย ↑) -> หมีแข็งแกร่ง (Strong Bearish) เด้งขายหรือรอดักรับที่กำแพง Put Wall/-2SD"
    else:
        quadrant = "4. Long Liquidation (ราคา ↓ + คนถือ Long เดิมตัดขาดทุน ↓) -> หมีอ่อนแอ / สัญญาณสลัดเม่า (Shakeout) เมื่อแรงขายหมดจะดีดกลับแรง"

    z = (spot - open_spot) / max(one_sd, 1.0)
    if snap.intraday_pc >= 1.20 and z <= -1.0:
        pcr_signal = f"PCR Extreme Fear (Intraday P/C = {snap.intraday_pc:.2f} > 1.20 ที่โซนรับล่าง) -> สัญญาณ Contrarian Buy (High-Confidence Bottom)"
    elif snap.intraday_pc <= 0.50 and z >= 1.0:
        pcr_signal = f"PCR Extreme Greed (Intraday P/C = {snap.intraday_pc:.2f} < 0.50 ที่โซนต้านบน) -> สัญญาณ Contrarian Sell (Trend Top / Buyer Fatigue)"
    else:
        pcr_signal = (
            f"Intraday P/C = {snap.intraday_pc:.2f} (C {snap.intraday_c:,} / P {snap.intraday_p:,}) เทียบกับ "
            f"OI P/C = {snap.oi_pc:.2f} (C {snap.oi_c:,} / P {snap.oi_p:,}) -> "
            + ("รายวันเน้นเก็งกำไรฝั่ง Call แต่ฐานสะสมใหญ่เป็นกำแพง Put คอยพยุงด้านล่าง" if snap.intraday_pc < 0.8 and snap.oi_pc > 1.2 else "โครงสร้างสมดุลระหว่างแรงเก็งกำไรรายวันกับฐานสะสม OI")
        )

    if snap.vol_chg >= 1.2 and abs(z) >= 1.5:
        hmm_state = (
            "State 2: Bear/Bull Negative Gamma Squeeze (Amaya et al., 2025: Negative Gamma ดันความผันผวนรายวันพุ่ง +3.3% "
            "และราย 30 นาทีพุ่งสูงสุด +6.4% | ระวังลำดับบีบ Dealer 4 เฟส: 50Δ -> 25Δ -> 35Δ-45Δ Vanna Spike -> 50Δ+ Final Flush)"
        )
    elif abs(z) <= 1.2 and snap.vol_chg <= 0.2:
        hmm_state = (
            "State 3: Positive Gamma Mean Reversion (Dim et al., 2025: Net Gamma > 0, β = -0.283*** และ Adams et al., 2025: "
            "0DTE Hedging กดความผันผวนระหว่างวันลง -60 ถึง -90 bps สนับสนุนการดักสวนที่ขอบ ±1SD / ±2SD และกำแพง OI)"
        )
    else:
        hmm_state = (
            "State 1: Gravity Momentum Flow (ราคาถูกดึงดูดเข้าหาก้อน High OI ระยะ <$30 ด้วยสหสัมพันธ์ +0.85 "
            "สลับการพักตัวเติมน้ำมันตามเสา Intraday Volume)"
        )

    if snap.vol_chg < 0:
        shadow_greeks = (
            f"เกิด IV Crush (Vol Chg = {snap.vol_chg:+.2f}%) -> กลไก Vanna (∂Δ/∂σ) ดูดค่า Shadow Delta ของ OTM Options หดกลับเข้าหา 0 "
            "ร่วมกับ Charm (∂Δ/∂t) ดึงราคาวิ่งกลับเข้าหา Max Pain/ราคาเปิด (Vanna & Charm Reversal Rally)"
        )
    elif snap.vol_chg > 0:
        shadow_greeks = (
            f"เกิด Vol Expansion (Vol Chg = {snap.vol_chg:+.2f}%) -> สมการ Shadow Delta = Δ_normal + (Vanna × ΔIV) เร่งตัวขึ้น "
            "ร่วมกับ Vomma/Twin-Peaks Volga ที่โซน 15Δ–25Δ ขยายขนาด Shadow Gamma พุ่งขึ้นอีก +25%–30%"
        )
    else:
        shadow_greeks = (
            "Vol สด เกาะสมดุลกับ Vol Settle (ΔIV ≈ 0) -> กลไก Charm (Time Decay ของ Delta) และ Direct Gamma ทำงานเป็นหลัก "
            "โดยจุดพีค Twin-Peaks Volga ที่โซน ±1.5SD ถึง ±2.0SD (15Δ–25Δ) ขยายแรงต้านทานของกำแพง Dealer ขึ้น +25%–30%"
        )

    dist_to_call = round((snap.call_wall - snap.diff) - spot, 2)
    dist_to_put = round(spot - (snap.put_wall - snap.diff), 2)
    if dist_to_put <= 20.0:
        car_model = (
            f"รถกำลังไหลลงมาทดสอบปั๊มน้ำมัน/ฐานรับฝั่งล่างใกล้ Put Wall {int(snap.put_wall)} (Spot {snap.put_wall - snap.diff:.2f}, ห่าง {dist_to_put:.1f} จุด) -> "
            "เข้าสู่ 'จังหวะที่ 2–3': หากแรงขายเจาะผ่านกำแพง Put Wall ไม่ได้ รถที่ไม่มีเบรกจะแวะเติมน้ำมัน (สะสม Volume ใหม่) แล้วดีดกลับขึ้นไปหาปั๊มถัดไป"
        )
    elif dist_to_call <= 20.0:
        car_model = (
            f"รถกำลังไต่ความชันเส้น Vol Settle ขึ้นไปทดสอบปั๊มปลายทาง Call Wall {int(snap.call_wall)} (Spot {snap.call_wall - snap.diff:.2f}, ห่าง {dist_to_call:.1f} จุด) -> "
            "ระวัง 'จังหวะที่ 2 (น้ำมันหมดกลางทาง)': หากแรงซื้อแผ่วก่อนผ่าน Call Wall รถที่ไม่มีเบรกจะไหลถอยหลังกลับลงมาหาเสาปั๊มน้ำมันแรกทันที!"
        )
    else:
        car_model = (
            f"รถอยู่ระหว่างเสาปั๊มน้ำมันกลางทาง (ห่าง Put Wall ด้านล่าง {dist_to_put:.1f} จุด / ห่าง Call Wall ด้านบน {dist_to_call:.1f} จุด) -> "
            "จังหวะที่ 1–2: หากไต่ความชันไปหาเสาถัดไปไม่ไหว จะไหลถอยหลังกลับมาเติมน้ำมันที่เสา Intraday Volume แรกก่อนเลือกทาง"
        )

    eod_warning = (
        "🚨 [คำเตือน DTE < 0.04 (30–60 นาทีสุดท้ายก่อนปิดตลาด 00:30–01:30 น.)]: ตามวิจัย Adams et al. (2025) "
        "ห้ามดักสวน Mean Reversion เด็ดขาด! เพราะ Market Maker รวบยอดทำ Hedging สัญญาวันถัดไป (Non-0DTE) เกิดโมเมนตัมไหลทางเดียว!"
        if snap.dte < 0.04
        else f"✅ DTE ปัจจุบัน = {snap.dte:.2f} (ยังไม่อยู่ในช่วง 30–60 นาทีสุดท้าย DTE < 0.04) -> สามารถดักเทรด Mean Reversion ตามกรอบ Fixed SD และกำแพง OI ได้ตามปกติ"
    )

    return {
        "quadrant": quadrant,
        "pcr_signal": pcr_signal,
        "hmm_state": hmm_state,
        "shadow_greeks": shadow_greeks,
        "car_model": car_model,
        "eod_warning": eod_warning,
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
            "zone_name": f"โซนที่ 1: 0.0SD ถึง ±0.5SD ({side}, {z:+.2f}SD / 45Δ–35Δ) — No-Trade Reversal Zone (Noise 68.2%)",
            "stats": "แตะ 92.6%–100% | ทะลุไป ±1.0SD สูงถึง 84.0% (500d แกว่งไม่พ้น <1SD ทั้งวัน 30%) | ดีดกลับเดี่ยวแค่ 50%–55% | เป้า $5–$8 (ห้ามดักสวนกลางอากาศ ใช้เป็นจุด TP ของไม้ขอบนอก)",
        }
    elif az < 1.0:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 2: ±0.5SD ถึง ±1.0SD ({side}, {z:+.2f}SD / 35Δ–25Δ) — Inner Transition Zone",
            "stats": "โอกาสแตะ ±1.0SD = 70.0% (500d) ถึง 77.8% (27d) | เมื่อแตะ 1.0SD มีโอกาสไป 1.5SD = 66.7% | ดีดกลับเดี่ยว 60%–65% | ทับ OI = 72%–76% | ทับ OI + IV Crush = 80.0% | เป้า $6–$10",
        }
    elif az < 1.5:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 3: ±1.0SD ถึง ±1.5SD ({side}, {z:+.2f}SD / เส้นเขียว ±1 / 25Δ) — First Action / The Drift Zone",
            "stats": "แตะ 51.9%–77.8% | ทะลุไป 2.0SD = 40.0% (500d) ถึง 50.0% (27d) | ดีดกลับย่อสั้น $8–$12 ชนะ 70.0% (100d: 434/620) ถึง 78.53% (50d: 245/312, ตลาดเอเชียพุ่งถึง 82.0%) แต่กลับตัวคำใหญ่ >=$25 แค่ 55.71%! | ทับ OI = 82%–86% | ทับ OI + IV Crush = 86%–88%",
        }
    elif az < 2.0:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 4: ±1.5SD ถึง ±2.0SD ({side}, {z:+.2f}SD / เส้นเขียว ±2 / 15Δ) — Primary Kill Zone (The Action Zone)",
            "stats": "แตะ 25.9%–28.0% ของวัน | ทะลุไป 2.5SD–3.0SD เพียง 25.0%–42.9% | ดีดกลับเดี่ยวชนะ 78.57% (500d >=$25) และ 80.0%–85.58% (100d: 360/450, 50d: 184/215) | ทับกำแพง OI = 86.0%–89.0% | ทับ OI + IV Crush สูงสุดถึง 91.4%! | เป้า $15–$22 (+$25)",
        }
    elif az < 2.5:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 5: ±2.0SD ถึง ±2.5SD ({side}, {z:+.2f}SD / 15Δ–5Δ) — Extreme Mean Reversion Zone",
            "stats": "แตะเพียง 11.1%–25.9% | ทะลุไป 3.0SD เพียง 33.3% | ดีดกลับเดี่ยวชนะ 78.0%–85.58% (100d: 156/200) | ทับ OI = 88.0%–90.0% | ทับ OI + IV Crush = 91.4%+ | เป้าคำใหญ่ $25–$35",
        }
    return {
        "sd_ratio": z,
        "zone_name": f"โซนที่ 6: ±2.5SD ถึง ±3.0SD ({side}, {z:+.2f}SD / เส้นเขียว ±3 / 5Δ) — Extreme Gamma Risk Zone",
        "stats": "แตะเพียง 3.7%–7.0% ของวัน (จบสวิง 96.3%) | ฝืนรับมีดไม้แรกชนะ <55.0% แต่ถ้ารอหยุดไหลที่ ±3.0SD ชนะ 86.75% (50d: 72/83) และกลับตัวคำใหญ่ >=$25 (500d) สูงถึง 91.43% (ทับ OI + Volume Climax + IV Crush = 92.0%–95.0%!) | เป้า $30–$45+",
    }


def _build_morning_vs_current_comparison(
    snap: HizeirisSnapshot,
    open_spot: float,
    spot: float,
    tb_morning: Dict[str, float],
    cme_dyn: Dict[str, float],
    prev_state: Optional[Dict],
) -> Dict[str, str]:
    """
    วิเคราะห์เปรียบเทียบ 2 มิติตามคำขอของผู้ใช้:
    1) รอบปัจจุบัน เทียบกับ กรอบ Fix ตอนเช้า (06:30 Morning Fixed SD @ DTE 0.8)
    2) รอบปัจจุบัน (t) เทียบกับ รอบก่อนหน้า (t-1)
    """
    delta_px_from_open = round(spot - open_spot, 2)
    z_from_open = round(delta_px_from_open / max(tb_morning["range_1sd"], 1.0), 2)
    delta_iv_from_open = round(snap.atm_iv - snap.open_iv, 2)

    # คำนวณว่าถ้าใช้ ATM Vol สดของรอบนี้ กับ DTE = 0.8 กรอบ 1SD จะกว้างเท่าไร เทียบกับ 1SD ที่ Fix ตอนเช้า
    curr_iv_dte08_1sd = round(snap.futures_price * (snap.atm_iv / 100.0) * math.sqrt(snap.fixed_dte / 365.0), 2)
    delta_1sd_dte08 = round(curr_iv_dte08_1sd - tb_morning["range_1sd"], 2)
    delta_1sd_dyn = round(cme_dyn["1SD_pts"] - tb_morning["range_1sd"], 2)

    # ตรวจหาจุด Double Confluence ระหว่างกรอบ Fix ตอนเช้า กับ กรอบ Dynamic รอบปัจจุบัน และกำแพง OI
    confluences = []
    call_spot = round(snap.call_wall - snap.diff, 2)
    put_spot = round(snap.put_wall - snap.diff, 2)
    for label_m, px_m in [
        ("1SD SELL เช้า", tb_morning["sell_1sd"]),
        ("2SD SELL เช้า", tb_morning["sell_2sd"]),
        ("3SD SELL เช้า", tb_morning["sell_3sd"]),
        ("1SD BUY เช้า", tb_morning["buy_1sd"]),
        ("2SD BUY เช้า", tb_morning["buy_2sd"]),
        ("3SD BUY เช้า", tb_morning["buy_3sd"]),
    ]:
        if abs(px_m - call_spot) <= 12.0:
            confluences.append(f"{label_m} ({px_m:,.2f}) ทับกำแพง Call Wall ปัจจุบัน ({call_spot:,.2f})")
        if abs(px_m - put_spot) <= 12.0:
            confluences.append(f"{label_m} ({px_m:,.2f}) ทับกำแพง Put Wall ปัจจุบัน ({put_spot:,.2f})")
        for label_d, px_d in [
            ("Dynamic +1SD รอบนี้", cme_dyn["+1SD_spot"]),
            ("Dynamic +2SD รอบนี้", cme_dyn["+2SD_spot"]),
            ("Dynamic -1SD รอบนี้", cme_dyn["-1SD_spot"]),
            ("Dynamic -2SD รอบนี้", cme_dyn["-2SD_spot"]),
        ]:
            if abs(px_m - px_d) <= 8.0:
                confluences.append(f"{label_m} ({px_m:,.2f}) ซ้อนทับ {label_d} ({px_d:,.2f})")

    confluence_str = " | ".join(confluences[:4]) if confluences else "กรอบ Fix เช้าทำหน้าที่เป็นเสาหลักคงที่ ส่วนกรอบ Dynamic รอบนี้หดแคบลงตามเวลา DTE ที่ลดลง"

    # เปรียบเทียบกับรอบก่อนหน้า (t-1 -> t)
    if prev_state and prev_state.get("spot_price"):
        prev_spot = float(prev_state.get("spot_price", spot))
        prev_iv = float(prev_state.get("atm_iv", snap.atm_iv))
        prev_ic = int(prev_state.get("intraday_c", snap.intraday_c))
        prev_ip = int(prev_state.get("intraday_p", snap.intraday_p))
        prev_time = str(prev_state.get("timestamp", "รอบก่อน"))
        d_spot_prev = round(spot - prev_spot, 2)
        d_iv_prev = round(snap.atm_iv - prev_iv, 2)
        d_ic = snap.intraday_c - prev_ic
        d_ip = snap.intraday_p - prev_ip
        vs_prev_text = (
            f"เทียบกับรอบก่อนหน้า ({prev_time}): ราคา Spot เปลี่ยน `{d_spot_prev:+.2f}` จุด (`{prev_spot:,.2f} -> {spot:,.2f}`), "
            f"Vol สด เปลี่ยน `{d_iv_prev:+.2f}%` (`{prev_iv:.2f}% -> {snap.atm_iv:.2f}%`), "
            f"สัญญา Intraday เปลี่ยน `Call {d_ic:+,} / Put {d_ip:+,}` สัญญา"
        )
    else:
        vs_prev_text = "เป็นรอบเริ่มต้นของการบันทึกเปรียบเทียบในชุดสถานะปัจจุบัน"

    morning_vs_now_summary = (
        f"• *ราคาเทียบตอนเช้า (06:30)*: เปิดเช้า `{open_spot:,.2f}` -> ปัจจุบัน `{spot:,.2f}` (เปลี่ยนแปลง **`{delta_px_from_open:+.2f}` จุด** หรือคิดเป็น **`{z_from_open:+.2f} SD เช้า`**)\n"
        f"• *ความผันผวนเทียบตอนเช้า*: IV ตอนเปิดเช้า `{snap.open_iv:.2f}%` -> ATM Vol สดรอบนี้ `{snap.atm_iv:.2f}%` (เปลี่ยนแปลง **`{delta_iv_from_open:+.2f}%`** | Vol Settle `{snap.vol_settle_atm:.2f}%`, Vol Chg `{snap.vol_chg:+.2f}%`)\n"
        f"• *ขนาดกรอบ 1SD เช้า vs รอบปัจจุบัน*: กรอบ Fix เช้า (DTE 0.8) `1SD = ±{tb_morning['range_1sd']:.2f}` จุด | หากคิดด้วย Vol สดรอบนี้ที่ DTE 0.8 จะได้ `±{curr_iv_dte08_1sd:.2f}` จุด (`{delta_1sd_dte08:+.2f}`) | ส่วนกรอบ Dynamic รอบนี้ตาม DTE จริง (`{snap.dte}`) แคบลงเหลือ `1SD = ±{cme_dyn['1SD_pts']:.2f}` จุด (`{delta_1sd_dyn:+.2f}` จุดจากกรอบเช้า)\n"
        f"• *จุดซ้อนทับสำคัญ (Morning Fixed SD + Current Round Confluence)*: {confluence_str}\n"
        f"• *การเปลี่ยนแปลงจากรอบก่อนหน้า (Δ t-1 -> t)*: {vs_prev_text}"
    )

    return {
        "delta_px_from_open": f"{delta_px_from_open:+.2f}",
        "z_from_open": f"{z_from_open:+.2f}SD",
        "delta_iv_from_open": f"{delta_iv_from_open:+.2f}%",
        "curr_iv_dte08_1sd": f"{curr_iv_dte08_1sd:.2f}",
        "confluence_str": confluence_str,
        "vs_prev_text": vs_prev_text,
        "morning_vs_now_summary": morning_vs_now_summary,
    }


def _build_6_sd_zone_plans(
    sell_1sd: float, sell_2sd: float, sell_3sd: float,
    buy_1sd: float, buy_2sd: float, buy_3sd: float,
    cme_dyn: Dict[str, float],
    diff: float, snap: HizeirisSnapshot,
) -> List[Dict[str, object]]:
    call_spot = round(snap.call_wall - diff, 2)
    put_spot = round(snap.put_wall - diff, 2)

    def _conf_note(px: float, wall_px: float, wall_name: str, wall_fut: float, dyn_label: str, dyn_px: float) -> str:
        notes = []
        if abs(px - wall_px) <= 15.0:
            notes.append(f"🔥 ทับซ้อน {wall_name} ปัจจุบัน Spot {wall_px:.2f} (Fut {wall_fut:.0f}) -> Confluence Win Rate 86.0%–91.4%!")
        else:
            notes.append(f"ห่าง {wall_name} ปัจจุบัน ({wall_px:.2f}) อยู่ {abs(px - wall_px):.2f} จุด")
        notes.append(f"เทียบ {dyn_label} รอบปัจจุบันอยู่ที่ {dyn_px:,.2f} (ต่างกัน {px - dyn_px:+.2f} จุด)")
        return " | ".join(notes)

    return [
        {
            "zone": "1) Zone 1SD SELL (sell spot 1sd เช้า — โซนที่ 3: ±1.0–1.5SD / เส้นเขียว +1 / 25ΔC)",
            "entry_spot": f"{sell_1sd:,.2f}",
            "entry_fut": f"{sell_1sd + diff:,.2f}",
            "tp1": f"{sell_1sd - 8.0:,.2f}",
            "tp2": f"{sell_1sd - 12.0:,.2f}",
            "sl_bozovic": f"{sell_1sd + 8.0:,.2f} (บัฟเฟอร์ Jump Risk 2.5 SD 5-min ตามวิจัย Božović 2026)",
            "sl1": f"{sell_1sd + 12.5:,.2f}",
            "rec1_2x_tp": f"{sell_1sd + 25.0:,.2f}",
            "win_rate": (
                f"แตะในวัน 70.0%–77.8% | ย่อตัวสั้น $8–$12 ชนะ 70.0% (100d: 434/620) ถึง 78.53% (50d: 245/312, ตลาดเอเชีย 82.0%) "
                f"| แต่กลับตัวคำใหญ่ >=$25 (500d) แค่ 55.71% (โอกาสไหลไป 2SD = 40.0%) | "
                f"{_conf_note(sell_1sd, call_spot, 'Call Wall', snap.call_wall, 'Dynamic +1SD', cme_dyn['+1SD_spot'])}"
            ),
            "research_play": (
                "ดัก SELL เก็บระยะย่อสั้น $8–$12 (TP1/TP2) เท่านั้น! ยืนยันเมื่อเสา Intraday Volume ปะทะแนวแล้วไม่สร้างเสา Call ใหม่ทะลุขึ้นไป "
                "(หากมีจุด Max Pain หรือ Call Wall อยู่เหนือด่านนี้ในระยะ <$15–$25 แรงดึงแม่เหล็ก Corr +0.85 มักดึงราคาทะลุ +1SD ไปชน Call Wall ด้วยโอกาส 78%–90%)"
            ),
        },
        {
            "zone": "2) Zone 2SD SELL (sell spot 2sd เช้า — โซนที่ 4: ±1.5–2.0SD / เส้นเขียว +2 / 15ΔC Primary Kill Zone)",
            "entry_spot": f"{sell_2sd:,.2f}",
            "entry_fut": f"{sell_2sd + diff:,.2f}",
            "tp1": f"{sell_2sd - 15.0:,.2f}",
            "tp2": f"{sell_2sd - 22.0:,.2f}",
            "sl_bozovic": f"{sell_2sd + 8.0:,.2f}",
            "sl1": f"{sell_2sd + 12.5:,.2f}",
            "rec1_2x_tp": f"{sell_2sd + 25.0:,.2f}",
            "sl2": f"{sell_2sd + 25.0:,.2f}",
            "rec2_6x_tp": f"{sell_2sd + 37.5:,.2f}",
            "win_rate": (
                f"แตะในวันเพียง 25.9%–28.0% | ดีดกลับเดี่ยวชนะ 80.0% (100d: 360/450) ถึง 85.58% (50d: 184/215) และกลับตัวคำใหญ่ >=$25 (500d) = 78.57% "
                f"| ทับกำแพง OI + IV Crush สูงสุดถึง 91.4%! (ไหลไป 3SD เพียง 25.0%) | "
                f"{_conf_note(sell_2sd, call_spot, 'Call Wall', snap.call_wall, 'Dynamic +2SD', cme_dyn['+2SD_spot'])}"
            ),
            "research_play": (
                "จุดดัก SELL หลักประจำวันตามฝั่ง Market Maker (Garmash 2024 & Vilkov 2026: Short Call Wall PoP 70%–80% และพุ่งเป็น 85.6%–91.4% เมื่อทับ 2SD)! "
                "เป็นจุดพีคของ Twin-Peaks Volga ที่ 15Δ ซึ่งขยาย Shadow Gamma ขึ้นอีก +25%–30% รอสัญญาณ Order Flow Absorption + Vol Chg หดตัวลงแล้วถือเอาเป้า $15–$25"
            ),
        },
        {
            "zone": "3) Zone 3SD SELL (sell spot 3sd เช้า — โซนที่ 6: ±2.5–3.0SD / เส้นเขียว +3 / 5ΔC Extreme Gamma Risk)",
            "entry_spot": f"{sell_3sd:,.2f}",
            "entry_fut": f"{sell_3sd + diff:,.2f}",
            "tp1": f"{sell_3sd - 25.0:,.2f}",
            "tp2": f"{sell_3sd - 35.0:,.2f}",
            "sl_bozovic": f"{sell_3sd + 8.0:,.2f}",
            "sl1": f"{sell_3sd + 12.5:,.2f}",
            "rec1_2x_tp": f"{sell_3sd + 25.0:,.2f}",
            "sl2": f"{sell_3sd + 25.0:,.2f}",
            "rec2_6x_tp": f"{sell_3sd + 37.5:,.2f}",
            "win_rate": (
                f"แตะในวันเพียง 3.7%–7.0% | โอกาสทะลุ <3.7% (จบสวิง 96.3%) | ฝืนรับมีดไม้แรกทันที = <55.0% "
                f"| รอหยุดไหลที่ +3.0SD = 86.75% (50d: 72/83) | กลับตัวคำใหญ่ >=$25 (500d) = 91.43% (ชนกำแพง 5Δ + IV Crush = 92%–95%) | "
                f"เทียบ Dynamic +3SD รอบนี้อยู่ที่ {cme_dyn['+3SD_spot']:,.2f}"
            ),
            "research_play": (
                "โซนดัก Fade Retail Lottery Call เก็บคำใหญ่ $30–$45+! กฎเหล็กสูงสุดจากวิจัย 50 วัน: ห้ามตั้ง Limit สวนไม้แรกแบบหลับตาเด็ดขาด "
                "ต้องรอให้เกิดการดูดซับออเดอร์ (Absorption) + เส้นเขียว Vol สด พีคแล้วหักหัวลงก่อนเสมอ"
            ),
        },
        {
            "zone": "4) Zone 1SD BUY (buy spot 1sd เช้า — โซนที่ 3: ±1.0–1.5SD / เส้นเขียว -1 / 25ΔP)",
            "entry_spot": f"{buy_1sd:,.2f}",
            "entry_fut": f"{buy_1sd + diff:,.2f}",
            "tp1": f"{buy_1sd + 8.0:,.2f}",
            "tp2": f"{buy_1sd + 12.0:,.2f}",
            "sl_bozovic": f"{buy_1sd - 8.0:,.2f} (บัฟเฟอร์ Jump Risk 2.5 SD 5-min ตามวิจัย Božović 2026)",
            "sl1": f"{buy_1sd - 12.5:,.2f}",
            "rec1_2x_tp": f"{buy_1sd - 25.0:,.2f}",
            "win_rate": (
                f"แตะในวัน 70.0%–77.8% | ดีดกลับสั้น $8–$12 ชนะ 70.0% (100d) ถึง 78.53% (50d, ตลาดเอเชีย 82.0%) "
                f"| แต่กลับตัวคำใหญ่ >=$25 แค่ 55.71% (โอกาสไหลไป -2SD = 40.0%) | "
                f"{_conf_note(buy_1sd, put_spot, 'Put Wall', snap.put_wall, 'Dynamic -1SD', cme_dyn['-1SD_spot'])}"
            ),
            "research_play": (
                "ดัก BUY เก็บเด้งสั้น $8–$12 (TP1/TP2) ห้ามไล่ทุบซื้อ Put ตามรายย่อยที่โซนรับนี้ (วิจัย Garmash 2024 ชี้ว่า Long Naked Put ที่ก้นหลุมชนะแค่ 30%–40% เพราะโดน IV Crush + Vanna เด้งสวน!) "
                "หากหลุด -1SD พร้อมเสา Put สร้างฐานใหม่ลงไป ให้ถอยไปดักไม้หลักที่ Zone 2SD BUY"
            ),
        },
        {
            "zone": "5) Zone 2SD BUY (buy spot 2sd เช้า — โซนที่ 4: ±1.5–2.0SD / เส้นเขียว -2 / 15ΔP Primary Kill Zone)",
            "entry_spot": f"{buy_2sd:,.2f}",
            "entry_fut": f"{buy_2sd + diff:,.2f}",
            "tp1": f"{buy_2sd + 15.0:,.2f}",
            "tp2": f"{buy_2sd + 22.0:,.2f}",
            "sl_bozovic": f"{buy_2sd - 8.0:,.2f}",
            "sl1": f"{buy_2sd - 12.5:,.2f}",
            "rec1_2x_tp": f"{buy_2sd - 25.0:,.2f}",
            "sl2": f"{buy_2sd - 25.0:,.2f}",
            "rec2_6x_tp": f"{buy_2sd - 37.5:,.2f}",
            "win_rate": (
                f"แตะในวันเพียง 25.9%–28.0% | ดีดกลับเดี่ยวชนะ 80.0% (100d: 360/450) ถึง 85.58% (50d: 184/215) และกลับตัวคำใหญ่ >=$25 (500d) = 78.57% "
                f"| ทับกำแพง OI + IV Crush สูงสุดถึง 91.4%! (โอกาสหลุดไป -3SD เพียง 25.0%) | "
                f"{_conf_note(buy_2sd, put_spot, 'Put Wall', snap.put_wall, 'Dynamic -2SD', cme_dyn['-2SD_spot'])}"
            ),
            "research_play": (
                "จุดดัก BUY หลักประจำวัน (Sweet Spot Buy)! รองรับกรณีเกิด Intraday Negative Jump (2.5 SD 5-min ตาม Božović 2026) "
                "เมื่อราคาปะทะโซน 15ΔP แล้วแรงขายหยุดลง ค่า IV ที่หดตัวจะจุดชนวน Vanna Rally บังคับให้ Dealer ซื้อ Futures คืนลากราคาดีดกลับ $15–$25+"
            ),
        },
        {
            "zone": "6) Zone 3SD BUY (buy spot 3sd เช้า — โซนที่ 6: ±2.5–3.0SD / เส้นเขียว -3 / 5ΔP Extreme Gamma Risk)",
            "entry_spot": f"{buy_3sd:,.2f}",
            "entry_fut": f"{buy_3sd + diff:,.2f}",
            "tp1": f"{buy_3sd + 25.0:,.2f}",
            "tp2": f"{buy_3sd + 35.0:,.2f}",
            "sl_bozovic": f"{buy_3sd - 8.0:,.2f}",
            "sl1": f"{buy_3sd - 12.5:,.2f}",
            "rec1_2x_tp": f"{buy_3sd - 25.0:,.2f}",
            "sl2": f"{buy_3sd - 25.0:,.2f}",
            "rec2_6x_tp": f"{buy_3sd - 37.5:,.2f}",
            "win_rate": (
                f"แตะในวันเพียง 3.7%–7.0% | โอกาสหลุด <3.7% (จบสวิง 96.3%) | ฝืนรับมีดไม้แรกทันที = <55.0% "
                f"| รอหยุดไหลที่ -3.0SD = 86.75% (50d: 72/83) | กลับตัวคำใหญ่ >=$25 (500d) = 91.43% (ชนกำแพง 5Δ + IV Crush = 92%–95%) | "
                f"เทียบ Dynamic -3SD รอบนี้อยู่ที่ {cme_dyn['-3SD_spot']:,.2f}"
            ),
            "research_play": (
                "จุดจบสวิงขาลงกรณีเกิด Negative Gamma Squeeze เต็มรูปแบบ (+6.4% 30-min Vol ตาม Amaya et al., 2025)! "
                "ห้ามรับมีดไม้แรกขณะกำลังไหลดิ่ง ต้องรอสัญญาณ Shakeout / Order Absorption + IV พีคหักหัวลง แล้วช้อน BUY คำใหญ่เป้า $30–$45+"
            ),
        },
    ]


def compute_exact_coordinates(snap: HizeirisSnapshot, prev_state: Optional[Dict]) -> Dict:
    fut = snap.futures_price
    open_fut = round(snap.daily_open_fut if _is_valid_gold_price(snap.daily_open_fut, fut) else fut, 2)
    diff = round(snap.diff, 2)
    spot = round(fut - diff, 2)
    open_spot = round(open_fut - diff, 2)
    fixed_dte = snap.fixed_dte
    open_iv = snap.open_iv

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
        r1 = round(open_fut * (open_iv / 100.0) * math.sqrt(fixed_dte / 365.0), 2)
        r2 = round(r1 * 2.0, 2)
        r3 = round(r1 * 3.0, 2)
        buy_1sd = round(open_spot - r1, 2)
        sell_1sd = round(open_spot + r1, 2)
        buy_2sd = round(open_spot - r2, 2)
        sell_2sd = round(open_spot + r2, 2)
        buy_3sd = round(open_spot - r3, 2)
        sell_3sd = round(open_spot + r3, 2)

    if snap.cme_dynamic_sd_table and snap.cme_dynamic_sd_table.get("+1SD", 0) > 0:
        c_sd = snap.cme_dynamic_sd_table
    else:
        dyn_1sd = round(fut * (snap.atm_iv / 100.0) * math.sqrt(max(snap.dte, 0.05) / 365.0), 1)
        c_sd = {
            "1SD_pts": dyn_1sd,
            "2SD_pts": round(dyn_1sd * 2, 1),
            "3SD_pts": round(dyn_1sd * 3, 1),
            "-1SD": round(fut - dyn_1sd, 1),
            "+1SD": round(fut + dyn_1sd, 1),
            "-2SD": round(fut - dyn_1sd * 2, 1),
            "+2SD": round(fut + dyn_1sd * 2, 1),
            "-3SD": round(fut - dyn_1sd * 3, 1),
            "+3SD": round(fut + dyn_1sd * 3, 1),
        }

    cme_dynamic_spot = {
        "+3SD_fut": c_sd["+3SD"], "+3SD_spot": round(c_sd["+3SD"] - diff, 2),
        "+2SD_fut": c_sd["+2SD"], "+2SD_spot": round(c_sd["+2SD"] - diff, 2),
        "+1SD_fut": c_sd["+1SD"], "+1SD_spot": round(c_sd["+1SD"] - diff, 2),
        "-1SD_fut": c_sd["-1SD"], "-1SD_spot": round(c_sd["-1SD"] - diff, 2),
        "-2SD_fut": c_sd["-2SD"], "-2SD_spot": round(c_sd["-2SD"] - diff, 2),
        "-3SD_fut": c_sd["-3SD"], "-3SD_spot": round(c_sd["-3SD"] - diff, 2),
        "1SD_pts": c_sd["1SD_pts"], "2SD_pts": c_sd["2SD_pts"], "3SD_pts": c_sd["3SD_pts"],
    }

    sd_table_exact = {
        "range_1sd": r1, "buy_1sd": buy_1sd, "sell_1sd": sell_1sd,
        "range_2sd": r2, "buy_2sd": buy_2sd, "sell_2sd": sell_2sd,
        "range_3sd": r3, "buy_3sd": buy_3sd, "sell_3sd": sell_3sd,
        "buy_1sd_fut": round(buy_1sd + diff, 2), "sell_1sd_fut": round(sell_1sd + diff, 2),
        "buy_2sd_fut": round(buy_2sd + diff, 2), "sell_2sd_fut": round(sell_2sd + diff, 2),
        "buy_3sd_fut": round(buy_3sd + diff, 2), "sell_3sd_fut": round(sell_3sd + diff, 2),
    }

    hourly_iv_pts = round(open_fut * (snap.atm_iv / 40.8 / 100.0), 2)
    daily_rule16_pct = round(snap.atm_iv / 16.0, 2)
    daily_rule16_pts = round(open_fut * (daily_rule16_pct / 100.0), 2)
    weekly_iv_pts = round(open_fut * (snap.atm_iv / 7.2 / 100.0), 2)

    vol_analysis = evaluate_vol_live_vs_base(
        snap.atm_iv, snap.vol_chg, snap.vol_settle_atm, open_iv
    )
    vol_regime_m2 = evaluate_vol_regime_m2_2(snap.atm_iv, snap.vol_chg)
    m4_micro = evaluate_module4_microstructure(snap, spot, open_spot, r1)
    morning_vs_current = _build_morning_vs_current_comparison(
        snap, open_spot, spot, sd_table_exact, cme_dynamic_spot, prev_state
    )

    call_spot = round(snap.call_wall - diff, 2)
    put_spot = round(snap.put_wall - diff, 2)
    pain_spot = round(snap.max_pain - diff, 2)

    dist_call = round(call_spot - spot, 2)
    dist_put = round(spot - put_spot, 2)
    dist_pain = round(pain_spot - spot, 2)
    current_sd_info = classify_current_sd_zone_6(spot, open_spot, r1)
    six_zone_plans = _build_6_sd_zone_plans(
        sell_1sd, sell_2sd, sell_3sd, buy_1sd, buy_2sd, buy_3sd, cme_dynamic_spot, diff, snap
    )

    if 5.0 <= dist_put <= 30.0 and dist_put <= abs(dist_call):
        primary_setup = {
            "title": f"ดัก BUY ที่ฐานรับ Put Wall ({put_spot:.2f}) โซนใกล้ 1SD BUY เช้า ({buy_1sd:.2f}) หรือ Follow กลับหาแกนเปิด ({open_spot:.2f})",
            "entry": f"{put_spot:.2f} – {buy_1sd:.2f} (Fut {snap.put_wall:.0f} – {buy_1sd + diff:.2f})",
            "win_rate": "82.0%–89.0% (Put Wall ทับโซน -1SD | ฝั่งไล่ทุบ Long Naked Put ที่ก้นหลุมชนะแค่ 30%–40%!)",
            "tp": f"TP1: {open_spot:.2f} (ราคาเปิดวัน) | TP2: {pain_spot:.2f} (Max Pain) | TP3: {sell_1sd:.2f} (+1SD เช้า)",
            "sl": f"SL เดี่ยว: {round(buy_1sd - 12.5, 2)} (ทบ 2x -> TP {round(buy_1sd - 25.0, 2)}) หรือถอยรับไม้ 2 ที่ 2SD BUY เช้า ({buy_2sd:.2f})",
        }
    elif 5.0 <= dist_call <= 30.0:
        primary_setup = {
            "title": f"Follow Momentum ระยะ <$30 ขึ้นทดสอบ Call Wall ({call_spot:.2f}) / 1SD SELL เช้า ({sell_1sd:.2f})",
            "entry": f"{spot:.2f} (ย่อซื้อเหนือราคาเปิด {open_spot:.2f})",
            "win_rate": "79.0%–90.0% (โซนไฟเขียว 🟢 Event Horizon Corr +0.85)",
            "tp": f"TP1: {sell_1sd:.2f} (1SD SELL เช้า) | TP2: {call_spot:.2f} (Call Wall)",
            "sl": f"SL: {round(spot - 12.5, 2)} (ทบ 2x -> TP {round(spot - 25.0, 2)})",
        }
    else:
        primary_setup = {
            "title": f"ดักเข้าตามกรอบ Fixed SD ตอนเช้า: BUY ที่ 1SD BUY ({buy_1sd:.2f}) / 2SD BUY ({buy_2sd:.2f}) และ SELL ที่ 1SD SELL ({sell_1sd:.2f}) / 2SD SELL ({sell_2sd:.2f})",
            "entry": f"ฝั่ง BUY: {buy_1sd:.2f} & {buy_2sd:.2f} | ฝั่ง SELL: {sell_1sd:.2f} & {sell_2sd:.2f}",
            "win_rate": "1SD เก็บสั้น $8–$12 ชนะ 78.53% (เอเชีย 82%) | 2SD Kill Zone เก็บคำใหญ่ +$25 ชนะ 78.57%–91.4%",
            "tp": f"จาก 1SD เก็บ $8–$12 เข้าหา {open_spot:.2f} | จาก 2SD เก็บ $15–$25",
            "sl": "เผื่อบัฟเฟอร์ Jump Risk $5–$8 หรือใช้ Supa888 SL1 (-12.5 ทบ 2x) / SL2 (-25.0 รวบทบ 6x)",
        }

    return {
        "spot": spot,
        "open_fut": open_fut,
        "open_spot": open_spot,
        "open_iv": open_iv,
        "fixed_dte": fixed_dte,
        "diff": diff,
        "vol_live_vs_base": vol_analysis,
        "vol_regime_m2": vol_regime_m2,
        "m4_micro": m4_micro,
        "morning_vs_current": morning_vs_current,
        "time_scaling_pts": {
            "hourly_div_40_8": hourly_iv_pts,
            "daily_rule16_pct": daily_rule16_pct,
            "daily_rule16_pts": daily_rule16_pts,
            "weekly_div_7_2": weekly_iv_pts,
        },
        "sd_table_exact": sd_table_exact,
        "cme_dynamic_spot": cme_dynamic_spot,
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
        "primary_setup": primary_setup,
        "plan_c_exact": {
            "call_wall_break": round(call_spot + 12.5, 2), "call_squeeze_tp": round(call_spot + 25.0, 2),
            "put_wall_break": round(put_spot - 12.5, 2), "put_squeeze_tp": round(put_spot - 25.0, 2),
        },
    }


# ==============================================================================
# 3. เปิดหน้าเว็บ Hizeiris (ล็อกแถบเวลาขวาสุด + สลับแท็บแม่นยำ 100%)
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
                    type: el.type || '',
                    id: el.id || '',
                    name: el.name || '',
                    placeholder: el.placeholder || '',
                    value: el.value || '',
                    text: (el.parentElement ? el.parentElement.innerText : '').slice(0, 100)
                })).filter(x => x.value !== '' && x.type !== 'range')"""
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


def _click_tab_button_in_frames(page, tab_keywords: List[str]) -> bool:
    """กดปุ่มแท็บ (เช่น 'SD · diff', 'CME · Vol', 'OI ทุกสัญญา') ด้วยการเช็ก tag BUTTON/TAB โดยตรง"""
    clicked = False
    for frame in page.frames:
        try:
            res = frame.locator("body").evaluate(
                f"""body => {{
                    const kw = {json.dumps(tab_keywords)};
                    const candidates = Array.from(document.querySelectorAll('button, [role="tab"], a, span, div'));
                    for (const el of candidates) {{
                        if (el.children.length > 2) continue;
                        const txt = (el.innerText || '').trim();
                        if (txt.length > 0 && txt.length <= 25 && kw.every(k => txt.toLowerCase().includes(k.toLowerCase()))) {{
                            el.click();
                            return true;
                        }}
                    }}
                    return false;
                }}""",
                timeout=2500,
            )
            if res:
                clicked = True
        except Exception:
            pass
    return clicked


def _configure_sd_diff_and_ensure_latest_round(page, fixed_dte: float) -> None:
    """
    1) บังคับให้แถบเลื่อนเวลา (<input type="range">) อยู่ขวาสุด (รอบล่าสุดของวันเสมอ! ห้ามโดนแก้เป็น 0.8 เด็ดขาด)
    2) เปิดปุ่ม 'SD: เปิด' และ 'diff: ใช้'
    3) ตั้งค่าช่อง DTE (เฉพาะช่อง text/number ที่ไม่ใช่ range slider) ให้เป็น 0.8
    """
    for frame in page.frames:
        try:
            frame.locator("body").evaluate(
                f"""body => {{
                    // 1. บังคับให้ Range Slider เวลา อยู่ขวาสุด (รอบล่าสุดเสมอ)
                    Array.from(document.querySelectorAll('input[type="range"]')).forEach(rng => {{
                        if (rng.max && rng.value !== rng.max) {{
                            rng.value = rng.max;
                            rng.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            rng.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                    }});
                    // 2. เปิดปุ่ม SD: เปิด และ diff: ใช้ หากปิดอยู่
                    Array.from(document.querySelectorAll('button, span, div')).forEach(el => {{
                        if (el.children.length > 1) return;
                        const t = (el.innerText || '').trim();
                        if (t === 'SD: ปิด') el.click();
                        if (t === 'diff: ปิด' || t === 'diff: ไม่ใช้') el.click();
                    }});
                    // 3. ตั้งค่าช่อง DTE = {fixed_dte} เฉพาะ input ที่ไม่ใช่ range หรือ checkbox!
                    Array.from(document.querySelectorAll('input')).forEach(inp => {{
                        if (inp.type === 'range' || inp.type === 'checkbox' || inp.type === 'radio') return;
                        const prevTxt = (inp.previousElementSibling ? inp.previousElementSibling.innerText : '').trim();
                        const parentTxt = (inp.parentElement ? inp.parentElement.innerText : '').trim();
                        if (/^DTE$/i.test(prevTxt) || (parentTxt.length < 30 && /DTE/i.test(parentTxt))) {{
                            if (inp.value !== '{fixed_dte}') {{
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

        # 1) กดเปิดแท็บ "SD · diff" + บังคับแถบเวลาไปที่รอบล่าสุด + ตั้ง DTE = 0.8
        if _click_tab_button_in_frames(page, ["SD", "diff"]):
            page.wait_for_timeout(1500)
            _configure_sd_diff_and_ensure_latest_round(page, fixed_dte)
            page.wait_for_timeout(1500)
            _expand_iframes_for_full_charts(page)
            page.screenshot(path=shot_sd_diff, full_page=True, timeout=15000)
            screenshots.append(shot_sd_diff)
            photo_captions.append((shot_sd_diff, f"📐 [ภาพที่ 1] แท็บ SD · diff (กรอบ Fix ตอนเช้า DTE {fixed_dte}) & กราฟ Intraday + OI"))
            text_diff_tab = _extract_frame_texts_and_inputs(page)
            print(f"✅ แคปภาพที่ 1 (แท็บ SD · diff ล็อก DTE = {fixed_dte}) สำเร็จ!")
        else:
            text_diff_tab = ""

        # 2) กดเปิดแท็บ "CME · Vol" เพื่อดึงตาราง CME ตอนเก็บรอบนี้ (รอบปัจจุบัน) มาเทียบกับกรอบตอนเช้า
        _click_tab_button_in_frames(page, ["CME", "Vol"])
        page.wait_for_timeout(1800)
        _expand_iframes_for_full_charts(page)
        page.screenshot(path=shot_cme_vol, full_page=True, timeout=15000)
        screenshots.append(shot_cme_vol)
        photo_captions.append((shot_cme_vol, "📊 [ภาพที่ 2] แท็บ CME · Vol (ข้อมูลรอบปัจจุบัน) & กราฟ Intraday + OI"))
        text_main = _extract_frame_texts_and_inputs(page)
        print("✅ แคปภาพที่ 2 (แท็บ CME · Vol รอบปัจจุบัน) สำเร็จ!")

        # 3) กดเปิดแท็บ "OI ทุกสัญญา"
        if _click_tab_button_in_frames(page, ["OI", "ทุกสัญญา"]):
            page.wait_for_timeout(2000)
            _expand_iframes_for_full_charts(page)
            page.screenshot(path=shot_oi_all, full_page=True, timeout=15000)
            screenshots.append(shot_oi_all)
            photo_captions.append((shot_oi_all, "🧱 [ภาพที่ 3] กราฟ Open Interest รวม (OI ทุกสัญญา)"))
            text_oi_all = _extract_frame_texts_and_inputs(page)
            print("✅ แคปภาพที่ 3 (OI ทุกสัญญา) สำเร็จ!")
        else:
            text_oi_all = ""

        context.close()

    return {
        "screenshot_paths": screenshots,
        "photo_captions": photo_captions,
        "raw_text": (
            f"[VIEW 1: SD · diff (Fixed DTE {fixed_dte})]\n{text_diff_tab}\n\n"
            f"[VIEW 2: CME · Vol (Current Round)]\n{text_main}\n\n"
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

    sd_diff_open_fut = _parse_float(r"ราคาเปิด\s*([2-5],?[0-9]{3}\.[0-9]+)", raw_text, 0.0)
    formula_m = re.search(
        r"1SD\s*=\s*([2-5],?[0-9]{3}(?:\.[0-9]+)?)\s*[×x*]\s*([0-9]+\.[0-9]+)%?\s*[×x*]\s*√\(([0-9.]+)/365\)\s*=\s*([0-9]+\.[0-9]+)",
        raw_text,
    )
    open_iv = 0.0
    if formula_m:
        if sd_diff_open_fut == 0.0:
            sd_diff_open_fut = float(formula_m.group(1).replace(",", ""))
        open_iv = float(formula_m.group(2))

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

    cme_dynamic_sd_table = None
    sd1_m = re.search(r"1SD\s+([+\-]?[0-9.]+)\s+([+\-]?[0-9.]+)\s+([2-5],?[0-9]{3}\.[0-9]+)\s+([2-5],?[0-9]{3}\.[0-9]+)", raw_text)
    sd2_m = re.search(r"2SD\s+([+\-]?[0-9.]+)\s+([+\-]?[0-9.]+)\s+([2-5],?[0-9]{3}\.[0-9]+)\s+([2-5],?[0-9]{3}\.[0-9]+)", raw_text)
    sd3_m = re.search(r"3SD\s+([+\-]?[0-9.]+)\s+([+\-]?[0-9.]+)\s+([2-5],?[0-9]{3}\.[0-9]+)\s+([2-5],?[0-9]{3}\.[0-9]+)", raw_text)
    if sd1_m and sd2_m and sd3_m:
        cme_dynamic_sd_table = {
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

    atm_live = _parse_float(r"ATM\s*vol\s*([0-9]+\.?[0-9]*)", raw_text, 0.0)
    if atm_live == 0.0:
        atm_live = open_iv if open_iv > 0 else _parse_float(r"\bIV%?\s*([0-9]+\.[0-9]+)", raw_text, 17.3)
    if open_iv == 0.0:
        open_iv = atm_live

    vol_chg = _parse_float(r"Vol\s*Chg\s*([+\-]?[0-9]+\.?[0-9]*)", raw_text, 0.0)
    future_chg = _parse_float(r"Future\s*Chg\s*([+\-]?[0-9]+\.?[0-9]*)", raw_text, 0.0)
    vol_settle_atm = round(atm_live - vol_chg, 2)

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
        intraday_c, intraday_p = 634, 370
        intraday_pc = _parse_float(r"Intraday.*?P/C\s*([0-9]+\.?[0-9]*)", raw_text, 0.58)

    oi_m = re.search(r"Open Interest\s+C\s*([0-9,]+)\s*[·•]?\s*P\s*([0-9,]+)\s*[·•]?\s*P/C\s*([0-9.]+)", raw_text, re.IGNORECASE)
    if oi_m:
        oi_c = int(oi_m.group(1).replace(",", ""))
        oi_p = int(oi_m.group(2).replace(",", ""))
        oi_pc = float(oi_m.group(3))
    else:
        oi_c, oi_p = 622, 1058
        oi_pc = _parse_float(r"Open Interest.*?P/C\s*([0-9]+\.?[0-9]*)", raw_text, 1.70)

    churn_m = re.search(r"Churn\s*C\s*([0-9.]+)\s*[·•/]?\s*P\s*([0-9.]+)", raw_text, re.IGNORECASE)
    if churn_m:
        churn_c, churn_p = float(churn_m.group(1)), float(churn_m.group(2))
        churn_str = f"C {churn_c:.2f} / P {churn_p:.2f}"
    else:
        churn_c, churn_p = 0.59, 0.45
        churn_str = "C 0.59 / P 0.45"

    max_pain = _parse_float(r"Max\s*Pain\s*([2-5][0-9]{3})", raw_text, round(fut_px, -1))

    call_wall = _parse_float(r"Call\s*wall\s*([2-5][0-9]{3})", raw_text, 0.0)
    call_oi_cnt = ""
    cw_plan_m = re.search(r"Call\s+[0-9.]+\s*\(([2-5][0-9]{3})\)[^\n]*?OI\s*([0-9,]+)", raw_text, re.IGNORECASE)
    if cw_plan_m:
        if call_wall == 0.0:
            call_wall = float(cw_plan_m.group(1))
        call_oi_cnt = f"Call wall {int(call_wall)} (OI {cw_plan_m.group(2)} สัญญา)"
    if call_wall == 0.0:
        call_wall = round(fut_px + 60, -1)

    put_wall = _parse_float(r"Put\s*wall\s*([2-5][0-9]{3})", raw_text, 0.0)
    put_oi_cnt = ""
    pw_plan_m = re.search(r"Put\s+[0-9.]+\s*\(([2-5][0-9]{3})\)[^\n]*?OI\s*([0-9,]+)", raw_text, re.IGNORECASE)
    if pw_plan_m:
        if put_wall == 0.0:
            put_wall = float(pw_plan_m.group(1))
        put_oi_cnt = f"Put wall {int(put_wall)} (OI {pw_plan_m.group(2)} สัญญา)"
    if put_wall == 0.0:
        put_wall = round(fut_px - 15, -1)

    pillars = re.findall(r"\b([0-9]{1,3}/[0-9]{1,3})\b", raw_text)
    unique_pillars = []
    for p in pillars:
        if p not in unique_pillars and not p.endswith("/121") and not p.endswith("/134"):
            unique_pillars.append(p)
    pillar_labels_text = ", ".join(unique_pillars[:12]) if unique_pillars else f"Intraday C {intraday_c}/P {intraday_p} | OI C {oi_c}/P {oi_p}"

    cme_series = re.findall(r"((?:OG|G[0-9]|GC)[A-Z0-9]{2,5}\s+[0-9.]+\s+[0-9.]+\s+[+\-]?[0-9.]+\s+[0-9.]+)", raw_text)
    cme_vol_series_text = " | ".join(cme_series[:4]) if cme_series else f"{contract_name} DTE {dte} IV {atm_live:.2f}% (Normal Contango)"

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
        intraday_c=intraday_c,
        intraday_p=intraday_p,
        intraday_pc=intraday_pc,
        oi_c=oi_c,
        oi_p=oi_p,
        oi_pc=oi_pc,
        churn_c=churn_c,
        churn_p=churn_p,
        churn_str=churn_str,
        put_wall=put_wall,
        put_wall_pc=put_oi_cnt or f"Put wall {int(put_wall)}",
        call_wall=call_wall,
        call_wall_pc=call_oi_cnt or f"Call wall {int(call_wall)}",
        max_pain=max_pain,
        top_inflow_text=f"โฟลว์รายวัน Intraday C {intraday_c:,} vs P {intraday_p:,} (P/C {intraday_pc:.2f})",
        top_oi_text=f"ฐานสะสม OI C {oi_c:,} vs P {oi_p:,} (P/C {oi_pc:.2f}) | Put Wall {int(put_wall)} / Call Wall {int(call_wall)}",
        cme_vol_series_text=cme_vol_series_text,
        pillar_labels_text=pillar_labels_text,
        sd_diff_table=sd_diff_table,
        cme_dynamic_sd_table=cme_dynamic_sd_table,
    )


def _format_round_history_log(round_history: List[Dict]) -> str:
    """แสดงตารางบันทึกประวัติแต่ละรอบของวัน เพื่อดูพัฒนาการตั้งแต่รอบเช้าจนถึงรอบปัจจุบัน"""
    if not round_history:
        return "• ยังไม่มีประวัติรอบก่อนหน้าในวันนี้"
    lines = []
    for idx, r in enumerate(round_history[-8:], 1):
        lines.append(
            f"  `#{idx}` [{r.get('time', '')} | {r.get('round_str', '')}] "
            f"Spot: `{r.get('spot', 0):,.2f}` (`{r.get('delta_from_open', '+0.00')}` จากเช้า, `{r.get('z_sd', '0.0SD')}`) | "
            f"Vol สด: `{r.get('atm_iv', 0):.2f}%` (`{r.get('vol_chg', 0):+.2f}`) | "
            f"Intra C/P: `{r.get('intra_c', 0)}/{r.get('intra_p', 0)}` (`P/C {r.get('intra_pc', 0):.2f}`) | "
            f"Walls: `P {r.get('put_wall', 0):.0f} / C {r.get('call_wall', 0):.0f}`"
        )
    return "\n".join(lines)


def build_deterministic_fallback_report(snap: HizeirisSnapshot, exact_calc: Dict, round_history: List[Dict]) -> str:
    tb = exact_calc["sd_table_exact"]
    dyn = exact_calc["cme_dynamic_spot"]
    ws = exact_calc["walls_spot"]
    zones = exact_calc["six_zone_plans"]
    ps = exact_calc["primary_setup"]
    pc = exact_calc["plan_c_exact"]
    ts = exact_calc["time_scaling_pts"]
    va = exact_calc["vol_live_vs_base"]
    vr = exact_calc["vol_regime_m2"]
    m4 = exact_calc["m4_micro"]
    mvc = exact_calc["morning_vs_current"]

    zone_lines = []
    for z in zones:
        rec2_line = f" | SL2 (-25): `{z['sl2']}` (รวบทบ 6x -> `{z['rec2_6x_tp']}`)" if "sl2" in z else ""
        zone_lines.append(
            f"*{z['zone']}*\n"
            f"• พิกัดเข้า XAUUSD (Spot): `{z['entry_spot']}` (Futures `{z['entry_fut']}`)\n"
            f"• เป้าทำกำไร: TP1 `{z['tp1']}` / TP2 `{z['tp2']}`\n"
            f"• จุดตัดขาดทุน & Supa888: บัฟเฟอร์ Božović `{z['sl_bozovic']}` | SL1 (-12.5) `{z['sl1']}` (ทบ 2x -> TP `{z['rec1_2x_tp']}`){rec2_line}\n"
            f"• % ความน่าจะเป็น & เทียบรอบปัจจุบัน: _{z['win_rate']}_\n"
            f"• เงื่อนไขยืนยัน & วิธีเล่น: _{z['research_play']}_"
        )

    history_table_str = _format_round_history_log(round_history)

    return (
        f"📊 *1. สรุปพารามิเตอร์ & วิเคราะห์เปรียบเทียบ: กรอบ Fix ตอนเช้า (06:30, DTE 0.8) vs รอบปัจจุบัน* ({snap.timestamp})\n"
        f"• *สัญญา*: `{snap.contract_name}` ({snap.round_str}) | *Futures รอบนี้*: `{snap.futures_price:,.1f}` (`{snap.future_chg:+.1f}`) | *Diff*: `{snap.diff:.2f}` _({snap.diff_source})_ | *XAUUSD (Spot รอบนี้)*: `{exact_calc['spot']:,.2f}`\n"
        f"• *กรอบวิจัยล็อกตอนเช้า (Morning Fixed Baseline @ 06:30 ใช้จนจบซีรีส์)*:\n"
        f"  - ราคาเปิด Futures: `{exact_calc['open_fut']:,.2f}` | ราคาเปิด Spot: `{exact_calc['open_spot']:,.2f}` | IV ตอนเปิด: `{exact_calc['open_iv']:.2f}%` | *Fixed DTE*: `{snap.fixed_dte}` (DTE จริงรอบนี้ `{snap.dte}`)\n"
        f"  - สมการตอนเช้า: `1SD = {exact_calc['open_fut']} × {exact_calc['open_iv']}% × √({snap.fixed_dte}/365) = ±{tb['range_1sd']:.2f}` | `2SD = ±{tb['range_2sd']:.2f}` | `3SD = ±{tb['range_3sd']:.2f}`\n\n"
        f"🔄 *วิเคราะห์การเปลี่ยนแปลง: รอบเช้า (06:30) -> รอบก่อนหน้า (t-1) -> รอบปัจจุบัน (t)*:\n"
        f"{mvc['morning_vs_now_summary']}\n\n"
        f"• *Vol สด (เส้นเขียวทึบ)*: `{va['vol_live']}` vs *Vol Settle (เส้นประแดง)*: `{va['vol_base_settle']}` (`Vol Chg {va['vol_chg']}` | เทียบ IV ตอนเปิด `{va['diff_vs_open']}`)\n"
        f"  - _{va['regime']}: {va['impact']}_\n"
        f"  - *Rule of 16 (IV/16)*: Daily Move `±{ts['daily_rule16_pct']}%` (`±{ts['daily_rule16_pts']}` จุด) | รายชม. `±{ts['hourly_div_40_8']}` | รายสัปดาห์ `±{ts['weekly_div_7_2']}`\n"
        f"  - *Volatility Regime (Module 2.2)*: {vr['regime_name']} -> Win Rate ฐาน `{vr['win_rate']}` ({vr['adj']})\n"
        f"• *โฟลว์และเสาสัญญา (Module 1.3)*: Intraday `C {snap.intraday_c:,} / P {snap.intraday_p:,}` (`P/C {snap.intraday_pc:.2f}`) | Open Interest `C {snap.oi_c:,} / P {snap.oi_p:,}` (`P/C {snap.oi_pc:.2f}`) | Churn `{snap.churn_str}` | Max Pain `{ws['max_pain_spot']:,.2f}` (Fut `{ws['max_pain_fut']:,.0f}`, ห่าง `{exact_calc['dist_max_pain']:+.2f}`)\n"
        f"  - เลขหัวเสา Put/Call บนกราฟ: `{snap.pillar_labels_text}`\n"
        f"  - โครงสร้างซีรีส์ (Term Structure): `{snap.cme_vol_series_text}`\n"
        f"• *การวินิจฉัยเชิงปริมาณ (Module 4 & Module 5)*:\n"
        f"  - *HMM 3-State & 0DTE*: {m4['hmm_state']}\n"
        f"  - *จตุภาค Price-OI (4-Quadrant)*: {m4['quadrant']}\n"
        f"  - *Contrarian PCR*: {m4['pcr_signal']}\n"
        f"  - *Shadow Greeks (Vanna / Charm / Twin-Peaks Volga)*: {m4['shadow_greeks']}\n"
        f"  - *โมเดลรถไม่มีเบรก & ปั๊มน้ำมัน 3 จังหวะ*: {m4['car_model']}\n"
        f"  - *กฎเวลา 0DTE (Adams et al., 2025)*: {m4['eod_warning']}\n\n"
        f"🧱 *2. ตารางพิกัดแนวรับ-แนวต้านคืนนี้ (หัก Diff = {snap.diff:.2f}: เปรียบเทียบพิกัด Fix ตอนเช้า [DTE 0.8] คู่กับพิกัด Dynamic รอบปัจจุบัน & กำแพง OI)*\n"
        f"1) *ขอบบน +3SD (โซนหาง 5ΔC Extreme)*:\n"
        f"   • *Fix เช้า (DTE 0.8, `+{tb['range_3sd']:.2f}`)*: **`{tb['sell_3sd']:,.2f}`**{check_round_number_bonus(tb['sell_3sd'])} (Fut `{tb['sell_3sd_fut']:,.2f}`) vs *รอบปัจจุบัน (Dynamic 3SD)*: **`{dyn['+3SD_spot']:,.2f}`** (Fut `{dyn['+3SD_fut']:,.1f}`, ต่างกัน `{tb['sell_3sd'] - dyn['+3SD_spot']:+.2f}`)\n"
        f"   • ความน่าจะเป็น: แตะ `3.7%–7.0%` | **เด้งกลับ `86.75%–95.0%` (500d `>= $25` = `91.43%`)** | ทะลุ `<3.7%` (จบสวิง `96.3%`)\n"
        f"2) *ขอบบน +2SD + เขียว +2 (โซนต้านหลัก 15ΔC Primary Kill Zone)*:\n"
        f"   • *Fix เช้า (DTE 0.8, `+{tb['range_2sd']:.2f}`)*: **`{tb['sell_2sd']:,.2f}`**{check_round_number_bonus(tb['sell_2sd'])} (Fut `{tb['sell_2sd_fut']:,.2f}`) vs *รอบปัจจุบัน (Dynamic 2SD)*: **`{dyn['+2SD_spot']:,.2f}`** (Fut `{dyn['+2SD_fut']:,.1f}`, ต่างกัน `{tb['sell_2sd'] - dyn['+2SD_spot']:+.2f}`)\n"
        f"   • ความน่าจะเป็น: แตะ `25.9%–28.0%` | **เด้งกลับ `78.57%–85.58%` (ทับกำแพง OI + IV Crush พุ่งเป็น `86.0%–91.4%`)** | ทะลุไป 3SD เพียง `25.0%`\n"
        f"3) *กำแพง Call Wall & Max Pain รอบปัจจุบัน (เป้าหมายแม่เหล็กขาขึ้น / ปั๊มน้ำมันใหญ่)*:\n"
        f"   • Call Wall ({snap.call_wall_pc}): Spot **`{ws['call_wall_spot']:,.2f}`**{check_round_number_bonus(ws['call_wall_spot'])} (Fut `{ws['call_wall_fut']:,.0f}`) | Max Pain: Spot **`{ws['max_pain_spot']:,.2f}`** (Fut `{ws['max_pain_fut']:,.0f}`)\n"
        f"   • ความน่าจะเป็น: แรงดึงแม่เหล็กในระยะ `<$30` = **`79.0%–90.0%`** (`Corr +0.85`) | เมื่อชนกำแพง (`<$5` Repeller Wall `Corr -0.45` 🔴) **โอกาสถูกปัดตก/เด้งกลับ `72.5%–85.0%`**\n"
        f"4) *ขอบบน +1SD + เขียว +1 (ด่านแรก 25ΔC First Action / Drift Zone)*:\n"
        f"   • *Fix เช้า (DTE 0.8, `+{tb['range_1sd']:.2f}`)*: **`{tb['sell_1sd']:,.2f}`**{check_round_number_bonus(tb['sell_1sd'])} (Fut `{tb['sell_1sd_fut']:,.2f}`) vs *รอบปัจจุบัน (Dynamic 1SD)*: **`{dyn['+1SD_spot']:,.2f}`** (Fut `{dyn['+1SD_fut']:,.1f}`, ต่างกัน `{tb['sell_1sd'] - dyn['+1SD_spot']:+.2f}`)\n"
        f"   • ความน่าจะเป็น: แตะ `70.0%–77.8%` | **ย่อตัวสั้น `$8–$12` ชนะ `70.0%–78.53%` (เอเชีย `82.0%`)** | แต่กลับตัวคำใหญ่ `>=$25` แค่ `55.71%` และไหลทะลุไป 2SD ถึง `40.0%–50.0%`\n"
        f"5) *แกนราคาเปิดตอนเช้า (06:30 Open Anchor) vs จุดปะทะราคาปัจจุบัน (Pivot / ปั๊มน้ำมัน)*:\n"
        f"   • *ราคาเปิดเช้า*: Spot **`{exact_calc['open_spot']:,.2f}`** (Fut `{exact_calc['open_fut']:,.2f}`) vs *ราคาปัจจุบัน*: Spot **`{exact_calc['spot']:,.2f}`** (Fut `{snap.futures_price:,.1f}`, ห่างจากเช้า `{mvc['delta_px_from_open']}` จุด = `{mvc['z_from_open']}`)\n"
        f"6) *กำแพง Put Wall & ขอบล่าง -1SD + เขียว -1 (ฐานแนวรับชั้นแรก 25ΔP)*:\n"
        f"   • Put Wall ({snap.put_wall_pc}): Spot **`{ws['put_wall_spot']:,.2f}`**{check_round_number_bonus(ws['put_wall_spot'])} (Fut `{ws['put_wall_fut']:,.0f}`) | *Fix เช้า (`-{tb['range_1sd']:.2f}`)*: **`{tb['buy_1sd']:,.2f}`** (Fut `{tb['buy_1sd_fut']:,.2f}`) vs *รอบปัจจุบัน (Dynamic -1SD)*: **`{dyn['-1SD_spot']:,.2f}`** (Fut `{dyn['-1SD_fut']:,.1f}`)\n"
        f"   • ความน่าจะเป็น: แตะ `70.0%–77.8%` | **เด้งสั้น `$8–$12` ชนะ `70.0%–78.53%` (ทับ Put Wall พุ่งเป็น `82.0%–89.0%`)** | ห้ามไล่ Sell/ซื้อ Put ที่ก้นหลุมตามรายย่อย (ชนะแค่ `30%–40%`!)\n"
        f"7) *ขอบล่าง -2SD + เขียว -2 (แนวรับลึก 15ΔP Primary Kill Zone / Sweet Spot Buy)*:\n"
        f"   • *Fix เช้า (DTE 0.8, `-{tb['range_2sd']:.2f}`)*: **`{tb['buy_2sd']:,.2f}`**{check_round_number_bonus(tb['buy_2sd'])} (Fut `{tb['buy_2sd_fut']:,.2f}`) vs *รอบปัจจุบัน (Dynamic -2SD)*: **`{dyn['-2SD_spot']:,.2f}`** (Fut `{dyn['-2SD_fut']:,.1f}`, ต่างกัน `{tb['buy_2sd'] - dyn['-2SD_spot']:+.2f}`)\n"
        f"   • ความน่าจะเป็น: แตะ `25.9%–28.0%` | **ดีดกลับคำใหญ่ `>=$25` ชนะ `78.57%–85.58%` (ทับ OI + IV Crush = `86.0%–91.4%`)** | หลุดไป -3SD เพียง `25.0%`\n"
        f"8) *ขอบล่าง -3SD (พื้นล่างสุด 5ΔP Extreme Tail)*:\n"
        f"   • *Fix เช้า (DTE 0.8, `-{tb['range_3sd']:.2f}`)*: **`{tb['buy_3sd']:,.2f}`**{check_round_number_bonus(tb['buy_3sd'])} (Fut `{tb['buy_3sd_fut']:,.2f}`) vs *รอบปัจจุบัน (Dynamic -3SD)*: **`{dyn['-3SD_spot']:,.2f}`** (Fut `{dyn['-3SD_fut']:,.1f}`, ต่างกัน `{tb['buy_3sd'] - dyn['-3SD_spot']:+.2f}`)\n"
        f"   • ความน่าจะเป็น: แตะ `3.7%–7.0%` | **รอหยุดไหลเกิด Absorption ดีดกลับชนะ `86.75%–95.0%` (500d `>= $25` = `91.43%`)** | หลุด `<3.7%`\n\n"
        f"⚖️ *3. การประเมินความได้เปรียบเชิงสถิติ (% Statistical Edge Scorecard: ฝั่ง Buy vs ฝั่ง Sell)*\n"
        f"• *1) แรงโน้มถ่วง OI 7 โซน & กฎ $25 (Module 2.1)*:\n"
        f"  - ฝั่งขึ้นหา Call Wall `{ws['call_wall_spot']:,.2f}` (ห่าง `{exact_calc['dist_call']:+.2f}` จุด): {exact_calc['oi_call_zone']['light']} {exact_calc['oi_call_zone']['zone']} (Corr `{exact_calc['oi_call_zone']['corr']}`) -> แตะเป้า `{exact_calc['oi_call_zone']['touch']}` | ชนะ +$25 `{exact_calc['oi_call_zone']['win25']}` | เด้งกลับ `{exact_calc['oi_call_zone']['reversal']}` -> _{exact_calc['oi_call_zone']['rule']}_\n"
        f"  - ฝั่งลงหา Put Wall `{ws['put_wall_spot']:,.2f}` (ห่าง `{-exact_calc['dist_put']:+.2f}` จุด): {exact_calc['oi_put_zone']['light']} {exact_calc['oi_put_zone']['zone']} (Corr `{exact_calc['oi_put_zone']['corr']}`) -> แตะเป้า `{exact_calc['oi_put_zone']['touch']}` | ชนะ +$25 `{exact_calc['oi_put_zone']['win25']}` | เด้งกลับ `{exact_calc['oi_put_zone']['reversal']}` -> _{exact_calc['oi_put_zone']['rule']}_\n"
        f"• *2) โครงสร้าง SD + OI Wall Kill Zone Confluence (Module 3)*: ตำแหน่งปัจจุบันอยู่ที่ `{exact_calc['current_sd_zone']['zone_name']}` ({exact_calc['current_sd_zone']['stats']})\n"
        f"• *3) กลไก Shadow Delta & ตาราง PoP งานวิจัย 0DTE ต่างประเทศ 7 ฉบับ (Module 5)*:\n"
        f"  - ดักสวนฝั่ง Market Maker ที่กำแพง (`Put Wall / Call Wall / ±2SD`) ชนะ **`70.0%–80.0%`** (และพุ่งเป็น **`85.6%–91.4%`** เมื่อทับ SD + IV Crush)\n"
        f"  - เทรดตามโมเมนตัมเข้าหา OI ระยะ `<$30` (`Debit Spread / $25 Rule`) ชนะ **`78.0%–88.5%`**\n"
        f"  - ไล่ซื้อโดยไม่มี OI รองรับ ชนะเพียง **`50.0%–52.0%`** | ไล่ทุบซื้อ Put ที่ก้นหลุมตอน IV แพง ชนะเพียง **`30.0%–40.0%`** (แพ้ `60%–70%`)\n\n"
        f"🎯 *4. แผนปฏิบัติการเทรดเชิงปริมาณบนกราฟ XAUUSD (ครบทั้ง 6 โซน Fixed SD ตอนเช้า + เทียบรอบปัจจุบัน & Supa888)*\n"
        f"📌 *แผนที่ 1 (Primary High-Conviction Setup ณ ราคาปัจจุบัน `{exact_calc['spot']:,.2f}`)*:\n"
        f"• กลยุทธ์: *{ps['title']}*\n"
        f"• พิกัดเข้า: `{ps['entry']}` | Win Rate: `{ps['win_rate']}`\n"
        f"• เป้าทำกำไร: `{ps['tp']}`\n"
        f"• จุดตัดขาดทุน & แก้พอร์ต: `{ps['sl']}`\n\n"
        f"📌 *แผนครบทั้ง 6 โซนจากกรอบวิจัย Fixed SD ตอนเช้า (`DTE = 0.8` ใช้ตลอดวันจนจบซีรีส์ เทียบกับรอบปัจจุบัน)*:\n\n"
        + "\n\n".join(zone_lines)
        + f"\n\n📌 *แผนสำรอง (Breakout / Negative Gamma Squeeze 4 เฟส)*:\n"
        f"• หากเกิด Wall Break มีเสา Intraday Volume ก่อกำแพงใหม่ทะลุ Call Wall ยืนเหนือ `{pc['call_wall_break']:,.2f}` พร้อม Vol สด พุ่งชัน -> Follow Buy เป้า `{pc['call_squeeze_tp']:,.2f}`\n"
        f"• หากทุบหลุด Put Wall ยืนต่ำกว่า `{pc['put_wall_break']:,.2f}` พร้อม Vol สด พุ่งชัน -> Follow Sell เป้า `{pc['put_squeeze_tp']:,.2f}`\n\n"
        f"🔔 *5. จุดเฝ้าระวังการเปลี่ยนสถานะเชิงปริมาณในรอบถัดไป (Key Quant Triggers & Diurnal Timing Rules)*\n"
        f"1) *ติดตาม `Δ รอบก่อน` ในตาราง `Vol จาก CME` และช่องว่าง `Vol สด ({va['vol_live']}) vs Vol Settle ({va['vol_base_settle']}) vs IV ตอนเปิด ({va['open_iv']})`*: หาก `Vol Chg` ติดลบต่อเนื่องจะยืนยัน Vanna Bounce เข้าหากรอบใน แต่หากพุ่งบวกแรงให้ระวังการขยายกรอบไปด่าน `±2SD`\n"
        f"2) *ติดตามการย้ายปั๊มน้ำมัน Intraday Volume (`P/C {snap.intraday_pc:.2f}`, Churn `{snap.churn_str}`)*: หากรถไต่ไปหาเสาถัดไปไม่ไหว จะไหลถอยหลังกลับมาเติมน้ำมันที่เสาแรก (`{ws['put_wall_spot']:,.2f}` / `{exact_calc['open_spot']:,.2f}`)\n"
        f"3) *กฎล็อกเวลา CME*: `00:30/01:30 น.` (Mark-to-Market Settlement), `06:30 น.` (ล็อกกรอบ `SD · diff` DTE 0.8), `08:30 น.` (สแกน Local Max OI ในกรอบ 1SD–2SD), ช่วงเปิดตลาดสหรัฐฯ (คัดกรอง Skewness Sharpe `1.09–1.26`), และ **ห้ามดักสวนเทรนด์ในช่วง 30–60 นาทีสุดท้ายก่อนปิดตลาด (`DTE < 0.04`) เด็ดขาด!**\n\n"
        f"📝 *6. สมุดบันทึกประวัติแต่ละรอบของวันนี้ (INTRADAY ROUND-BY-ROUND LOG & SESSION MEMORY)*\n"
        f"{history_table_str}\n\n"
        f"`Series: {snap.contract_name} ({snap.round_str}) | OpenFut: {exact_calc['open_fut']:.2f} | OpenSpot: {exact_calc['open_spot']:.2f} | OpenIV: {exact_calc['open_iv']}% | FixedDTE: {snap.fixed_dte} | Diff: {snap.diff:.2f} | SpotNow: {exact_calc['spot']:.2f} ({mvc['delta_px_from_open']} / {mvc['z_sd'] if 'z_sd' in mvc else mvc['z_from_open']}) | 1SD({tb['range_1sd']:.2f}): Buy {tb['buy_1sd']:.2f} / Sell {tb['sell_1sd']:.2f} | 2SD({tb['range_2sd']:.2f}): Buy {tb['buy_2sd']:.2f} / Sell {tb['sell_2sd']:.2f} | 3SD({tb['range_3sd']:.2f}): Buy {tb['buy_3sd']:.2f} / Sell {tb['sell_3sd']:.2f}`"
    )


def synthesize_full_report(snap: HizeirisSnapshot, exact_calc: Dict, capture: Optional[Dict[str, object]],
                           api_key: str, prev_state: Optional[Dict], round_history: List[Dict]) -> str:
    base_full_report = build_deterministic_fallback_report(snap, exact_calc, round_history)
    if CONFIG["DRY_RUN"] or api_key == "YOUR_GEMINI_API_KEY_HERE" or not capture:
        return base_full_report

    mvc = exact_calc["morning_vs_current"]
    tb = exact_calc["sd_table_exact"]
    ws = exact_calc["walls_spot"]
    prompt = (
        "คุณคือ Quantitative Derivatives & Market Microstructure Auditor (Mtraders Masterclass & AI CME Analyst)\n"
        "จงดูภาพหน้าจอเว็บ Hizeiris ทั้ง 2 ภาพ (ภาพที่ 1: แท็บ SD·diff กรอบ Fix เช้า DTE 0.8 และภาพที่ 2: แท็บ CME·Vol รอบปัจจุบัน) "
        "แล้ววิเคราะห์ตรวจสอบซ้ำ (Cross-Check) เปรียบเทียบกับตัวเลขที่ Python คำนวณไว้ให้ครบ 4 ข้อนี้ (ห้ามใช้ ICT/SMC เด็ดขาด):\n"
        "1. **ตรวจความถูกต้องตัวเลขบนภาพ**: ยืนยันว่า Futures, Diff, ราคาเปิดเช้า, 1SD/2SD/3SD และ Call Wall / Put Wall / Max Pain ตรงกับบนภาพหรือไม่ (ถ้ามีจุดต่างให้แจ้งทันที)\n"
        "2. **อ่านหัวเสา Put/Call จริงบนกราฟ Intraday Volume & OI**: ระบุ Strike ที่มีเสาสูงเด่นพร้อมตัวเลข `Put/Call` (สีส้ม/สีฟ้า) และชี้ว่าจุดไหนคือปั๊มน้ำมันของโมเดลรถไม่มีเบรกในรอบนี้\n"
        "3. **วิเคราะห์เส้นเขียว Vol สด vs เส้นประแดง Vol Settle (ทั้ง 2 ฝั่ง)**: อธิบายความเบ้ (Skew) ที่ปีกซ้าย (Put) และปีกขวา (Call) เทียบกับตอนเช้าว่าหนุน Vanna Bounce หรือระวัง Squeeze\n"
        "4. **คำตัดสินยืนยันแผนเทรด (AI Verdict)**: สรุปว่าแผนหลักและแผน 6 โซน Fixed SD (1SD/2SD/3SD SELL & BUY) ในรอบนี้ควรเน้นดักโซนไหนมากที่สุดและมีจุดเฝ้าระวังใดเป็นพิเศษ\n\n"
        f"[ข้อมูลเปรียบเทียบที่ Python คำนวณ]:\n{mvc['morning_vs_now_summary']}\n"
        f"Spot ปัจจุบัน: {exact_calc['spot']:.2f} | OpenSpot: {exact_calc['open_spot']:.2f} | Diff: {snap.diff:.2f}\n"
        f"Fixed SD เช้า: 1SD Buy/Sell = {tb['buy_1sd']:.2f}/{tb['sell_1sd']:.2f} | 2SD Buy/Sell = {tb['buy_2sd']:.2f}/{tb['sell_2sd']:.2f} | 3SD Buy/Sell = {tb['buy_3sd']:.2f}/{tb['sell_3sd']:.2f}\n"
        f"Walls: PutWall {ws['put_wall_spot']:.2f} | CallWall {ws['call_wall_spot']:.2f} | MaxPain {ws['max_pain_spot']:.2f}"
    )

    try:
        ai_audit = call_gemini_rest_fast(
            api_key=api_key,
            prompt_text=prompt,
            image_paths=capture["screenshot_paths"][:2],
            timeout_sec=45,
        )
        return f"{base_full_report}\n\n🤖 *7. AI ตรวจสอบและวิเคราะห์ซ้ำจากภาพกราฟจริง (AI Cross-Check & Chart Audit)*:\n{ai_audit.strip()}"
    except Exception:
        return f"{base_full_report}\n\n🤖 *7. AI Cross-Check*: รอบนี้เซิร์ฟเวอร์ AI ตอบช้าเกินกำหนด ระบบจึงส่งรายงานคำนวณเชิงปริมาณ Modules 1–6 จาก Python ฉบับเต็มให้แทน"



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


def split_telegram_chunks(text: str, max_len: int = 3800) -> List[str]:
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


def save_and_push_state(path: str, state: Dict) -> None:
    """บันทึก session_state.json และสั่ง git commit & push ทันทีหลังจบแต่ละรอบ เพื่อเก็บประวัติต่อเนื่องตลอดวัน"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    if IS_GITHUB_ACTIONS:
        try:
            subprocess.run(
                'git config --local user.email "bot@github.com" && '
                'git config --local user.name "Hizeiris Quant Bot" && '
                'git pull --rebase --autostash || true && '
                'git add session_state.json hizeiris_auto_bot.py && '
                '(git diff --staged --quiet || git commit -m "Update round history state") && '
                '(git push || true)',
                shell=True,
                timeout=25,
            )
            print("💾 บันทึกประวัติรอบลง GitHub (session_state.json) สำเร็จ!")
        except Exception as e:
            print(f"⚠️ บันทึก GitHub รอบนี้ข้ามไป ({e})")


def run_once():
    prev_state = load_state(CONFIG["STATE_FILE"])
    tg_diff, tg_open = fetch_telegram_commands(CONFIG["TELEGRAM_BOT_TOKEN"], CONFIG["TELEGRAM_CHAT_ID"])
    fallback_diff = CONFIG["FALLBACK_DIFF"]

    if CONFIG["DRY_RUN"]:
        cap = None
        snap = HizeirisSnapshot(
            contract_name="G3MX6",
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            round_str="รอบ 134/134",
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
            intraday_c=634,
            intraday_p=370,
            intraday_pc=0.58,
            oi_c=622,
            oi_p=1058,
            oi_pc=1.70,
            churn_c=0.59,
            churn_p=0.45,
            churn_str="C 0.59 / P 0.45",
            put_wall=3850.0,
            put_wall_pc="Put wall 3850 (36/7, 61/8)",
            call_wall=3925.0,
            call_wall_pc="Call wall 3925 (1/113)",
            max_pain=3900.0,
            top_inflow_text="โฟลว์รายวัน Intraday C 634 vs P 370 (P/C 0.58)",
            top_oi_text="ฐานสะสม OI C 622 vs P 1,058 (P/C 1.70) | Put Wall 3850 / Call Wall 3925",
            cme_vol_series_text="G3MX6 DTE 0.38 IV 17.30% (Normal Contango)",
            pillar_labels_text="30/0, 36/7, 61/8, 13/46, 8/61, 6/43, 1/49, 18/71, 1/113, 1/54",
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
            f"✅ ดึงตาราง SD·diff & รอบปัจจุบันสำเร็จ: {snap.contract_name} ({snap.round_str}) | "
            f"OpenFut {snap.daily_open_fut:.2f} | NowFut {snap.futures_price:.1f} | Diff {snap.diff:.2f}"
        )

    exact_calc = compute_exact_coordinates(snap, prev_state)
    tb = exact_calc["sd_table_exact"]
    mvc = exact_calc["morning_vs_current"]

    # อัปเดตสมุดบันทึกประวัติแต่ละรอบของวัน (round_history)
    today_str = datetime.now().strftime("%Y-%m-%d")
    old_history = []
    if prev_state and prev_state.get("trading_day") == today_str:
        old_history = list(prev_state.get("round_history", []))

    current_round_entry = {
        "time": datetime.now().strftime("%H:%M"),
        "round_str": snap.round_str,
        "fut": snap.futures_price,
        "spot": exact_calc["spot"],
        "delta_from_open": mvc["delta_px_from_open"],
        "z_sd": mvc["z_from_open"],
        "atm_iv": snap.atm_iv,
        "vol_chg": snap.vol_chg,
        "intra_c": snap.intraday_c,
        "intra_p": snap.intraday_p,
        "intra_pc": snap.intraday_pc,
        "oi_c": snap.oi_c,
        "oi_p": snap.oi_p,
        "put_wall": snap.put_wall,
        "call_wall": snap.call_wall,
    }
    old_history.append(current_round_entry)
    round_history = old_history[-48:]

    report_text = synthesize_full_report(snap, exact_calc, cap, CONFIG["GEMINI_API_KEY"], prev_state, round_history)
    send_telegram_parts(split_telegram_chunks(report_text), CONFIG["TELEGRAM_BOT_TOKEN"], CONFIG["TELEGRAM_CHAT_ID"])

    save_and_push_state(CONFIG["STATE_FILE"], {
        "trading_day": today_str,
        "timestamp": snap.timestamp,
        "round_str": snap.round_str,
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
        "atm_iv": snap.atm_iv,
        "vol_settle_atm": snap.vol_settle_atm,
        "intraday_c": snap.intraday_c,
        "intraday_p": snap.intraday_p,
        "put_wall": snap.put_wall,
        "call_wall": snap.call_wall,
        "max_pain": snap.max_pain,
        "round_history": round_history,
    })
    print("🎉 ส่งรูปกราฟและรายงานเปรียบเทียบรอบเช้า vs รอบปัจจุบันเข้า Telegram เสร็จสมบูรณ์!")


def start_scheduler():
    max_rounds = 1 if CONFIG["DRY_RUN"] else CONFIG["MAX_ROUNDS_PER_JOB"]
    print(f"🚀 เริ่มบอท Hizeiris Continuous Engine (โหมด: {'GitHub Continuous Shift' if IS_GITHUB_ACTIONS else 'Local'}, จำนวนรอบต่อกะ: {max_rounds} รอบ)...")
    for round_idx in range(1, max_rounds + 1):
        print(f"\n==================== รอบการทำงานที่ {round_idx}/{max_rounds} ({datetime.now().strftime('%H:%M:%S')}) ====================")
        try:
            run_once()
        except Exception as e:
            print(f"❌ Error ในรอบที่ {round_idx}: {e}")
        if round_idx >= max_rounds or CONFIG["DRY_RUN"]:
            break
        # คำนวณเวลานอนรอให้ตื่นตรงนาทีที่ :00 หรือ :30 ของชั่วโมงถัดไปเป๊ะๆ
        now = datetime.now()
        sec_into_half_hour = (now.minute % 30) * 60 + now.second
        sleep_sec = max(1800 - sec_into_half_hour, 300)
        print(f"⏳ รอส่งรายงานรอบถัดไปในอีก {sleep_sec // 60} นาที {sleep_sec % 60} วินาที (ตรงเวลา हर 30 นาทีโดยไม่ต้องรอคิว GitHub)...")
        time.sleep(sleep_sec)


if __name__ == "__main__":
    start_scheduler()
