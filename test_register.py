import requests
import uuid

PHONE = "5585225546"
USERNAME = "testscb001"
PASSWORD = "Aa123456"
FIRST_NAME = "mg"
LAST_NAME = "mura"
BANK_ID = 5
BANK_NAME = "ธนาคารไทยพาณิชย์"
BANK_ACCOUNT_NUMBER = "55467886555"
BANK_ACCOUNT_NAME = "mg mura"

COMMON_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "agent-code": "KRUE",
    "authorization": "Bearer",
    "company-code": "XONE",
    "content-type": "application/json",
    "force-user-agent": "Website",
    "origin": "https://ngwe99.co",
    "referer": "https://ngwe99.co/",
    "user-agent": "Mozilla/5.0",
}

def validate_phone(phone: str) -> str:
    url = "https://api.noproky.net/lobby/auth/register/phone/validate"
    payload = {
        "phone": phone,
        "ref_code": "",
        "captcha_provider": "turnstile",
        "captcha_token": "TEST_TOKEN"
    }

    r = requests.post(url, json=payload, headers=COMMON_HEADERS, timeout=30)
    print("VALIDATE STATUS:", r.status_code)
    print("VALIDATE RESPONSE:", r.text)
    r.raise_for_status()

    data = r.json()
    return data["data"]["verified_token"]

def register_account(verified_token: str):
    url = "https://api.noproky.net/lobby/auth/register"
    event_id = f"CompleteRegistration-{uuid.uuid4()}"

    payload = {
        "livechat_identifier": "",
        "phone": PHONE,
        "whatsapp_number": "",
        "otp": "",
        "otp_ref": "",
        "ref_code": None,
        "ref_code_agent": None,
        "verified_token": verified_token,
        "first_name": FIRST_NAME,
        "last_name": LAST_NAME,
        "username": USERNAME,
        "password": PASSWORD,
        "confirm_password": PASSWORD,
        "bank_id": BANK_ID,
        "bank_name": BANK_NAME,
        "bank_account_number": BANK_ACCOUNT_NUMBER,
        "bank_account_name": BANK_ACCOUNT_NAME,
        "gender": "male",
        "event_id": event_id,
        "fbc": None,
        "fbp": None,
        "action_source": "website",
    }

    r = requests.post(url, json=payload, headers=COMMON_HEADERS, timeout=30)
    print("REGISTER STATUS:", r.status_code)
    print("REGISTER RESPONSE:", r.text)

if __name__ == "__main__":
    token = validate_phone(PHONE)
    print("VERIFIED TOKEN:", token)
    register_account(token)