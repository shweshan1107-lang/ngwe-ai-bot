import os
import json
import random
import string
import re
from datetime import datetime

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from openai import OpenAI
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

ACCOUNTS_SHEET_NAME = "accounts"
SETTINGS_SHEET_NAME = "settings"
USERS_SHEET_NAME = "users"
LOGIN_LINK = "https://sites.google.com/view/bestforyoue/home"

client_ai = OpenAI(api_key=OPENAI_API_KEY)


def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS_FILE,
        scopes=scopes,
    )
    return gspread.authorize(creds)


def get_worksheet(sheet_name: str):
    client = get_gspread_client()
    spreadsheet = client.open(SPREADSHEET_NAME)
    return spreadsheet.worksheet(sheet_name)


SETTINGS_CACHE = {}
SETTINGS_CACHE_LOADED_AT = None


def load_settings_cache():
    global SETTINGS_CACHE, SETTINGS_CACHE_LOADED_AT
    ws = get_worksheet(SETTINGS_SHEET_NAME)
    rows = ws.get_all_values()

    data = {}
    for row in rows:
        if len(row) >= 2:
            key = row[0].strip()
            value = row[1].strip()
            if key:
                data[key] = value

    SETTINGS_CACHE = data
    SETTINGS_CACHE_LOADED_AT = datetime.now()


def get_setting_value(setting_key: str) -> str:
    global SETTINGS_CACHE
    if not SETTINGS_CACHE:
        load_settings_cache()
    return SETTINGS_CACHE.get(setting_key, "")


def refresh_settings_cache():
    try:
        load_settings_cache()
    except Exception as e:
        print("refresh_settings_cache error:", e)


def has_user_seen_welcome(user_id: str) -> bool:
    try:
        ws = get_worksheet(USERS_SHEET_NAME)
        values = ws.col_values(1)
        return str(user_id) in values
    except Exception:
        return False


def mark_user_seen_welcome(user_id: str):
    try:
        ws = get_worksheet(USERS_SHEET_NAME)
        values = ws.col_values(1)
        if str(user_id) not in values:
            ws.append_row([str(user_id), datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    except Exception as e:
        print("mark_user_seen_welcome error:", e)


def generate_password() -> str:
    letters = "".join(random.choices(string.ascii_letters, k=2))
    digits = "".join(random.choices(string.digits, k=6))
    return f"{letters}{digits}"


def get_next_number_and_increment():
    ws = get_worksheet(SETTINGS_SHEET_NAME)

    label = ws.acell("A1").value
    number_str = ws.acell("B1").value

    if label != "next_number":
        raise ValueError("settings sheet A1 must be 'next_number'")

    if not number_str or not number_str.isdigit():
        raise ValueError("settings sheet B1 must contain a number")

    current_number = int(number_str)
    next_number = current_number + 1

    ws.update("B1", [[str(next_number)]])
    refresh_settings_cache()
    return current_number


def append_account_row(
    customer_name: str,
    phone: str,
    bank_type: str,
    bank_account: str,
    generated_username: str,
    generated_password: str,
):
    ws = get_worksheet(ACCOUNTS_SHEET_NAME)

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [
        generated_username,
        created_at,
        "telegram",
        customer_name,
        phone,
        bank_type,
        bank_account,
        "account_opening",
        generated_username,
        generated_password,
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")


def safe_json_loads(text: str):
    text = text.strip()

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


def normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def is_greeting_message(text: str) -> bool:
    lowered = text.lower().strip()
    normalized = re.sub(r"[^\w\u1000-\u109f]+", "", lowered)

    greeting_words = [
        "hi", "hii", "hiii", "hiiii",
        "hello", "hey", "hy", "helo",
        "ဟိုင်း", "ဟိုင်းဟိုင်း", "ဟိုင္း", "ဟိုင္းဟိုင္း",
        "မင်္ဂလာပါ", "မဂ်လာပါ",
        "ရှိလား",
    ]

    normalized_greetings = [
        re.sub(r"[^\w\u1000-\u109f]+", "", g.lower())
        for g in greeting_words
    ]

    if lowered in greeting_words:
        return True

    if normalized in normalized_greetings:
        return True

    if re.fullmatch(r"h+i+", normalized):
        return True

    if re.fullmatch(r"he+y+", normalized):
        return True

    if normalized.startswith("ဟိုင်း") or normalized.startswith("ဟိုင္း"):
        return True

    return False


def get_returning_greeting_message() -> str:
    return "ဟုတ်ကဲ့ရှင့်ဘာလေးကူညီပေးရမှာလဲရှင့်"


def looks_like_permission_question(text: str) -> bool:
    lowered = text.lower().strip()
    patterns = [
        "ရလား",
        "ရနိုင်လား",
        "အကောင့်ဖွင့်လို့ရလား",
        "ဖွင့်လို့ရလား",
        "လုပ်လို့ရလား",
        "ok လား",
        "အိုကေလား",
    ]
    return any(p in lowered for p in patterns)


def build_truemoney_signup_missing_message(missing: list[str]) -> str:
    field_map = {
        "customer_name": "အမည်",
        "phone": "ဖုန်းနံပါတ်",
        "bank_account": "TrueMoney နံပါတ်",
    }

    labels = [field_map[m] for m in missing if m in field_map]

    if not labels:
        return ""

    return "အကောင့်ဖွင့်ရန် လိုအပ်တဲ့အချက်အလက်လေး ပို့ပေးပါရှင့်\n\n" + "\n".join(
        [f"✅{label}" for label in labels]
    )


def detect_fast_intent(text: str) -> str:
    lowered = normalize_text(text).lower()

    if is_greeting_message(lowered):
        return "greeting"

    true_signup_patterns = [
        "true နဲ့ဖွင့်", "true နဲ့အကောင့်ဖွင့်",
        "truemoney နဲ့ဖွင့်", "truemoney နဲ့အကောင့်ဖွင့်",
        "true money နဲ့ဖွင့်", "true money နဲ့အကောင့်ဖွင့်",
    ]
    if any(p in lowered for p in true_signup_patterns):
        return "true_signup"

    signup_patterns = [
        "အကောင့်ဖွင့်", "account ဖွင့်", "register", "signup"
    ]
    if any(p in lowered for p in signup_patterns):
        return "signup"

    true_deposit_patterns = [
        "true နဲ့သွင်း", "truemoney နဲ့သွင်း", "true money နဲ့သွင်း",
        "true number ပို့", "truemoney number ပို့", "true money number ပို့",
    ]
    if any(p in lowered for p in true_deposit_patterns):
        delayed_patterns = [
            "ကြာပြီ", "မဝင်သေး", "လွှဲထား", "ပို့ထား", "စောင့်နေရ", "မရသေး"
        ]
        if not any(d in lowered for d in delayed_patterns):
            return "truemoney_deposit"

    deposit_patterns = [
        "ဘဏ်ပို့", "ဘဏ်နံပါတ်ပို့", "ငွေသွင်းမယ်", "ငွေလွှဲမယ်",
        "bank number", "bank account", "deposit", "transfer"
    ]
    delayed_patterns = [
        "ကြာပြီ", "မဝင်သေး", "လွှဲထား", "ပို့ထား", "စောင့်နေရ", "မရသေး"
    ]
    if any(p in lowered for p in deposit_patterns) and not any(d in lowered for d in delayed_patterns):
        return "deposit_bank"

    mmk_patterns = [
        "ကျပ်နဲ့", "mmk", "မြန်မာငွေ", "ကျပ် site", "mmk site"
    ]
    if any(p in lowered for p in mmk_patterns):
        return "mmk_site"

    bonus_patterns = ["bonus", "ပရိုမိုးရှင်း", "promotion", "promo"]
    if any(p in lowered for p in bonus_patterns):
        return "bonus"

    return ""


def smart_extract_fields(user_message: str, current_data: dict) -> dict:
    text = normalize_text(user_message)
    lowered = text.lower()

    extracted = {
        "customer_name": "",
        "phone": "",
        "bank_type": "",
        "bank_account": "",
    }

    if "truemoney" in lowered or "true money" in lowered or re.search(r"\btrue\b", lowered):
        extracted["bank_type"] = "TrueMoney"

    bank_keywords = [
        "scb",
        "bangkok bank",
        "bbl",
        "kbank",
        "kasikorn",
        "krungsri",
        "ktb",
        "ttb",
        "truemoney",
        "true money",
        "true",
    ]

    for bk in bank_keywords:
        if bk in lowered:
            if bk in ["true", "truemoney", "true money"]:
                extracted["bank_type"] = "TrueMoney"
            elif not extracted["bank_type"]:
                extracted["bank_type"] = bk.upper()

    numbers = re.findall(r"\d[\d\- ]{6,}", text)
    cleaned_numbers = []
    for n in numbers:
        cleaned = re.sub(r"[^\d]", "", n)
        if len(cleaned) >= 7:
            cleaned_numbers.append(cleaned)

    for n in cleaned_numbers:
        if len(n) in [9, 10, 11] and (
            n.startswith("09") or n.startswith("01") or n.startswith("06")
        ):
            if not current_data.get("phone"):
                extracted["phone"] = n
                break

    for n in cleaned_numbers:
        if n != extracted["phone"]:
            if not extracted["bank_account"]:
                extracted["bank_account"] = n

    if len(cleaned_numbers) == 1 and extracted["bank_type"] == "TrueMoney":
        if not current_data.get("bank_account"):
            extracted["bank_account"] = cleaned_numbers[0]

    if not re.search(r"\d", text):
        if not any(bk in lowered for bk in bank_keywords):
            if len(text) <= 40:
                extracted["customer_name"] = text

    return extracted


def analyze_message_with_ai(user_message: str, current_data: dict):
    system_prompt = f"""
You are Ngwe99 AI customer support assistant.

You must understand the customer's intention naturally, not by keyword matching.
Reply in Burmese.
Sound human, warm, and natural.

Only classify into these supported intents when the user's meaning clearly matches:
- greeting
- account_opening
- deposit_bank_info
- truemoney_deposit_info
- bonus
- loss_bonus
- game_link
- line_link
- mmk_site
- other

If the message does not clearly belong to one of the supported intents, return intent = "other".
Do not invent answers outside these trained intents.

Main business rules:
1. Deposit flow and account opening flow are different.
2. deposit_bank_info is ONLY for cases where the customer is clearly asking for bank details to pay now or asking where/how to transfer now.
3. Examples of deposit_bank_info:
   - ဘဏ်ပို့ပေး
   - ဘဏ်နံပါတ်ပို့ပေး
   - ငွေသွင်းမယ်
   - ငွေလွှဲမယ်
   - bank ချပေး
   - ဘဏ်ချပေး
   - ဘယ် account ကိုလွှဲရမလဲ
   - deposit လုပ်မယ်
   - transfer လုပ်မယ်
4. If customer says they already paid, already transferred, payment is late, deposit is delayed, money not arrived yet, waiting already, sent slip already:
   this is NOT deposit_bank_info. Return "other".
5. truemoney_deposit_info is ONLY when customer clearly wants TrueMoney payment details now.
6. Examples of truemoney_deposit_info:
   - true နဲ့သွင်းမယ်
   - truemoney နဲ့ငွေသွင်းမယ်
   - true money နဲ့ငွေသွင်းမယ်
   - true number ပို့
   - truemoney number ပို့
   - true money number ပို့
   - true နဲ့သွင်းလို့ရလား
   - truemoney နဲ့သွင်းလို့ရလား
7. If customer says they already sent by TrueMoney, waiting already, payment delayed, money not in yet:
   this is NOT truemoney_deposit_info. Return "other".
8. If customer asks to open account with TrueMoney, says things like:
   - true နဲ့အကောင့်ဖွင့်မယ်
   - truemoney နဲ့ဖွင့်မယ်
   - true နဲ့ဖွင့်ရလား
   - true နဲ့အကောင့်ဖွင့်လို့ရလား
   - truemoney နဲ့ account ဖွင့်လို့ရလား
   then intent must be "account_opening", not "truemoney_deposit_info".
9. If customer says general account opening like:
   - အကောင့်ဖွင့်မယ်
   - အကောင့်ဖွင့်ချင်တယ်
   - account ဖွင့်မယ်
   - register လုပ်မယ်
   then intent must be "account_opening".
10. If customer asks whether they can play with MMK / kyat / ကျပ်, such as:
   - ကျပ်နဲ့ဆော့လို့ရလား
   - mmk နဲ့ဆော့လို့ရလား
   - ကျပ် site ရှိလား
   - mmk site ရှိလား
   - မြန်မာငွေနဲ့ဆော့လို့ရလား
   then intent must be "mmk_site".
11. IMPORTANT:
   "ကျပ်", "mmk", "မြန်မာငွေ" do NOT mean TrueMoney.
   Do not classify them as truemoney_deposit_info or account_opening unless the customer clearly says TrueMoney or account opening.
12. For account opening, required fields are:
   - customer_name
   - phone
   - bank_type
   - bank_account
13. If user wants account opening with TrueMoney, set bank_type = "TrueMoney".
14. If all required fields are complete for account opening, the reply must be exactly:
ဟုတ်ကဲ့ရှင့် ခနလေးစောင့်ပေးပါရှင့် ဂိမ်းအကောင့်လေးဖွင့်ပေးပါမယ်ရှင့်

Field extraction rules:
- customer_name = customer's name
- phone = customer's phone number
- bank_type = bank type / bank name / TrueMoney
- bank_account = bank number or TrueMoney number
- If not found, return empty string.

Current collected data:
{json.dumps(current_data, ensure_ascii=False)}

Return ONLY valid JSON in this exact shape:
{{
  "intent": "greeting|account_opening|deposit_bank_info|truemoney_deposit_info|bonus|loss_bonus|game_link|line_link|mmk_site|other",
  "customer_name": "",
  "phone": "",
  "bank_type": "",
  "bank_account": "",
  "reply": ""
}}
"""

    response = client_ai.responses.create(
        model="gpt-5-mini",
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )

    raw = response.output_text.strip()
    return safe_json_loads(raw)


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


def build_final_account_message(phone: str, password: str) -> str:
    return (
        f"Username={phone}\n\n"
        f"Password={password}\n\n"
        "Login ဆိုသည့်နေရာလေးကနေ username လေးကိုအကို့ဖုန်းနံပါတ်လေးနဲ့ အကောင့်၀င်ကြည့်ပေးပါရှင့်\n"
        f"Game Link 👉👉 {LOGIN_LINK}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    context.user_data["signup_data"] = context.user_data.get("signup_data", {})
    context.user_data["signup_completed"] = False

    if not has_user_seen_welcome(user_id):
        welcome_text = get_setting_value("welcome_message")
        if not welcome_text:
            welcome_text = "မင်္ဂလာပါရှင့်"
        mark_user_seen_welcome(user_id)
        await update.message.reply_text(welcome_text)
    else:
        await update.message.reply_text(get_returning_greeting_message())


async def checksheet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ws = get_worksheet(ACCOUNTS_SHEET_NAME)
        data = ws.get_all_values()

        if not data:
            await update.message.reply_text("accounts sheet is empty.")
            return

        preview_rows = data[:5]
        msg = "\n".join([" | ".join(row) for row in preview_rows])
        await update.message.reply_text(f"accounts sheet OK\n\n{msg}")

    except Exception as e:
        await update.message.reply_text(f"Sheet error:\n{e}")


async def reloadsettings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    refresh_settings_cache()
    await update.message.reply_text("settings cache reloaded")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = (update.message.text or "").strip()

    if not user_message:
        return

    user_id = str(update.effective_user.id)

    if not has_user_seen_welcome(user_id):
        mark_user_seen_welcome(user_id)
        welcome_text = get_setting_value("welcome_message")
        if not welcome_text:
            welcome_text = "မင်္ဂလာပါရှင့်"
        await update.message.reply_text(welcome_text)
        return

    signup_data = context.user_data.get("signup_data", {})
    signup_completed = context.user_data.get("signup_completed", False)

    if signup_completed:
        context.user_data["signup_data"] = {}
        context.user_data["signup_completed"] = False
        signup_data = {}

    fast_intent = detect_fast_intent(user_message)

    if fast_intent == "greeting":
        await update.message.reply_text(get_returning_greeting_message())
        return

    if fast_intent == "deposit_bank":
        base_message = get_setting_value("deposit_bank_message")
        if base_message:
            await update.message.reply_text(base_message)
        return

    if fast_intent == "truemoney_deposit":
        base_message = get_setting_value("truemoney_deposit_message")
        if base_message:
            await update.message.reply_text(base_message)
        return

    if fast_intent == "mmk_site":
        base_message = get_setting_value("mmk_site_message")
        if base_message:
            await update.message.reply_text(base_message)
        return

    if fast_intent == "bonus":
        base_message = get_setting_value("bonus_message")
        if base_message:
            await update.message.reply_text(base_message)
        return

    if fast_intent == "true_signup":
        context.user_data["signup_data"] = {"bank_type": "TrueMoney"}
        true_signup_intro = get_setting_value("truemoney_signup_ok_message")
        if not true_signup_intro:
            true_signup_intro = "ရပါတယ်ရှင့် TrueMoney နဲ့လည်း အကောင့်ဖွင့်လို့ရပါတယ်ရှင့်"

        missing_message = (
            "အကောင့်ဖွင့်ရန် လိုအပ်တဲ့အချက်အလက်လေး ပို့ပေးပါရှင့်\n\n"
            "✅အမည်\n✅ဖုန်းနံပါတ်\n✅TrueMoney နံပါတ်"
        )
        await update.message.reply_text(f"{true_signup_intro}\n\n{missing_message}")
        return

    if fast_intent == "signup":
        full_request_message = get_setting_value("account_opening_request")
        if full_request_message:
            await update.message.reply_text(full_request_message)
        return

    try:
        ai_result = analyze_message_with_ai(user_message, signup_data)
    except Exception as e:
        print("OpenAI analyze error:", e)
        return

    intent = (ai_result.get("intent") or "other").strip()
    new_fields = {
        "customer_name": ai_result.get("customer_name", ""),
        "phone": ai_result.get("phone", ""),
        "bank_type": ai_result.get("bank_type", ""),
        "bank_account": ai_result.get("bank_account", ""),
    }

    smart_fields = smart_extract_fields(user_message, signup_data)
    for key in ["customer_name", "phone", "bank_type", "bank_account"]:
        if not new_fields.get(key):
            new_fields[key] = smart_fields.get(key, "")

    if intent == "deposit_bank_info":
        base_message = get_setting_value("deposit_bank_message")
        if base_message:
            await update.message.reply_text(base_message)
        return

    if intent == "truemoney_deposit_info":
        base_message = get_setting_value("truemoney_deposit_message")
        if base_message:
            await update.message.reply_text(base_message)
        return

    if intent == "account_opening" or signup_data:
        merged_data = merge_signup_data(signup_data, new_fields)
        context.user_data["signup_data"] = merged_data

        missing = missing_signup_fields(merged_data)
        is_true_signup = (merged_data.get("bank_type") == "TrueMoney")

        if missing:
            if is_true_signup:
                true_signup_intro = get_setting_value("truemoney_signup_ok_message")
                if not true_signup_intro:
                    true_signup_intro = "ရပါတယ်ရှင့် TrueMoney နဲ့လည်း အကောင့်ဖွင့်လို့ရပါတယ်ရှင့်"

                true_missing = [m for m in missing if m in ["customer_name", "phone", "bank_account"]]
                missing_message = build_truemoney_signup_missing_message(true_missing)

                if looks_like_permission_question(user_message):
                    if missing_message:
                        await update.message.reply_text(f"{true_signup_intro}\n\n{missing_message}")
                    else:
                        await update.message.reply_text(true_signup_intro)
                else:
                    if missing_message:
                        await update.message.reply_text(missing_message)
                    else:
                        await update.message.reply_text(true_signup_intro)
                return

            full_request_message = get_setting_value("account_opening_request")
            if not full_request_message:
                full_request_message = (
                    "အကောင့်ဖွင့်ရန် အချက်အလက်အပြည့်အစုံလေး ပို့ပေးပါရှင့်\n\n"
                    "✅ အမည်\n"
                    "✅ ဖုန်းနံပါတ်\n"
                    "✅ Bank အမျိုးအစား\n"
                    "✅ Bank နံပါတ် (or) TrueMoney နံပါတ်"
                )

            if looks_like_permission_question(user_message):
                await update.message.reply_text(f"ရပါတယ်ရှင့်\n\n{full_request_message}")
            else:
                await update.message.reply_text(full_request_message)
            return

        waiting_message = "ဟုတ်ကဲ့ရှင့် ခနလေးစောင့်ပေးပါရှင့် ဂိမ်းအကောင့်လေးဖွင့်ပေးပါမယ်ရှင့်"
        await update.message.reply_text(waiting_message)

        try:
            seq = get_next_number_and_increment()
            generated_username = f"Ngwe{seq}"
            generated_password = generate_password()

            append_account_row(
                customer_name=merged_data["customer_name"],
                phone=merged_data["phone"],
                bank_type=merged_data["bank_type"],
                bank_account=merged_data["bank_account"],
                generated_username=generated_username,
                generated_password=generated_password,
            )

            final_message = build_final_account_message(
                phone=merged_data["phone"],
                password=generated_password,
            )

            context.user_data["signup_completed"] = True
            context.user_data["signup_data"] = {}

            await update.message.reply_text(final_message)

        except Exception as e:
            print("Account creation save error:", e)
        return

    intent_to_setting = {
        "bonus": "bonus_message",
        "loss_bonus": "loss_bonus_message",
        "game_link": "game_link_message",
        "line_link": "line_link_message",
        "mmk_site": "mmk_site_message",
    }

    if intent in intent_to_setting:
        setting_key = intent_to_setting[intent]
        base_message = get_setting_value(setting_key)
        if base_message:
            await update.message.reply_text(base_message)
        return

    return


def main():
    if not BOT_TOKEN:
        print("BOT_TOKEN not found in .env")
        return

    if not OPENAI_API_KEY:
        print("OPENAI_API_KEY not found in .env")
        return

    if not SPREADSHEET_NAME:
        print("SPREADSHEET_NAME not found in .env")
        return

    if not GOOGLE_CREDENTIALS_FILE:
        print("GOOGLE_CREDENTIALS_FILE not found in .env")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("checksheet", checksheet))
    application.add_handler(CommandHandler("reloadsettings", reloadsettings))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    print("Bot is running...")
    application.run_polling(
        poll_interval=1.0,
        timeout=10,
        drop_pending_updates=True,
        close_loop=False,
    )


if __name__ == "__main__":
    main()