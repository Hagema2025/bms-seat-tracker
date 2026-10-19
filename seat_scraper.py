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

# ==============================================================================
# ⚙️ SECURE CONFIGURATION & ENVIRONMENT VARIABLES
# ==============================================================================
SHOWS_FILE = "shows.json"
STATE_FILE = "state.json"

# Credentials & API Keys
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_GROUP_CHAT_ID = os.environ.get("TG_GROUP_CHAT_ID") 
NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh").strip()
NTFY_ERROR_TOPIC = os.environ.get("NTFY_ERROR_TOPIC", "").strip()
NTFY_SEAT_TOPIC = os.environ.get("NTFY_SEAT_TOPIC", "").strip()

# User details for the checkout pipeline binding
USER_EMAIL = os.environ.get("BMS_EMAIL")
USER_PHONE = os.environ.get("BMS_MOBILE")

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

# Company Code Mapping for Checkout API
VENUE_TO_COMPANY = {
    "INTO": "INOX", "INPR": "INOX", "INCH": "INOX", "FMCN": "INOX",
    "PVHR": "PVR", "PGMV": "PVR", "PVES": "PVR", "PVSR": "PVR",
    "PABC": "PVR", "PGRA": "PVR", "PVPZ": "PVR", "PVHC": "PVR",
    "PCAN": "PVR", "PBRM": "PVR", "PSKL": "PVR",
    "CBMC": "CINEPOLIS"
}

# ==============================================================================
# 🔐 FAST HTTP CHECKOUT PIPELINE (CURL_CFFI)
# ==============================================================================
import subprocess

# ... (keep your other imports) ...

def extract_tokens(event_code, venue_code, session_id, date_str):
    print("   -> 🕵️ Booting Puppeteer stealth to attempt Cloudflare bypass...")
    
    try:
        # Call the Node.js script
        result = subprocess.run(
            ["node", "token_extractor.js", event_code, venue_code, session_id, date_str],
            capture_output=True,
            text=True,
            timeout=45 # Give Puppeteer enough time to solve CF JS challenges
        )
        
        # Parse the JSON output from Node.js
        output = result.stdout.strip()
        tokens = json.loads(output)
        
        if "error" in tokens:
            print(f"   -> ⚠️ Puppeteer encountered an error: {tokens['error']}")
            return None, None

        # Build the ultra-fast curl_cffi session for the rest of the pipeline
        # Build the ultra-fast curl_cffi session for the rest of the pipeline
        session = cffi_requests.Session(impersonate="chrome120")
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Upgrade-Insecure-Requests": "1"
        })
        
        # Inject the Puppeteer-derived cookies into the cffi session
        if tokens.get("cf_clearance"):
            session.cookies.set("cf_clearance", tokens["cf_clearance"], domain=".bookmyshow.com")
        if tokens.get("bmsId"):
            session.cookies.set("bmsId", tokens["bmsId"], domain=".bookmyshow.com")
            
        print(f"   -> 📊 Status -> bmsId: {bool(tokens.get('bmsId'))}, cf_clearance: {bool(tokens.get('cf_clearance'))}")
        
        return tokens, session

    except subprocess.TimeoutExpired:
        print("   -> ❌ Puppeteer extraction timed out.")
        return None, None
    except json.JSONDecodeError:
        print(f"   -> ❌ Failed to parse Puppeteer output. Raw output: {result.stdout}")
        return None, None
    except Exception as e:
        print(f"   -> ⚠️ Unexpected token extraction failure: {e}")
        return None, None

def format_seat_string(seat_objs):
    formatted = f"|{len(seat_objs)}|"
    for s in seat_objs:
        formatted += f"{s['category']}|1|{s['row']}|{s['num']}|"
    return formatted

def run_auto_checkout(tokens, session, e_code, v_code, s_id, seat_objs, company_code):
    print("   -> 🚀 Initiating zero-latency TLS-impersonated checkout sequence...")
    primary_category = seat_objs[0]["category"] if seat_objs else "CL"

    # --- STAGE 1: INVENTORY LOCK ---
    lock_url = "https://in.bookmyshow.com/api/v2/mobile/booking/movies"
    lock_headers = {
        "x-app-code": "WEB", "x-platform": "WEB", "x-platform-code": "WEB",
        "x-bms-id": tokens.get("bmsId", "")
    }
    lock_payload = {
        "appCode": "WEB", "venueCode": v_code, "eventCode": e_code,
        "sessionId": s_id, "numberOfTickets": len(seat_objs),
        "ticketCategory": primary_category,
        "selectedSeats": format_seat_string(seat_objs),
        "bmsId": tokens.get("bmsId", ""), "token": tokens["x-token"],
        "seatLayoutType": "Y", "offerData": {"offerSelected": False}, "companyCode": company_code,
    }
    
    print(f"   -> 🔒 Executing inventory lock on selected array (Category: {primary_category})...")
    resp = session.post(lock_url, json=lock_payload, headers=lock_headers)
    print(f"DEBUG Response Status: {resp.status_code}")
    print(f"DEBUG Response Body: {resp.text}") # <--- This will tell you exactly why it failed
    if resp.status_code != 200:
        print(f"   -> ❌ Lock acquisition failed. Gateway returned HTTP {resp.status_code}")
        return None
    
    lock_data = resp.json()
    t_uid = lock_data.get("transactionUID") or lock_data.get("bookingUID")
    t_id = lock_data.get("transactionId") or lock_data.get("bookingId")
    
    if not t_uid or not t_id: 
        print("   -> ❌ Transaction identifier missing from response payload.")
        return None
        
    print(f"   -> ✅ Inventory secured. Registered Transaction ID: {t_id}")

    # --- STAGE 2: CONTACT BINDING ---
    print("   -> 📝 Binding registered contact configuration to transaction parameters...")
    bind_headers = lock_headers.copy()
    bind_headers.update({
        "x-transaction-uid": t_uid, "x-token": tokens["x-token"], "x-payment-client-id": "movies",
        "x-email": USER_EMAIL, "x-phone": USER_PHONE, "x-mobile": USER_PHONE
    })
    
    bind_resp = session.get("https://in.bookmyshow.com/api/osp/v1/summary/layout/osp", headers=bind_headers)
    if bind_resp.status_code != 200: 
        print(f"   -> ❌ Contact binding failed. Gateway returned HTTP {bind_resp.status_code}")
        return None

    # --- STAGE 3: PAYMENT GATEWAY INTENT GENERATION ---
    print("   -> 💸 Transmitting SETPAYMENT directive to payment orchestrator...")
    pay_url = "https://services-in.bookmyshow.com/doTrans.aspx"
    boundary = "----WebKitFormBoundaryan5s3RBMD9AfBuf0"
    
    form_fields = {
        "strAppCode": "WEB", "lngTransactionIdentifier": str(t_id),
        "strCommand": "SETPAYMENT", "strVenueCode": v_code,
        "strParam1": "|TYPE=UPI|UPITYPE=QRCODE|IMAGEURL=''|PROCESSTYPE=REQUEST|LSID=|MEMBERID=|CLIENTID=movies|",
        "strParam2": "|ETICKET=Y|MTICKET=Y|", "strParam3": USER_EMAIL, "strParam4": USER_PHONE,
        "strParam5": "", "strParam6": "", "strParam7": "", "strParam8": "", "strParam9": "", "strParam10": "",
        "strFormat": "json",
    }

    body_parts = []
    for name, value in form_fields.items():
        body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n")
    body_parts.append(f"--{boundary}--\r\n")
    raw_body = "".join(body_parts).encode("utf-8")

    pay_headers = bind_headers.copy()
    pay_headers.update({
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "origin": "https://in.bookmyshow.com", "referer": "https://in.bookmyshow.com/",
    })

    pay_resp = session.post(pay_url, headers=pay_headers, data=raw_body)
    
    if pay_resp.status_code == 200:
        try:
            str_data_list = pay_resp.json().get("BookMyShow", {}).get("strData", [])
            if str_data_list and isinstance(str_data_list, list):
                encoded_upi = str_data_list[0].get("BMSUPIQRPAYURL", "")
                if encoded_upi:
                    clean_upi = urllib.parse.unquote(encoded_upi)
                    print("   -> 🎉 SUCCESS! Deep link payment intent isolated and decoded.")
                    return clean_upi
        except Exception as e: 
            print(f"   -> ⚠️ Payment extraction parsing error: {e}")
            
    print("   -> ⚠️ Intent generation exhausted. Proceed to manual fallback.")
    return None

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
def send_telegram_alert(message, thread_id=None, pin=False, upi_url=None):
    import urllib.parse
    
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
            
        return msg_id  # <--- NEW: Returns the ID
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
    try: requests.post(url, json=payload, timeout=10)
    except Exception: pass

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

# ==============================================================================
# 📡 TELEMETRY & DATA PARSING ENGINE
# ==============================================================================

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
            
        time.sleep(attempt * 3)
            
    print(f"   ❌ Final telemetry extraction failure for {session_id}: {last_reason}")
    return "", last_reason    

def parse_layout(str_data):
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
            if len(seat) >= 4 and seat[1] == '1': 
                raw_seat_num = seat[2:]
                display_num = raw_seat_num.split("+")[1] if "+" in raw_seat_num else raw_seat_num
                try: clean_num = str(int(display_num))
                except ValueError: clean_num = display_num.lstrip("0") or display_num
                avail_seats.append({"num": clean_num, "idx": grid_idx})
        
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
        
        c_code = show.get("company_code") or VENUE_TO_COMPANY.get(v_code, "INOX")
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

        started_banner = "⚠️ **NOTICE: Presentation Already Commenced!**\n\n" if is_show_started else ""
        
        str_data, error_reason = fetch_seat_layout(s_id, v_code, s_name)
        
        if state_key not in state: state[state_key] = {}

        current_time_str = ist_now.strftime("%I:%M:%S %p")

        if not str_data:
            # Check if we already recorded a failure for this show
            if not state[state_key].get("serverfailed", False):
                # First time failing: Send alert and save state
                fail_time_str = ist_now.strftime("%I:%M:%S %p")
                state[state_key]["serverfailed"] = True
                state[state_key]["first_failed_time"] = fail_time_str
                state[state_key]["failure_count"] = 1
                state_updated = True
                status_msg_id = show.get("status_msg_id")
                if status_msg_id:
                    error_text = (
                        f"🔴 **Live Status:** `Upstream Disconnect`\n"
                        f"• _Outage Began: {fail_time_str}_\n"
                        f"• _Status: Remote server unreachable. Retrying..._"
                    )
                    edit_telegram_message(status_msg_id, error_text, thread_id)
                print(f"    -> ⚠️ Upstream disconnect detected for '{s_name}'. Status card updated to red.")


            else:
                # Already in a failed state: Ignore completely (no spam, no edits)
                state[state_key]["failure_count"] = state[state_key].get("failure_count", 1) + 1
                state_updated = True
                print(f"   -> ⚠️ Server still down for '{s_name}'. Suppressing duplicate alert.")
            
            continue 
        else:
            # Server is responding successfully
            if state[state_key].get("serverfailed", False):
                # It just recovered from a failure!
                start_fail_time = state[state_key].get("first_failed_time", "Unknown")
                fail_count = state[state_key].get("failure_count", 1)
                recovery_time_str = ist_now.strftime("%I:%M:%S %p")
              
                state[state_key]["serverfailed"] = False
                state[state_key].pop("first_failed_time", None)
                state[state_key].pop("failure_count", None)
                state_updated = True
                # Edit the status message back to green success
                status_msg_id = show.get("status_msg_id")
                if status_msg_id:
                    success_text = (
                        f"🟢 **Live Status:** `Operational (Restored)`\n"
                        f"• _Outage Began: {start_fail_time}_\n"
                        f"• _Recovered At: {recovery_time_str}_\n"
                        f"• _Skipped Cycles: {fail_count}_"
                    )
                    edit_telegram_message(status_msg_id, success_text, thread_id)
                    
                print(f"    -> ✅ Upstream restored for '{s_name}'. Status card updated to green.")

        current_avail = parse_layout(str_data)
        current_valid_seats = set()
        row_prefs = show.get("row_preferences", {})
        any_row = len(row_prefs) == 0
        
        for row, row_data in current_avail.items():
            if not any_row and row not in row_prefs: continue
            for s in row_data["seats"]:
                if not row_prefs.get(row, []) or s["num"] in row_prefs.get(row, []):
                    current_valid_seats.add(f"{row}-{s['num']}")

        is_match, top_5_matches, current_all_matches, current_raw_lists = find_matching_seats(current_avail, show)
        
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
        still_avail_text = "🚫 **Inventory Exhausted.**"
        
        if current_all_matches:
            top_5_still = "\n".join([f"• ✅ {m}" for m in current_all_matches[:5]])
            still_avail_text = f"🎯 **Top Viable Matches:**\n{top_5_still}"
            if len(current_all_matches) > 5: still_avail_text += f"\n*...and {len(current_all_matches) - 5} subsequent configurations.*"

        bms_link = f"[BMS Application](https://in.bookmyshow.com/booktickets/{v_code}/{s_id})"
        chain_url = CINEMA_CHAIN_URLS.get(v_code)
        
        if chain_url:
            chain_label = 'PVR Terminal' if 'pvr' in str(chain_url).lower() else 'INOX Terminal'
            action_links = f"🔗 {bms_link}  |  [{chain_label}]({chain_url})"
        else:
            action_links = f"🔗 {bms_link}"

        trigger_type = None
        if is_match and new_combinations:
            trigger_type = "NEW"
            print(f"   -> 🟢 PRIMARY TRIGGER EXECUTED: Availability detected for {s_name}!")
        elif lost_combinations and new_combinations:
            trigger_type = "FLUX"
            print(f"   -> 🔄 SYMMETRIC FLUX: Inventory simultaneously secured and released for {s_name}.")
        elif lost_combinations:
            trigger_type = "LOST"
            print(f"   -> 🔴 NEGATIVE FLUX: Target matches acquired by competing sessions.")
            
        if trigger_type:
            final_upi_link = None
            locked_seats_text = ""
            
            # 🔥 UNIVERSAL AUTO-CHECKOUT
            if is_match and current_raw_lists:
                top_best_seat_combo = current_raw_lists[0] 
                if e_code and date_str:
                    try:
                        tokens, active_session = extract_tokens(e_code, v_code, s_id, date_str) 
                        
                        final_upi_link = run_auto_checkout(
                            tokens, active_session, e_code, v_code, s_id, top_best_seat_combo, c_code
                        )
                        if final_upi_link:
                            seat_names_str = ", ".join([f"{s['row']}-{s['num']}" for s in top_best_seat_combo])
                            # Highlight the successfully locked seats right at the top
                            locked_seats_text = f"🎟️ **AUTO-LOCKED SEATS:** `{seat_names_str}`\n\n"
                    except Exception as ex:
                        print(f"   -> ⚠️ Automated checkout phase aborted: {ex}")
                else:
                    print("   -> ⚠️ Auto-checkout constraint: Missing 'event_code' or 'date'.")
            
            if trigger_type == "NEW":
                msg = (f"🚨 **TARGET ACQUIRED!**\n\n{started_banner}🎬 **Feature:** {s_name}\n"
                       f"🆕 **Volume:** {len(newly_unblocked_raw)} seats unblocked\n\n{locked_seats_text}{still_avail_text}\n\n{action_links}")
                send_telegram_alert(msg, thread_id, pin=bool(final_upi_link), upi_url=final_upi_link)
                
            elif trigger_type == "FLUX":
                msg = (f"🔄 **INVENTORY SHIFT**\n\n{started_banner}🎬 **{s_name}**\n\n"
                       f"❌ **Secured by Others:** {len(lost_combinations)} blocks\n🆕 **Newly Released:** {len(new_combinations)} blocks\n\n"
                       f"{locked_seats_text}➖➖➖➖➖➖➖➖➖➖\n\n{still_avail_text}\n\n{action_links}")
                send_telegram_alert(msg, thread_id, pin=bool(final_upi_link), upi_url=final_upi_link)
                
            elif trigger_type == "LOST":
                lost_list = list(lost_combinations)
                lost_text = "\n".join([f"• ❌ {m}" for m in lost_list[:5]])
                if len(lost_list) > 5: lost_text += f"\n*...and {len(lost_list) - 5} subsequent configurations.*"
                msg = (f"💔 **TARGETS COMPROMISED**\n\n{started_banner}🎬 **Feature:** {s_name}\n\n"
                       f"Just Acquired By Others:\n{lost_text}\n\n{locked_seats_text}➖➖➖➖➖➖➖➖➖➖\n\n{still_avail_text}")
                if current_all_matches: msg += f"\n\n{action_links}"
                send_telegram_alert(msg, thread_id, pin=bool(final_upi_link), upi_url=final_upi_link)
                
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
