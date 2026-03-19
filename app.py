import hashlib
import hmac
import json
import os
import re
from typing import Optional

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "")
APP_SECRET = os.getenv("APP_SECRET", "")

# ====== YOUR SETTINGS / REPLIES ======
WELCOME_MESSAGE = "မင်္ဂလာပါရှင့်"
RETURNING_GREETING_MESSAGE = "ဟုတ်ကဲ့ရှင့်ဘာလေးကူညီပေးရမှာလဲရှင့်"

DEPOSIT_BANK_MESSAGE = """ဘဏ်အကောင့် - Mr. Surachai Ladbasri
ဘဏ်နံပါတ် - 604-279000-7 (SCB)

ငွေလွှဲပြီးပါက slip လေးပို့ပေးပါရှင့်🤍"""

TRUEMONEY_DEPOSIT_MESSAGE = """ရပါတယ်ရှင့် TrueMoney နဲ့လည်း ငွေသွင်းလို့ရပါတယ်ရှင့်🤍

TrueMoney အကောင့် = 098852221
အမည် = andra"""

ACCOUNT_OPENING_REQUEST = """အကောင့်ဖွင့်ရန် အချက်အလက်အပြည့်အစုံလေး ပို့ပေးပါရှင့်

✅ အမည်
✅ ဖုန်းနံပါတ်
✅ Bank အမျိုးအစား
✅ Bank နံပါတ် (or) True Money"""

TRUEMONEY_SIGNUP_OK_MESSAGE = """ရပါတယ်ရှင့် TrueMoney နဲ့လည်း အကောင့်ဖွင့်လို့ရပါတယ်ရှင့်🤍"""

BONUS_MESSAGE = "Bonus / Promotion အသေးစိတ်ကို ဒီမှာကြည့်ပေးပါရှင့် ..."
LOSS_BONUS_MESSAGE = "ရှုံးကြေးအကြောင်း အသေးစိတ်ကို ဒီမှာကြည့်ပေးပါရှင့် ..."
GAME_LINK_MESSAGE = "ဂိမ်းလင့်ပါရှင့်\nhttps://ngwe99.co/home"
LINE_LINK_MESSAGE = "လိုင်းစိမ်းလင့်ပါရှင့်\nhttps://line.me/R/ti/p/@ngwe"
MMK_SITE_MESSAGE = "ကျပ်ဆိုက်လင့်ပါရှင့်\nhttps://t.me/ngwe99mmkchannel"


# ====== SIMPLE IN-MEMORY USER STATE ======
# Render free restart ရင် reset ဖြစ်နိုင်တယ်
USER_STATE: dict[str, dict] = {}


def get_user_state(psid: str) -> dict:
    if psid not in USER_STATE:
        USER_STATE[psid] = {
            "seen_welcome": False,
            "signup_data": {},
            "signup_completed": False,
        }
    return USER_STATE[psid]


def normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


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



def detect_fast_intent(text: str) -> str:
    lowered = normalize_text(text).lower()
    compact = lowered.replace(" ", "")

    if is_greeting_message(lowered):
        return "greeting"

    # TrueMoney signup
    true_signup_patterns = [
        "trueနဲ့ဖွင့်",
        "trueနဲ့အကောင့်ဖွင့်",
        "truemoneyနဲ့ဖွင့်",
        "truemoneyနဲ့အကောင့်ဖွင့်",
        "truemoneyနဲ့accountဖွင့်",
        "truemoneyနဲ့signup",
        "truemoneyနဲ့register",
        "truemoneyဖွင့်",
        "truemoneyနဲ့ဖွင့်",
        "truemoney",
        "truemoney",
    ]
    if any(p in compact for p in true_signup_patterns):
        if "ဖွင့်" in compact or "signup" in compact or "register" in compact or "account" in compact:
            return "true_signup"

    # Normal signup
    signup_patterns = [
        "အကောင့်ဖွင့်",
        "အကောင့်ဖွင့်",
        "အကောင့်ဖွင့်",
        "အကောင့်ဖွင့်",
        "accountဖွင့်",
        "accountဖွင့်",
        "accဖွင့်",
        "register",
        "signup",
        "sign up",
        "ဖွင့်မယ်",
        "ဖွင့်မယ်",
    ]
    if any(p.replace(" ", "") in compact for p in signup_patterns):
        return "signup"

    # TrueMoney deposit
    true_deposit_patterns = [
        "trueနဲ့သွင်း",
        "truemoneyနဲ့သွင်း",
        "truemoneyနဲ့သွင်း",
        "truemoneynumberပို့",
        "truemoneynumberပို့",
        "truenumberပို့",
    ]
    delayed_patterns = [
        "ကြာပြီ", "မဝင်သေး", "လွှဲထား", "ပို့ထား", "စောင့်နေရ", "မရသေး"
    ]
    if any(p in compact for p in true_deposit_patterns) and not any(d in compact for d in delayed_patterns):
        return "truemoney_deposit"

    # Bank deposit
    deposit_patterns = [
        "ဘဏ်ပို့",
        "ဘဏ်နံပါတ်ပို့",
        "ငွေသွင်းမယ်",
        "ငွေလွှဲမယ်",
        "banknumber",
        "bankaccount",
        "deposit",
        "transfer",
    ]
    if any(p in compact for p in deposit_patterns) and not any(d in compact for d in delayed_patterns):
        return "deposit_bank"

    # MMK site
    mmk_patterns = [
        "ကျပ်နဲ့", "mmk", "မြန်မာငွေ", "ကျပ်site", "mmksite"
    ]
    if any(p in compact for p in mmk_patterns):
        return "mmk_site"

    # Bonus
    bonus_patterns = ["bonus", "ပရိုမိုးရှင်း", "promotion", "promo", "ဘောနပ်"]
    if any(p in compact for p in bonus_patterns):
        return "bonus"

    # Line
    line_patterns = ["line", "လိုင်း", "လိုင်းစိမ်း"]
    if any(p in compact for p in line_patterns):
        return "line_link"

    # Game link
    game_link_patterns = ["ဂိမ်းလင့်", "gamelink", "sitelink", "ဆိုက်လင့်"]
    if any(p in compact for p in game_link_patterns):
        return "game_link"

    return ""   


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


def send_text_message(psid: str, text: str) -> None:
    if not PAGE_ACCESS_TOKEN:
        print("PAGE_ACCESS_TOKEN missing")
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


def handle_user_message(psid: str, text: str) -> None:
    state = get_user_state(psid)
    msg = normalize_text(text)
    print("USER MESSAGE:", repr(msg))

    if not msg:
        return

    if not state["seen_welcome"]:
        state["seen_welcome"] = True
        send_text_message(psid, WELCOME_MESSAGE)
        return

    if is_greeting_message(msg):
        send_text_message(psid, RETURNING_GREETING_MESSAGE)
        return

    fast_intent = detect_fast_intent(msg)
    print("FAST INTENT:", fast_intent)

    if fast_intent == "deposit_bank":
        print("REPLY: deposit_bank")
        send_text_message(psid, DEPOSIT_BANK_MESSAGE)
        return

    if fast_intent == "truemoney_deposit":
        print("REPLY: truemoney_deposit")
        send_text_message(psid, TRUEMONEY_DEPOSIT_MESSAGE)
        return

    if fast_intent == "mmk_site":
        print("REPLY: mmk_site")
        send_text_message(psid, MMK_SITE_MESSAGE)
        return

    if fast_intent == "bonus":
        print("REPLY: bonus")
        send_text_message(psid, BONUS_MESSAGE)
        return

    if fast_intent == "line_link":
        print("REPLY: line_link")
        send_text_message(psid, LINE_LINK_MESSAGE)
        return

    if fast_intent == "game_link":
        print("REPLY: game_link")
        send_text_message(psid, GAME_LINK_MESSAGE)
        return

    if fast_intent == "true_signup":
        print("REPLY: true_signup")
        state["signup_data"] = {"bank_type": "TrueMoney"}
        send_text_message(
            psid,
            TRUEMONEY_SIGNUP_OK_MESSAGE + "\n\n" +
            "အကောင့်ဖွင့်ရန် လိုအပ်တဲ့အချက်အလက်လေး ပို့ပေးပါရှင့်\n\n"
            "✅အမည်\n✅ဖုန်းနံပါတ်\n✅TrueMoney နံပါတ်"
        )
        return

    if fast_intent == "signup":
        print("REPLY: signup")
        send_text_message(psid, ACCOUNT_OPENING_REQUEST)
        return

    print("REPLY: none")
    return


@app.route("/", methods=["GET"])
def home():
    return "Messenger bot is running", 200


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

                message = messaging_event.get("message", {})
                text = message.get("text")

                if text:
                    handle_user_message(psid, text)

        return "EVENT_RECEIVED", 200

    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))