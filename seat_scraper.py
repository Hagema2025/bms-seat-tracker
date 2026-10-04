import os
import time
import json
from zoneinfo import ZoneInfo
import requests
import random  
from datetime import datetime
from curl_cffi import requests as cffi_requests
import base64
import io
import qrcode
from urllib.parse import urljoin
import re

# --- CONFIGURATION ---
SHOWS_FILE = "shows.json"
STATE_FILE = "state.json"
ENABLE_BOOKING = False

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TG_GROUP_CHAT_ID = os.environ.get("TG_GROUP_CHAT_ID", "YOUR_CHAT_ID_HERE") 
NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh").strip()
NTFY_ERROR_TOPIC = os.environ.get("NTFY_ERROR_TOPIC", "").strip()
NTFY_SEAT_TOPIC = os.environ.get("NTFY_SEAT_TOPIC", "").strip()

BMS_ID = os.environ.get("BMS_ID", "")
BMS_EMAIL = os.environ.get("BMS_EMAIL", "")
BMS_MOBILE = os.environ.get("BMS_MOBILE", "")


BMS_BASE_URL = "https://in.bookmyshow.com"


def get_booking_token(show_url, session):
    """
    Discover the current BookMyShow seat-layout JS bundle and
    extract the booking token used by /api/v2/mobile/booking/movies.

    Returns:
        str: booking token

    Raises:
        RuntimeError: if bundle or token cannot be found
    """

    print("🔎 Discovering current BMS booking bundle...")

    # ------------------------------------------------------------
    # 1. Load the actual seat-layout page
    # ------------------------------------------------------------
    response = session.get(
        show_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/154.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=20,
    )

    response.raise_for_status()

    html_text = response.text

    # ------------------------------------------------------------
    # 2. Find app-seatlayout-movies.<hash>.js
    # ------------------------------------------------------------
    bundle_matches = re.findall(
        r'<script[^>]+src=["\']([^"\']*app-seatlayout-movies[^"\']*\.js)["\']',
        html_text,
        re.IGNORECASE,
    )

    if not bundle_matches:
        # Fallback: sometimes the script URL appears elsewhere
        bundle_matches = re.findall(
            r'["\']([^"\']*app-seatlayout-movies[^"\']*\.js)["\']',
            html_text,
            re.IGNORECASE,
        )

    if not bundle_matches:
        raise RuntimeError(
            "Could not find app-seatlayout-movies JS bundle"
        )

    bundle_url = urljoin(BMS_BASE_URL, bundle_matches[0])

    print(f"   -> Bundle found: {bundle_url}")

    # ------------------------------------------------------------
    # 3. Download the current bundle
    # ------------------------------------------------------------
    js_response = session.get(
        bundle_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/154.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Referer": show_url,
        },
        timeout=20,
    )

    js_response.raise_for_status()

    js = js_response.text

    # ------------------------------------------------------------
    # 4. Find the token specifically near the booking payload
    #
    # We do NOT simply search for `token:` because the bundle
    # contains many unrelated token fields.
    # ------------------------------------------------------------
    booking_block = re.search(
        r'bmsId\s*:\s*[^,]+,\s*'
        r'token\s*:\s*["\']([^"\']+)["\']\s*,\s*'
        r'seatLayoutType',
        js,
        re.DOTALL,
    )

    if not booking_block:
        raise RuntimeError(
            "Could not extract booking token from app-seatlayout-movies bundle"
        )

    token = booking_block.group(1)

    if not token:
        raise RuntimeError("Extracted booking token is empty")

    print("   -> ✅ Booking token discovered")

    return token


if not TG_BOT_TOKEN or not TG_GROUP_CHAT_ID:
    print("❌ ERROR: Telegram secrets are missing!")
    exit(1)

POST_HEADERS = {
    "Host": "services-in.bookmyshow.com",
    "X-App-Code": "MOBAND2",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10)",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept-Encoding": "gzip, deflate",
}


# --- CINEMA CHAIN URLS ---
CINEMA_CHAIN_URLS = {
    # INOX
    "INTO": "https://www.inoxmovies.com/cinemasessions/Chennai/INOX-The-Marina-Mall,-OMR,-Chennai/232",
    "INPR": "https://www.inoxmovies.com/cinemasessions/Chennai/INOX-Luxe-Phoenix-Market-City,-Velachery--(formerly-Jazz-Cinemas)Chennai/320",
    "INCH": "https://www.inoxmovies.com/cinemasessions/Chennai/INOX-Chennai-Citi-Centre,Dr.-R.-K.-Salai-Chennai/113",
    "FMCN": "https://www.inoxmovies.com/cinemasessions/Chennai/INOX-National,Virugambakkam-Chennai/28",

    # PVR
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

     #Cinepolis
        "CBMC":"https://cinepolisindia.com/movie-list/38",
}

# --- HELPERS ---

def load_json(filepath, default_val):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f: return json.load(f)
        except Exception: pass
    return default_val

def save_json(filepath, data):
    with open(filepath, "w") as f: json.dump(data, f, indent=2)

def send_telegram_alert(message, thread_id=None):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_GROUP_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    if thread_id: payload["message_thread_id"] = thread_id
    try: 
        requests.post(url, json=payload, timeout=10)
    except Exception as e: 
        print(f"⚠️ Telegram alert failed: {e}")

def send_expired_alert_with_button(show_name, thread_id, uid, scheduled_time, current_time):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    
    msg_text = (
        f"⏰ **Showtime Reached!**\n\n"
        f"🎬 **Show:** '{show_name}'\n"
        f"📅 **Scheduled Time:** {scheduled_time}\n"
        f"🕒 **Crossed At:** {current_time}\n\n"
        f"Seat tracking has been paused."
    )
    
    payload = {
        "chat_id": TG_GROUP_CHAT_ID, 
        "text": msg_text, 
        "parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "Close Topic", "callback_data": f"delshow_{uid}"}],
                [{"text": "Delete Topic", "callback_data": f"delshowperm_{uid}"}]
            ]
        }
    }
    
    if thread_id: 
        payload["message_thread_id"] = thread_id
        
    try: 
        requests.post(url, json=payload, timeout=10)
    except Exception as e: 
        print(f"⚠️ Telegram alert failed: {e}")

def send_ntfy_alert(movie_name, venue, show_time, status_msg):
    if not NTFY_SEAT_TOPIC:
        return
    
    url = f"{NTFY_URL}/{NTFY_SEAT_TOPIC}"
    message = f"🎬 {movie_name}\n🏟️ Venue: {venue}\n⏰ {show_time}\n{status_msg}"
    
    headers = {
        "Title": "Seat Alert",
        "Priority": "high",
        "Tags": "ticket,movie_camera"
    }
    
    try:
        requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
    except Exception as e:
        print(f"  ⚠️ Ntfy alert failed: {e}")

def send_ntfy_error(show_name):
    if not NTFY_ERROR_TOPIC:
        return
    
    url = f"{NTFY_URL}/{NTFY_ERROR_TOPIC}"
    headers = {
        "Title": "⚠️ Seat Scraper Fetch Failed",
        "Priority": "high",
        "Tags": "warning,rotating_light"
    }
    message = f"Failed to fetch seat layout for '{show_name}' after maximum retries. Cloudflare might be blocking the request."
    
    try:
        requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
    except Exception as e:
        print(f"  ⚠️ Ntfy error alert failed: {e}")

# --- CORE LOGIC ---
def fetch_seat_layout(session_id, venue_code, show_name, max_retries=3):
    url = f"https://services-in.bookmyshow.com/doTrans.aspx?_={int(time.time() * 1000)}"
    payload = f"strParam4=&strParam5=Y&strParam6=&strParam7=N&strParam1={session_id}&strParam2=WEB&strParam3=&strVenueCode={venue_code}&lngTransactionIdentifier=0&strAppCode=MOBAND2&strFormat=json&strCommand=GETSEATLAYOUT"
    
    time.sleep(random.uniform(1.0, 2.5))

    for attempt in range(1, max_retries + 1):
        try:
            resp = cffi_requests.post(url, headers=POST_HEADERS, data=payload, impersonate="chrome", timeout=15)
            
            if resp.status_code == 200:
                return resp.json().get("BookMyShow", {}).get("strData", "")
            
            print(f"   ⚠️ Fetch HTTP {resp.status_code} for session {session_id} (Attempt {attempt}/{max_retries})")
            
            if resp.status_code in [403, 429]:
                time.sleep(attempt * 3)
            else:
                time.sleep(2)

        except Exception as e:
            print(f"   ⚠️ Fetch error: {e} (Attempt {attempt}/{max_retries})")
            time.sleep(2)
            
    print(f"   ❌ Failed to fetch layout for {session_id} after {max_retries} attempts.")
    
    send_ntfy_error(show_name)
    
    return ""
    

def parse_layout(str_data):
    if not str_data:
        return {}

    parts = str_data.split("||")
    rows_data = parts[1] if len(parts) > 1 else parts[0]

    available_seats_by_row = {}

    for row_str in rows_data.split("|"):
        if not row_str or ":" not in row_str:
            continue

        elements = row_str.split(":")

        if len(elements) < 3:
            continue

        # Example:
        # 20:U:A121+16:A122+17:...
        #
        # 20 = BMS row index
        # U  = display row
        row_index = elements[0]
        row_letter = elements[1]
        seats = elements[2:]

        avail_seats = []

        for grid_idx, seat in enumerate(seats):

            if len(seat) >= 4 and seat[1] == "1":

                raw_seat_num = seat[2:]

                if "+" in raw_seat_num:
                    display_num = raw_seat_num.split("+")[1]
                else:
                    display_num = raw_seat_num

                try:
                    clean_num = str(int(display_num))
                except ValueError:
                    clean_num = display_num.lstrip("0") or display_num

                avail_seats.append({
                    "num": clean_num,
                    "idx": grid_idx,
                    "row_index": row_index,
                    "raw_token": seat,
                })

        if avail_seats:
            available_seats_by_row[row_letter] = {
                "width": len(seats),
                "row_index": row_index,
                "seats": avail_seats
            }

    return available_seats_by_row

def build_selected_seats(available_by_row, selected_seat_names):
    """
    Convert seats such as:
        ["U16", "U17"]

    into BMS selectedSeats format:

        |2|20|21|0000000001|2|20|22|0000000001

    based on the actual parsed row index and grid index.
    """

    selected_parts = []

    for seat_name in selected_seat_names:

        if len(seat_name) < 2:
            raise ValueError(f"Invalid seat name: {seat_name}")

        row = seat_name[0]
        seat_number = seat_name[1:]

        if row not in available_by_row:
            raise ValueError(
                f"Row {row} not found in current seat layout"
            )

        row_data = available_by_row[row]

        matching_seat = next(
            (
                seat
                for seat in row_data["seats"]
                if seat["num"] == seat_number
            ),
            None
        )

        if matching_seat is None:
            raise ValueError(
                f"Seat {seat_name} not found or unavailable"
            )

        row_index = matching_seat["row_index"]
        grid_idx = matching_seat["idx"]

        selected_parts.append(
            f"|2|{row_index}|{grid_idx + 1}|0000000001"
        )

    return "".join(selected_parts)

def build_selected_seats_from_objects(match_seats):
    """
    Build BMS selectedSeats directly from the actual parsed
    seat objects.

    This is important for couple rows where two physical seats
    can have the same display number.
    """

    selected_parts = []

    for seat in match_seats:

        row_index = seat["row_index"]
        grid_idx = seat["idx"]

        selected_parts.append(
            f"|2|{row_index}|{grid_idx + 1}|0000000001"
        )

    return "".join(selected_parts)

def find_matching_seats(available_by_row, show_reqs):
    seat_count = show_reqs.get("seat_count", 1)
    req_adj = show_reqs.get("require_adjacent", False)
    row_prefs = show_reqs.get("row_preferences", {})
    any_row = len(row_prefs) == 0

    valid_seats_pool = {}

    for row, row_data in available_by_row.items():
        if not any_row and row not in row_prefs:
            continue

        allowed_seats = row_prefs.get(row, [])
        seat_objs = row_data["seats"]

        valid = (
            [s for s in seat_objs if s["num"] in allowed_seats]
            if allowed_seats
            else seat_objs
        )

        if valid:
            valid_seats_pool[row] = {
                "width": row_data["width"],
                "seats": valid
            }

    if not valid_seats_pool:
        return False, [], [], []

    # ==========================================================
    # ADJACENT SEATS
    # ==========================================================
    if req_adj:

        ranked_matches = []

        for row, row_data in valid_seats_pool.items():

            seat_objs = row_data["seats"]
            row_center = row_data["width"] / 2.0

            for i in range(len(seat_objs) - seat_count + 1):

                window = seat_objs[i:i + seat_count]

                # Must be physically adjacent in the raw grid
                if all(
                    window[j]["idx"] - window[j - 1]["idx"] == 1
                    for j in range(1, seat_count)
                ):

                    block_center = sum(
                        s["idx"] for s in window
                    ) / seat_count

                    ranked_matches.append({
                        "score": abs(row_center - block_center),
                        "row": row,
                        "seats": window,
                        "text": (
                            f"Row {row}: "
                            f"{', '.join(s['num'] for s in window)}"
                        )
                    })

        if not ranked_matches:
            return False, [], [], []

        ranked_matches.sort(key=lambda x: x["score"])

        top_matches = ranked_matches[:5]

        top_5_matches = [
            m["text"]
            for m in top_matches
        ]

        all_matches_text = [
            m["text"]
            for m in ranked_matches
        ]

        return (
            True,
            top_5_matches,
            all_matches_text,
            top_matches
        )

    # ==========================================================
    # NON-ADJACENT
    # ==========================================================
    else:

        all_valid_seats = []

        for row, row_data in valid_seats_pool.items():

            row_center = row_data["width"] / 2.0

            for seat in row_data["seats"]:

                all_valid_seats.append({
                    "score": abs(
                        row_center - seat["idx"]
                    ),
                    "row": row,
                    "seats": [seat],
                    "text": f"Row {row}: {seat['num']}"
                })

        if not all_valid_seats:
            return False, [], [], []

        all_valid_seats.sort(
            key=lambda x: x["score"]
        )

        top_matches = all_valid_seats[:5]

        top_5_matches = [
            m["text"]
            for m in top_matches
        ]

        all_matches_text = [
            m["text"]
            for m in all_valid_seats
        ]

        return (
            True,
            top_5_matches,
            all_matches_text,
            top_matches
        )

def build_booking_payload(show, match_seats,booking_token):
    """
    Build the current BMS WEB booking payload.

    Uses the actual physical seat objects so couple seats
    with duplicate display numbers are handled correctly.
    """

    selected_seats = build_selected_seats_from_objects(match_seats)

    payload = {
        "appCode": "WEB",
        "bmsId": BMS_ID,
        "companyCode": show.get("company_code", "AGS"),
        "eventCode": show.get("event_code", ""),
        "numberOfTickets": len(match_seats),
        "offerData": {
            "offerSelected": False
        },
        "seatLayoutType": "Y",
        "selectedSeats": selected_seats,
        "sessionId": str(show.get("session_id", "")),
        "ticketCategory": "0001",
        "token": booking_token,
        "venueCode": show.get("venue_code", "")
    }

    return payload
def build_booking_request(booking_payload):
    """
    Build the current BMS web booking request.

    This only prepares the request.
    It does NOT send anything.
    """

    url = "https://in.bookmyshow.com/api/v2/mobile/booking/movies"

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://in.bookmyshow.com",
        "Referer": "https://in.bookmyshow.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/154.0.0.0 Safari/537.36"
        ),
        "X-App-Code": "WEB",
        "X-Platform": "WEB",
        "X-Platform-Code": "WEB",
        "X-Region-Code": "CHEN",
        "X-Region-Slug": "chennai",
    }

    return url, headers, booking_payload

def parse_booking_response(response_data):
    """
    Parse the successful BMS booking response.

    No payment or authorization is performed here.
    """

    if not isinstance(response_data, dict):
        raise ValueError("Invalid booking response")

    transaction_id = response_data.get("transactionId")
    transaction_uid = response_data.get("transactionUID")
    booking_id = response_data.get("bookingId")
    numeric_booking_id = response_data.get("numericBookingId")

    if not transaction_id or not transaction_uid or not booking_id:
        raise ValueError(
            f"Incomplete booking response: {response_data}"
        )

    return {
        "transactionId": transaction_id,
        "transactionUID": transaction_uid,
        "bookingId": booking_id,
        "numericBookingId": numeric_booking_id,
    }

def process_booking_response(response):
    """
    Process the HTTP response returned by BMS.
    """

    try:
        response_data = response.json()

        print("\n=== BOOKING RESPONSE ===")
        print(response_data)

        booking_result = parse_booking_response(response_data)

        print("\n=== BOOKING RESPONSE PARSED ===")
        print(
            f"transactionId    = "
            f"{booking_result['transactionId']}"
        )
        print(
            f"transactionUID   = "
            f"{booking_result['transactionUID']}"
        )
        print(
            f"bookingId        = "
            f"{booking_result['bookingId']}"
        )
        print(
            f"numericBookingId = "
            f"{booking_result['numericBookingId']}"
        )
        print("========================\n")

        return booking_result

    except Exception as e:
        print(f"❌ Failed to process booking response: {e}")
        return None

# --- MAIN EXECUTION ---
def main():
    print("🚀 CRON JOB STARTED: Checking Seat Availability")
    state = load_json(STATE_FILE, {})
    shows = load_json(SHOWS_FILE, [])
    
    if not shows:
        print("No shows in shows.json. Exiting...")
        return
        
    current_time = time.time()
    FOURTEEN_DAYS_SECONDS = 14 * 86400
    ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    
    surviving_shows = []
    shows_updated = False
    state_updated = False

    for idx, show in enumerate(shows):
        status = show.get("status")
        closed_at = show.get("closed_at", 0)
        s_name = show.get("name", "Unknown")
        thread_id = show.get("message_thread_id")
        s_id = show.get("session_id")
        v_code = show.get("venue_code")
        event_code = show.get("event_code", "")
        theatre = show.get("theatre")

        # --- 1. 14-DAY PASSIVE CLEANUP FOR CLOSED SHOWS ---
        if status == "closed":
            if closed_at and (current_time - closed_at) > FOURTEEN_DAYS_SECONDS:
                if thread_id and TG_GROUP_CHAT_ID and TG_BOT_TOKEN:
                    try:
                        del_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/deleteForumTopic"
                        payload = {
                            "chat_id": TG_GROUP_CHAT_ID,
                            "message_thread_id": thread_id
                        }
                        res = requests.post(del_url, json=payload, timeout=10)
                        if res.status_code == 200:
                            print(f"🗑️ 14-day retention expired. Purged topic ID {thread_id} for '{s_name}'")
                        else:
                            print(f"⚠️ Failed to purge topic for '{s_name}': {res.text}")
                    except Exception as e:
                        print(f"⚠️ Error deleting forum topic: {e}")
                
                try:
                    raw_thread = show.get("message_thread_id", "")
                    thread_id_str = str(raw_thread).strip() if raw_thread is not None else ""
                    exact_state_key = f"{v_code}_{s_id}_{thread_id_str}"
                    
                    if exact_state_key in state:
                        del state[exact_state_key]
                        state_updated = True
                        print(f"🧹 Cleaned up exact state record '{exact_state_key}' for '{s_name}'")
                    else:
                        print(f"⚠️ Exact state key '{exact_state_key}' not found in state.json.")
                except Exception as e:
                    print(f"⚠️ Error cleaning residual state for '{s_name}': {e}")

                print(f"🧹 Removing closed show '{s_name}' permanently from shows.json.")
                shows_updated = True
                continue
            else:
                surviving_shows.append(show)
                continue

        # --- 2. ACTIVE SHOW PROCESSING ---
        surviving_shows.append(show)
        state_key = f"{v_code}_{s_id}_{thread_id or ''}"
        
        print(f"Checking '{s_name}' (Session: {s_id})...")

        # --- EXPIRATION CHECK ---
        date_str = show.get("date")      
        time_str = show.get("show_time")  
        
        if date_str and time_str:
            try:
                show_datetime_str = f"{date_str} {time_str.upper()}"
                show_dt = datetime.strptime(show_datetime_str, "%Y%m%d %I:%M %p").replace(tzinfo=ZoneInfo("Asia/Kolkata"))
                
                if ist_now >= show_dt:
                    if not state.get(state_key, {}).get("expired_notified"):
                        print(f"   -> ⏰ Showtime crossed! Sending clear button to Telegram.")
                        formatted_scheduled = show_dt.strftime("%d/%m/%Y %I:%M %p")
                        formatted_current = ist_now.strftime("%d/%m/%Y %I:%M %p")
                        uid = f"{str(v_code).strip().upper()}-{str(s_id).strip()}"
                        
                        send_expired_alert_with_button(
                            s_name, 
                            thread_id, 
                            uid, 
                            formatted_scheduled,
                            formatted_current
                        )
                        
                        if state_key not in state:
                            state[state_key] = {}
                        state[state_key]["expired_notified"] = True
                        save_json(STATE_FILE, state)
                        
                    print(f"   -> ⏰ Movie started. Paused tracking pending manual clear.")
                    continue  
            except Exception as e:
                print(f"   -> ⚠️ Could not parse date/time: {e}. Checking anyway...")

        str_data = fetch_seat_layout(s_id, v_code, s_name)
        # print("RAW SEAT DATA:")
        # print(str_data)
        current_avail = parse_layout(str_data)

        # print("\n=== BOOKING MAPPING DEBUG ===")

        # for row, row_data in current_avail.items():
        #   for seat in row_data["seats"]:
        #      print(
        #     f"{row}{seat['num']} -> "
        #     f"row_index={seat['row_index']} "
        #     f"grid_idx={seat['idx']} "
        #     f"booking_position={seat['idx'] + 1}"
        # )

        # print("=============================\n")
        
        current_valid_seats = set()
        row_prefs = show.get("row_preferences", {})
        any_row = len(row_prefs) == 0
        
        for row, row_data in current_avail.items():
            if not any_row and row not in row_prefs: continue
            allowed_seats = row_prefs.get(row, [])
            for s in row_data["seats"]:
                if not allowed_seats or s["num"] in allowed_seats:
                    current_valid_seats.add(f"{row}-{s['num']}")

        is_match, top_5_matches, current_all_matches, ranked_match_objects = find_matching_seats(
    current_avail, show
)

        # --- DEBUG TOP MATCHING SEATS + BMS MAPPING ---
        # --- DRY-RUN BOOKING PAYLOAD ---
        if is_match and ranked_match_objects:
         print("\n" + "=" * 60)
         print("🎟️ DRY-RUN BOOKING PAYLOAD")
         print("=" * 60)

    # Best-ranked seat combination
         best_match = ranked_match_objects[0]

         print(f"🎯 Selected match: {best_match['text']}")

         print("\nPhysical seats:")

         for seat in best_match["seats"]:
           print(
            f"   Row {best_match['row']} "
            f"Seat {seat['num']} "
            f"(row_index={seat['row_index']}, "
            f"grid_idx={seat['idx']}, "
            f"booking_position={seat['idx'] + 1})"
        )
         booking_token = None

         if ENABLE_BOOKING:
             booking_token = get_booking_token(
        show["url"],
        cffi_requests
    )

         booking_payload = build_booking_payload(
    show,
    best_match["seats"],
    booking_token or ""
)

         booking_url, booking_headers, booking_data = build_booking_request(
        booking_payload
    )

         print("\nBooking request:")
         print(f"URL = {booking_url}")
         print("Method = POST")
         print("Content-Type = application/x-www-form-urlencoded")

         print("\nBooking data:")
         for key, value in booking_data.items():
           if key == "sessionId":
            value = "[REDACTED]"
           print(f"{key} = {value}")

         if ENABLE_BOOKING:
           print("\n⚠️ ENABLE_BOOKING=True")
           print("Live booking request is enabled.")
           try:

              response = cffi_requests.post(
                booking_url,
                headers=booking_headers,
                data=booking_data,
                impersonate="chrome",
                timeout=15,
            )

              print(
                f"Booking HTTP status: "
                f"{response.status_code}"
            )

              if response.status_code == 200:

                booking_result = process_booking_response(
                    response
                )

                if booking_result:
                    print(
                        "✅ Booking transaction created."
                    )

              else:

                print(
                    "❌ Booking request failed:"
                )
                print(response.text[:2000])

           except Exception as e:

            print(
                f"❌ Booking request error: {e}"
            )

        # DO NOT add the live request yet.
         else:
          print("\n🚫 ENABLE_BOOKING=False")
          print("No booking request sent.")

         print("\nselectedSeats:")
         print(booking_payload["selectedSeats"])

         print("\nPayload:")

         for key, value in booking_payload.items():

        # Never print session credentials
            if key in {"sessionId", "token", "bmsId"}:
             value = "[REDACTED]"

            print(f"{key} = {value}")

         print("\n🚫 DRY RUN ONLY — NO BOOKING REQUEST SENT")
         print("=" * 60 + "\n")
        
        # --- NON-SILENT INITIALIZATION FOR FIRST RUN ---
        if state_key not in state: 
            print(f"   -> ⚡ First run detected for {s_name}. Checking for immediate availability...")
            previous_seats = set()
            previous_all_matches = set()
        else:
            previous_seats = set(state[state_key].get("known_seats", []))
            previous_all_matches = set(state[state_key].get("all_matches", []))
            
        newly_unblocked_raw = current_valid_seats - previous_seats
        new_combinations = set(current_all_matches) - previous_all_matches
        lost_combinations = previous_all_matches - set(current_all_matches)
        
        state_changed = False
        
        # --- BUILD CLEAN LIST (TOP 5 ONLY) ---
        still_avail_text = ""
        if current_all_matches:
            top_5_still = "\n".join([f"• ✅ {m}" for m in current_all_matches[:5]])
            still_avail_text = f"🎯 **Top 5 Matching Options:**\n{top_5_still}"
            
            if len(current_all_matches) > 5:
                hidden_count = len(current_all_matches) - 5
                still_avail_text += f"\n*...and {hidden_count} more options available.*"
        else:
            still_avail_text = "🚫 **No matching seats left.**"

        # Build the links
        bms_link = f"[BMS App](https://in.bookmyshow.com/booktickets/{v_code}/{s_id})"
        chain_url = CINEMA_CHAIN_URLS.get(v_code)
        
        if chain_url:
            if "pvr" in chain_url.lower():
                chain_name="PVR LINK"
            elif "inox" in chain_url.lower():
                chain_name="INOX LINK"
            else:
                chain_name="CINEPOLIS LINK"
            action_links = f"🔗 {bms_link}  |  [{chain_name}]({chain_url})"
        else:
            s_name_upper = s_name.upper()
            if "PVR" in s_name_upper:
                action_links = f"🔗 {bms_link}  |  [PVR App](https://www.pvrcinemas.com/)"
            elif "INOX" in s_name_upper:
                action_links = f"🔗 {bms_link}  |  [INOX App](https://www.inoxmovies.com/)"
            else:
                action_links = f"🔗 {bms_link}"

        show_time_display = show.get("show_time", "Unknown Time")

        # --- SEND ALERTS BASED ON SCENARIO ---
        
        # SCENARIO 1: BOTH lost and new seats at the exact same time (Short & Neat)
        if lost_combinations and new_combinations:
            print(f"   -> 🔄 SIMULTANEOUS SEAT UPDATE for {s_name}!")
            
            msg = (
                f"🔄 **SEATS UPDATED!**\n\n"
                f"🎬 **{s_name}**\n\n"
                f"❌ **Lost:** {len(lost_combinations)} seat combo(s)\n"
                f"🆕 **New:** {len(new_combinations)} seat combo(s)\n\n"
                f"➖➖➖➖➖➖➖➖➖➖\n\n"
                f"{still_avail_text}\n\n"
                f"{action_links}"
            )
            send_telegram_alert(msg, thread_id)
            send_ntfy_alert(s_name, theatre, show_time_display, "🔄 STATUS: Seats Lost & Unlocked simultaneously!")
            state_changed = True

        # SCENARIO 2: ONLY seats were lost
        elif lost_combinations:
            print(f"   -> 🔴 SEATS BOOKED/LOST for {s_name}!")
            lost_list = list(lost_combinations)
            lost_to_show = lost_list[:5] # Capped at Top 5
            lost_text = "\n".join([f"• ❌ {m}" for m in lost_to_show])
            
            if len(lost_list) > 5:
                lost_hidden = len(lost_list) - 5
                lost_text += f"\n*...and {lost_hidden} more booked.*"
            
            msg = (
                f"💔 **SEATS BOOKED!**\n\n"
                f"🎬 **Show:** {s_name}\n\n"
                f"Just Taken:\n{lost_text}\n\n"
                f"➖➖➖➖➖➖➖➖➖➖\n\n"
                f"{still_avail_text}"
            )
            # Only show action links if there are actually seats left to book
            if current_all_matches:
                msg += f"\n\n{action_links}"
                
            send_telegram_alert(msg, thread_id)
            send_ntfy_alert(s_name, theatre, show_time_display, "💔 STATUS: Seats Booked/Lost!")
            state_changed = True

        # SCENARIO 3: ONLY new seats became available
        elif is_match and new_combinations:
            print(f"   -> 🟢 UNBLOCK/INITIAL AVAILABILITY DETECTED for {s_name}!")
            
            msg = (
                f"🚨 **SEATS AVAILABLE NOW!**\n\n"
                f"🎬 **Show:** {s_name}\n"
                f"🆕 **Available:** {len(newly_unblocked_raw)} seat(s) unblocked\n\n"
                f"{still_avail_text}\n\n"
                f"{action_links}"
            )
            send_telegram_alert(msg, thread_id)
            send_ntfy_alert(s_name, theatre, show_time_display, "🚨 STATUS: New Seats Unlocked!")
            state_changed = True
            
        if not state_changed and current_valid_seats != previous_seats:
            print(f"   -> ⚪ Seats changed in background. Updating state quietly.")
            state_changed = True
        elif not state_changed:
            print(f"   -> ⚪ No actionable changes.")

        if state_changed or state_key not in state:
            if state_key not in state:
                state[state_key] = {}
            state[state_key]["known_seats"] = list(current_valid_seats)
            state[state_key]["all_matches"] = current_all_matches
            state_updated = True

    # Save state and updated shows list if any items were purged or modified
    if state_updated:
        save_json(STATE_FILE, state)
        
    if shows_updated:
        save_json(SHOWS_FILE, surviving_shows)

    print("✅ CRON JOB FINISHED. Exiting.\n")

if __name__ == "__main__":
    main()
