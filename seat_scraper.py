import os
import time
import json
from zoneinfo import ZoneInfo
import requests
from datetime import datetime
from curl_cffi import requests as cffi_requests

# --- CONFIGURATION ---
SHOWS_FILE = "shows.json"
STATE_FILE = "state.json"

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TG_GROUP_CHAT_ID = os.environ.get("TG_GROUP_CHAT_ID", "YOUR_CHAT_ID_HERE") 

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

# --- CORE LOGIC ---

def fetch_seat_layout(session_id, venue_code):
    url = f"https://services-in.bookmyshow.com/doTrans.aspx?_={int(time.time() * 1000)}"
    payload = f"strParam4=&strParam5=Y&strParam6=&strParam7=N&strParam1={session_id}&strParam2=WEB&strParam3=&strVenueCode={venue_code}&lngTransactionIdentifier=0&strAppCode=MOBAND2&strFormat=json&strCommand=GETSEATLAYOUT"
    
    try:
        resp = cffi_requests.post(url, headers=POST_HEADERS, data=payload, impersonate="chrome", timeout=15)
        if resp.status_code == 200:
            return resp.json().get("BookMyShow", {}).get("strData", "")
    except Exception as e:
        print(f"Fetch error: {e}")
    return ""
def parse_layout(str_data):
    if not str_data: return {}
    parts = str_data.split("||")
    rows_data = parts[1] if len(parts) > 1 else parts[0]
    available_seats_by_row = {}
    
    for row_str in rows_data.split("|"):
        if not row_str or ":" not in row_str: continue
        elements = row_str.split(":")
        row_letter, seats = elements[1], elements[2:]
        
        avail_seats = []
        for grid_idx, seat in enumerate(seats):
            if seat.endswith("000") or seat == "0000": continue
            if len(seat) >= 4 and seat[1] == '2': # '2' means available
                
                # --- NEW SAFE PARSING LOGIC ---
                raw_seat_num = seat[2:]
                try:
                    # Try normal integer conversion (turns "04" into "4")
                    clean_num = str(int(raw_seat_num))
                except ValueError:
                    # If it has weird characters like "01+01", just strip the leading zero safely
                    clean_num = raw_seat_num.lstrip("0") or raw_seat_num
                
                avail_seats.append({"num": clean_num, "idx": grid_idx})
                # ------------------------------
                
        if avail_seats:
            available_seats_by_row[row_letter] = {"width": len(seats), "seats": avail_seats}
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
        seat_objs = row_data["seats"]
        valid = [s for s in seat_objs if s["num"] in allowed_seats] if allowed_seats else seat_objs
        if valid: valid_seats_pool[row] = {"width": row_data["width"], "seats": valid}

    if not valid_seats_pool: return False, []

    if req_adj:
        ranked_matches = []
        for row, row_data in valid_seats_pool.items():
            seat_objs, row_center = row_data["seats"], row_data["width"] / 2.0
            for i in range(len(seat_objs) - seat_count + 1):
                window = seat_objs[i : i + seat_count]
                # Check if seats are physically next to each other
                if all(window[j]["idx"] - window[j-1]["idx"] == 1 for j in range(1, seat_count)):
                    block_center = sum(s["idx"] for s in window) / seat_count
                    ranked_matches.append({
                        "score": abs(row_center - block_center),
                        "text": f"Row {row}: {', '.join([s['num'] for s in window])}"
                    })
        
        if not ranked_matches: return False, []
        ranked_matches.sort(key=lambda x: x["score"])
        return True, [m["text"] for m in ranked_matches[:3]] # Return top 3 matches
    
    return True, ["Seats available (Adjacency not required)"]

# --- MAIN EXECUTION ---

def main():
    print("🚀 CRON JOB STARTED: Checking Seat Availability")
    state = load_json(STATE_FILE, {})
    shows = load_json(SHOWS_FILE, [])
    
    if not shows:
        print("No shows in shows.json. Exiting...")
        return
        
    for show in shows:
        s_id, v_code, s_name = show.get("session_id"), show.get("venue_code"), show.get("name")
        state_key = f"{v_code}_{s_id}_{show.get('message_thread_id', '')}"
        
        print(f"Checking '{s_name}' (Session: {s_id})...")

        # --- NEW EXPIRATION CHECK LOGIC ---
        # --- NEW EXPIRATION CHECK LOGIC (IST SECURE) ---
        date_str = show.get("date")      # e.g., "20260912"
        time_str = show.get("show_time") # e.g., "4:20 pm"
        
        if date_str and time_str:
            try:
                # Get the current time in IST
                ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
                
                # Combine them and force uppercase for AM/PM consistency
                show_datetime_str = f"{date_str} {time_str.upper()}"
                
                # Parse the time AND tell Python this time is in IST
                show_dt = datetime.strptime(show_datetime_str, "%Y%m%d %I:%M %p").replace(tzinfo=ZoneInfo("Asia/Kolkata"))
                
                # Now it accurately compares IST to IST
                if ist_now >= show_dt:
                    print(f"   -> ⏰ Movie has already started! Skipping check.")
                    continue
            except Exception as e:
                print(f"   -> ⚠️ Could not parse date/time: {e}. Checking anyway...")
        # ----------------------------------
        # ----------------------------------
        
        str_data = fetch_seat_layout(s_id, v_code)
        current_avail = parse_layout(str_data)
        
        # Get flat list of all currently valid available seats in preferred rows
        current_valid_seats = set()
        row_prefs = show.get("row_preferences", {})
        any_row = len(row_prefs) == 0
        
        for row, row_data in current_avail.items():
            if not any_row and row not in row_prefs: continue
            allowed_seats = row_prefs.get(row, [])
            for s in row_data["seats"]:
                if not allowed_seats or s["num"] in allowed_seats:
                    current_valid_seats.add(f"{row}-{s['num']}")

        # First time tracking - establish baseline silently
        if state_key not in state: 
            print(f"   -> 🤫 Establishing silent baseline for {s_name}...")
            state[state_key] = {"known_seats": list(current_valid_seats)}
            save_json(STATE_FILE, state)
            continue 
            
        previous_seats = set(state[state_key].get("known_seats", []))
        newly_unblocked = current_valid_seats - previous_seats
        
        # Check if current available seats match user preferences
        is_match, match_details = find_matching_seats(current_avail, show)
        
        if is_match and newly_unblocked:
            print(f"   -> 🟢 UNBLOCK DETECTED for {s_name}!")
            seats_text = "\n".join([f"• {m}" for m in match_details])
            
            msg = (
                f"🚨 **NEW SEATS UNBLOCKED!** 🚨\n\n"
                f"🎬 **Show:** {s_name}\n"
                f"✅ **Best Matches:**\n{seats_text}\n\n"
                f"[Book Now!](https://in.bookmyshow.com/booktickets/{v_code}/{s_id})"
            )
            
            send_telegram_alert(msg, show.get("message_thread_id"))
            
            # Update state so we don't spam the same notification
            state[state_key]["known_seats"] = list(current_valid_seats)
            save_json(STATE_FILE, state)
            
        elif current_valid_seats != previous_seats:
            print(f"   -> ⚪ Seats changed (booked or partial open). Updating state quietly.")
            state[state_key]["known_seats"] = list(current_valid_seats)
            save_json(STATE_FILE, state)
        else:
             print(f"   -> ⚪ No new unblocks.")

    print("✅ CRON JOB FINISHED. Exiting.\n")

if __name__ == "__main__":
    main()
