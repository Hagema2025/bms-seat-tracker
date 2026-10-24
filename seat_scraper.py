import os
import re
import json
import time
import random
import urllib.parse
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

import requests
from curl_cffi import requests as cffi_requests

# [DISABLED - auto-checkout] only needed to call token_extractor.js
# import subprocess

# ==============================================================================
# ⚙️ SECURE CONFIGURATION & ENVIRONMENT VARIABLES
# ==============================================================================
SHOWS_FILE = "shows.json"
STATE_FILE = "state.json"

# Credentials & API Keys
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_GROUP_CHAT_ID = os.environ.get("TG_GROUP_CHAT_ID")
NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh").strip() or "https://ntfy.sh"
NTFY_ERROR_TOPIC = os.environ.get("NTFY_ERROR_TOPIC", "").strip()
NTFY_SEAT_TOPIC = os.environ.get("NTFY_SEAT_TOPIC", "").strip()


# Seat status codes (slot[1]):
#   0 = space/aisle, 2 = booked, 1 = bookable,
#   7 = handicap (bookable), 8 = handicap caretaker (bookable)
# Anything NOT listed in NOT_BOOKABLE_CODES counts as bookable.
SPACE_CODE = "0"
NOT_BOOKABLE_CODES = {"0", "2"}
KNOWN_CODES = {"0", "1", "2", "7", "8"}
_unknown_codes = set()    # unknown codes already warned about

# [DISABLED - auto-checkout] user details for the checkout pipeline binding
# USER_EMAIL = os.environ.get("BMS_EMAIL")
# USER_PHONE = os.environ.get("BMS_MOBILE")

# Flip to True (and uncomment the checkout code below) to re-enable auto-checkout.
# NOTE: from GitHub Actions IPs, BMS/Cloudflare returns a hard 403 block, so it
# will not work there regardless of cookies.
AUTO_CHECKOUT = False

if not TG_BOT_TOKEN or not TG_GROUP_CHAT_ID:
    print("❌ CRITICAL ERROR: Telegram bot token or chat ID is missing. Execution halted.")
    exit(1)

POST_HEADERS = {
    "Host": "services-in.bookmyshow.com",
    "X-App-Code": "MOBAND2",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10)",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept-Encoding": "gzip, deflate",
}

# Cinema chain quick-links for manual fallback booking
CINEMA_CHAIN_URLS = {
    "INTO": "https://www.inoxmovies.com/cinemasessions/Chennai/INOX-The-Marina-Mall,-OMR,-Chennai/232",
    "INPR": "https://www.inoxmovies.com/cinemasessions/Chennai/INOX-Luxe-Phoenix-Market-City,-Velachery--(formerly-Jazz-Cinemas)Chennai/320",
    "INCH": "https://www.inoxmovies.com/cinemasessions/Chennai/INOX-Chennai-Citi-Centre,Dr.-R.-K.-Salai-Chennai/113",
    "FMCN": "https://www.inoxmovies.com/cinemasessions/Chennai/INOX-National,Virugambakkam-Chennai/28",
    "PVHR": "https://www.pvrcinemas.com/cinemasessions/Chennai/PVR-Heritage-RSL-ECR-Chennai/417",
    "PGMV": "https://www.pvrcinemas.com/cinemasessions/Chennai/PVR,-Grand-Mall,-Velachery/389",
    "PVES": "https://www.pvrcinemas.com/cinemasessions/Chennai/HDFC-Millennia-PVR:-Escape-Express-Avenue-Mall/359",
    "PVSR": "https://www.pvrcinemas.com/cinemasessions/Chennai/PVR-Sathyam-Royapettah-Chennai/331",
    "PABC": "https://www.pvrcinemas.com/cinemasessions/Chennai/PVR-AEROHUB-Chennai/432",
    "PGRA": "https://www.pvrcinemas.com/cinemasessions/Chennai/PVR-Grand-Galada-Chennai/400",
    "PVPZ": "https://www.pvrcinemas.com/cinemasessions/Chennai/PVR-Palazzo-The-Nexus-Vijaya-Mall/388",
    "PVHC": "https://www.pvrcinemas.com/cinemasessions/Chennai/PVR,-Ampa-Mall,-Nelson-Manickam-Road-Chennai/358",
    "PCAN": "https://www.pvrcinemas.com/cinemasessions/Chennai/PVR-VR-Chennai-Anna-Nagar/523",
    "PBRM": "https://www.pvrcinemas.com/cinemasessions/Chennai/PVR-Perambur---Spectrum-Mall-Chennai/372",
    "PSKL": "https://www.pvrcinemas.com/cinemasessions/Chennai/PVR-SKLS-Galaxy-Mall,-Red-Hills-Chennai/410",
    "CBMC": "https://cinepolisindia.com/movie-list/38",
}

# Company Code Mapping (only used by the checkout API; kept for later)
VENUE_TO_COMPANY = {
    "INTO": "INOX", "INPR": "INOX", "INCH": "INOX", "FMCN": "INOX",
    "PVHR": "PVR", "PGMV": "PVR", "PVES": "PVR", "PVSR": "PVR",
    "PABC": "PVR", "PGRA": "PVR", "PVPZ": "PVR", "PVHC": "PVR",
    "PCAN": "PVR", "PBRM": "PVR", "PSKL": "PVR",
    "CBMC": "CINEPOLIS"
}

# ==============================================================================
# [DISABLED] 🔐 FAST HTTP CHECKOUT PIPELINE (CURL_CFFI + PUPPETEER)
# Commented out: Cloudflare blocks GitHub Actions IPs (HTTP 403), and the
# Puppeteer step added 15+ seconds before every alert.
# token_extractor.js is kept in the repo but is not called.
# ==============================================================================

# def extract_tokens(event_code, venue_code, session_id, date_str):
#     print("   -> 🕵️ Booting Puppeteer stealth to attempt Cloudflare bypass...")
#
#     try:
#         # Call the Node.js script
#         result = subprocess.run(
#             ["node", "token_extractor.js", event_code, venue_code, session_id, date_str],
#             capture_output=True,
#             text=True,
#             timeout=120
#         )
#         if result.stderr.strip():
#             print(f"   -> Node stderr: {result.stderr.strip()[:500]}")
#
#         # Parse the JSON output from Node.js
#         output = result.stdout.strip()
#         tokens = json.loads(output)
#
#         if "error" in tokens:
#             print(f"   -> ⚠️ Puppeteer encountered an error: {tokens['error']}")
#             return None, None
#
#         # Build the curl_cffi session for the rest of the pipeline
#         session = cffi_requests.Session(impersonate="chrome120")
#         session.headers.update({
#             "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
#             "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
#             "Accept-Language": "en-US,en;q=0.9",
#             "Upgrade-Insecure-Requests": "1"
#         })
#
#         # Inject the Puppeteer-derived cookies into the cffi session
#         if tokens.get("cf_clearance"):
#             session.cookies.set("cf_clearance", tokens["cf_clearance"], domain=".bookmyshow.com")
#         if tokens.get("bmsId"):
#             session.cookies.set("bmsId", tokens["bmsId"], domain=".bookmyshow.com")
#
#         print(f"   -> 📊 Status -> bmsId: {bool(tokens.get('bmsId'))}, cf_clearance: {bool(tokens.get('cf_clearance'))}")
#
#         return tokens, session
#
#     except subprocess.TimeoutExpired:
#         print("   -> ❌ Puppeteer extraction timed out.")
#         return None, None
#     except json.JSONDecodeError:
#         print(f"   -> ❌ Failed to parse Puppeteer output. Raw output: {result.stdout}")
#         return None, None
#     except Exception as e:
#         print(f"   -> ⚠️ Unexpected token extraction failure: {e}")
#         return None, None
#
# def format_seat_string(seat_objs):
#     formatted = f"|{len(seat_objs)}|"
#     for s in seat_objs:
#         formatted += f"{s['category']}|1|{s['row']}|{s['num']}|"
#     return formatted
#
# def run_auto_checkout(tokens, session, e_code, v_code, s_id, seat_objs, company_code):
#     print("   -> 🚀 Initiating checkout sequence...")
#     primary_category = seat_objs[0]["category"] if seat_objs else "CL"
#
#     # --- STAGE 1: INVENTORY LOCK ---
#     lock_url = "https://in.bookmyshow.com/api/v2/mobile/booking/movies"
#     lock_headers = {
#         "x-app-code": "WEB", "x-platform": "WEB", "x-platform-code": "WEB",
#         "x-bms-id": tokens.get("bmsId", "")
#     }
#     lock_payload = {
#         "appCode": "WEB", "venueCode": v_code, "eventCode": e_code,
#         "sessionId": s_id, "numberOfTickets": len(seat_objs),
#         "ticketCategory": primary_category,
#         "selectedSeats": format_seat_string(seat_objs),
#         "bmsId": tokens.get("bmsId", ""), "token": tokens["x-token"],
#         "seatLayoutType": "Y", "offerData": {"offerSelected": False}, "companyCode": company_code,
#     }
#
#     print(f"   -> 🔒 Executing inventory lock (Category: {primary_category})...")
#     resp = session.post(lock_url, json=lock_payload, headers=lock_headers)
#     print(f"DEBUG Response Status: {resp.status_code}")
#     print(f"DEBUG Response Body: {resp.text[:500]}")
#     if resp.status_code != 200:
#         print(f"   -> ❌ Lock acquisition failed. HTTP {resp.status_code}")
#         return None
#
#     lock_data = resp.json()
#     t_uid = lock_data.get("transactionUID") or lock_data.get("bookingUID")
#     t_id = lock_data.get("transactionId") or lock_data.get("bookingId")
#
#     if not t_uid or not t_id:
#         print("   -> ❌ Transaction identifier missing from response payload.")
#         return None
#
#     print(f"   -> ✅ Inventory secured. Transaction ID: {t_id}")
#
#     # --- STAGE 2: CONTACT BINDING ---
#     bind_headers = lock_headers.copy()
#     bind_headers.update({
#         "x-transaction-uid": t_uid, "x-token": tokens["x-token"], "x-payment-client-id": "movies",
#         "x-email": USER_EMAIL, "x-phone": USER_PHONE, "x-mobile": USER_PHONE
#     })
#
#     bind_resp = session.get("https://in.bookmyshow.com/api/osp/v1/summary/layout/osp", headers=bind_headers)
#     if bind_resp.status_code != 200:
#         print(f"   -> ❌ Contact binding failed. HTTP {bind_resp.status_code}")
#         return None
#
#     # --- STAGE 3: PAYMENT INTENT GENERATION ---
#     pay_url = "https://services-in.bookmyshow.com/doTrans.aspx"
#     boundary = "----WebKitFormBoundaryan5s3RBMD9AfBuf0"
#
#     form_fields = {
#         "strAppCode": "WEB", "lngTransactionIdentifier": str(t_id),
#         "strCommand": "SETPAYMENT", "strVenueCode": v_code,
#         "strParam1": "|TYPE=UPI|UPITYPE=QRCODE|IMAGEURL=''|PROCESSTYPE=REQUEST|LSID=|MEMBERID=|CLIENTID=movies|",
#         "strParam2": "|ETICKET=Y|MTICKET=Y|", "strParam3": USER_EMAIL, "strParam4": USER_PHONE,
#         "strParam5": "", "strParam6": "", "strParam7": "", "strParam8": "", "strParam9": "", "strParam10": "",
#         "strFormat": "json",
#     }
#
#     body_parts = []
#     for name, value in form_fields.items():
#         body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n")
#     body_parts.append(f"--{boundary}--\r\n")
#     raw_body = "".join(body_parts).encode("utf-8")
#
#     pay_headers = bind_headers.copy()
#     pay_headers.update({
#         "Content-Type": f"multipart/form-data; boundary={boundary}",
#         "origin": "https://in.bookmyshow.com", "referer": "https://in.bookmyshow.com/",
#     })
#
#     pay_resp = session.post(pay_url, headers=pay_headers, data=raw_body)
#
#     if pay_resp.status_code == 200:
#         try:
#             str_data_list = pay_resp.json().get("BookMyShow", {}).get("strData", [])
#             if str_data_list and isinstance(str_data_list, list):
#                 encoded_upi = str_data_list[0].get("BMSUPIQRPAYURL", "")
#                 if encoded_upi:
#                     clean_upi = urllib.parse.unquote(encoded_upi)
#                     print("   -> 🎉 SUCCESS! Payment intent decoded.")
#                     return clean_upi
#         except Exception as e:
#             print(f"   -> ⚠️ Payment extraction parsing error: {e}")
#
#     print("   -> ⚠️ Intent generation failed. Proceed to manual fallback.")
#     return None

# ==============================================================================
# 📨 COMMUNICATION & NOTIFICATION HANDLERS
# ==============================================================================

def load_json(filepath, default_val):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except Exception as err:
            print(f"⚠️ JSON load failure on {filepath}: {err}")
    return default_val

def save_json(filepath, data):
    with open(filepath, "w") as f:
        json.dump(data, f, indent=4)

def send_ntfy_push(title, message, click_url=None, priority="5"):
    """Instant phone push via ntfy. Title must be plain ASCII (HTTP header)."""
    if not NTFY_SEAT_TOPIC:
        return
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Priority": priority, "Tags": "tada"}
    if click_url:
        headers["Click"] = click_url
    try:
        requests.post(
            f"{NTFY_URL}/{NTFY_SEAT_TOPIC}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=8,
        )
    except Exception as e:
        print(f"   -> ⚠️ ntfy push failed: {e}")

def send_telegram_alert(message, thread_id=None, pin=False, upi_url=None):
    # upi_url is kept for compatibility with the (disabled) auto-checkout flow.
    if upi_url:
        message += (
            f"\n\n➖➖➖➖➖➖➖➖➖➖\n"
            f"📱 **FAST CHECKOUT (MOBILE ONLY)**\n"
            f"1. Tap the link below to copy it.\n"
            f"2. Paste into Safari/Chrome to instantly open GPay/PhonePe.\n\n"
            f"`{upi_url}`\n"
            f"➖➖➖➖➖➖➖➖➖➖"
        )

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_GROUP_CHAT_ID, "text": message, "parse_mode": "Markdown"}

    if thread_id:
        try: payload["message_thread_id"] = int(thread_id)
        except ValueError: pass

    if upi_url:
        encoded_upi = urllib.parse.quote(upi_url, safe="")
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={encoded_upi}"
        payload["reply_markup"] = {"inline_keyboard": [[{"text": "📱 Show QR Code (Scan to Pay)", "url": qr_url}]]}

    try:
        response = requests.post(url, json=payload, timeout=12)
        if response.status_code != 200:
            print(f"   -> ❌ TELEGRAM REJECTED THE MESSAGE! Reason: {response.text}")
            return None

        # Capture and return the message_id for later editing
        msg_id = response.json().get("result", {}).get("message_id")

        if pin and msg_id:
            pin_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/pinChatMessage"
            requests.post(pin_url, json={
                "chat_id": TG_GROUP_CHAT_ID, "message_id": msg_id, "disable_notification": False
            }, timeout=10)
            print(f"   -> 📌 Alert pinned to thread {thread_id}.")

        return msg_id
    except Exception as e:
        print(f"⚠️ Telegram communication channel failed: {e}")
        return None

def edit_telegram_message(message_id, new_text, thread_id=None):
    """Edits an existing Telegram message to prevent spam."""
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": TG_GROUP_CHAT_ID,
        "message_id": message_id,
        "text": new_text,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200 and "not modified" not in r.text:
            print(f"   -> ⚠️ Telegram edit failed: {r.text[:200]}")
    except Exception as e:
        print(f"   -> ⚠️ Telegram edit error: {e}")

def send_expired_alert_with_button(show_name, thread_id, uid, scheduled_time, current_time):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    msg_text = (
        f"⏰ **Runtime Boundary Exceeded!**\n\n"
        f"🎬 **Feature:** '{show_name}'\n"
        f"📅 **Scheduled Start:** {scheduled_time}\n"
        f"🕒 **Current Stamp:** {current_time}\n\n"
        f"Seat tracking interval terminated. Resource tracking paused."
    )
    payload = {
        "chat_id": TG_GROUP_CHAT_ID, "text": msg_text, "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": [[{"text": "Purge Thread", "callback_data": f"delshowperm_{uid}"}]]}
    }
    if thread_id: payload["message_thread_id"] = thread_id
    try: requests.post(url, json=payload, timeout=10)
    except Exception: pass


def now_ist_str():
    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%I:%M:%S %p")


def short_reason(reason):
    text = str(reason).replace("`", "'").replace("\n", " ")
    text = re.sub(r"\b\d{4,}\b", "N", text)   # 15001 -> N, but HTTP 503 stays
    return text[:80]

MAX_LOG_LINES = 50   # safety cap for what is saved in state.json
CARD_LINES = 3       # how many latest lines the Telegram card shows

def record_failure(fs, reason):
    """Same reason as the previous run -> count+1, else a new line (keeps exact order)."""
    reason = short_reason(reason)
    now = datetime.now(ZoneInfo("Asia/Kolkata")).strftime(FULL_FMT)
    fs["fail_total"] = fs.get("fail_total", 0) + 1
    log = fs.setdefault("fail_log", [])
    if log and log[-1]["reason"] == reason:
        log[-1]["count"] += 1
        log[-1]["last"] = now
    else:
        log.append({"reason": reason, "first": now, "last": now, "count": 1})
    del log[:-MAX_LOG_LINES]

def format_fail_log(log):
    return "\n".join(f"• `{e['reason']}` — {e['since']} — ×{e['count']}" for e in log)


FULL_FMT = "%Y-%m-%d %I:%M:%S %p"

def fmt_mins(mins):
    mins = int(round(mins))
    if mins < 1:
        return "<1 min"
    h, m = divmod(mins, 60)
    return f"{h} hr {m} min" if h else f"{mins} min"

def build_status_card(fs, header):
    outages = fs.get("outages", [])
    if outages:
        avg_secs = sum(o.get("secs", o["mins"] * 60) for o in outages) / len(outages)
        prev = (f"📊 Previous: {len(outages)} outage{'s' if len(outages) != 1 else ''}"
                f" · avg {fmt_dur(avg_secs)}")
    else:
        prev = "📊 Previous: none"
    log = fs.get("fail_log", [])
    shown = log[-CARD_LINES:]
    hidden = fs.get("fail_total", 0) - sum(e["count"] for e in shown)
    lines = [prev, header]
    if hidden > 0:
        lines.append(f"_…{hidden} earlier errors_")
    lines += [f"• `{e['reason']}` — {(e.get('first') or e.get('since') or '')[11:]} — ×{e['count']}" for e in shown]
    return "\n".join(lines)[:3900]
def slot_number(slot):
    """Display seat number (string) of a slot, or None if the slot is too short to be a seat."""
    if len(slot) < 4:
        return None
    raw = slot[2:]
    disp = raw.split("+")[1] if "+" in raw else raw
    try:
        return str(int(disp))
    except ValueError:
        return disp.lstrip("0") or disp

def count_seats(str_data, exclude_cats=()):
    """Venue-wide (bookable, total) seat counts. Spaces are skipped, a couple seat counts once."""
    if not str_data:
        return 0, 0
    parts = str_data.split("||")
    rows_data = parts[1] if len(parts) > 1 else parts[0]
    avail = total = 0
    for row_str in rows_data.split("|"):
        if not row_str or ":" not in row_str:
            continue
        units = []        # [display_no, all_slots_bookable]
        prev_no = None
        for slot in row_str.split(":")[2:]:
            no = slot_number(slot)
            if no is None or no == "0" or slot[1] == SPACE_CODE:   # short slot or space
                prev_no = None
                continue
            if slot[0] in exclude_cats:
                prev_no = None
                continue
            ok = slot[1] not in NOT_BOOKABLE_CODES
            if prev_no == no:                # second half of a couple seat
                units[-1][1] = units[-1][1] and ok
            else:
                units.append([no, ok])
            prev_no = no
        total += len(units)
        avail += sum(1 for _, ok in units if ok)
    return avail, total

def category_stats(str_data):
    rows = str_data.split("||")[-1]
    stats = {}
    for row_str in rows.split("|"):
        if ":" not in row_str:
            continue
        for slot in row_str.split(":")[2:]:
            no = slot_number(slot)
            if no is None or no == "0":
                continue
            c = stats.setdefault(slot[0], {"free": 0, "booked": 0})
            if slot[1] == "2":
                c["booked"] += 1
            elif slot[1] != "0":
                c["free"] += 1
    return stats

def auto_hidden_cats(str_data, st):
    stats = category_stats(str_data)
    open_cats = set(st.get("open_cats", []))
    open_cats |= {c for c, v in stats.items() if v["booked"] > 0}
    st["open_cats"] = sorted(open_cats)
    if not open_cats:
        return set()
    return {c for c in stats if c not in open_cats}

def fmt_dur(secs):
    secs = int(round(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h: parts.append(f"{h} hr")
    if m: parts.append(f"{m} min")
    if s: parts.append(f"{s} sec")
    return " ".join(parts) or "0 sec"
# ==============================================================================
# 📡 TELEMETRY & DATA PARSING ENGINE
# ==============================================================================
RETRY_DELAYS = [1.5, 2.5, 3]   # seconds to wait after attempt 1, 2, 3

def fetch_seat_layout(session_id, venue_code, show_name, max_retries=3):
    url = f"https://services-in.bookmyshow.com/doTrans.aspx?_={int(time.time() * 1000)}"
    payload = f"strParam4=&strParam5=Y&strParam6=&strParam7=N&strParam1={session_id}&strParam2=WEB&strParam3=&strVenueCode={venue_code}&lngTransactionIdentifier=0&strAppCode=MOBAND2&strFormat=json&strCommand=GETSEATLAYOUT"

    time.sleep(random.uniform(1.0, 2.5))
    last_reason = "Unknown Origin Error"

    for attempt in range(1, max_retries + 1):
        try:
            resp = cffi_requests.post(url, headers=POST_HEADERS, data=payload, impersonate="chrome", timeout=15)
            if resp.status_code == 200:
                str_data = resp.json().get("BookMyShow", {}).get("strData", "")
                if str_data: return str_data, None
                last_reason = "Empty structural array (Error Code 5)"
                print(f"   ⚠️ Polling successful but structure invalid for {session_id} (Attempt {attempt}/{max_retries})")
            elif resp.status_code in [403, 429]:
                last_reason = f"Cloudflare Request Block (HTTP {resp.status_code})"
            else:
                last_reason = f"Upstream Gateway Error (HTTP {resp.status_code})"
        except Exception as e:
            last_reason = f"Socket Exception: {e}"

        if attempt < max_retries:
            time.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])

    print(f"   ❌ Final telemetry extraction failure for {session_id}: {last_reason}")
    return "", last_reason


def parse_layout(str_data, exclude_cats=()):
    if not str_data: return {}
    parts = str_data.split("||")

    valid_categories = []
    if len(parts) > 1:
        for cat_str in parts[0].split("|"):
            if ":" in cat_str: valid_categories.append(cat_str.split(":")[0])

    fallback_category = valid_categories[0] if valid_categories else "CL"
    rows_data = parts[1] if len(parts) > 1 else parts[0]
    available_seats_by_row = {}

    for row_str in rows_data.split("|"):
        if not row_str or ":" not in row_str: continue
        elements = row_str.split(":")

        raw_cat = elements[0]
        category_code = raw_cat if raw_cat in valid_categories else fallback_category
        row_letter = elements[1]
        seats = elements[2:]

        avail_seats = []
        for grid_idx, seat in enumerate(seats):
            no = slot_number(seat)
            if no is None or no == "0":
                continue                      # short slot or space
            code = seat[1]
            if code in NOT_BOOKABLE_CODES:
                continue                      # space (0) / booked (2)
            if seat[0] in exclude_cats:
                continue                      # hidden category
            if code not in KNOWN_CODES and code not in _unknown_codes:
                _unknown_codes.add(code)
                print(f"   ⚠️ Unknown seat status code '{code}' (slot {seat!r}) - treated as bookable")
            avail_seats.append({"num": no, "idx": grid_idx})

        if avail_seats:
            available_seats_by_row[row_letter] = {"category": category_code, "width": len(seats), "seats": avail_seats}

    return available_seats_by_row

def find_matching_seats(available_by_row, show_reqs):
    seat_count = show_reqs.get("seat_count", 1)
    req_adj = show_reqs.get("require_adjacent", False)
    row_prefs = show_reqs.get("row_preferences", {})
    any_row = len(row_prefs) == 0

    valid_seats_pool = {}
    for row, row_data in available_by_row.items():
        if not any_row and row not in row_prefs: continue
        allowed_seats = row_prefs.get(row, [])
        valid = [s for s in row_data["seats"] if s["num"] in allowed_seats] if allowed_seats else row_data["seats"]
        if valid:
            valid_seats_pool[row] = {"category": row_data["category"], "width": row_data["width"], "seats": valid}

    if not valid_seats_pool: return False, [], [], []

    if req_adj:
        ranked_matches = []
        for row, row_data in valid_seats_pool.items():
            cat = row_data["category"]
            seat_objs, row_center = row_data["seats"], row_data["width"] / 2.0
            for i in range(len(seat_objs) - seat_count + 1):
                window = seat_objs[i : i + seat_count]
                if all(window[j]["idx"] - window[j-1]["idx"] == 1 for j in range(1, seat_count)):
                    block_center = sum(s["idx"] for s in window) / seat_count
                    ranked_matches.append({
                        "score": abs(row_center - block_center),
                        "text": f"Row {row}: {', '.join([s['num'] for s in window])}",
                        "raw_seats": [{"row": row, "num": s["num"], "category": cat} for s in window]
                    })
        if not ranked_matches: return False, [], [], []

        ranked_matches.sort(key=lambda x: x["score"])
        all_matches_text = [m["text"] for m in ranked_matches]
        all_raw_lists = [m["raw_seats"] for m in ranked_matches]
        return True, all_matches_text[:5], all_matches_text, all_raw_lists
    else:
        all_valid_seats = []
        for row, row_data in valid_seats_pool.items():
            cat = row_data["category"]
            for s in row_data["seats"]: all_valid_seats.append({"row": row, "num": s["num"], "category": cat})
        if len(all_valid_seats) >= seat_count:
            found_seats = all_valid_seats[:seat_count]
            seat_sequence = ", ".join(f"{s['row']}-{s['num']}" for s in found_seats)
            match_text = f"Scattered Sequence: {seat_sequence}"
            return True, [match_text], [match_text], [found_seats]

        return False, [], [], []
    
from collections import Counter

def print_status_summary(str_data):
    parts = str_data.split("||")
    rows_data = parts[1] if len(parts) > 1 else parts[0]
    counts = Counter()
    bookable = []
    for row_str in rows_data.split("|"):
        if not row_str or ":" not in row_str:
            continue
        el = row_str.split(":")
        row = el[1]
        for slot in el[2:]:
            no = slot_number(slot)
            if no is None or no == "0":
                continue
            counts[slot[1]] += 1
            if slot[1] not in NOT_BOOKABLE_CODES:
                bookable.append(f"{row}-{no}[{slot}]")
    print("   -> 🔢 Status codes: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("   -> 🪑 Bookable (raw slot): " + " ".join(bookable))
# ==============================================================================
# 🚀 CORE EXECUTION ORCHESTRATOR
# ==============================================================================

def main():
    print(f"🚀 CRON JOB INITIATED: {datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')}")
    state = load_json(STATE_FILE, {})
    shows = load_json(SHOWS_FILE, {})

    if not shows:
        print("Empty tracking directive (shows.json). Concluding execution cycle.")
        return

    current_time = time.time()
    ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    shows_updated = False
    state_updated = False

    for uid, show in list(shows.items()):
        status = show.get("status")
        closed_at = show.get("closed_at", 0)
        s_name = show.get("name", "Unknown")
        thread_id = show.get("message_thread_id")
        s_id = show.get("session_id")
        v_code = show.get("venue_code")
        e_code = show.get("event_code")

        # [DISABLED - auto-checkout] only used by the checkout API
        # c_code = show.get("company_code") or VENUE_TO_COMPANY.get(v_code, "INOX")
        theatre = show.get("theatre")

        if status == "closed":
            if closed_at and (current_time - closed_at) > (14 * 86400):
                if thread_id and TG_GROUP_CHAT_ID:
                    try:
                        requests.post(
                            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/deleteForumTopic",
                            json={"chat_id": TG_GROUP_CHAT_ID, "message_thread_id": thread_id},
                            timeout=10
                        )
                    except Exception: pass
                exact_state_key = f"{v_code}_{s_id}_{str(thread_id).strip() if thread_id else ''}"
                if exact_state_key in state:
                    del state[exact_state_key]
                    state_updated = True
                del shows[uid]
                shows_updated = True
                print(f"🗑️ Retention boundary elapsed. Purged historical artifact for '{s_name}'.")
            continue

        state_key = f"{v_code}_{s_id}_{thread_id or ''}"
        print(f"\n🔍 Executing diagnostic for '{s_name}' (ID: {s_id})...")

        date_str = show.get("date")
        time_str = show.get("show_time")
        is_show_started = False

        if date_str and time_str:
            try:
                show_dt = datetime.strptime(f"{date_str} {time_str.upper()}", "%Y%m%d %I:%M %p").replace(tzinfo=ZoneInfo("Asia/Kolkata"))
                if ist_now >= show_dt: is_show_started = True
                if ist_now >= show_dt + timedelta(minutes=30):
                    if not state.get(state_key, {}).get("expired_notified"):
                        send_expired_alert_with_button(s_name, thread_id, uid, show_dt.strftime("%d/%m/%Y %I:%M %p"), ist_now.strftime("%d/%m/%Y %I:%M %p"))
                        if state_key not in state: state[state_key] = {}
                        state[state_key]["expired_notified"] = True
                        save_json(STATE_FILE, state)
                        print(f"   -> ⏰ Runtime elapsed. Disabling subsequent intervals.")
                    continue
            except Exception as dt_err:
                print(f"   -> ⚠️ Date parsing anomaly bypass: {dt_err}")

        started_banner = "⚠️ **NOTICE: Presentation Already Commenced!**\n" if is_show_started else ""
        str_data, error_reason = fetch_seat_layout(s_id, v_code, s_name)

        if state_key not in state: state[state_key] = {}

        if not str_data:
            fs = state[state_key]
            if not fs.get("serverfailed", False):
                fs["serverfailed"] = True
                fs["first_failed_time"] = now_ist_str()
                fs["first_failed_full"] = datetime.now(ZoneInfo("Asia/Kolkata")).strftime(FULL_FMT)
                fs["first_failed_ts"] = time.time()
                fs["fail_log"] = []
                fs["fail_total"] = 0
            record_failure(fs, error_reason or "Unknown error")
            state_updated = True
            save_json(STATE_FILE, state)

            status_msg_id = show.get("status_msg_id")
            if status_msg_id:
                header = f"🔴 **Outage #{len(fs.get('outages', [])) + 1}** — began {fs['first_failed_time']}"
                edit_telegram_message(status_msg_id, build_status_card(fs, header), thread_id)
            print(f"   -> ⚠️ Fetch failed for '{s_name}': {error_reason}")
            continue
        else:
            fs = state[state_key]
            if fs.get("serverfailed", False):
                dur_mins = (time.time() - fs.get("first_failed_ts", time.time())) / 60
                n = len(fs.get("outages", [])) + 1

                status_msg_id = show.get("status_msg_id")
                if status_msg_id:
                    header = f"🟢 **Restored** — outage #{n} lasted {fmt_dur(dur_mins * 60)} (at {now_ist_str()})"
                    edit_telegram_message(status_msg_id, build_status_card(fs, header), thread_id)

                # Save the finished outage, then clear the live data
                fs.setdefault("outages", []).append({
    "n": n,
    "start": fs.get("first_failed_full", "?"),
    "end": datetime.now(ZoneInfo("Asia/Kolkata")).strftime(FULL_FMT),
    "mins": round(dur_mins, 1),
        "secs": round(dur_mins * 60),
    "total_fails": fs.get("fail_total", 0),
        "reasons": [f"{e['reason']} | {e.get('first', e.get('since', '?'))} -> {e.get('last', e.get('first', e.get('since', '?')))} | x{e['count']}"
                for e in fs.get("fail_log", [])],
})
                fs["serverfailed"] = False
                for k in ("first_failed_time", "first_failed_full", "first_failed_ts",
                          "fail_log", "fail_total", "failure_count"):
                    fs.pop(k, None)
                state_updated = True
                print(f"    -> ✅ Upstream restored for '{s_name}'. Status card updated to green.")
        _unknown_codes.clear()            # collect unknown status codes for THIS show only
        excl = auto_hidden_cats(str_data, state[state_key])
        print(f"   -> 🏷️ Auto-hidden: {sorted(excl)}  stats: {category_stats(str_data)}")
        current_avail = parse_layout(str_data, excl)
        unknown_codes_now = sorted(_unknown_codes)
        current_valid_seats = set()
        row_prefs = show.get("row_preferences", {})
        any_row = len(row_prefs) == 0

        for row, row_data in current_avail.items():
            if not any_row and row not in row_prefs: continue
            for s in row_data["seats"]:
                if not row_prefs.get(row, []) or s["num"] in row_prefs.get(row, []):
                    current_valid_seats.add(f"{row}-{s['num']}")

        is_match, top_5_matches, current_all_matches, current_raw_lists = find_matching_seats(current_avail, show)
        # print_status_summary(str_data)
        # print("   -> 🏷️ Categories:", str_data.split("||")[0])
        print(f"   -> 🎯 Matches: {current_all_matches}")

        if state_key not in state or "known_seats" not in state[state_key]:
            print(f"   -> ⚡ Initialization cycle mapped for {s_name}. Bypassing delta logic.")
            previous_seats, previous_all_matches = set(), set()
        else:
            previous_seats = set(state[state_key].get("known_seats", []))
            previous_all_matches = set(state[state_key].get("all_matches", []))

        newly_unblocked_raw = current_valid_seats - previous_seats
        new_combinations = set(current_all_matches) - previous_all_matches
        lost_combinations = previous_all_matches - set(current_all_matches)

        state_changed = False
                # ---------------- ALERT CONTENT ----------------
        avail_cnt, total_cnt = count_seats(str_data, excl)
        seats_line = f"**Seats:** {avail_cnt} bookable in {total_cnt}"
        total_matches = len(current_all_matches)
        new_n, lost_n = len(new_combinations), len(lost_combinations)
        sep = "➖➖➖➖➖➖➖➖➖➖"

        if current_all_matches:
            top_lines = "\n".join(f"• ✅ {m}" for m in current_all_matches[:5])
            matches_text = f"🎯 **Top Viable Matches ({total_matches}):**\n{top_lines}"
            if total_matches > 5:
                matches_text += f"\n...and {total_matches - 5} more match{'es' if total_matches - 5 != 1 else ''}"
        else:
            matches_text = "🚫 **Inventory Exhausted.**"

        bms_url = f"https://in.bookmyshow.com/booktickets/{v_code}/{s_id}"
        bms_link = f"[BMS Application]({bms_url})"
        chain_url = CINEMA_CHAIN_URLS.get(v_code)
        if chain_url:
            chain_label = 'PVR Terminal' if 'pvr' in str(chain_url).lower() else 'INOX Terminal'
            action_links = f"🔗 {bms_link}  |  [{chain_label}]({chain_url})"
        else:
            action_links = f"🔗 {bms_link}"
        links = f"\n{action_links}" if current_all_matches else ""
        code_note = ""
        if unknown_codes_now:
            shown = ", ".join(f"`{c if c.isalnum() else '?'}`" for c in unknown_codes_now)
            code_note = f"\n⚠️ New seat status code appeared: {shown} — considered bookable"
        top_seats = " | ".join(current_all_matches[:3])

        # ---------------- SEND ----------------
        if new_n and not lost_n:
            print(f"   -> 🟢 NEW MATCHES: {new_n} released for {s_name}")
            send_ntfy_push(f"NEW MATCHES: {s_name}",
                           f"{new_n} blocks released. Seats {avail_cnt}/{total_cnt}. Best: {top_seats}",
                           click_url=bms_url)
            msg = (f"🚨 **NEW MATCHES**\n{started_banner}🎬 **Feature:** {s_name}\n"
                   f"{seats_line}\n"
                   f"🆕 **Newly Released:** {new_n} blocks\n"
                   f"{sep}\n{matches_text}{links}{code_note}")
            send_telegram_alert(msg, thread_id)

        elif new_n and lost_n:
            print(f"   -> 🔄 NEW AND LOST for {s_name}")
            send_ntfy_push(f"NEW AND LOST: {s_name}",
                           f"{new_n} new, {lost_n} taken. Seats {avail_cnt}/{total_cnt}. Best: {top_seats}",
                           click_url=bms_url)
            msg = (f"🔄 **NEW AND LOST MATCHES**\n{started_banner}🎬 **{s_name}**\n"
                   f"{seats_line}\n"
                   f"🆕 **Newly Released:** {new_n} blocks\n"
                   f"❌ **Secured by Others:** {lost_n} blocks\n"
                   f"{sep}\n{matches_text}{links}{code_note}")
            send_telegram_alert(msg, thread_id)

        elif lost_n:
            print(f"   -> 💔 LOST MATCHES for {s_name}")
            # Telegram only: no phone buzz when there's nothing to grab
            msg = (f"💔 **LOST MATCHES**\n{started_banner}🎬 **Feature:** {s_name}\n"
                   f"{seats_line}\n"
                   f"❌ **Secured by Others:** {lost_n} blocks\n"
                   f"{sep}\n{matches_text}{links}{code_note}")
            send_telegram_alert(msg, thread_id)

        # ---------------- SNAPSHOT (every run, so later comparisons stay accurate) ----------------
        # state[state_key]["last_checked_ist"] = ist_now.strftime("%Y-%m-%d %I:%M:%S %p")
        # state[state_key]["known_seats"] = list(current_valid_seats)
        # state[state_key]["all_matches"] = current_all_matches
        # state_updated = True
        # save_json(STATE_FILE, state)

        state_changed = True

        # Always record the exact time the status was last checked/polled
        state[state_key]["last_checked_ist"] = ist_now.strftime("%Y-%m-%d %I:%M:%S %p")

        if state_changed:
            state[state_key]["known_seats"] = list(current_valid_seats)
            state[state_key]["all_matches"] = current_all_matches

        state_updated = True

    if state_updated: save_json(STATE_FILE, state)
    if shows_updated: save_json(SHOWS_FILE, shows)

    print(f"\n✅ CRON CYCLE TERMINATED: {datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    main()
