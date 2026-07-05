import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response

app = FastAPI()


# Automatically load .env file if it exists in the project root
def load_dotenv():
    # File is at src/fluxion/channels/line/dev_line_webhook.py
    # Project root is 4 levels up
    root = Path(__file__).resolve().parents[4]
    env_path = root / ".env"
    if env_path.exists():
        print(f"Loading environment from {env_path}")
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            # Only load if not already set in active process environment
            if k not in os.environ:
                os.environ[k] = v


load_dotenv()

LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_ALLOWED_USERS = set(
    x.strip() for x in os.environ.get("FLUXION_LINE_ALLOWED_USERS", "").split(",") if x.strip()
)


# Allow the script to run even if variables are not set yet, so it doesn't crash on import during setup.
def check_config():
    global LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN, LINE_ALLOWED_USERS
    LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
    LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    LINE_ALLOWED_USERS = set(
        x.strip() for x in os.environ.get("FLUXION_LINE_ALLOWED_USERS", "").split(",") if x.strip()
    )
    if not LINE_CHANNEL_SECRET:
        print("[Warning] LINE_CHANNEL_SECRET is not set in environment variables or .env.")
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[Warning] LINE_CHANNEL_ACCESS_TOKEN is not set in environment variables or .env.")
    if LINE_ALLOWED_USERS:
        print(f"[Info] LINE User Whitelist active: {LINE_ALLOWED_USERS}")


def verify_line_signature(body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    if not LINE_CHANNEL_SECRET:
        print("[Error] Cannot verify signature: LINE_CHANNEL_SECRET is not set.")
        return False
    hash_val = hmac.new(LINE_CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(hash_val).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def reply_line(reply_token: str, text: str) -> None:
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[Error] Cannot reply to LINE: LINE_CHANNEL_ACCESS_TOKEN is not set.")
        return
    url = "https://api.line.me/v2/bot/message/reply"
    payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8")
        raise RuntimeError(f"LINE reply failed: {exc.code} {err_body}") from exc


@app.get("/health")
def health():
    return Response(content="ok", media_type="text/plain")


@app.post("/line/webhook")
async def line_webhook(request: Request, x_line_signature: str = Header(None)):
    check_config()
    body = await request.body()
    if not verify_line_signature(body, x_line_signature):
        print("Invalid LINE signature")
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json") from None

    print("LINE webhook body:", json.dumps(payload, indent=2))

    events = payload.get("events") or []
    for event in events:
        if event.get("type") != "message":
            continue
        message = event.get("message") or {}
        if message.get("type") != "text":
            continue

        source = event.get("source") or {}
        user_id = source.get("userId")
        text = message.get("text")
        reply_token = event.get("replyToken")

        if not user_id:
            continue

        if LINE_ALLOWED_USERS and user_id not in LINE_ALLOWED_USERS:
            print(f"Unauthorized LINE user: {user_id}")
            continue

        print(
            "LINE message:",
            {
                "userId": user_id,
                "text": text,
                "replyToken": reply_token,
                "webhookEventId": event.get("webhookEventId"),
            },
        )

        if not text or not reply_token:
            continue

        clean_text = text.strip().lower()
        try:
            if clean_text == "ping":
                reply_line(reply_token, "pong")
            else:
                reply_line(reply_token, f"Fluxion received: {text}")
        except Exception as exc:
            print(f"Failed to reply to LINE message: {exc}")

    return Response(content="ok", media_type="text/plain")


if __name__ == "__main__":
    check_config()
    port = int(os.environ.get("PORT", 8766))
    print(f"LINE webhook listening on http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port)
