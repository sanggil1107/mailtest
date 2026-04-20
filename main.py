from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import RedirectResponse
import msal
import requests
import certifi
import json
import os
import time
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
API_KEY = os.getenv("API_KEY")


TENANT_ID = "consumers"

REDIRECT_URI = os.getenv("REDIRECT_URI")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["Mail.Read", "User.Read"]

TOKEN_FILE = "/tmp/token.json"

session = requests.Session()
session.verify = False
msal_app = msal.ConfidentialClientApplication(
    CLIENT_ID,
    authority=AUTHORITY,
    client_credential=CLIENT_SECRET,
    http_client=session
)
# 1. 로그인 요청
@app.get("/")
def login():

    auth_url = msal_app.get_authorization_request_url(
        SCOPES,
        redirect_uri=REDIRECT_URI,
        prompt="login"
    )

    return RedirectResponse(auth_url)

# 2. 로그인 후 callback
@app.get("/callback")
def callback(code: str):
    token = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

    if "access_token" not in token:
        return {"error": token}

    # 만료 시간 저장
    token["expires_at"] = time.time() + token["expires_in"]

    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f)

    return {"message": "로그인 완료"}

def get_token():
    if not os.path.exists(TOKEN_FILE):
        raise HTTPException(status_code=401, detail="로그인 필요")

    with open(TOKEN_FILE, "r") as f:
        token = json.load(f)

    # 만료 체크
    if time.time() > token.get("expires_at", 0):
        raise HTTPException(status_code=401, detail="토큰 만료 (재로그인 필요)")

    return token  


@app.get("/mail")
def get_mails(x_api_key: str = Header(None)):
    # 🔐 API Key 체크 (헤더 방식)
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized")

    token = get_token()

    headers = {
        "Authorization": f"Bearer {token['access_token']}"
    }

    url = "https://graph.microsoft.com/v1.0/me/messages?$top=5&orderby=receivedDateTime desc&select=subject,bodyPreview,receivedDateTime,from"

    res = requests.get(url, headers=headers,verify=certifi.where(),timeout=30)

    if res.status_code != 200:
        raise HTTPException(status_code=500, detail=res.text)

    data = res.json()

    mails = []
    for item in data.get("value", []):
        mails.append({
            "subject": item.get("subject", ""),
            "bodyPreview": item.get("bodyPreview", ""),
            "receivedDateTime": item.get("receivedDateTime", ""),
            "from": item.get("from", {}).get("emailAddress", {}).get("address", "")
        })

    return {
        "count": len(mails),
        "mails": mails
    }


@app.get("/mails")
def get_mails(
    x_api_key: str = Header(None),
    sender: str = None,
    top: int = 20
):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized")

    token = get_token()

    headers = {
        "Authorization": f"Bearer {token['access_token']}"
    }

    # ✅ Inbox만 조회
    url = (
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
        f"?$top={top}"
        "&$orderby=receivedDateTime desc"
        "&$select=id,subject,bodyPreview,receivedDateTime,from"
    )

    res = requests.get(
        url,
        headers=headers,
        verify=certifi.where(),
        timeout=30
    )

    if res.status_code != 200:
        raise HTTPException(status_code=500, detail=res.text)

    data = res.json()

    mails = []
    for item in data.get("value", []):
        mails.append({
            "id": item.get("id", ""),
            "subject": item.get("subject", ""),
            "bodyPreview": item.get("bodyPreview", ""),
            "receivedDateTime": item.get("receivedDateTime", ""),
            "from": item.get("from", {}).get("emailAddress", {}).get("address", "")
        })

    # ✅ 발신자 필터
    if sender:
        mails = [
            m for m in mails
            if sender.lower() in (m.get("from", "") or "").lower()
        ]

    # ✅ 2건만 선택
    mails = mails[:2]

    return {
        "count": len(mails),
        "mails": mails
    }

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# ==============================
# 🔑 토큰 로드
# ==============================
def load_access_token():
    if not os.path.exists("token.json"):
        raise Exception("❌ token.json 파일이 없습니다 (Railway에 없음 가능성)")

    with open("token.json", "r", encoding="utf-8") as f:
        token_data = json.load(f)

    access_token = token_data.get("access_token")

    if not access_token:
        raise Exception("❌ access_token 이 없습니다")

    return access_token


# ==============================
# 📡 Graph API 공통 호출
# ==============================
def graph_get(url, access_token, params=None):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    res = requests.get(url, headers=headers, params=params, timeout=30)
    return res


# ==============================
# 🧪 1. token.json 존재 확인
# ==============================
@app.get("/debug/token")
def debug_token():
    return {
        "exists": os.path.exists("token.json"),
        "cwd": os.getcwd(),
        "files": os.listdir(".")
    }


# ==============================
# 🧪 2. 로그인 계정 확인
# ==============================
@app.get("/debug/me")
def debug_me():
    try:
        access_token = load_access_token()

        res = graph_get(f"{GRAPH_BASE}/me", access_token)

        return {
            "status_code": res.status_code,
            "response": res.json() if res.status_code == 200 else res.text
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==============================
# 🧪 3. 받은편지함 정보 확인
# ==============================
@app.get("/debug/inbox")
def debug_inbox():
    try:
        access_token = load_access_token()

        res = graph_get(f"{GRAPH_BASE}/me/mailFolders/inbox", access_token)

        return {
            "status_code": res.status_code,
            "response": res.json() if res.status_code == 200 else res.text
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==============================
# 🧪 4. 최신 메일 5건
# ==============================
@app.get("/debug/mails")
def debug_mails():
    try:
        access_token = load_access_token()

        params = {
            "$top": 5,
            "$orderby": "receivedDateTime DESC",
            "$select": "id,subject,from,receivedDateTime,bodyPreview"
        }

        res = graph_get(
            f"{GRAPH_BASE}/me/mailFolders/inbox/messages",
            access_token,
            params
        )

        return {
            "status_code": res.status_code,
            "count": len(res.json().get("value", [])) if res.status_code == 200 else 0,
            "response": res.json() if res.status_code == 200 else res.text
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==============================
# 🧪 5. 특정 발신자 메일 2건
# ==============================
@app.get("/debug/mails/by-sender")
def debug_mails_by_sender(sender_email: str):
    try:
        access_token = load_access_token()

        params = {
            "$top": 10,
            "$orderby": "receivedDateTime DESC",
            "$select": "id,subject,from,receivedDateTime",
            "$filter": f"from/emailAddress/address eq '{sender_email}'"
        }

        res = graph_get(
            f"{GRAPH_BASE}/me/mailFolders/inbox/messages",
            access_token,
            params
        )

        data = res.json() if res.status_code == 200 else {}

        return {
            "status_code": res.status_code,
            "count": len(data.get("value", [])[:2]),
            "mails": data.get("value", [])[:2],
            "raw": data if res.status_code == 200 else res.text
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # # 3. 메일 가져오기
    # url = "https://graph.microsoft.com/v1.0/me/messages?$top=2&$orderby=receivedDateTime desc&select=subject,bodyPreview"
    # res = requests.get(
    #     url,
    #     headers=headers,
    #     verify=certifi.where()
    # )

    # data = res.json()



    # return data
