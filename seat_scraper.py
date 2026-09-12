import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import re
import subprocess

# --- CONFIGURATION ---
SHOWS_FILE = "shows.json"
STATE_FILE = "state.json"
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60) # 5 hours 55 mins

# Telegram Configuration
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_GROUP_CHAT_ID = os.environ.get("TG_GROUP_CHAT_ID") 

USE_WARP = False
PROXIES = {
    "http": "socks5://127.0.0.1:40000",
    "https": "socks5://127.0.0.1:40000"
}

POST_HEADERS = {
    "Host": "services-in.bookmyshow.com",
    "X-Timeout": "10",
    "X-App-Code": "MOBAND2",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10; Android SDK built for x86_64 Build/QSR1.211112.011)",
    "X-App-Version": "18.2.3",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept-Encoding": "gzip, deflate"
}

# --- GIT SYNC ENGINE ---

def pull_latest_changes():
    subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, check=False)
    subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, check=False)

def push_state_to_github():
    subprocess.run(["git", "add", STATE_FILE], capture_output=True)
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    
    if STATE_FILE in status.stdout:
        print("    -> 💾 Pushing updated state.json to GitHub...")
        subprocess.run(["git", "commit", "-m", "Auto-update state.json"], capture_output=True)
        subprocess.run(["git", "push", "origin", "main"], capture_output=True)

# --- NOTIFICATION ENGINE ---

def send_telegram_alert(message, thread_id=None):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_GROUP_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    if thread_id: payload["message_thread_id"] = thread_id
    try: requests.post(url, json=payload, timeout=10)
    except Exception as e: print(f"    -> ⚠️ Telegram alert exception: {e}")

# --- NETWORK ENGINE ---

def toggle_warp():
    global USE_WARP
    if USE_WARP:
        subprocess.run(["warp-cli", "--accept-tos", "disconnect"], capture_output=True, check=False)
        USE_WARP = False
    else:
        subprocess.run(["warp-cli", "--accept-tos", "connect"], capture_output=True, check=False)
        time.sleep(5)
        USE_WARP = True

def make_bms_request(method, url, max_retries=3, **kwargs):
    for attempt in range(1, max_retries + 1):
        current_proxies = PROXIES if USE_WARP else None
        try:
            if method.upper() == 'GET':
                resp = cffi_requests.get(url, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
            else:
                resp = cffi_requests.post(url, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
            
            if resp.status_code == 429:
                if attempt < max_retries:
                    toggle_warp()
                    continue
                return None
            return resp
        except Exception:
            if attempt < max_retries: time.sleep(3)
    return None

# --- PARSING & SCORING LOGIC ---

def load_json(filepath, default_val):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f: return json.load(f)
        except Exception: pass
    return default_val

def save_json(filepath, data):
    with open(filepath, "w") as f: json.dump(data, f, indent=2)

def fetch_seat_layout(session_id, venue_code):
    url = "https://services-in.bookmyshow.com/doTrans.aspx"
    
    # 💥 THE FIX: Add a live Unix Timestamp to force BMS to bypass its cache and give us REAL-TIME data!
    cache_buster = int(time.time() * 1000)
    payload = f"strParam4=&strParam5=Y&strParam6=&strParam7=N&strParam1={session_id}&strParam2=WEB&strParam3=&strVenueCode={venue_code}&lngTransactionIdentifier={cache_buster}&strAppCode=MOBAND2&strFormat=json&strCommand=GETSEATLAYOUT"
    
    resp = make_bms_request('POST', url, headers=POST_HEADERS, data=payload)
    if not resp or resp.status_code != 200: return ""
    try: return resp.json().get("BookMyShow", {}).get("strData", "")
    except Exception: return ""

def parse_layout(str_data):
    if not str_data: return {}
    parts = str_data.split("||")
    rows_data = parts[1] if len(parts) > 1 else parts[0]
    available_seats_by_row = {}
    
    for row_str in rows_data.split("|"):
        if not row_str or ":" not in row_str: continue
        elements = row_str.split(":")
        row_letter, seats = elements[1], elements[2:]
        row_width = len(seats)
        
        avail_seats = []
        for grid_idx, seat in enumerate(seats):
            if seat.endswith("000") or seat == "0000": continue
            if len(seat) >= 4 and seat[1] == '2':
                avail_seats.append({"num": str(int(seat[2:])), "idx": grid_idx})
                
        if avail_seats:
            available_seats_by_row[row_letter] = {"width": row_width, "seats": avail_seats}
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
                is_strictly_adjacent = all(window[j]["idx"] - window[j-1]["idx"] == 1 for j in range(1, seat_count))
                
                if is_strictly_adjacent:
                    block_center = sum(s["idx"] for s in window) / seat_count
                    ranked_matches.append({
                        "row": row, "score": abs(row_center - block_center),
                        "text": f"Row {row}: {', '.join([s['num'] for s in window])}"
                    })
        if not ranked_matches: return False, []
        ranked_matches.sort(key=lambda x: x["score"])
        
        best_matches = []
        for idx, match in enumerate(ranked_matches[:3]):
            best_matches.append(match["text"] + (" ⭐ (Best Center Seats)" if idx == 0 else ""))
        return True, best_matches
    else:
        all_valid_seats = []
        for row, row_data in valid_seats_pool.items():
            row_center = row_data["width"] / 2.0
            for s in row_data["seats"]:
                all_valid_seats.append({"row": row, "num": s["num"], "score": abs(row_center - s["idx"])})
                
        if len(all_valid_seats) >= seat_count:
            all_valid_seats.sort(key=lambda x: x["score"])
            grouped_by_row = {}
            for s in all_valid_seats[:seat_count]: grouped_by_row.setdefault(s["row"], []).append(s["num"])
            
            match_text = [f"Row {r}: {', '.join(sorted(nums, key=lambda x: int(x) if x.isdigit() else x))}" for r, nums in grouped_by_row.items()]
            match_text.append("⭐ (Algorithmically selected best centered seats)")
            return True, match_text
        return False, []

# --- MAIN LOOP (6 HOURS) ---

def main():
    start_time = time.time()
    print("🚀 STARTING 6-HOUR CONTINUOUS SEAT SCRAPER")
    
    cycle = 1
    state = load_json(STATE_FILE, {})
    
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        print(f"\n🔄 CYCLE {cycle}")
        
        pull_latest_changes()
        shows = load_json(SHOWS_FILE, [])
        state_changed = False
        
        if not shows:
            print("    -> No shows found in shows.json. Waiting 60s...")
            time.sleep(60)
            continue
            
        for index, show in enumerate(shows, 1):
            s_id, v_code, s_name = show.get("session_id"), show.get("venue_code"), show.get("name")
            
            # 💥 THE FIX: Create a 100% Unique ID for memory tracking based on the Telegram Thread
            # This prevents multiple searches for the same session from overwriting each other!
            thread_id = show.get("message_thread_id", str(index))
            state_key = f"{v_code}_{s_id}_{thread_id}"
            
            print(f"\n[{index}/{len(shows)}] Checking '{s_name}' (Session: {s_id})")
            time.sleep(15) 
            
            str_data = fetch_seat_layout(s_id, v_code)
            if not str_data:
                print("    -> ⚠️ Failed to fetch layout.")
                continue
                
            current_avail = parse_layout(str_data)
            
            current_valid_seats = set()
            row_prefs = show.get("row_preferences", {})
            any_row = len(row_prefs) == 0
            
            for row, row_data in current_avail.items():
                if not any_row and row not in row_prefs: continue
                allowed = row_prefs.get(row, [])
                for s in row_data["seats"]:
                    if not allowed or s["num"] in allowed:
                        current_valid_seats.add(f"{row}-{s['num']}")

            if state_key not in state: 
                state[state_key] = {"known_seats": []}
                
            previous_seats = set(state[state_key].get("known_seats", []))
            newly_unblocked = current_valid_seats - previous_seats
            
            is_match, match_details = find_matching_seats(current_avail, show)
            
            if is_match:
                if not previous_seats or newly_unblocked:
                    if newly_unblocked and previous_seats:
                        print(f"    -> 🟢 UNBLOCK DETECTED! {len(newly_unblocked)} new seats opened up.")
                    else:
                        print("    -> 🟢 INITIAL MATCH FOUND!")
                        
                    req_type = "Strictly Adjacent" if show.get('require_adjacent') else "Distributed OK"
                    seats_text = "\n".join([f"• {m}" for m in match_details])
                    
                    msg = (
                        f"🚨 **SEATS FOUND!** 🚨\n\n"
                        f"🎬 **Show:** {s_name}\n"
                        f"📅 **Date/Time:** {show.get('date')} | {show.get('show_time')}\n"
                        f"💺 **Requirement:** {show.get('seat_count')} seats ({req_type})\n\n"
                        f"✅ **Best Available Matches:**\n{seats_text}\n\n"
                        f"[Book Now!](https://in.bookmyshow.com/booktickets/{v_code}/{s_id})"
                    )
                    
                    send_telegram_alert(msg, show.get("message_thread_id"))
                    
                    state[state_key]["known_seats"] = list(current_valid_seats)
                    state_changed = True
                else:
                    print("    -> ⚪ Matches exist, but no NEW seats unblocked. Staying quiet.")
                    
                    if current_valid_seats != previous_seats:
                        state[state_key]["known_seats"] = list(current_valid_seats)
                        state_changed = True
            else:
                if previous_seats:
                    print("    -> 🔴 Match lost (seats booked). Clearing memory.")
                    state[state_key]["known_seats"] = []
                    state_changed = True
                else:
                    print("    -> ⚪ Requirements not met yet.")

        if state_changed:
            save_json(STATE_FILE, state)
            push_state_to_github()

        cycle += 1
        
    print("\n🏁 Time limit reached (5h 55m). Shutting down gracefully.")

if __name__ == "__main__":
    main()
