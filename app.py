import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Optional

import gspread
import requests
from dotenv import load_dotenv
from flask import Flask, request
from google.oauth2.service_account import Credentials
from openai import OpenAI

load_dotenv()

app = Flask(__name__)

# =========================
# ENV
# =========================
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "")
APP_SECRET = os.getenv("APP_SECRET", "")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")

SETTINGS_SHEET_NAME = "settings"
USERS_SHEET_NAME = "users"

client_ai = OpenAI(api_key=OPENAI_API_KEY)


# =========================
# GOOGLE SHEETS
# =========================
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    if GOOGLE_CREDENTIALS_JSON.strip():
        creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_FILE,
            scopes=scopes,
        )

    return gspread.authorize(creds)


def open_spreadsheet():
    client = get_gspread_client()
    return client.open(SPREADSHEET_NAME)


def get_settings_sheet():
    spreadsheet = open_spreadsheet()
    return spreadsheet.worksheet(SETTINGS_SHEET_NAME)


def get_or_create_users_sheet():
    spreadsheet = open_spreadsheet()

    try:
        ws = spreadsheet.worksheet(USERS_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=USERS_SHEET_NAME, rows=2000, cols=20)
        ws.append_row(
            [
                "psid",
                "first_seen_at",
                "last_seen_at",
                "signup_name",
                "signup_phone",
                "signup_bank_type",
                "signup_bank_account",
                "signup_completed",
                "last_user_messages",
                "last_intent",
            ],
            value_input_option="USER_ENTERED",
        )
    return ws


def get_setting_value(setting_key: str) -> str:
    ws = get_settings_sheet()
    rows = ws.get_all_values()

    for row in rows:
        if len(row) >= 2 and row[0].strip() == setting_key:
            return row[1].strip()

    return ""


# =========================
# USER STORAGE
# =========================
def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_user_row(psid: str):
    ws = get_or_create_users_sheet()
    rows = ws.get_all_values()

    if not rows:
        return ws, None, None

    headers = rows[0]
    for idx, row in enumerate(rows[1:], start=2):
        if row and len(row) > 0 and row[0].strip() == psid:
            data = {}
            for i, h in enumerate(headers):
                data[h] = row[i] if i < len(row) else ""
            return ws, idx, data

    return ws, None, None


def ensure_user_exists(psid: str):
    ws, row_index, data = get_user_row(psid)

    if row_index:
        return ws, row_index, data

    now_value = now_str()
    ws.append_row(
        [psid, now_value, now_value, "", "", "", "", "FALSE", "", ""],
        value_input_option="USER_ENTERED",
    )
    return get_user_row(psid)


def update_user_fields(psid: str, updates: dict):
    ws, row_index, _ = ensure_user_exists(psid)
    headers = ws.row_values(1)
    header_map = {name: idx + 1 for idx, name in enumerate(headers)}

    for key, value in updates.items():
        if key in header_map:
            ws.update_cell(row_index, header_map[key], value)

    return get_user_row(psid)


def get_previous_context(user_data: dict) -> str:
    return (user_data.get("last_user_messages") or "").strip()


def update_conversation_context(psid: str, new_message: str, intent: str):
    _, _, user_data = ensure_user_exists(psid)
    old_context = (user_data.get("last_user_messages") or "").strip()

    parts = []
    if old_context:
        parts.extend([p for p in old_context.split("\n") if p.strip()])
    if new_message.strip():
        parts.append(new_message.strip())

    parts = parts[-6:]
    combined = "\n".join(parts)

    update_user_fields(
        psid,
        {
            "last_user_messages": combined,
            "last_intent": intent,
            "last_seen_at": now_str(),
        },
    )


# =========================
# HELPERS
# =========================
def normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def safe_json_loads(text: str):
    text = (text or "").strip()

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]

    return json.loads(text)


def verify_signature(req) -> bool:
    if not APP_SECRET:
        return True

    signature = req.headers.get("X-Hub-Signature-256")
    if not signature:
        return False

    try:
        sha_name, signature_hash = signature.split("=")
        if sha_name != "sha256":
            return False
    except ValueError:
        return False

    mac = hmac.new(APP_SECRET.encode("utf-8"), msg=req.data, digestmod=hashlib.sha256)
    expected_hash = mac.hexdigest()
    return hmac.compare_digest(expected_hash, signature_hash)


# =========================
# SEND MESSAGE
# =========================
def send_text_message(psid: str, text: str):
    text = (text or "").strip()
    if not text:
        return

    url = "https://graph.facebook.com/v23.0/me/messages"
    params = {"access_token": PAGE_ACCESS_TOKEN}
    payload = {
        "recipient": {"id": psid},
        "messaging_type": "RESPONSE",
        "message": {"text": text},
    }

    r = requests.post(url, params=params, json=payload, timeout=30)
    print("SEND STATUS:", r.status_code, r.text)


def send_image_message(psid: str, image_url: str):
    image_url = (image_url or "").strip()
    if not image_url:
        return

    url = "https://graph.facebook.com/v23.0/me/messages"
    params = {"access_token": PAGE_ACCESS_TOKEN}
    payload = {
        "recipient": {"id": psid},
        "messaging_type": "RESPONSE",
        "message": {
            "attachment": {
                "type": "image",
                "payload": {
                    "url": image_url,
                    "is_reusable": True,
                },
            }
        },
    }

    r = requests.post(url, params=params, json=payload, timeout=30)
    print("SEND IMAGE STATUS:", r.status_code, r.text)


# =========================
# AI
# =========================
def analyze_message_with_ai(
    user_message: str,
    current_signup_data: dict,
    previous_context: str = "",
    has_image_attachment: bool = False,
    has_other_attachment: bool = False,
):
    system_prompt = f"""
You are an intent classifier for a Facebook Messenger customer support bot.
You understand Burmese, English, mixed Burmese-English, slang, short messages, natural wording, and follow-up messages.

Return ONLY valid JSON.

Supported intents:
- greeting
- account_opening
- deposit_bank_info
- truemoney_deposit_info
- deposit_submitted
- bonus
- loss_bonus
- game_link
- line_link
- mmk_site
- other

Rules:
1. You do NOT write the final customer support answer.
2. You only decide the intent and extract account opening fields if present.
3. The bot must reply immediately based on the current message.
4. Use previous conversation context to understand short follow-up messages.
5. If the current message is short like "ဟုတ်", "အင်း", "ပို့", "လင့်", "အကောင့်", infer meaning from recent context.
6. If user wants to open account / register / signup / create account / sends account opening details, use "account_opening".
7. If user is asking for bank account to deposit money, use "deposit_bank_info".
8. If user is asking whether TrueMoney can be used for deposit, or asking for TrueMoney deposit info/number, use "truemoney_deposit_info".
9. If user says they already transferred, already deposited, sends receipt/slip/screenshot, says "ငွေသွင်းပြီးပြီ", "ပြေစာ", "slip", "screenshot", "ငွေသွင်းဖောင်", "ဖောင်တင်", "ဖောင်တင်ပေး", use "deposit_submitted".
10. If there is an image attachment and the text looks related to deposit, transfer, receipt, slip, form submission, or proof of payment, classify as "deposit_submitted".
11. If asking bonus/promotion, use "bonus".
12. If asking loss bonus, use "loss_bonus".
13. If asking game site link, game link, site link, use "game_link".
14. If asking line link, use "line_link".
15. If asking MMK site / kyat site, use "mmk_site".
16. If only greeting like hi, hello, hey, ဟိုင်း, မင်္ဂလာပါ, use "greeting".
17. If text contains both a greeting and a real request, prioritize the real request.
18. If not sure, use "other".
19. Extract these fields only if clearly present:
   - customer_name
   - phone
   - bank_type
   - bank_account
20. If user is already in account opening flow and sends partial details, still use "account_opening".

Previous conversation context:
{previous_context}

Current collected signup data:
{json.dumps(current_signup_data, ensure_ascii=False)}

Attachment hints:
- has_image_attachment = {has_image_attachment}
- has_other_attachment = {has_other_attachment}

Return JSON in this exact shape:
{{
  "intent": "greeting|account_opening|deposit_bank_info|truemoney_deposit_info|deposit_submitted|bonus|loss_bonus|game_link|line_link|mmk_site|other",
  "customer_name": "",
  "phone": "",
  "bank_type": "",
  "bank_account": ""
}}
"""

    user_input = user_message.strip() if user_message.strip() else "[empty text]"

    response = client_ai.responses.create(
        model="gpt-5-mini",
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
    )

    raw = response.output_text.strip()
    return safe_json_loads(raw)


# =========================
# ACCOUNT OPENING HELPERS
# =========================
def get_signup_data_from_user_row(user_data: dict) -> dict:
    return {
        "customer_name": (user_data.get("signup_name") or "").strip(),
        "phone": (user_data.get("signup_phone") or "").strip(),
        "bank_type": (user_data.get("signup_bank_type") or "").strip(),
        "bank_account": (user_data.get("signup_bank_account") or "").strip(),
    }


def merge_signup_data(old_data: dict, new_data: dict) -> dict:
    merged = dict(old_data)

    for key in ["customer_name", "phone", "bank_type", "bank_account"]:
        value = (new_data.get(key) or "").strip()
        if value:
            merged[key] = value

    return merged


def missing_signup_fields(data: dict):
    missing = []

    if not data.get("customer_name"):
        missing.append("customer_name")
    if not data.get("phone"):
        missing.append("phone")
    if not data.get("bank_type"):
        missing.append("bank_type")
    if not data.get("bank_account"):
        missing.append("bank_account")

    return missing


def save_signup_data_to_user_row(psid: str, signup_data: dict, completed: bool = False):
    update_user_fields(
        psid,
        {
            "signup_name": signup_data.get("customer_name", ""),
            "signup_phone": signup_data.get("phone", ""),
            "signup_bank_type": signup_data.get("bank_type", ""),
            "signup_bank_account": signup_data.get("bank_account", ""),
            "signup_completed": "TRUE" if completed else "FALSE",
            "last_seen_at": now_str(),
        },
    )


def build_missing_fields_message(base_message: str, missing: list[str]) -> str:
    field_map = {
        "customer_name": "✅အမည်",
        "phone": "✅ဖုန်းနံပါတ်",
        "bank_type": "✅Bank အမျိုးအစား",
        "bank_account": "✅Bank နံပါတ် (or) True Money",
    }

    if len(missing) == 4:
        return base_message.strip()

    lines = [field_map[m] for m in missing if m in field_map]
    if not lines:
        return base_message.strip()

    return base_message.strip() + "\n\n" + "\n".join(lines)


# =========================
# CORE LOGIC
# =========================
def handle_user_message(psid: str, text: str, attachments: list | None = None):
    attachments = attachments or []
    msg = normalize_text(text or "")

    has_image_attachment = any(a.get("type") == "image" for a in attachments)
    has_other_attachment = any(a.get("type") != "image" for a in attachments)

    print("USER MESSAGE:", repr(msg))
    print("HAS IMAGE ATTACHMENT:", has_image_attachment)
    print("HAS OTHER ATTACHMENT:", has_other_attachment)

    _, _, user_data = ensure_user_exists(psid)
    current_signup_data = get_signup_data_from_user_row(user_data or {})
    signup_completed = (user_data.get("signup_completed") or "").strip().upper() == "TRUE"
    previous_context = get_previous_context(user_data or {})

    print("PREVIOUS CONTEXT:", repr(previous_context))

    try:
        ai_result = analyze_message_with_ai(
            user_message=msg,
            current_signup_data=current_signup_data,
            previous_context=previous_context,
            has_image_attachment=has_image_attachment,
            has_other_attachment=has_other_attachment,
        )
    except Exception as e:
        print("AI ERROR:", e)
        return

    intent = (ai_result.get("intent") or "other").strip()
    print("AI INTENT:", intent)

    extracted_fields = {
        "customer_name": (ai_result.get("customer_name") or "").strip(),
        "phone": (ai_result.get("phone") or "").strip(),
        "bank_type": (ai_result.get("bank_type") or "").strip(),
        "bank_account": (ai_result.get("bank_account") or "").strip(),
    }
    print("EXTRACTED FIELDS:", extracted_fields)

    update_conversation_context(psid, msg, intent)

    welcome_message = get_setting_value("welcome_message")
    returning_greeting_message = get_setting_value("returning_greeting_message")
    account_opening_request = get_setting_value("account_opening_request")
    deposit_bank_message = get_setting_value("deposit_bank_message")
    truemoney_deposit_message = get_setting_value("truemoney_deposit_message")
    truemoney_signup_ok_message = get_setting_value("truemoney_signup_ok_message")
    deposit_submitted_message = get_setting_value("deposit_submitted_message")
    deposit_submitted_image_url = get_setting_value("deposit_submitted_image_url")
    bonus_message = get_setting_value("bonus_message")
    loss_bonus_message = get_setting_value("loss_bonus_message")
    game_link_message = get_setting_value("game_link_message")
    line_link_message = get_setting_value("line_link_message")
    mmk_site_message = get_setting_value("mmk_site_message")

    is_first_time_user = not (user_data.get("first_seen_at") or "").strip()
    if is_first_time_user:
        update_user_fields(psid, {"first_seen_at": now_str()})

    if intent == "greeting":
        if is_first_time_user and welcome_message.strip():
            print("REPLY: welcome_message")
            send_text_message(psid, welcome_message)
            return
        if returning_greeting_message.strip():
            print("REPLY: returning_greeting_message")
            send_text_message(psid, returning_greeting_message)
            return
        print("REPLY: none")
        return

    if intent == "deposit_bank_info":
        if deposit_bank_message.strip():
            print("REPLY: deposit_bank_message")
            send_text_message(psid, deposit_bank_message)
        else:
            print("REPLY: none")
        return

    if intent == "truemoney_deposit_info":
        if truemoney_deposit_message.strip():
            print("REPLY: truemoney_deposit_message")
            send_text_message(psid, truemoney_deposit_message)
        else:
            print("REPLY: none")
        return

    if intent == "deposit_submitted":
        replied = False

        if deposit_submitted_message.strip():
            print("REPLY: deposit_submitted_message")
            send_text_message(psid, deposit_submitted_message)
            replied = True

        if deposit_submitted_image_url.strip():
            print("REPLY: deposit_submitted_image_url")
            send_image_message(psid, deposit_submitted_image_url)
            replied = True

        if not replied:
            print("REPLY: none")
        return

    if intent == "bonus":
        if bonus_message.strip():
            print("REPLY: bonus_message")
            send_text_message(psid, bonus_message)
        else:
            print("REPLY: none")
        return

    if intent == "loss_bonus":
        if loss_bonus_message.strip():
            print("REPLY: loss_bonus_message")
            send_text_message(psid, loss_bonus_message)
        else:
            print("REPLY: none")
        return

    if intent == "game_link":
        if game_link_message.strip():
            print("REPLY: game_link_message")
            send_text_message(psid, game_link_message)
        else:
            print("REPLY: none")
        return

    if intent == "line_link":
        if line_link_message.strip():
            print("REPLY: line_link_message")
            send_text_message(psid, line_link_message)
        else:
            print("REPLY: none")
        return

    if intent == "mmk_site":
        if mmk_site_message.strip():
            print("REPLY: mmk_site_message")
            send_text_message(psid, mmk_site_message)
        else:
            print("REPLY: none")
        return

    if intent == "account_opening" or (not signup_completed and any(extracted_fields.values())):
        merged_signup_data = merge_signup_data(current_signup_data, extracted_fields)
        missing = missing_signup_fields(merged_signup_data)

        print("MERGED SIGNUP DATA:", merged_signup_data)
        print("MISSING FIELDS:", missing)

        save_signup_data_to_user_row(psid, merged_signup_data, completed=(len(missing) == 0))

        if missing:
            bank_type = (merged_signup_data.get("bank_type") or "").lower()
            if bank_type == "truemoney" and truemoney_signup_ok_message.strip():
                print("REPLY: truemoney_signup_ok_message")
                send_text_message(psid, truemoney_signup_ok_message)

            if account_opening_request.strip():
                final_text = build_missing_fields_message(account_opening_request, missing)
                print("REPLY: account_opening_request")
                send_text_message(psid, final_text)
            else:
                print("REPLY: none")
            return

        print("REPLY: none (signup complete, admin will continue)")
        return

    print("REPLY: none")
    return


# =========================
# ROUTES
# =========================
@app.route("/", methods=["GET"])
def home():
    return "Messenger bot is running - immediate AI sheet version", 200


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def webhook():
    if not verify_signature(request):
        return "Invalid signature", 403

    data = request.get_json(silent=True) or {}
    print("WEBHOOK EVENT:", json.dumps(data, ensure_ascii=False))

    if data.get("object") == "page":
        for entry in data.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                sender = messaging_event.get("sender", {})
                psid = sender.get("id")
                if not psid:
                    continue

                message = messaging_event.get("message", {}) or {}
                text = message.get("text", "") or ""
                attachments = message.get("attachments", []) or []

                if message.get("is_echo"):
                    continue

                handle_user_message(psid, text, attachments)

        return "EVENT_RECEIVED", 200

    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))