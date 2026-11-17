"""
================================================================================
HIZEIRIS AUTO BOT — 100% SYNCED SPOT CHART PRICE + VALIDATED SINGLE-ENTRY TP/SL
================================================================================
1. แก้ปัญหา "ราคาในภาพกราฟ Spot XAU/USD ไม่ตรงกับตัวเลขในข้อความรายงาน":
   - ดึงราคา Spot XAU/USD สดจาก TradingView (OANDA:XAUUSD API + อ่านค่าปิดจริงจาก DOM ของกราฟ TradingView)
   - อัปเดตตัวเลขบนหัวกราฟ, แถบสีเหลือง "👉 ราคา XAU/USD (Spot) ณ ปัจจุบัน", คำบรรยายใต้ภาพที่ 4
     และข้อความวิเคราะห์ทั้ง 5 ส่วนใน Telegram ให้ใช้ "ราคา Spot XAU/USD ตัวเดียวกันเป๊ะ 100%"
   - ดึงราคา Futures ล่าสุดจากช่อง "ล่าสุด XXXX.X" ในแท็บ SD·diff คู่กับราคา Futures ของรอบเก็บ OI
2. แก้ปัญหาข้อความด้านขวาของ [ภาพที่ 4] ล้นขอบจอ:
   - ปรับดีไซน์ผังบันไดราคาด้านขวาใหม่ให้กระชับ คมชัด มี flex-shrink: 0 ที่ตัวเลขราคาด้านขวาสุด
     รับประกันว่าตัวเลขราคาและ TP/SL ไม่ตกขอบแม้แต่ตัวอักษรเดียว
3. คงระบบตรวจสอบสมการจุดเข้า–ออก (Single Exact Entry: BUY TP > Entry > SL | SELL TP < Entry < SL) ครบ 100%
================================================================================
"""

import base64
import html as html_lib
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
                                return re.sub(r"\*{2,}", "*", part["text"])
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
    latest_gc_fut: Optional[float]
    future_chg: float
    daily_open_fut: float
    daily_open_spot: float
    open_iv: float
    open_source: str
    diff: float
    raw_market_diff: Optional[float]
    diff_source: str
    dte: float
    fixed_dte: float
    atm_iv: float
    vol_chg: float
    vol_settle_atm: float
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


def fetch_realtime_xauusd_spot(ref_spot: float) -> Optional[float]:
    """
    ดึงราคา Spot XAU/USD สดจาก TradingView Scanner API (OANDA:XAUUSD)
    เพื่อให้ราคาเริ่มต้นตรงกับกราฟ TradingView OANDA:XAUUSD ก่อนแคปภาพ
    """
    try:
        url = "https://scanner.tradingview.com/cfd/scan"
        payload = {
            "symbols": {"tickers": ["OANDA:XAUUSD", "FX_IDC:XAUUSD", "FOREXCOM:XAUUSD"]},
            "columns": ["close"],
        }
        resp = requests.post(url, json=payload, timeout=6)
        if resp.status_code == 200:
            rows = resp.json().get("data", [])
            for r in rows:
                d = r.get("d", [])
                if d and d[0] is not None:
                    px = round(float(d[0]), 2)
                    if _is_valid_gold_price(px, ref_spot):
                        print(f"📡 ดึงราคา Spot XAU/USD สดจาก TradingView API สำเร็จ: {px:,.2f}")
                        return px
    except Exception as e:
        print(f"ℹ️ ข้าม TradingView Scanner API ({e})")
    return None


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
        regime = f"IV Crush รุนแรง (Vol สด {atm_live:.2f}% ต่ำกว่า Vol Settle {vol_settle:.2f}% อยู่ {vol_chg:+.2f}% | เทียบ IV เช้า {open_iv:.2f}% = {diff_vs_open:+.2f}%)"
        impact = "เส้นเขียว (Vol สด) ยุบตัวต่ำกว่าเส้นประแดง -> ดูดซับแรงกระแทกและเร่งแรงดีดกลับเข้าหากรอบ SD ตอนเปิด (Vanna Bounce แข็งแกร่ง)"
    elif vol_chg < 0:
        regime = f"IV Crush เบาบาง (Vol สด {atm_live:.2f}% ต่ำกว่า Vol Settle {vol_settle:.2f}% อยู่ {vol_chg:+.2f}% | เทียบ IV เช้า {open_iv:.2f}% = {diff_vs_open:+.2f}%)"
        impact = "ความผันผวนสดลดต่ำกว่าฐาน Settle -> สนับสนุนการดักเล่นตามกรอบ Fixed SD ตอนเปิด (1SD/2SD/3SD)"
    elif vol_chg >= 1.0:
        regime = f"Volatility Expansion รุนแรง (Vol สด {atm_live:.2f}% พุ่งสูงกว่า Vol Settle {vol_settle:.2f}% อยู่ {vol_chg:+.2f}% | เทียบ IV เช้า {open_iv:.2f}% = {diff_vs_open:+.2f}%)"
        impact = "เส้นเขียว (Vol สด) ยกตัวเหนือเส้นประแดง -> ระวังการฉีกกรอบทะลุ ±1SD ไปหา ±2SD (เน้นดักที่ ±2SD / ±3SD เป็นหลัก)"
    else:
        regime = f"Vol สมดุลเกาะฐาน (Vol สด {atm_live:.2f}% vs Vol Settle {vol_settle:.2f}% | เทียบ IV เช้า {open_iv:.2f}% = {diff_vs_open:+.2f}%)"
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
            "note": "ตลาดมีพลังงานสูง",
        }
    elif atm_iv <= 13.5 and abs(vol_chg) < 0.8:
        return {
            "regime_name": "4. โซนความผันผวนต่ำ (Low Vol / Sideways)",
            "win_rate": "64.50%",
            "adj": "-12.33% (เทียบค่าฐาน 2 ปี 76.83%)",
            "note": "ตลาดไร้ทิศทาง/วันหยุดธนาคาร เน้นดักกรอบแคบ",
        }
    return {
        "regime_name": "2. โซนความผันผวนปกติ (Normal Vol Day) & ค่าฐาน 2 ปี (2-Year Baseline)",
        "win_rate": "81.20% (ค่าฐาน 2 ปี = 76.83% | กรองด้วย OI พุ่งเป็น 78.00%–84.00%)",
        "adj": "+4.37% เหนือค่าฐาน",
        "note": "สภาพคล่องมาตรฐาน เทรดตามกรอบ Fixed SD (DTE 0.8) และแรงโน้มถ่วง OI มาตรฐาน",
    }


def evaluate_module4_microstructure(snap: HizeirisSnapshot, spot: float, open_spot: float, one_sd: float) -> Dict[str, str]:
    price_up = (spot - open_spot) >= 0
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
            "State 2: Negative Gamma Squeeze (Amaya et al., 2025: ดันความผันผวนรายวันพุ่ง +3.3% และราย 30 นาทีพุ่งสูงสุด +6.4% | "
            "ระวังลำดับบีบ Dealer 4 เฟส: 50Δ -> 25Δ -> 35Δ-45Δ Vanna Spike -> 50Δ+ Final Flush)"
        )
    elif abs(z) <= 1.2 and snap.vol_chg <= 0.2:
        hmm_state = (
            "State 3: Positive Gamma Mean Reversion (Dim et al., 2025: Net Gamma > 0, β = -0.283 (p<0.01) และ Adams et al., 2025: "
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
            f"รถอยู่ใกล้ฐานรับฝั่งล่าง Put Wall {int(snap.put_wall)} (Spot {snap.put_wall - snap.diff:.2f}, ห่าง {dist_to_put:.1f} จุด) -> "
            "เข้าสู่ 'จังหวะที่ 2–3': หากแรงขายเจาะผ่านกำแพง Put Wall ไม่ได้ รถที่ไม่มีเบรกจะแวะเติมน้ำมัน (สะสม Volume ใหม่) แล้วดีดกลับขึ้นไปหาปั๊มถัดไป"
        )
    elif dist_to_call <= 20.0:
        car_model = (
            f"รถกำลังไต่ความชันเส้น Vol Settle ขึ้นไปใกล้ปั๊มปลายทาง Call Wall {int(snap.call_wall)} (Spot {snap.call_wall - snap.diff:.2f}, ห่าง {dist_to_call:.1f} จุด) -> "
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
        else f"✅ DTE ปัจจุบัน = {snap.dte:.2f} (ยังไม่อยู่ในช่วง 30–60 นาทีสุดท้าย DTE < 0.04) -> ดักเทรดตามกรอบ Fixed SD และกำแพง OI ได้ตามปกติ"
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
            "zone_name": f"โซนที่ 1: 0.0SD ถึง ±0.5SD ({side}, {z:+.2f}SD / 45Δ–35Δ) — โซนสมดุลใกล้ราคาเปิด (No-Trade Reversal Zone)",
            "stats": "แตะ 92.6%–100% | วิ่งออกหา ±1.0SD สูงถึง 84.0% (500d แกว่งไม่พ้น <1SD ทั้งวัน 30%) | ห้ามดักสวนกลางอากาศ ให้รอเข้าที่กรอบ ±1SD / ±2SD หรือกำแพง OI",
        }
    elif az < 1.0:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 2: ±0.5SD ถึง ±1.0SD ({side}, {z:+.2f}SD / 35Δ–25Δ) — โซนเดินทางเข้าหา ±1SD (Inner Transition Zone)",
            "stats": "โอกาสแตะ ±1.0SD = 70.0% (500d) ถึง 77.8% (27d) | ดีดกลับเดี่ยว 60%–65% | ทับ OI = 72%–76% | ทับ OI + IV Crush = 80.0% | เป้า $6–$10",
        }
    elif az < 1.5:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 3: ±1.0SD ถึง ±1.5SD ({side}, {z:+.2f}SD / เส้นเขียว ±1 / 25Δ) — โซนปะทะด่านแรก (First Action / Drift Zone)",
            "stats": "แตะ 51.9%–77.8% | ย่อ/เด้งสั้น $8–$12 ชนะ 70.0%–78.53% (เอเชีย 82.0%) แต่กลับตัวคำใหญ่ >=$25 แค่ 55.71% (มีโอกาสไหลต่อไป ±2SD ถึง 40.0%) | ทับ OI + IV Crush = 86%–88%",
        }
    elif az < 2.0:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 4: ±1.5SD ถึง ±2.0SD ({side}, {z:+.2f}SD / เส้นเขียว ±2 / 15Δ) — โซนสังหารหลัก (Primary Kill Zone)",
            "stats": "แตะเพียง 25.9%–28.0% ของวัน | ดีดกลับคำใหญ่ >=$25 ชนะ 78.57%–85.58% | ทับกำแพง OI + IV Crush สูงสุดถึง 91.4%! (โอกาสไหลไป 3SD เพียง 25.0%) | เป้า $15–$25",
        }
    elif az < 2.5:
        return {
            "sd_ratio": z,
            "zone_name": f"โซนที่ 5: ±2.0SD ถึง ±2.5SD ({side}, {z:+.2f}SD / 15Δ–5Δ) — โซนตึงตัวขั้นสุด (Extreme Mean Reversion Zone)",
            "stats": "แตะเพียง 11.1%–25.9% | ดีดกลับคำใหญ่ $25–$35 ชนะ 78.0%–85.58% | ทับ OI + IV Crush = 91.4%+",
        }
    return {
        "sd_ratio": z,
        "zone_name": f"โซนที่ 6: ±2.5SD ถึง ±3.0SD ({side}, {z:+.2f}SD / เส้นเขียว ±3 / 5Δ) — โซนปลายหาง (Extreme Gamma Risk Zone)",
        "stats": "แตะเพียง 3.7%–7.0% ของวัน (จบสวิง 96.3%) | ฝืนรับมีดไม้แรกชนะ <55.0% แต่ถ้ารอหยุดไหลเกิด Absorption ชนะ 86.75%–95.0% (500d >=$25 = 91.43%) | เป้า $30–$45+",
    }


def _build_morning_vs_current_comparison(
    snap: HizeirisSnapshot,
    open_spot: float,
    spot: float,
    tb_morning: Dict[str, float],
    cme_dyn: Dict[str, float],
    prev_state: Optional[Dict],
) -> Dict[str, str]:
    delta_px_from_open = round(spot - open_spot, 2)
    z_from_open = round(delta_px_from_open / max(tb_morning["range_1sd"], 1.0), 2)
    delta_iv_from_open = round(snap.atm_iv - snap.open_iv, 2)

    curr_iv_dte08_1sd = round((spot + snap.diff) * (snap.atm_iv / 100.0) * math.sqrt(snap.fixed_dte / 365.0), 2)
    delta_1sd_dte08 = round(curr_iv_dte08_1sd - tb_morning["range_1sd"], 2)
    delta_1sd_dyn = round(cme_dyn["1SD_pts"] - tb_morning["range_1sd"], 2)

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
            confluences.append(f"{label_m} ({px_m:,.2f}) อยู่ใกล้ Call Wall ปัจจุบัน ({call_spot:,.2f}, ห่าง {abs(px_m - call_spot):.2f} จุด)")
        if abs(px_m - put_spot) <= 12.0:
            confluences.append(f"{label_m} ({px_m:,.2f}) อยู่ใกล้ Put Wall ปัจจุบัน ({put_spot:,.2f}, ห่าง {abs(px_m - put_spot):.2f} จุด)")
        for label_d, px_d in [
            ("Dynamic +1SD รอบนี้", cme_dyn["+1SD_spot"]),
            ("Dynamic +2SD รอบนี้", cme_dyn["+2SD_spot"]),
            ("Dynamic -1SD รอบนี้", cme_dyn["-1SD_spot"]),
            ("Dynamic -2SD รอบนี้", cme_dyn["-2SD_spot"]),
        ]:
            if abs(px_m - px_d) <= 8.0:
                confluences.append(f"{label_m} ({px_m:,.2f}) ซ้อนทับ {label_d} ({px_d:,.2f})")

    confluence_str = "\n   • ".join(confluences[:4]) if confluences else "กรอบ Fix เช้าเป็นแนวหลักคงที่ ส่วนกรอบ Dynamic รอบนี้หดแคบลงตามเวลา DTE ที่ลดลง"

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
            f"เทียบกับรอบก่อนหน้า ({prev_time}):\n"
            f"   • ราคา Spot เปลี่ยน: `{d_spot_prev:+.2f}` จุด (`{prev_spot:,.2f}` -> `{spot:,.2f}`)\n"
            f"   • Vol สด เปลี่ยน: `{d_iv_prev:+.2f}%` (`{prev_iv:.2f}%` -> `{snap.atm_iv:.2f}%`)\n"
            f"   • สัญญา Intraday เปลี่ยน: `Call {d_ic:+,}` / `Put {d_ip:+,}` สัญญา"
        )
    else:
        vs_prev_text = "เป็นรอบเริ่มต้นของการบันทึกเปรียบเทียบในชุดสถานะปัจจุบัน"

    morning_vs_now_summary = (
        f"1) *ราคาเทียบตอนเช้า (06:30)*:\n"
        f"   • เปิดเช้า `{open_spot:,.2f}` -> ปัจจุบัน `{spot:,.2f}` (เปลี่ยน *{delta_px_from_open:+.2f} จุด* = *{z_from_open:+.2f} SD เช้า*)\n"
        f"2) *ความผันผวนเทียบตอนเช้า*:\n"
        f"   • IV ตอนเปิดเช้า `{snap.open_iv:.2f}%` -> ATM Vol สดรอบนี้ `{snap.atm_iv:.2f}%` (เปลี่ยน *{delta_iv_from_open:+.2f}%* | Vol Settle `{snap.vol_settle_atm:.2f}%`)\n"
        f"3) *ขนาดกรอบ 1SD เช้า vs รอบปัจจุบัน*:\n"
        f"   • กรอบ Fix เช้า (DTE 0.8): `1SD = ±{tb_morning['range_1sd']:.2f}` จุด\n"
        f"   • คิดด้วย Vol สดรอบนี้ (DTE 0.8): `1SD = ±{curr_iv_dte08_1sd:.2f}` จุด (`{delta_1sd_dte08:+.2f}`)\n"
        f"   • กรอบ Dynamic รอบนี้ (DTE จริง `{snap.dte}`): `1SD = ±{cme_dyn['1SD_pts']:.2f}` จุด (`{delta_1sd_dyn:+.2f}` จากกรอบเช้า)\n"
        f"4) *จุดซ้อนทับสำคัญ (Double Confluence)*:\n"
        f"   • {confluence_str}\n"
        f"5) *การเปลี่ยนแปลงจากรอบก่อนหน้า (Δ t-1 -> t)*:\n"
        f"   {vs_prev_text}"
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


# ==============================================================================
# 2.5 ระบบสร้างใบสั่งเทรดแบบ "พิกัดเดียวชัดเจน + ตรวจสอบสมการ TP/SL 100%"
# ==============================================================================
def build_verified_order_ticket(
    side: str,
    zone_title: str,
    short_title: str,
    entry_spot: float,
    diff: float,
    tp1_pts: float,
    tp2_pts: float,
    sl_normal_pts: float,
    win_rate_text: str,
    research_play: str,
    include_bundle_6x: bool = False,
    structural_sl_spot: Optional[float] = None,
    structural_sl_note: str = "",
) -> Dict[str, object]:
    side_u = side.upper().strip()
    e_spot = round(float(entry_spot), 2)
    e_fut = round(e_spot + float(diff), 2)
    d_tp1 = abs(float(tp1_pts))
    d_tp2 = max(abs(float(tp2_pts)), d_tp1 + 1.0)
    d_sl = abs(float(sl_normal_pts))
    rr_ratio = round(d_tp2 / max(d_sl, 1.0), 1)

    if side_u == "BUY":
        tp1_spot = round(e_spot + d_tp1, 2)
        tp2_spot = round(e_spot + d_tp2, 2)
        sl_norm_spot = round(e_spot - d_sl, 2)
        sl1_cut_spot = round(e_spot - 12.5, 2)
        rec1_flip_tp_spot = round(e_spot - 25.0, 2)
        sl2_cut_spot = round(e_spot - 25.0, 2)
        rec2_flip_tp_spot = round(e_spot - 37.5, 2)

        assert tp2_spot > tp1_spot > e_spot, f"Invalid BUY TP: {tp2_spot}, {tp1_spot} vs Entry {e_spot}"
        assert e_spot > sl_norm_spot, f"Invalid BUY SL: {sl_norm_spot} vs Entry {e_spot}"
        assert e_spot > sl1_cut_spot > rec1_flip_tp_spot, f"Invalid BUY Recovery: {sl1_cut_spot} -> {rec1_flip_tp_spot}"

        tp_line = (
            f"TP1: `{tp1_spot:,.2f}` (ราคาขึ้น `+{d_tp1:.0f}` จุด = กำไร `+${d_tp1:.0f}`) | "
            f"TP2: `{tp2_spot:,.2f}` (ราคาขึ้น `+{d_tp2:.0f}` จุด = กำไร `+${d_tp2:.0f}`)"
        )
        sl_norm_line = f"`{sl_norm_spot:,.2f}` (ต่ำกว่าจุดเข้า `-{d_sl:.0f}` จุด | R:R `1:{rr_ratio}`)"
        if structural_sl_spot is not None and structural_sl_spot < sl_norm_spot:
            sl_norm_line += f" | SL กันสะบัดใต้แนวถัดไป: `{structural_sl_spot:,.2f}` ({structural_sl_note})"

        rec1_line = (
            f"คัต BUY ที่ `{sl1_cut_spot:,.2f}` (`-12.5` จุด) แล้วสลับเปิด *SELL ทบ 2x* "
            f"-> เป้าปิดทำกำไรขา SELL ที่ `{rec1_flip_tp_spot:,.2f}` (ลงต่ออีก `12.5` จุด = กำไรสุทธิ `+$12.5`)"
        )
        rec2_line = (
            f"กรณีถัว BUY ไม้สองที่ `{sl1_cut_spot:,.2f}` (เด้งกลับปิดรวบที่ `{e_spot:,.2f}`) | "
            f"หากหลุดถึง `{sl2_cut_spot:,.2f}` (`-25.0` จากไม้แรก) คัตรวบ 2 ไม้แล้วสลับเปิด *SELL ทบ 6x* -> เป้าปิดขา SELL ที่ `{rec2_flip_tp_spot:,.2f}`"
            if include_bundle_6x else ""
        )
    else:
        tp1_spot = round(e_spot - d_tp1, 2)
        tp2_spot = round(e_spot - d_tp2, 2)
        sl_norm_spot = round(e_spot + d_sl, 2)
        sl1_cut_spot = round(e_spot + 12.5, 2)
        rec1_flip_tp_spot = round(e_spot + 25.0, 2)
        sl2_cut_spot = round(e_spot + 25.0, 2)
        rec2_flip_tp_spot = round(e_spot + 37.5, 2)

        assert tp2_spot < tp1_spot < e_spot, f"Invalid SELL TP: {tp2_spot}, {tp1_spot} vs Entry {e_spot}"
        assert e_spot < sl_norm_spot, f"Invalid SELL SL: {sl_norm_spot} vs Entry {e_spot}"
        assert e_spot < sl1_cut_spot < rec1_flip_tp_spot, f"Invalid SELL Recovery: {sl1_cut_spot} -> {rec1_flip_tp_spot}"

        tp_line = (
            f"TP1: `{tp1_spot:,.2f}` (ราคาลง `-{d_tp1:.0f}` จุด = กำไร `+${d_tp1:.0f}`) | "
            f"TP2: `{tp2_spot:,.2f}` (ราคาลง `-{d_tp2:.0f}` จุด = กำไร `+${d_tp2:.0f}`)"
        )
        sl_norm_line = f"`{sl_norm_spot:,.2f}` (สูงกว่าจุดเข้า `+{d_sl:.0f}` จุด | R:R `1:{rr_ratio}`)"
        if structural_sl_spot is not None and structural_sl_spot > sl_norm_spot:
            sl_norm_line += f" | SL กันสะบัดเหนือแนวถัดไป: `{structural_sl_spot:,.2f}` ({structural_sl_note})"

        rec1_line = (
            f"คัต SELL ที่ `{sl1_cut_spot:,.2f}` (`+12.5` จุด) แล้วสลับเปิด *BUY ทบ 2x* "
            f"-> เป้าปิดทำกำไรขา BUY ที่ `{rec1_flip_tp_spot:,.2f}` (ขึ้นต่ออีก `12.5` จุด = กำไรสุทธิ `+$12.5`)"
        )
        rec2_line = (
            f"กรณีถัว SELL ไม้สองที่ `{sl1_cut_spot:,.2f}` (ย่อกลับปิดรวบที่ `{e_spot:,.2f}`) | "
            f"หากทะลุถึง `{sl2_cut_spot:,.2f}` (`+25.0` จากไม้แรก) คัตรวบ 2 ไม้แล้วสลับเปิด *BUY ทบ 6x* -> เป้าปิดขา BUY ที่ `{rec2_flip_tp_spot:,.2f}`"
            if include_bundle_6x else ""
        )

    return {
        "side": side_u,
        "zone": zone_title,
        "short_title": short_title,
        "entry_spot_num": e_spot,
        "entry_fut_num": e_fut,
        "entry_spot": f"{e_spot:,.2f}",
        "entry_fut": f"{e_fut:,.2f}",
        "tp1_num": tp1_spot,
        "tp2_num": tp2_spot,
        "tp1": f"{tp1_spot:,.2f}",
        "tp2": f"{tp2_spot:,.2f}",
        "tp1_pts": d_tp1,
        "tp2_pts": d_tp2,
        "tp_line": tp_line,
        "sl_normal_num": sl_norm_spot,
        "sl_bozovic": f"{sl_norm_spot:,.2f}",
        "sl_norm_line": sl_norm_line,
        "sl1_num": sl1_cut_spot,
        "sl1": f"{sl1_cut_spot:,.2f}",
        "rec1_2x_tp_num": rec1_flip_tp_spot,
        "rec1_2x_tp": f"{rec1_flip_tp_spot:,.2f}",
        "rec1_line": rec1_line,
        "sl2": f"{sl2_cut_spot:,.2f}" if include_bundle_6x else "",
        "rec2_6x_tp": f"{rec2_flip_tp_spot:,.2f}" if include_bundle_6x else "",
        "rec2_line": rec2_line,
        "win_rate": win_rate_text,
        "research_play": research_play,
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
        if abs(px - wall_px) <= 12.0:
            notes.append(f"🔥 อยู่ใกล้ {wall_name} ปัจจุบัน Spot {wall_px:,.2f} (Fut {wall_fut:.0f}, ห่าง {abs(px - wall_px):.2f} จุด) -> Confluence Win Rate 86.0%–91.4%!")
        else:
            notes.append(f"ห่าง {wall_name} ปัจจุบัน ({wall_px:,.2f}) อยู่ {abs(px - wall_px):.2f} จุด")
        notes.append(f"เทียบ {dyn_label} รอบนี้อยู่ที่ {dyn_px:,.2f} (ต่างกัน {px - dyn_px:+.2f} จุด)")
        return " | ".join(notes)

    return [
        build_verified_order_ticket(
            side="SELL",
            zone_title="🔴 โซนที่ 1: 1SD SELL (sell spot 1sd เช้า — 25ΔC Drift Zone)",
            short_title="1SD SELL เช้า (เก็บย่อสั้น 25ΔC)",
            entry_spot=sell_1sd,
            diff=diff,
            tp1_pts=8.0,
            tp2_pts=12.0,
            sl_normal_pts=8.0,
            win_rate_text=(
                f"แตะในวัน 70.0%–77.8% | ย่อตัวสั้น $8–$12 ชนะ 70.0%–78.53% (เอเชีย 82.0%) "
                f"| กลับตัว >=$25 แค่ 55.71% (โอกาสไหลไป 2SD = 40.0%) | "
                f"{_conf_note(sell_1sd, call_spot, 'Call Wall', snap.call_wall, 'Dynamic +1SD', cme_dyn['+1SD_spot'])}"
            ),
            research_play="ดัก SELL ที่พิกัด 1SD SELL เก็บย่อสั้น $8–$12 (TP1/TP2 ต่ำกว่าจุดเข้า) แล้วล็อกกำไรทันที",
            include_bundle_6x=False,
        ),
        build_verified_order_ticket(
            side="SELL",
            zone_title="🔴 โซนที่ 2: 2SD SELL (sell spot 2sd เช้า — 15ΔC Primary Kill Zone โซนหลัก)",
            short_title="2SD SELL เช้า (Kill Zone คำใหญ่ 15ΔC)",
            entry_spot=sell_2sd,
            diff=diff,
            tp1_pts=15.0,
            tp2_pts=25.0,
            sl_normal_pts=8.0,
            win_rate_text=(
                f"แตะเพียง 25.9%–28.0% | ดีดกลับ >=$25 ชนะ 78.57%–85.58% (ทับ OI + IV Crush พุ่งถึง 91.4%!) | "
                f"{_conf_note(sell_2sd, call_spot, 'Call Wall', snap.call_wall, 'Dynamic +2SD', cme_dyn['+2SD_spot'])}"
            ),
            research_play="จุดดัก SELL หลักประจำวัน! รอสัญญาณ Absorption + Vol Chg หดตัวแล้วถือเอาเป้าลง $15–$25",
            include_bundle_6x=True,
        ),
        build_verified_order_ticket(
            side="SELL",
            zone_title="🔴 โซนที่ 3: 3SD SELL (sell spot 3sd เช้า — 5ΔC Extreme Tail โซนปลายหาง)",
            short_title="3SD SELL เช้า (Extreme Tail 5ΔC)",
            entry_spot=sell_3sd,
            diff=diff,
            tp1_pts=25.0,
            tp2_pts=35.0,
            sl_normal_pts=8.0,
            win_rate_text=f"แตะเพียง 3.7%–7.0% (จบสวิง 96.3%) | รอหยุดไหลชนะ 86.75%–95.0% (500d >=$25 = 91.43%) | เทียบ Dynamic +3SD รอบนี้อยู่ที่ {cme_dyn['+3SD_spot']:,.2f}",
            research_play="ดักทุบคำใหญ่ $25–$35! ต้องรอให้ราคาหยุดไหลและเส้นเขียว Vol สด พีคแล้วหักหัวลงก่อนเข้า",
            include_bundle_6x=True,
        ),
        build_verified_order_ticket(
            side="BUY",
            zone_title="🟢 โซนที่ 4: 1SD BUY (buy spot 1sd เช้า — 25ΔP Drift Zone)",
            short_title="1SD BUY เช้า (เก็บเด้งสั้น 25ΔP)",
            entry_spot=buy_1sd,
            diff=diff,
            tp1_pts=8.0,
            tp2_pts=12.0,
            sl_normal_pts=8.0,
            win_rate_text=(
                f"แตะในวัน 70.0%–77.8% | เด้งสั้น $8–$12 ชนะ 70.0%–78.53% (เอเชีย 82.0%) "
                f"| กลับตัว >=$25 แค่ 55.71% (โอกาสไหลไป -2SD = 40.0%) | "
                f"{_conf_note(buy_1sd, put_spot, 'Put Wall', snap.put_wall, 'Dynamic -1SD', cme_dyn['-1SD_spot'])}"
            ),
            research_play="ดัก BUY ที่พิกัด 1SD BUY เก็บเด้งสั้น $8–$12 (TP1/TP2 สูงกว่าจุดเข้า) ห้ามไล่ทุบซื้อ Put ก้นหลุมเพราชนะแค่ 30%–40%!",
            include_bundle_6x=False,
        ),
        build_verified_order_ticket(
            side="BUY",
            zone_title="🟢 โซนที่ 5: 2SD BUY (buy spot 2sd เช้า — 15ΔP Primary Kill Zone โซนหลัก)",
            short_title="2SD BUY เช้า (Kill Zone คำใหญ่ 15ΔP)",
            entry_spot=buy_2sd,
            diff=diff,
            tp1_pts=15.0,
            tp2_pts=25.0,
            sl_normal_pts=8.0,
            win_rate_text=(
                f"แตะเพียง 25.9%–28.0% | ดีดกลับ >=$25 ชนะ 78.57%–85.58% (ทับ OI + IV Crush พุ่งถึง 91.4%!) | "
                f"{_conf_note(buy_2sd, put_spot, 'Put Wall', snap.put_wall, 'Dynamic -2SD', cme_dyn['-2SD_spot'])}"
            ),
            research_play="จุดดัก BUY หลักประจำวัน (Sweet Spot Buy)! เมื่อแรงขายหยุดลง ค่า IV ที่หดตัวจะจุดชนวน Vanna Rally ดีดกลับ $15–$25",
            include_bundle_6x=True,
        ),
        build_verified_order_ticket(
            side="BUY",
            zone_title="🟢 โซนที่ 6: 3SD BUY (buy spot 3sd เช้า — 5ΔP Extreme Tail โซนปลายหาง)",
            short_title="3SD BUY เช้า (Extreme Tail 5ΔP)",
            entry_spot=buy_3sd,
            diff=diff,
            tp1_pts=25.0,
            tp2_pts=35.0,
            sl_normal_pts=8.0,
            win_rate_text=f"แตะเพียง 3.7%–7.0% (จบสวิง 96.3%) | รอหยุดไหลชนะ 86.75%–95.0% (500d >=$25 = 91.43%) | เทียบ Dynamic -3SD รอบนี้อยู่ที่ {cme_dyn['-3SD_spot']:,.2f}",
            research_play="ดักช้อนคำใหญ่ $25–$35! รอสัญญาณ Shakeout / Order Absorption + IV พีคหักหัวลงแล้วช้อนถือรันกำไร",
            include_bundle_6x=True,
        ),
    ]


def _build_tactical_tickets_from_spot(
    spot: float,
    open_spot: float,
    diff: float,
    snap: HizeirisSnapshot,
    six_zone_plans: List[Dict[str, object]],
) -> Dict[str, List[Dict[str, object]]]:
    sell_1sd_t, sell_2sd_t, sell_3sd_t, buy_1sd_t, buy_2sd_t, buy_3sd_t = six_zone_plans
    put_spot = round(snap.put_wall - diff, 2)
    call_spot = round(snap.call_wall - diff, 2)
    buy_1sd_px = buy_1sd_t["entry_spot_num"]
    buy_2sd_px = buy_2sd_t["entry_spot_num"]
    sell_1sd_px = sell_1sd_t["entry_spot_num"]
    sell_2sd_px = sell_2sd_t["entry_spot_num"]

    buy_tickets: List[Dict[str, object]] = []
    if put_spot <= spot + 3.0 and abs(put_spot - buy_1sd_px) > 6.0 and abs(put_spot - buy_2sd_px) > 6.0:
        struct_sl = round(buy_1sd_px - 5.0, 2) if 0 < (put_spot - buy_1sd_px) <= 15.0 else None
        pw_ticket = build_verified_order_ticket(
            side="BUY",
            zone_title=f"🧱 ด่านรับกำแพงออปชัน: Put Wall ({snap.put_wall_pc})",
            short_title=f"กำแพง Put Wall (Fut {snap.put_wall:.0f})",
            entry_spot=put_spot,
            diff=diff,
            tp1_pts=10.0,
            tp2_pts=25.0,
            sl_normal_pts=8.0,
            win_rate_text="เด้งกลับจากกำแพง Put Wall ชนะ 72.5%–85.0% (ห้ามไล่ทุบซื้อ Put ก้นหลุมเพราชนะแค่ 30%–40%)",
            research_play="ดัก BUY เมื่อราคาลงมาแตะกำแพง Put Wall แล้วแรงขายชะลอตัว เก็บเด้ง +10 ถึง +25 จุด",
            include_bundle_6x=False,
            structural_sl_spot=struct_sl,
            structural_sl_note="เผื่อไส้ลงไปแตะ 1SD BUY",
        )
        buy_tickets.append(pw_ticket)

    buy_tickets.extend([buy_1sd_t, buy_2sd_t, buy_3sd_t])
    buy_tickets.sort(key=lambda x: float(x["entry_spot_num"]), reverse=True)

    sell_tickets: List[Dict[str, object]] = []
    if call_spot >= spot - 3.0 and abs(call_spot - sell_1sd_px) > 6.0 and abs(call_spot - sell_2sd_px) > 6.0:
        struct_sl = round(sell_1sd_px + 5.0, 2) if 0 < (sell_1sd_px - call_spot) <= 15.0 else None
        cw_ticket = build_verified_order_ticket(
            side="SELL",
            zone_title=f"🧱 ด่านต้านกำแพงออปชัน: Call Wall ({snap.call_wall_pc})",
            short_title=f"กำแพง Call Wall (Fut {snap.call_wall:.0f})",
            entry_spot=call_spot,
            diff=diff,
            tp1_pts=10.0,
            tp2_pts=25.0,
            sl_normal_pts=8.0,
            win_rate_text="ชนกำแพง Call Wall แล้วเด้งกลับลงชนะ 72.5%–85.0%",
            research_play="ดัก SELL เมื่อราคาขึ้นชนกำแพง Call Wall แล้วเกิดสัญญาณดูดซับแรงซื้อ เก็บย่อ -10 ถึง -25 จุด",
            include_bundle_6x=False,
            structural_sl_spot=struct_sl,
            structural_sl_note="เผื่อไส้ขึ้นไปแตะ 1SD SELL",
        )
        sell_tickets.append(cw_ticket)

    sell_tickets.extend([sell_1sd_t, sell_2sd_t, sell_3sd_t])
    sell_tickets.sort(key=lambda x: float(x["entry_spot_num"]))

    return {"buy_tickets": buy_tickets, "sell_tickets": sell_tickets}


def compute_exact_coordinates(
    snap: HizeirisSnapshot,
    prev_state: Optional[Dict],
    live_spot_override: Optional[float] = None,
) -> Dict:
    fut_round = snap.futures_price
    open_fut = round(snap.daily_open_fut if _is_valid_gold_price(snap.daily_open_fut, fut_round) else fut_round, 2)
    diff = round(snap.diff, 2)

    # ใช้ราคา Spot XAU/USD สดจากกราฟ TradingView (ถ้ามี) เพื่อให้ตรงกับ [ภาพที่ 4] 100.00%
    if live_spot_override is not None and _is_valid_gold_price(live_spot_override, fut_round):
        spot = round(float(live_spot_override), 2)
        spot_source = "กราฟสด TradingView (OANDA:XAUUSD)"
    elif snap.latest_gc_fut is not None and _is_valid_gold_price(snap.latest_gc_fut, fut_round):
        spot = round(float(snap.latest_gc_fut) - diff, 2)
        spot_source = f"ราคา Futures ล่าสุดบนแท็บ SD·diff ({snap.latest_gc_fut:,.1f}) - Diff ({diff:.2f})"
    else:
        spot = round(fut_round - diff, 2)
        spot_source = f"ราคา Futures รอบ {snap.round_str} ({fut_round:,.1f}) - Diff ({diff:.2f})"

    implied_live_fut = round(spot + diff, 2)
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
        dyn_1sd = round(fut_round * (snap.atm_iv / 100.0) * math.sqrt(max(snap.dte, 0.05) / 365.0), 1)
        c_sd = {
            "1SD_pts": dyn_1sd,
            "2SD_pts": round(dyn_1sd * 2, 1),
            "3SD_pts": round(dyn_1sd * 3, 1),
            "-1SD": round(fut_round - dyn_1sd, 1),
            "+1SD": round(fut_round + dyn_1sd, 1),
            "-2SD": round(fut_round - dyn_1sd * 2, 1),
            "+2SD": round(fut_round + dyn_1sd * 2, 1),
            "-3SD": round(fut_round - dyn_1sd * 3, 1),
            "+3SD": round(fut_round + dyn_1sd * 3, 1),
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
    tactical_tickets = _build_tactical_tickets_from_spot(
        spot, open_spot, diff, snap, six_zone_plans
    )
    first_buy = tactical_tickets["buy_tickets"][0]
    first_sell = tactical_tickets["sell_tickets"][0]

    z_val = current_sd_info["sd_ratio"]
    if abs(z_val) < 0.5 and abs(spot - first_buy["entry_spot_num"]) > 6.0 and abs(first_sell["entry_spot_num"] - spot) > 6.0:
        immediate_verdict = (
            f"⏳ *รอราคาวิ่งเข้าด่าน (Wait for Zone)*: ตอนนี้ราคา `{spot:,.2f}` อยู่ในโซนสมดุลใกล้ราคาเปิด (`{z_val:+.2f}SD`) "
            f"ห้ามดักสวนกลางอากาศ! ให้ตั้ง Pending แยกตามพิกัดด่านด้านล่าง (ด่านละ 1 ราคาชัดเจน):"
        )
    elif abs(spot - first_buy["entry_spot_num"]) <= 6.0:
        immediate_verdict = (
            f"🟢 *โซนเตรียมเข้า BUY หน้างาน*: ราคา `{spot:,.2f}` ลงมาใกล้ด่านรับแรก *{first_buy['short_title']}* ที่ `{first_buy['entry_spot']}` "
            f"รอแท่งหยุดไหลแล้วเข้า BUY เป้า `{first_buy['tp1']} / {first_buy['tp2']}` (SL ปกติ `{first_buy['sl_bozovic']}`)"
        )
    elif abs(first_sell["entry_spot_num"] - spot) <= 6.0:
        immediate_verdict = (
            f"🔴 *โซนเตรียมเข้า SELL หน้างาน*: ราคา `{spot:,.2f}` ขึ้นมาใกล้ด่านต้านแรก *{first_sell['short_title']}* ที่ `{first_sell['entry_spot']}` "
            f"รอสัญญาณดูดซับแรงซื้อแล้วเข้า SELL เป้า `{first_sell['tp1']} / {first_sell['tp2']}` (SL ปกติ `{first_sell['sl_bozovic']}`)"
        )
    else:
        immediate_verdict = (
            f"🎯 *วางใบสั่งดักตามด่านวิจัย (ห้ามผสมข้ามด่าน)*: ราคาปัจจุบัน `{spot:,.2f}` กำลังเดินทางระหว่างกรอบ "
            f"ด่านรับ BUY ใกล้สุดอยู่ที่ `{first_buy['entry_spot']}` และด่านต้าน SELL ใกล้สุดอยู่ที่ `{first_sell['entry_spot']}`"
        )

    if abs(spot - first_buy["entry_spot_num"]) <= abs(first_sell["entry_spot_num"] - spot):
        chosen = first_buy
    else:
        chosen = first_sell

    primary_setup = {
        "title": f"ดัก {chosen['side']} ที่ {chosen['short_title']} พิกัดเดียว `{chosen['entry_spot']}` (ห่างราคาปัจจุบัน `{abs(spot - chosen['entry_spot_num']):.2f}` จุด)",
        "entry": f"{chosen['side']} ที่ `{chosen['entry_spot']}` (Futures `{chosen['entry_fut']}`)",
        "win_rate": chosen["win_rate"],
        "tp": chosen["tp_line"],
        "sl": f"SL ปกติ: {chosen['sl_norm_line']} | หรือแก้พอร์ต Supa888: {chosen['rec1_line']}",
    }

    return {
        "spot": spot,
        "spot_source": spot_source,
        "implied_live_fut": implied_live_fut,
        "open_fut": open_fut,
        "open_spot": open_spot,
        "open_iv": open_iv,
        "fixed_dte": fixed_dte,
        "diff": diff,
        "immediate_verdict": immediate_verdict,
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
        "tactical_tickets": tactical_tickets,
        "primary_setup": primary_setup,
        "plan_c_exact": {
            "call_wall_break": round(call_spot + 12.5, 2),
            "call_squeeze_tp": round(call_spot + 25.0, 2),
            "call_squeeze_sl": round(call_spot + 4.5, 2),
            "put_wall_break": round(put_spot - 12.5, 2),
            "put_squeeze_tp": round(put_spot - 25.0, 2),
            "put_squeeze_sl": round(put_spot - 4.5, 2),
        },
    }


# ==============================================================================
# 3. สร้าง "[ภาพที่ 4] กราฟราคา Spot XAU/USD ปัจจุบัน & ผังพิกัด 6 โซน" พร้อมดึงราคาปิดจริงจากกราฟ
# ==============================================================================
def render_xauusd_spot_chart_and_sync_price(
    snap: HizeirisSnapshot,
    prev_state: Optional[Dict],
    initial_live_spot: Optional[float] = None,
) -> Tuple[Dict, Optional[Tuple[str, str]]]:
    """
    1. คำนวณพิกัดเริ่มต้นจากราคา Spot สด
    2. เปิดกราฟ TradingView OANDA:XAUUSD (15m) ใน Playwright
    3. อ่านตัวเลขราคาปิดจริง ("ปิด X,XXX.XXX") ที่แสดงบนหน้าจอกราฟ TradingView ณ วินาทีนั้น
    4. อัปเดตตัวเลขบนหัวกราฟ, แถบสีเหลือง "👉 ราคา XAU/USD (Spot) ณ ปัจจุบัน", และเรียงลำดับบันไดราคาใหม่ทันทีก่อนแคปภาพ!
    5. คืนค่า exact_calc ที่คำนวณด้วยราคา Spot เดียวกับบนกราฟ 100.00% เพื่อใช้สร้างข้อความรายงานใน Telegram
    """
    exact_calc = compute_exact_coordinates(snap, prev_state, live_spot_override=initial_live_spot)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = f"hizeiris_4_xauusd_spot_{ts}.png"
    tb = exact_calc["sd_table_exact"]
    ws = exact_calc["walls_spot"]
    mvc = exact_calc["morning_vs_current"]
    spot = exact_calc["spot"]
    open_spot = exact_calc["open_spot"]
    one_sd_range = tb["range_1sd"]

    ladder_items = [
        {
            "id": "sell3",
            "price": tb["sell_3sd"],
            "cls": "sell3",
            "title": "🔴 3SD SELL (ปลายหาง 5ΔC)",
            "sub": f"TP {tb['sell_3sd']-25:,.2f} / {tb['sell_3sd']-35:,.2f} | SL {tb['sell_3sd']+8:,.2f}",
        },
        {
            "id": "sell2",
            "price": tb["sell_2sd"],
            "cls": "sell2",
            "title": "🔥 2SD SELL (Kill Zone 15ΔC)",
            "sub": f"TP {tb['sell_2sd']-15:,.2f} / {tb['sell_2sd']-25:,.2f} | SL {tb['sell_2sd']+8:,.2f}",
        },
        {
            "id": "sell1",
            "price": tb["sell_1sd"],
            "cls": "sell1",
            "title": "🟠 1SD SELL (ย่อสั้น 25ΔC)",
            "sub": f"TP {tb['sell_1sd']-8:,.2f} / {tb['sell_1sd']-12:,.2f} | SL {tb['sell_1sd']+8:,.2f}",
        },
        {
            "id": "call_wall",
            "price": ws["call_wall_spot"],
            "cls": "wall",
            "title": f"🧱 Call Wall (Fut {snap.call_wall:.0f})",
            "sub": f"SELL TP {ws['call_wall_spot']-10:,.2f} / {ws['call_wall_spot']-25:,.2f} | SL {ws['call_wall_spot']+8:,.2f}",
        },
        {
            "id": "max_pain",
            "price": ws["max_pain_spot"],
            "cls": "wall",
            "title": f"🧲 Max Pain (Fut {snap.max_pain:.0f})",
            "sub": "จุดดูดแม่เหล็ก (ไม่ใช่จุดดักทุบ)",
        },
        {
            "id": "open_spot",
            "price": open_spot,
            "cls": "open-lvl",
            "title": "⚓ ราคาเปิดเช้า (06:30 Open)",
            "sub": f"Futures ตอนเปิด {exact_calc['open_fut']:,.2f} (หัก Diff {snap.diff:.2f})",
        },
        {
            "id": "spot_now",
            "price": spot,
            "cls": "spot-now",
            "title": "👉 ราคา XAU/USD (Spot) ปัจจุบัน",
            "sub": f"เปลี่ยนจากเช้า {mvc['delta_px_from_open']} จุด ({mvc['z_from_open']})",
        },
        {
            "id": "put_wall",
            "price": ws["put_wall_spot"],
            "cls": "wall",
            "title": f"🧱 Put Wall (Fut {snap.put_wall:.0f})",
            "sub": f"BUY TP {ws['put_wall_spot']+10:,.2f} / {ws['put_wall_spot']+25:,.2f} | SL {ws['put_wall_spot']-8:,.2f}",
        },
        {
            "id": "buy1",
            "price": tb["buy_1sd"],
            "cls": "buy1",
            "title": "🟢 1SD BUY (เด้งสั้น 25ΔP)",
            "sub": f"TP {tb['buy_1sd']+8:,.2f} / {tb['buy_1sd']+12:,.2f} | SL {tb['buy_1sd']-8:,.2f}",
        },
        {
            "id": "buy2",
            "price": tb["buy_2sd"],
            "cls": "buy2",
            "title": "🔥 2SD BUY (Kill Zone 15ΔP)",
            "sub": f"TP {tb['buy_2sd']+15:,.2f} / {tb['buy_2sd']+25:,.2f} | SL {tb['buy_2sd']-8:,.2f}",
        },
        {
            "id": "buy3",
            "price": tb["buy_3sd"],
            "cls": "buy3",
            "title": "🟢 3SD BUY (ปลายหาง 5ΔP)",
            "sub": f"TP {tb['buy_3sd']+25:,.2f} / {tb['buy_3sd']+35:,.2f} | SL {tb['buy_3sd']-8:,.2f}",
        },
    ]
    ladder_items.sort(key=lambda item: float(item["price"]), reverse=True)
    ladder_html = "\n".join(
        f'      <div class="lvl {it["cls"]}" id="row_{it["id"]}" data-price="{it["price"]:.2f}">'
        f'<div class="lvl-left"><span class="lvl-title">{it["title"]}</span><span class="lvl-sub" id="sub_{it["id"]}">{it["sub"]}</span></div>'
        f'<span class="px" id="px_{it["id"]}">{it["price"]:,.2f}</span></div>'
        for it in ladder_items
    )

    html_content = f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Thai:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; font-family: 'Noto Sans Thai', sans-serif; margin: 0; padding: 0; }}
  body {{ background: #0b0e14; color: #f1f5f9; width: 1600px; height: 980px; display: flex; overflow: hidden; }}
  .left-chart {{ width: 960px; height: 980px; border-right: 2px solid #1e293b; display: flex; flex-direction: column; }}
  .chart-header {{ height: 72px; background: #111827; padding: 10px 18px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #1e293b; }}
  .spot-badge {{ font-size: 26px; font-weight: 800; color: #fbbf24; }}
  .sub-badge {{ font-size: 13.5px; color: #94a3b8; margin-top: 2px; }}
  #tv_chart_container {{ flex: 1; width: 100%; height: 908px; }}
  .right-panel {{ width: 640px; height: 980px; background: #0f172a; padding: 12px 16px; display: flex; flex-direction: column; gap: 7px; overflow: hidden; }}
  .panel-title {{ font-size: 17px; font-weight: 800; color: #38bdf8; border-bottom: 1px solid #334155; padding-bottom: 5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .action-card {{ background: #1e293b; border-left: 4px solid #fbbf24; border-radius: 8px; padding: 8px 11px; font-size: 12.2px; line-height: 1.45; }}
  .action-row {{ display: flex; justify-content: space-between; gap: 6px; white-space: nowrap; }}
  .ladder {{ display: flex; flex-direction: column; gap: 4px; flex: 1; justify-content: space-between; }}
  .lvl {{ display: flex; justify-content: space-between; align-items: center; padding: 5px 11px; border-radius: 6px; gap: 8px; }}
  .lvl-left {{ display: flex; align-items: baseline; gap: 8px; min-width: 0; flex: 1; overflow: hidden; }}
  .lvl-title {{ font-size: 13px; font-weight: 700; white-space: nowrap; flex-shrink: 0; }}
  .lvl-sub {{ font-size: 11.8px; opacity: 0.92; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .sell3 {{ background: rgba(239,68,68,0.20); border: 1px solid #ef4444; color: #fca5a5; }}
  .sell2 {{ background: rgba(239,68,68,0.30); border: 2px solid #f87171; color: #fecaca; }}
  .sell1 {{ background: rgba(251,146,60,0.20); border: 1px solid #fb923c; color: #fdba74; }}
  .wall {{ background: rgba(56,189,248,0.16); border: 1px dashed #38bdf8; color: #bae6fd; }}
  .open-lvl {{ background: rgba(148,163,184,0.18); border: 1px solid #94a3b8; color: #e2e8f0; }}
  .spot-now {{ background: linear-gradient(90deg, #ca8a04, #eab308); color: #000; padding: 6px 11px; border-radius: 7px; box-shadow: 0 0 10px rgba(234,179,8,0.45); }}
  .spot-now .lvl-title, .spot-now .lvl-sub, .spot-now .px {{ color: #000 !important; font-weight: 800 !important; }}
  .buy1 {{ background: rgba(74,222,128,0.18); border: 1px solid #4ade80; color: #bbf7d0; }}
  .buy2 {{ background: rgba(34,197,94,0.28); border: 2px solid #22c55e; color: #dcfce7; }}
  .buy3 {{ background: rgba(22,163,74,0.20); border: 1px solid #16a34a; color: #86efac; }}
  .px {{ font-size: 15px; font-weight: 800; flex-shrink: 0; text-align: right; min-width: 72px; }}
</style>
</head>
<body>
  <div class="left-chart">
    <div class="chart-header">
      <div>
        <div class="spot-badge" id="hdr_spot">🥇 XAU/USD (Spot): {spot:,.2f}</div>
        <div class="sub-badge" id="hdr_sub_left">CME {html_lib.escape(snap.contract_name)} ({html_lib.escape(snap.round_str)}): Fut รอบ OI {snap.futures_price:,.1f} | หัก Diff: {snap.diff:.2f} | เวลา: {html_lib.escape(snap.timestamp)}</div>
      </div>
      <div style="text-align:right;">
        <div style="font-size:15px;font-weight:700;color:#38bdf8;">ราคาเปิดเช้า (06:30): {open_spot:,.2f}</div>
        <div class="sub-badge" id="hdr_sub_right">เปลี่ยนจากเช้า: {mvc['delta_px_from_open']} จุด ({mvc['z_from_open']}) | IV เช้า {snap.open_iv:.2f}% -> สด {snap.atm_iv:.2f}%</div>
      </div>
    </div>
    <div id="tv_chart_container"></div>
  </div>
  <div class="right-panel">
    <div class="panel-title">📐 ผังพิกัดจุดเข้า–ออก XAU/USD (จุดเข้าเดียวชัดเจน TP/SL สัมพันธ์กัน 100%)</div>
    <div class="action-card">
      <div style="color:#fbbf24;font-weight:800;margin-bottom:3px;">⚡ โพยจุดเข้าหลัก (BUY: TP &gt; จุดเข้า &gt; SL | SELL: TP &lt; จุดเข้า &lt; SL):</div>
      <div class="action-row"><span>🟢 <b>1SD BUY (เด้งสั้น)</b>: เข้า <b>{tb['buy_1sd']:,.2f}</b></span><span>TP <b>{tb['buy_1sd']+8:,.2f} / {tb['buy_1sd']+12:,.2f}</b> | SL <b>{tb['buy_1sd']-8:,.2f}</b> (ทบ 2x <b>{tb['buy_1sd']-12.5:,.2f}</b>)</span></div>
      <div class="action-row"><span>🟢 <b>2SD BUY (คำใหญ่)</b>: เข้า <b>{tb['buy_2sd']:,.2f}</b></span><span>TP <b>{tb['buy_2sd']+15:,.2f} / {tb['buy_2sd']+25:,.2f}</b> | SL <b>{tb['buy_2sd']-8:,.2f}</b> (ทบ 2x <b>{tb['buy_2sd']-12.5:,.2f}</b>)</span></div>
      <div class="action-row"><span>🔴 <b>1SD SELL (ย่อสั้น)</b>: เข้า <b>{tb['sell_1sd']:,.2f}</b></span><span>TP <b>{tb['sell_1sd']-8:,.2f} / {tb['sell_1sd']-12:,.2f}</b> | SL <b>{tb['sell_1sd']+8:,.2f}</b> (ทบ 2x <b>{tb['sell_1sd']+12.5:,.2f}</b>)</span></div>
      <div class="action-row"><span>🔴 <b>2SD SELL (คำใหญ่)</b>: เข้า <b>{tb['sell_2sd']:,.2f}</b></span><span>TP <b>{tb['sell_2sd']-15:,.2f} / {tb['sell_2sd']-25:,.2f}</b> | SL <b>{tb['sell_2sd']+8:,.2f}</b> (ทบ 2x <b>{tb['sell_2sd']+12.5:,.2f}</b>)</span></div>
    </div>
    <div class="ladder" id="ladder_box">
{ladder_html}
    </div>
  </div>
  <script src="https://s3.tradingview.com/tv.js"></script>
  <script>
    function updateSpotOnCard(newSpot) {{
      const openSpot = {open_spot:.2f};
      const oneSd = {max(one_sd_range, 1.0):.2f};
      const delta = newSpot - openSpot;
      const z = delta / oneSd;
      const signDelta = (delta >= 0 ? '+' : '') + delta.toFixed(2);
      const signZ = (z >= 0 ? '+' : '') + z.toFixed(2) + 'SD';
      const fmtSpot = newSpot.toLocaleString('en-US', {{minimumFractionDigits: 2, maximumFractionDigits: 2}});

      const hdr = document.getElementById('hdr_spot');
      if (hdr) hdr.innerText = '🥇 XAU/USD (Spot): ' + fmtSpot;

      const hdrRight = document.getElementById('hdr_sub_right');
      if (hdrRight) {{
        hdrRight.innerText = 'เปลี่ยนจากเช้า: ' + signDelta + ' จุด (' + signZ + ') | IV เช้า {snap.open_iv:.2f}% -> สด {snap.atm_iv:.2f}%';
      }}

      const rowSpot = document.getElementById('row_spot_now');
      const pxSpot = document.getElementById('px_spot_now');
      const subSpot = document.getElementById('sub_spot_now');
      if (rowSpot && pxSpot && subSpot) {{
        rowSpot.setAttribute('data-price', newSpot.toFixed(2));
        pxSpot.innerText = fmtSpot;
        subSpot.innerText = 'เปลี่ยนจากเช้า ' + signDelta + ' จุด (' + signZ + ')';
      }}

      const box = document.getElementById('ladder_box');
      if (box) {{
        const rows = Array.from(box.querySelectorAll('.lvl'));
        rows.sort((a, b) => parseFloat(b.getAttribute('data-price') || '0') - parseFloat(a.getAttribute('data-price') || '0'));
        rows.forEach(r => box.appendChild(r));
      }}
    }}

    if (window.TradingView) {{
      new TradingView.widget({{
        "autosize": true,
        "symbol": "OANDA:XAUUSD",
        "interval": "15",
        "timezone": "Asia/Bangkok",
        "theme": "dark",
        "style": "1",
        "locale": "th_TH",
        "toolbar_bg": "#0b0e14",
        "enable_publishing": false,
        "hide_top_toolbar": false,
        "hide_legend": false,
        "save_image": false,
        "container_id": "tv_chart_container"
      }});
    }}
  </script>
</body>
</html>"""

    html_file = os.path.abspath(f"xauusd_spot_card_{ts}.html")
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    if CONFIG["DRY_RUN"] and not os.getenv("TEST_RENDER_SPOT_CARD"):
        return exact_calc, None

    try:
        from playwright.sync_api import sync_playwright
        detected_chart_spot: Optional[float] = None
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 980})
            page.goto(f"file://{html_file}", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5500)

            # ดึงราคาปิดสด ("ปิด 3,870.275" หรือ "C 3,870.275") จาก iframe ของกราฟ TradingView โดยตรง!
            for frame in page.frames:
                try:
                    ftxt = frame.inner_text("body", timeout=1500)
                    m_close = re.search(r"(?:ปิด|C)\s*([2-5],?[0-9]{3}\.[0-9]{1,3})", ftxt)
                    if m_close:
                        cand = round(float(m_close.group(1).replace(",", "")), 2)
                        if _is_valid_gold_price(cand, snap.futures_price):
                            detected_chart_spot = cand
                            break
                except Exception:
                    pass

            # หากอ่านราคาปิดจากกราฟ TradingView ได้ ให้ซิงค์ตัวเลขบนการ์ดและบันไดราคาทันทีก่อนแคปภาพ!
            if detected_chart_spot is not None:
                print(f"🎯 ซิงค์ราคา Spot XAU/USD จากหน้าจอกราฟ TradingView จริงสำเร็จ: {detected_chart_spot:,.2f}")
                page.evaluate(f"updateSpotOnCard({detected_chart_spot:.2f})")
                page.wait_for_timeout(300)
                exact_calc = compute_exact_coordinates(snap, prev_state, live_spot_override=detected_chart_spot)
                spot = exact_calc["spot"]
                tb = exact_calc["sd_table_exact"]

            page.screenshot(path=out_path, full_page=False)
            browser.close()

        caption = (
            f"🥇 [ภาพที่ 4] กราฟราคา Spot XAU/USD ปัจจุบัน ({spot:,.2f}) & ผังพิกัดจุดเข้า–ออก 6 โซน Fixed SD\n"
            f"• ราคาเปิดเช้า: {open_spot:,.2f} | 1SD Buy/Sell: {tb['buy_1sd']:,.2f} / {tb['sell_1sd']:,.2f} | "
            f"2SD Buy/Sell: {tb['buy_2sd']:,.2f} / {tb['sell_2sd']:,.2f}"
        )
        print(f"✅ สร้างภาพกราฟ Spot XAU/USD ({out_path}) ที่ตรงกับข้อความรายงาน 100% สำเร็จ!")
        return exact_calc, (out_path, caption)
    except Exception as e:
        print(f"⚠️ สร้างภาพกราฟ Spot XAU/USD ข้ามไป ({e})")
        return exact_calc, None


# ==============================================================================
# 4. เปิดหน้าเว็บ Hizeiris (ล็อกแถบเวลาขวาสุด + สลับแท็บแม่นยำ 100%)
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
    for frame in page.frames:
        try:
            frame.locator("body").evaluate(
                f"""body => {{
                    Array.from(document.querySelectorAll('input[type="range"]')).forEach(rng => {{
                        if (rng.max && rng.value !== rng.max) {{
                            rng.value = rng.max;
                            rng.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            rng.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                    }});
                    Array.from(document.querySelectorAll('button, span, div')).forEach(el => {{
                        if (el.children.length > 1) return;
                        const t = (el.innerText || '').trim();
                        if (t === 'SD: ปิด') el.click();
                        if (t === 'diff: ปิด' || t === 'diff: ไม่ใช้') el.click();
                    }});
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

        _click_tab_button_in_frames(page, ["CME", "Vol"])
        page.wait_for_timeout(1800)
        _expand_iframes_for_full_charts(page)
        page.screenshot(path=shot_cme_vol, full_page=True, timeout=15000)
        screenshots.append(shot_cme_vol)
        photo_captions.append((shot_cme_vol, "📊 [ภาพที่ 2] แท็บ CME · Vol (ข้อมูลรอบปัจจุบัน) & กราฟ Intraday + OI"))
        text_main = _extract_frame_texts_and_inputs(page)
        print("✅ แคปภาพที่ 2 (แท็บ CME · Vol รอบปัจจุบัน) สำเร็จ!")

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

    # ดึงราคา Futures ล่าสุดจากข้อความ "ล่าสุด XXXX.X" บนแท็บ SD·diff (ถ้ามี)
    latest_gc_val = _parse_float(r"ล่าสุด\s*([2-5],?[0-9]{3}(?:\.[0-9]+)?)", raw_text, 0.0)
    latest_gc_fut = latest_gc_val if _is_valid_gold_price(latest_gc_val, fut_px) else None

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
        latest_gc_fut=latest_gc_fut,
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
    if not round_history:
        return "• ยังไม่มีประวัติรอบก่อนหน้าในวันนี้"
    lines = []
    for idx, r in enumerate(round_history[-8:], 1):
        lines.append(
            f"• *รอบ #{idx}* (`{r.get('time', '')}` | `{r.get('round_str', '')}`)\n"
            f"  Spot: `{r.get('spot', 0):,.2f}` (`{r.get('delta_from_open', '+0.00')}` จากเช้า, `{r.get('z_sd', '0.0SD')}`)\n"
            f"  Vol สด: `{r.get('atm_iv', 0):.2f}%` (`{r.get('vol_chg', 0):+.2f}`) | Intra C/P: `{r.get('intra_c', 0)}/{r.get('intra_p', 0)}` (`P/C {r.get('intra_pc', 0):.2f}`)"
        )
    return "\n\n".join(lines)


def _format_cheat_sheet_ticket(idx: int, t: Dict[str, object], spot: float) -> str:
    dist = round(abs(spot - float(t["entry_spot_num"])), 2)
    direction_hint = "สูงกว่าจุดเข้า" if t["side"] == "BUY" else "ต่ำกว่าจุดเข้า"
    return (
        f"*{idx}) {t['short_title']}* (ห่างราคาปัจจุบัน `{dist:.2f}` จุด)\n"
        f"   • 📍 พิกัดเข้า {t['side']} เดียวชัดเจน: *`{t['entry_spot']}`* (Futures `{t['entry_fut']}`)\n"
        f"   • 🎯 เป้าทำกำไร ({direction_hint}): {t['tp_line']}\n"
        f"   • 🛑 SL ปกติ (ตัดจบ): {t['sl_norm_line']}\n"
        f"   • 🔄 แก้พอร์ต Supa888: {t['rec1_line']}"
    )


def build_clean_section_messages(snap: HizeirisSnapshot, exact_calc: Dict, round_history: List[Dict], ai_audit_text: str = "") -> List[str]:
    tb = exact_calc["sd_table_exact"]
    dyn = exact_calc["cme_dynamic_spot"]
    ws = exact_calc["walls_spot"]
    zones = exact_calc["six_zone_plans"]
    tactical = exact_calc["tactical_tickets"]
    ps = exact_calc["primary_setup"]
    pc = exact_calc["plan_c_exact"]
    ts = exact_calc["time_scaling_pts"]
    va = exact_calc["vol_live_vs_base"]
    vr = exact_calc["vol_regime_m2"]
    m4 = exact_calc["m4_micro"]
    mvc = exact_calc["morning_vs_current"]
    spot = exact_calc["spot"]

    buy_cheat_lines = "\n\n".join(
        _format_cheat_sheet_ticket(i, t, spot)
        for i, t in enumerate(tactical["buy_tickets"][:3], 1)
    )
    sell_cheat_lines = "\n\n".join(
        _format_cheat_sheet_ticket(i, t, spot)
        for i, t in enumerate(tactical["sell_tickets"][:3], 1)
    )

    msg_0 = (
        f"⚡ *สรุปฟันธงหน้างาน (จากราคาปัจจุบันควรเข้าตรงไหน)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🥇 *ราคา XAU/USD (Spot) ปัจจุบัน*: *`{spot:,.2f}`* _(ตรงกับกราฟ `[ภาพที่ 4]` 100%)_\n"
        f"• ราคา Futures เทียบ Spot ปัจจุบัน: `{exact_calc['implied_live_fut']:,.2f}` | Futures ตอนเก็บรอบ OI (`{snap.contract_name}` {snap.round_str}): `{snap.futures_price:,.1f}` (หัก Diff `{snap.diff:.2f}`)\n"
        f"• เทียบราคาเปิดเช้า (`{exact_calc['open_spot']:,.2f}`): เปลี่ยนแปลง *`{mvc['delta_px_from_open']}` จุด* (`{mvc['z_from_open']}`)\n"
        f"• สถานะหน้างาน: {exact_calc['immediate_verdict']}\n\n"
        f"🎯 *โพยจุดเข้า–ออกแยกตามด่าน (จุดเข้าเดียวชัดเจน — TP/SL สัมพันธ์กัน 100%)*:\n"
        f"_(กฎเหล็ก: ฝั่ง BUY เป้า TP ต้องสูงกว่าจุดเข้า และ SL ต้องต่ำกว่าจุดเข้า | ฝั่ง SELL เป้า TP ต้องต่ำกว่าจุดเข้า และ SL ต้องสูงกว่าจุดเข้าเสมอ)_\n\n"
        f"🟢 *[ฝั่งดัก BUY — แนวรับด้านล่าง เรียงจากใกล้ราคาปัจจุบันที่สุด]*\n\n"
        f"{buy_cheat_lines}\n\n"
        f"🔴 *[ฝั่งดัก SELL — แนวต้านด้านบน เรียงจากใกล้ราคาปัจจุบันที่สุด]*\n\n"
        f"{sell_cheat_lines}"
    )

    msg_1 = (
        f"📊 *ส่วนที่ 1: สรุปพารามิเตอร์ & เปรียบเทียบกรอบเช้า (06:30, DTE 0.8) vs รอบปัจจุบัน*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{mvc['morning_vs_now_summary']}\n\n"
        f"• *สมการกรอบเช้า (DTE 0.8)*: `1SD = {exact_calc['open_fut']} × {exact_calc['open_iv']}% × √(0.8/365) = ±{tb['range_1sd']:.2f}` | `2SD = ±{tb['range_2sd']:.2f}` | `3SD = ±{tb['range_3sd']:.2f}`\n"
        f"• *Vol สด (เส้นเขียว)*: `{va['vol_live']}` vs *Vol Settle (เส้นประแดง)*: `{va['vol_base_settle']}` (`Vol Chg {va['vol_chg']}`)\n"
        f"  -> _{va['regime']}: {va['impact']}_\n"
        f"• *Rule of 16 (IV/16)*: กรอบวัน `±{ts['daily_rule16_pct']}%` (`±{ts['daily_rule16_pts']}` จุด) | รายชม. `±{ts['hourly_div_40_8']}` | รายสัปดาห์ `±{ts['weekly_div_7_2']}`\n"
        f"• *Volatility Regime*: {vr['regime_name']} -> Win Rate `{vr['win_rate']}`\n"
        f"• *Intraday Volume*: C `{snap.intraday_c:,}` / P `{snap.intraday_p:,}` (`P/C {snap.intraday_pc:.2f}`) | *Open Interest*: C `{snap.oi_c:,}` / P `{snap.oi_p:,}` (`P/C {snap.oi_pc:.2f}`) | Churn: `{snap.churn_str}`\n"
        f"• *เลขหัวเสา Put/Call บนกราฟ*: `{snap.pillar_labels_text}`\n"
        f"• *HMM 3-State*: {m4['hmm_state']}\n"
        f"• *จตุภาค Price-OI*: {m4['quadrant']}\n"
        f"• *Contrarian PCR*: {m4['pcr_signal']}\n"
        f"• *Shadow Greeks*: {m4['shadow_greeks']}\n"
        f"• *โมเดลรถไม่มีเบรก 3 จังหวะ*: {m4['car_model']}\n"
        f"• *กฎเวลา 0DTE*: {m4['eod_warning']}"
    )

    msg_2 = (
        f"🧱 *ส่วนที่ 2: ตารางพิกัดแนวรับ-แนวต้านคืนนี้ (หัก Diff {snap.diff:.2f}: เทียบ Fix เช้า DTE 0.8 vs รอบปัจจุบัน)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔼 *โซนแนวต้านฝั่งบน (เรียงจากสูงสุดลงมากลาง)*:\n\n"
        f"1️⃣ *ขอบบน +3SD (5ΔC Extreme Tail)*\n"
        f"   • พิกัดเข้า SELL (`+{tb['range_3sd']:.2f}`): *`{tb['sell_3sd']:,.2f}`*{check_round_number_bonus(tb['sell_3sd'])} (Fut `{tb['sell_3sd_fut']:,.2f}`)\n"
        f"   • เป้า TP ขา SELL: `{tb['sell_3sd'] - 25.0:,.2f}` (กำไร `+$25`) / `{tb['sell_3sd'] - 35.0:,.2f}` (กำไร `+$35`) | SL ปกติ: `{tb['sell_3sd'] + 8.0:,.2f}`\n"
        f"   • พิกัด Dynamic รอบนี้: `{dyn['+3SD_spot']:,.2f}` (Fut `{dyn['+3SD_fut']:,.1f}`, ต่างกัน `{tb['sell_3sd'] - dyn['+3SD_spot']:+.2f}`)\n"
        f"   • สถิติวิจัย: แตะ `3.7%–7.0%` | เด้งกลับ `86.75%–95.0%` (500d `>=$25` = `91.43%`) | จบสวิง `96.3%`\n\n"
        f"2️⃣ *ขอบบน +2SD (15ΔC Primary Kill Zone — จุดดัก SELL หลัก)*\n"
        f"   • พิกัดเข้า SELL (`+{tb['range_2sd']:.2f}`): *`{tb['sell_2sd']:,.2f}`*{check_round_number_bonus(tb['sell_2sd'])} (Fut `{tb['sell_2sd_fut']:,.2f}`)\n"
        f"   • เป้า TP ขา SELL: `{tb['sell_2sd'] - 15.0:,.2f}` (กำไร `+$15`) / `{tb['sell_2sd'] - 25.0:,.2f}` (กำไร `+$25`) | SL ปกติ: `{tb['sell_2sd'] + 8.0:,.2f}`\n"
        f"   • พิกัด Dynamic รอบนี้: `{dyn['+2SD_spot']:,.2f}` (Fut `{dyn['+2SD_fut']:,.1f}`, ต่างกัน `{tb['sell_2sd'] - dyn['+2SD_spot']:+.2f}`)\n"
        f"   • สถิติวิจัย: แตะ `25.9%–28.0%` | เด้งกลับ `78.57%–85.58%` (ทับ OI + IV Crush พุ่งเป็น `86.0%–91.4%`)\n\n"
        f"3️⃣ *ขอบบน +1SD (25ΔC First Action / Drift Zone)*\n"
        f"   • พิกัดเข้า SELL (`+{tb['range_1sd']:.2f}`): *`{tb['sell_1sd']:,.2f}`*{check_round_number_bonus(tb['sell_1sd'])} (Fut `{tb['sell_1sd_fut']:,.2f}`)\n"
        f"   • เป้า TP ขา SELL: `{tb['sell_1sd'] - 8.0:,.2f}` (กำไร `+$8`) / `{tb['sell_1sd'] - 12.0:,.2f}` (กำไร `+$12`) | SL ปกติ: `{tb['sell_1sd'] + 8.0:,.2f}`\n"
        f"   • พิกัด Dynamic รอบนี้: `{dyn['+1SD_spot']:,.2f}` (Fut `{dyn['+1SD_fut']:,.1f}`, ต่างกัน `{tb['sell_1sd'] - dyn['+1SD_spot']:+.2f}`)\n"
        f"   • สถิติวิจัย: แตะ `70.0%–77.8%` | ย่อสั้น `$8–$12` ชนะ `70.0%–78.53%` (เอเชีย `82%`) | ไหลต่อ 2SD `40%`\n\n"
        f"4️⃣ *กำแพง Call Wall & จุดดูด Max Pain ปัจจุบัน (แยกพิกัดชัดเจน)*\n"
        f"   • Call Wall ({snap.call_wall_pc}): *`{ws['call_wall_spot']:,.2f}`* (Fut `{ws['call_wall_fut']:,.0f}`) -> หากดัก SELL ที่ `{ws['call_wall_spot']:,.2f}` เป้า TP = `{ws['call_wall_spot'] - 10.0:,.2f}` / `{ws['call_wall_spot'] - 25.0:,.2f}` | SL ปกติ = `{ws['call_wall_spot'] + 8.0:,.2f}`\n"
        f"   • Max Pain (จุดดูดแม่เหล็ก ไม่ใช่จุดดักทุบ): *`{ws['max_pain_spot']:,.2f}`* (Fut `{ws['max_pain_fut']:,.0f}`, ห่างราคาปัจจุบัน `{exact_calc['dist_max_pain']:+.2f}`)\n\n"
        f"⚓ *แกนกลางวันนี้ (Pivot & ราคาปัจจุบัน)*:\n"
        f"   • ราคาเปิดเช้า (06:30): *`{exact_calc['open_spot']:,.2f}`* (Fut `{exact_calc['open_fut']:,.2f}`)\n"
        f"   • 👉 *ราคา XAU/USD ปัจจุบัน (ตรงกับกราฟภาพที่ 4)*: *`{spot:,.2f}`* (Fut เทียบ Spot `{exact_calc['implied_live_fut']:,.2f}`)\n\n"
        f"🔽 *โซนแนวรับฝั่งล่าง (เรียงจากกลางลงมาต่ำสุด)*:\n\n"
        f"5️⃣ *กำแพง Put Wall ปัจจุบัน (แยกพิกัดเฉพาะ)*\n"
        f"   • Put Wall ({snap.put_wall_pc}): *`{ws['put_wall_spot']:,.2f}`* (Fut `{ws['put_wall_fut']:,.0f}`) -> หากดัก BUY ที่ `{ws['put_wall_spot']:,.2f}` เป้า TP = `{ws['put_wall_spot'] + 10.0:,.2f}` / `{ws['put_wall_spot'] + 25.0:,.2f}` | SL ปกติ = `{ws['put_wall_spot'] - 8.0:,.2f}`\n\n"
        f"6️⃣ *ขอบล่าง -1SD (25ΔP First Support)*\n"
        f"   • พิกัดเข้า BUY (`-{tb['range_1sd']:.2f}`): *`{tb['buy_1sd']:,.2f}`*{check_round_number_bonus(tb['buy_1sd'])} (Fut `{tb['buy_1sd_fut']:,.2f}`)\n"
        f"   • เป้า TP ขา BUY: `{tb['buy_1sd'] + 8.0:,.2f}` (กำไร `+$8`) / `{tb['buy_1sd'] + 12.0:,.2f}` (กำไร `+$12`) | SL ปกติ: `{tb['buy_1sd'] - 8.0:,.2f}`\n"
        f"   • พิกัด Dynamic รอบนี้: `{dyn['-1SD_spot']:,.2f}` (Fut `{dyn['-1SD_fut']:,.1f}`)\n"
        f"   • สถิติวิจัย: แตะ `70.0%–77.8%` | เด้งสั้น `$8–$12` ชนะ `70.0%–78.53%` (ทับ Put Wall พุ่งเป็น `82%–89%`)\n\n"
        f"7️⃣ *ขอบล่าง -2SD (15ΔP Primary Kill Zone — จุดดัก BUY หลัก)*\n"
        f"   • พิกัดเข้า BUY (`-{tb['range_2sd']:.2f}`): *`{tb['buy_2sd']:,.2f}`*{check_round_number_bonus(tb['buy_2sd'])} (Fut `{tb['buy_2sd_fut']:,.2f}`)\n"
        f"   • เป้า TP ขา BUY: `{tb['buy_2sd'] + 15.0:,.2f}` (กำไร `+$15`) / `{tb['buy_2sd'] + 25.0:,.2f}` (กำไร `+$25`) | SL ปกติ: `{tb['buy_2sd'] - 8.0:,.2f}`\n"
        f"   • พิกัด Dynamic รอบนี้: `{dyn['-2SD_spot']:,.2f}` (Fut `{dyn['-2SD_fut']:,.1f}`, ต่างกัน `{tb['buy_2sd'] - dyn['-2SD_spot']:+.2f}`)\n"
        f"   • สถิติวิจัย: แตะ `25.9%–28.0%` | ดีดกลับคำใหญ่ `>=$25` ชนะ `78.57%–85.58%` (ทับ OI + IV Crush = `86%–91.4%`)\n\n"
        f"8️⃣ *ขอบล่าง -3SD (5ΔP Extreme Tail)*\n"
        f"   • พิกัดเข้า BUY (`-{tb['range_3sd']:.2f}`): *`{tb['buy_3sd']:,.2f}`*{check_round_number_bonus(tb['buy_3sd'])} (Fut `{tb['buy_3sd_fut']:,.2f}`)\n"
        f"   • เป้า TP ขา BUY: `{tb['buy_3sd'] + 25.0:,.2f}` (กำไร `+$25`) / `{tb['buy_3sd'] + 35.0:,.2f}` (กำไร `+$35`) | SL ปกติ: `{tb['buy_3sd'] - 8.0:,.2f}`\n"
        f"   • พิกัด Dynamic รอบนี้: `{dyn['-3SD_spot']:,.2f}` (Fut `{dyn['-3SD_fut']:,.1f}`, ต่างกัน `{tb['buy_3sd'] - dyn['-3SD_spot']:+.2f}`)\n"
        f"   • สถิติวิจัย: แตะ `3.7%–7.0%` | รอหยุดไหลดีดกลับชนะ `86.75%–95.0%` (500d `>=$25` = `91.43%`)\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚖️ *ส่วนที่ 3: การประเมินความได้เปรียบเชิงสถิติ (Statistical Edge Scorecard)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔼 *ฝั่งขึ้นหา Call Wall (`{ws['call_wall_spot']:,.2f}`, ห่าง `{exact_calc['dist_call']:+.2f}` จุด)*:\n"
        f"   • สถานะ: {exact_calc['oi_call_zone']['light']} — {exact_calc['oi_call_zone']['zone']}\n"
        f"   • โอกาสแตะเป้า: `{exact_calc['oi_call_zone']['touch']}` | โอกาสชนะ +$25: `{exact_calc['oi_call_zone']['win25']}`\n"
        f"   • กฎประจำโซน: _{exact_calc['oi_call_zone']['rule']}_\n\n"
        f"🔽 *ฝั่งลงหา Put Wall (`{ws['put_wall_spot']:,.2f}`, ห่าง `{-exact_calc['dist_put']:+.2f}` จุด)*:\n"
        f"   • สถานะ: {exact_calc['oi_put_zone']['light']} — {exact_calc['oi_put_zone']['zone']}\n"
        f"   • โอกาสแตะเป้า: `{exact_calc['oi_put_zone']['touch']}` | โอกาสชนะ +$25: `{exact_calc['oi_put_zone']['win25']}`\n"
        f"   • กฎประจำโซน: _{exact_calc['oi_put_zone']['rule']}_\n\n"
        f"📊 *ตำแหน่งโซน SD ปัจจุบัน*: {exact_calc['current_sd_zone']['zone_name']}\n"
        f"   • _{exact_calc['current_sd_zone']['stats']}_\n\n"
        f"🌍 *แต้มต่อจากวิจัย 0DTE ต่างประเทศ 7 ฉบับ (Module 5)*:\n"
        f"   • ดักสวนที่กำแพง (`Put Wall / Call Wall / ±2SD`): ชนะ *`70.0%–80.0%`* (ทับ SD + IV Crush = *`85.6%–91.4%`*)\n"
        f"   • Follow โมเมนตัมเข้าหา OI ระยะ `<$30` (`$25 Rule`): ชนะ *`78.0%–88.5%`*\n"
        f"   • ไล่ซื้อไม่มี OI รองรับ: ชนะแค่ *`50.0%–52.0%`* | ไล่ทุบซื้อ Put ก้นหลุมตอน IV แพง: ชนะแค่ *`30.0%–40.0%`*"
    )

    zone_blocks = []
    for z in zones:
        dir_label = "ราคาขึ้นสูงกว่าจุดเข้า" if z["side"] == "BUY" else "ราคาลงต่ำกว่าจุดเข้า"
        rec2_str = f"\n   • 🔄 แก้พอร์ตรวบ 2 ไม้ (SL2 6x): {z['rec2_line']}" if z.get("rec2_line") else ""
        zone_blocks.append(
            f"*{z['zone']}*\n"
            f"   • 📍 พิกัดเข้า {z['side']} เดียวชัดเจน: *`{z['entry_spot']}`* (Futures `{z['entry_fut']}`)\n"
            f"   • 🎯 เป้าทำกำไร ({dir_label}): {z['tp_line']}\n"
            f"   • 🛑 SL ปกติ (ตัดขาดทุนตามวิจัย Božović): {z['sl_norm_line']}\n"
            f"   • 🔄 แก้พอร์ตไม้ 1 (Supa888 2x): {z['rec1_line']}{rec2_str}\n"
            f"   • 📊 สถิติ & เทียบรอบนี้: _{z['win_rate']}_\n"
            f"   • 💡 วิธีเล่นตามวิจัย: _{z['research_play']}_"
        )

    msg_3 = (
        f"🎯 *ส่วนที่ 4: แผนปฏิบัติการเทรดครบทั้ง 6 โซน Fixed SD ตอนเช้า (DTE 0.8) & ระบบแก้พอร์ต Supa888*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 *แผนหลักหน้างานที่ใกล้ราคาปัจจุบัน (`{spot:,.2f}`) ที่สุด*:\n"
        f"   • กลยุทธ์: *{ps['title']}*\n"
        f"   • พิกัดเข้า: {ps['entry']}\n"
        f"   • เป้าทำกำไร: {ps['tp']}\n"
        f"   • จุดตัดขาดทุน & แก้พอร์ต: {ps['sl']}\n"
        f"   • อัตราชนะ: _{ps['win_rate']}_\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        + "\n\n".join(zone_blocks)
        + f"\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🚀 *แผนสำรอง (Breakout / Negative Gamma Squeeze 4 เฟส — ใช้เมื่อทะลุกำแพงชัดเจนเท่านั้น)*:\n"
        f"   • ฝั่งทะลุขึ้น: ยืนเหนือ *`{pc['call_wall_break']:,.2f}`* พร้อม Vol สดพุ่งชัน -> Follow BUY เป้า *`{pc['call_squeeze_tp']:,.2f}`* (กำไร `+12.5` จุด) | SL *`{pc['call_squeeze_sl']:,.2f}`* (`-8.0` จุด)\n"
        f"   • ฝั่งทะลุลง: ยืนต่ำกว่า *`{pc['put_wall_break']:,.2f}`* พร้อม Vol สดพุ่งชัน -> Follow SELL เป้า *`{pc['put_squeeze_tp']:,.2f}`* (กำไร `+12.5` จุด) | SL *`{pc['put_squeeze_sl']:,.2f}`* (`-8.0` จุด)"
    )

    history_table_str = _format_round_history_log(round_history)
    ai_block = (
        f"\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 *ส่วนที่ 7: AI ตรวจสอบและวิเคราะห์ซ้ำจากภาพกราฟจริง (AI Cross-Check)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{ai_audit_text.strip()}"
        if ai_audit_text.strip()
        else ""
    )

    msg_4 = (
        f"🔔 *ส่วนที่ 5: จุดเฝ้าระวังการเปลี่ยนสถานะในรอบถัดไป (Key Quant Triggers)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"1️⃣ *เช็กช่องว่าง Vol สด (`{va['vol_live']}`) vs Vol Settle (`{va['vol_base_settle']}`) vs IV เช้า (`{va['open_iv']}`)*:\n"
        f"   • หาก `Vol Chg` ติดลบต่อเนื่องจะหนุน Vanna Bounce ดึงราคากลับเข้าหากรอบใน แต่หากพุ่งบวกแรงให้ระวังการขยายกรอบไปด่าน `±2SD`\n"
        f"2️⃣ *เช็กการย้ายปั๊มน้ำมัน Intraday Volume (`P/C {snap.intraday_pc:.2f}`, Churn `{snap.churn_str}`)*:\n"
        f"   • หากรถไต่ไปหาเสาถัดไปไม่ไหว จะไหลถอยหลังกลับมาเติมน้ำมันที่เสาแรก (`{ws['put_wall_spot']:,.2f}` / `{exact_calc['open_spot']:,.2f}`)\n"
        f"3️⃣ *กฎล็อกเวลา CME*: `00:30/01:30 น.` (Settlement), `06:30 น.` (ล็อกกรอบ `SD·diff` DTE 0.8), `08:30 น.` (สแกน Local Max OI), และ *ห้ามดักสวนเทรนด์ในช่วง 30–60 นาทีสุดท้ายก่อนปิดตลาด (`DTE < 0.04`) เด็ดขาด!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 *ส่วนที่ 6: สมุดบันทึกประวัติแต่ละรอบของวันนี้ (Intraday Round-by-Round Log)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{history_table_str}\n\n"
        f"📌 *SESSION MEMORY*: `Open {exact_calc['open_spot']:.2f} | Now {spot:.2f} ({mvc['delta_px_from_open']}) | 1SD {tb['buy_1sd']:.2f}/{tb['sell_1sd']:.2f} | 2SD {tb['buy_2sd']:.2f}/{tb['sell_2sd']:.2f} | 3SD {tb['buy_3sd']:.2f}/{tb['sell_3sd']:.2f}`"
        f"{ai_block}"
    )

    return [msg_0, msg_1, msg_2, msg_3, msg_4]


def synthesize_full_report_messages(snap: HizeirisSnapshot, exact_calc: Dict, capture: Optional[Dict[str, object]],
                                    api_key: str, prev_state: Optional[Dict], round_history: List[Dict]) -> List[str]:
    if CONFIG["DRY_RUN"] or api_key == "YOUR_GEMINI_API_KEY_HERE" or not capture:
        return build_clean_section_messages(snap, exact_calc, round_history, "")

    tb = exact_calc["sd_table_exact"]
    ws = exact_calc["walls_spot"]
    prompt = (
        "คุณคือ Quantitative Derivatives & Market Microstructure Auditor (Mtraders Masterclass & AI CME Analyst)\n"
        "จงดูภาพหน้าจอเว็บ Hizeiris ทั้ง 2 ภาพ (ภาพที่ 1: แท็บ SD·diff กรอบ Fix เช้า DTE 0.8 และภาพที่ 2: แท็บ CME·Vol รอบปัจจุบัน) "
        "แล้วสรุปผลการตรวจสอบซ้ำ (Cross-Check) เป็น 4 หัวข้อย่อยที่อ่านง่าย เว้นบรรทัดชัดเจน:\n"
        "กฎเหล็ก: ห้ามใช้เครื่องหมาย ** (ให้ใช้ * ตัวเดียวสำหรับตัวหนา), ห้ามเอาตัวเลขคนละโซนมารวมเป็นช่วงเดียวกัน, "
        "ฝั่ง BUY ต้องมี TP > จุดเข้า > SL เสมอ, ฝั่ง SELL ต้องมี TP < จุดเข้า < SL เสมอ และห้ามใช้ ICT/SMC เด็ดขาด:\n"
        "1) ✅ ตรวจความถูกต้องตัวเลขบนภาพ (Futures, Diff, ราคาเปิดเช้า, 1SD/2SD/3SD, Call/Put Wall)\n"
        "2) 📊 อ่านหัวเสา Put/Call จริงที่โดดเด่นบนกราฟ Intraday & OI และชี้จุดปั๊มน้ำมันของรถไม่มีเบรก\n"
        "3) 📈 วิเคราะห์ความชันเส้นเขียว Vol สด vs เส้นประแดง Vol Settle ทั้งฝั่งซ้าย (Put) และฝั่งขวา (Call)\n"
        "4) 🎯 ฟันธงยืนยันจุดเข้าเทรดที่ดีที่สุดจากราคา Spot ปัจจุบัน โดยระบุ จุดเข้าเดียว -> TP1/TP2 -> SL ปกติ ที่สัมพันธ์กัน 100%\n\n"
        f"Spot ปัจจุบัน (ตรงกับกราฟ TradingView): {exact_calc['spot']:.2f} | OpenSpot: {exact_calc['open_spot']:.2f} | Diff: {snap.diff:.2f}\n"
        f"Fixed SD เช้า: 1SD Buy/Sell = {tb['buy_1sd']:.2f}/{tb['sell_1sd']:.2f} | 2SD Buy/Sell = {tb['buy_2sd']:.2f}/{tb['sell_2sd']:.2f}\n"
        f"Walls: PutWall {ws['put_wall_spot']:.2f} | CallWall {ws['call_wall_spot']:.2f} | MaxPain {ws['max_pain_spot']:.2f}"
    )

    ai_audit = ""
    try:
        ai_audit = call_gemini_rest_fast(
            api_key=api_key,
            prompt_text=prompt,
            image_paths=capture["screenshot_paths"][:2],
            timeout_sec=45,
        )
    except Exception:
        ai_audit = "• รอบนี้ระบบใช้ผลคำนวณเชิงปริมาณ Modules 1–6 จาก Python โดยตรง (ซิงค์ราคา Spot ตรงกับกราฟภาพที่ 4 และตรวจสมการ Entry / TP / SL ครบ 100%)"

    return build_clean_section_messages(snap, exact_calc, round_history, ai_audit)


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
    for block in text.split("\n\n"):
        piece = block + "\n\n"
        if len(current) + len(piece) > max_len:
            if current.strip():
                chunks.append(current.strip())
            current = piece
        else:
            current += piece
    if current.strip():
        chunks.append(current.strip())
    return chunks


def send_telegram_parts(section_messages: List[str], token: str, chat_id: str) -> None:
    all_chunks: List[str] = []
    for msg in section_messages:
        clean_msg = re.sub(r"\*{2,}", "*", msg)
        all_chunks.extend(split_telegram_chunks(clean_msg))

    if token == "YOUR_BOT_TOKEN_HERE" or chat_id == "YOUR_CHAT_ID_HERE":
        for i, p in enumerate(all_chunks, 1):
            print(f"\n📱 [TELEGRAM PREVIEW {i}/{len(all_chunks)}]:\n{p}")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for p in all_chunks:
        resp = requests.post(url, json={"chat_id": chat_id, "text": p, "parse_mode": "Markdown"}, timeout=15)
        if resp.status_code != 200:
            resp = requests.post(url, json={"chat_id": chat_id, "text": p.replace("*", "").replace("`", "")}, timeout=15)
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
            futures_price=3858.5,
            latest_gc_fut=3872.78,
            future_chg=13.28,
            daily_open_fut=3859.50,
            daily_open_spot=3857.00,
            open_iv=26.45,
            open_source="ตาราง SD·diff (IV ตอนเปิด 06:30)",
            diff=tg_diff or 2.50,
            raw_market_diff=2.50,
            diff_source="แท็บ SD·diff (ปัด 2.50)",
            dte=0.38,
            fixed_dte=CONFIG["FIXED_DTE"],
            atm_iv=23.06,
            vol_chg=-3.39,
            vol_settle_atm=26.45,
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
            call_wall=3900.0,
            call_wall_pc="Call wall 3900 (1/113)",
            max_pain=3875.0,
            top_inflow_text="โฟลว์รายวัน Intraday C 634 vs P 370 (P/C 0.58)",
            top_oi_text="ฐานสะสม OI C 622 vs P 1,058 (P/C 1.70) | Put Wall 3850 / Call Wall 3900",
            cme_vol_series_text="G3MX6 DTE 0.38 IV 23.06% (Normal Contango)",
            pillar_labels_text="30/0, 36/7, 61/8, 13/46, 8/61, 6/43, 1/49, 18/71, 1/113, 1/54",
            sd_diff_table={
                "range_1sd": 47.79, "buy_1sd": 3809.21, "sell_1sd": 3904.79,
                "range_2sd": 95.58, "buy_2sd": 3761.42, "sell_2sd": 3952.58,
                "range_3sd": 143.38, "buy_3sd": 3713.62, "sell_3sd": 4000.38,
            },
        )
    else:
        cap = capture_hizeiris_web(CONFIG["HIZEIRIS_URL"], CONFIG["CHROME_PROFILE_DIR"], CONFIG["FIXED_DTE"])
        snap = parse_snapshot_from_dom_text(
            str(cap.get("raw_text") or ""), fallback_diff, tg_diff, tg_open, prev_state
        )
        print(
            f"✅ ดึงตาราง SD·diff & รอบปัจจุบันสำเร็จ: {snap.contract_name} ({snap.round_str}) | "
            f"OpenFut {snap.daily_open_fut:.2f} | RoundFut {snap.futures_price:.1f} | LatestFut {snap.latest_gc_fut} | Diff {snap.diff:.2f}"
        )

    # 1. ดึงราคา Spot XAU/USD สดจาก TradingView API (ถ้ามี) และแคป [ภาพที่ 4] พร้อมซิงค์ราคาปิดจริงจากกราฟ TradingView 100%
    ref_spot_est = round((snap.latest_gc_fut or snap.futures_price) - snap.diff, 2)
    api_live_spot = None if CONFIG["DRY_RUN"] else fetch_realtime_xauusd_spot(ref_spot_est)
    exact_calc, spot_chart_item = render_xauusd_spot_chart_and_sync_price(
        snap, prev_state, initial_live_spot=api_live_spot
    )
    tb = exact_calc["sd_table_exact"]
    mvc = exact_calc["morning_vs_current"]

    # 2. ส่งภาพทั้ง 4 ภาพเข้า Telegram (โดยภาพที่ 4 และข้อความรายงานใช้ราคา Spot XAU/USD ตัวเดียวกันเป๊ะ 100%)
    if not CONFIG["DRY_RUN"] and cap is not None:
        photos_to_send = list(cap.get("photo_captions", []))
        if spot_chart_item:
            photos_to_send.append(spot_chart_item)
        if photos_to_send:
            send_telegram_photos(
                photos_to_send,
                CONFIG["TELEGRAM_BOT_TOKEN"],
                CONFIG["TELEGRAM_CHAT_ID"],
            )

    today_str = datetime.now().strftime("%Y-%m-%d")
    old_history = []
    if prev_state and prev_state.get("trading_day") == today_str:
        old_history = list(prev_state.get("round_history", []))

    current_round_entry = {
        "time": datetime.now().strftime("%H:%M"),
        "round_str": snap.round_str,
        "fut": exact_calc["implied_live_fut"],
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

    section_messages = synthesize_full_report_messages(snap, exact_calc, cap, CONFIG["GEMINI_API_KEY"], prev_state, round_history)
    send_telegram_parts(section_messages, CONFIG["TELEGRAM_BOT_TOKEN"], CONFIG["TELEGRAM_CHAT_ID"])

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
        "futures_price": exact_calc["implied_live_fut"],
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
    print("🎉 ส่งรูปกราฟ 4 ภาพ (ราคา Spot ซิงค์ตรงกับข้อความ 100%) และรายงานที่ตรวจสมการ TP/SL ครบ 100% เข้า Telegram เสร็จสมบูรณ์!")


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
        now = datetime.now()
        sec_into_half_hour = (now.minute % 30) * 60 + now.second
        sleep_sec = max(1800 - sec_into_half_hour, 300)
        print(f"⏳ รอส่งรายงานรอบถัดไปในอีก {sleep_sec // 60} นาที {sleep_sec % 60} วินาที...")
        time.sleep(sleep_sec)


if __name__ == "__main__":
    start_scheduler()
