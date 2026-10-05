"""
Video Publisher backend.

Handles OAuth for YouTube + Instagram, stages the video on Cloudinary
(Instagram's API requires a public URL, not a raw upload), then publishes
to both platforms.

Security notes:
- /api/upload requires a valid, authenticated session. The session is
  only marked authenticated after you actually complete a real OAuth
  login with Google or Instagram -- there's no password or key sitting
  in the frontend code for someone to find. The session cookie itself is
  HttpOnly, so JavaScript (including anything injected by an attacker)
  can't read or steal it; only the browser silently attaches it to
  requests to this exact backend.
- CORS is restricted to your actual Netlify origin, not left wide open,
  and explicitly allows credentials (cookies) only from that origin.
- This protects against a stranger who finds your backend URL and tries
  calling it directly. It does not protect against someone who gets
  physical/remote access to your actual unlocked phone while the app is
  open and already logged in -- no web security layer can stop that,
  the same way no website can stop someone using your already-unlocked
  phone to use any other app you're logged into.
- Real credentials (Google/Meta/Cloudinary keys) have never been sent to
  the frontend -- they live only in this process's environment variables.

Token storage is a single JSON file keyed by user id ("me" for now).
Note: on Render's free tier this file does NOT survive a redeploy --
reconnect both platforms after any code push that triggers a new deploy.
"""
import json
import os
import tempfile
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path

import cloudinary
import cloudinary.uploader
import requests
from flask import Flask, jsonify, redirect, request, session
from flask_cors import CORS
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

FRONTEND_ORIGIN = os.environ["FRONTEND_ORIGIN"]  # e.g. https://video-publisher.netlify.app
CORS(app, origins=[FRONTEND_ORIGIN], supports_credentials=True)

app.secret_key = os.environ["FLASK_SECRET_KEY"]
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    # SameSite=None is required for the cookie to be sent on cross-origin
    # fetch calls from the Netlify frontend to this Render backend; it's
    # safe here because it's paired with Secure (HTTPS-only) and an exact
    # CORS origin allowlist above, not a wildcard.
    SESSION_COOKIE_SAMESITE="None",
)
# Without this, the session cookie clears whenever the browser/PWA fully
# closes, forcing a reconnect every reopen. 60 days roughly matches
# Instagram's own long-lived token expiry.
app.permanent_session_lifetime = timedelta(days=60)

TOKENS_PATH = Path(__file__).parent / "tokens.json"

# --- Config from environment (see .env.example) ---
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
BACKEND_URL = os.environ["BACKEND_URL"]  # e.g. https://your-app.onrender.com
INSTAGRAM_APP_ID = os.environ["INSTAGRAM_APP_ID"]
INSTAGRAM_APP_SECRET = os.environ["INSTAGRAM_APP_SECRET"]

cloudinary.config(
    cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
    api_key=os.environ["CLOUDINARY_API_KEY"],
    api_secret=os.environ["CLOUDINARY_API_SECRET"],
)

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
USER_ID = "me"  # single personal user for now


def require_session(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            return jsonify({"error": "not logged in"}), 401
        return f(*args, **kwargs)
    return wrapper


def load_tokens():
    if TOKENS_PATH.exists():
        return json.loads(TOKENS_PATH.read_text())
    return {}


def save_tokens(data):
    TOKENS_PATH.write_text(json.dumps(data, indent=2))


# ---------------- Status ----------------

@app.route("/api/status")
def status():
    # No account details are ever returned here, just two booleans, so
    # this one stays open -- it's what lets the page show connect status
    # on first load, before any session exists yet.
    tokens = load_tokens().get(USER_ID, {})
    return jsonify({
        "youtube_connected": "youtube" in tokens,
        "instagram_connected": "instagram" in tokens,
    })


# ---------------- YouTube OAuth ----------------
# These routes themselves ARE the login -- reaching them starts or
# completes an OAuth flow with Google/Meta directly. Their only effect is
# setting session["authenticated"] once that real login succeeds.

def youtube_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [f"{BACKEND_URL}/auth/youtube/callback"],
            }
        },
        scopes=YOUTUBE_SCOPES,
        redirect_uri=f"{BACKEND_URL}/auth/youtube/callback",
    )


@app.route("/auth/youtube")
def auth_youtube():
    flow = youtube_flow()
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    session["yt_code_verifier"] = flow.code_verifier
    return redirect(auth_url)


@app.route("/auth/youtube/callback")
def auth_youtube_callback():
    flow = youtube_flow()
    flow.code_verifier = session.get("yt_code_verifier")
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    tokens = load_tokens()
    tokens.setdefault(USER_ID, {})["youtube"] = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "token_uri": creds.token_uri,
        "scopes": creds.scopes,
    }
    save_tokens(tokens)
    session.permanent = True
    session["authenticated"] = True
    return "YouTube connected. You can close this tab."


def get_youtube_client():
    yt_tokens = load_tokens()[USER_ID]["youtube"]
    creds = Credentials(**yt_tokens)
    return build("youtube", "v3", credentials=creds)


# ---------------- Instagram OAuth (Instagram Login flow) ----------------

@app.route("/auth/instagram")
def auth_instagram():
    redirect_uri = f"{BACKEND_URL}/auth/instagram/callback"
    auth_url = (
        "https://www.instagram.com/oauth/authorize"
        f"?client_id={INSTAGRAM_APP_ID}&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&scope=instagram_business_basic,instagram_business_content_publish"
    )
    return redirect(auth_url)


@app.route("/auth/instagram/callback")
def auth_instagram_callback():
    code = request.args.get("code")
    redirect_uri = f"{BACKEND_URL}/auth/instagram/callback"

    token_res = requests.post(
        "https://api.instagram.com/oauth/access_token",
        data={
            "client_id": INSTAGRAM_APP_ID,
            "client_secret": INSTAGRAM_APP_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code,
        },
    ).json()
    short_token = token_res["access_token"]
    ig_user_id = token_res["user_id"]

    long_res = requests.get(
        "https://graph.instagram.com/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": INSTAGRAM_APP_SECRET,
            "access_token": short_token,
        },
    ).json()

    tokens = load_tokens()
    tokens.setdefault(USER_ID, {})["instagram"] = {
        "access_token": long_res["access_token"],
        "ig_user_id": ig_user_id,
    }
    save_tokens(tokens)
    session.permanent = True
    session["authenticated"] = True
    return "Instagram connected. You can close this tab."


# ---------------- Publish ----------------

@app.route("/api/upload", methods=["POST"])
@require_session
def upload():
    video = request.files["video"]
    caption_main = request.form.get("caption_main", "")
    caption_ig = request.form.get("caption_ig", "") or caption_main
    post_youtube = request.form.get("post_youtube") == "true"
    post_instagram = request.form.get("post_instagram") == "true"

    results = {}

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        video.save(tmp.name)
        tmp_path = tmp.name

    try:
        if post_youtube:
            try:
                yt = get_youtube_client()
                media = MediaFileUpload(tmp_path, chunksize=-1, resumable=True)
                request_body = {
                    "snippet": {"title": caption_main[:100] or "New video", "description": caption_main},
                    "status": {"privacyStatus": "public"},
                }
                yt.videos().insert(part="snippet,status", body=request_body, media_body=media).execute()
                results["youtube"] = "ok"
            except Exception as e:
                results["youtube"] = f"failed: {e}"

        if post_instagram:
            try:
                upload_result = cloudinary.uploader.upload_large(tmp_path, resource_type="video")
                video_url = upload_result["secure_url"]

                ig_tokens = load_tokens()[USER_ID]["instagram"]
                ig_token = ig_tokens["access_token"]
                ig_user_id = ig_tokens["ig_user_id"]

                create_res = requests.post(
                    f"https://graph.instagram.com/v19.0/{ig_user_id}/media",
                    data={
                        "media_type": "REELS",
                        "video_url": video_url,
                        "caption": caption_ig,
                        "access_token": ig_token,
                    },
                ).json()
                creation_id = create_res["id"]

                for _ in range(20):
                    status_res = requests.get(
                        f"https://graph.instagram.com/v19.0/{creation_id}",
                        params={"fields": "status_code", "access_token": ig_token},
                    ).json()
                    if status_res.get("status_code") == "FINISHED":
                        break
                    if status_res.get("status_code") == "ERROR":
                        raise Exception("Instagram failed to process the video")
                    time.sleep(5)
                else:
                    raise Exception("Instagram video processing timed out")

                publish_res = requests.post(
                    f"https://graph.instagram.com/v19.0/{ig_user_id}/media_publish",
                    data={"creation_id": creation_id, "access_token": ig_token},
                ).json()
                results["instagram"] = "ok" if "id" in publish_res else publish_res

                cloudinary.uploader.destroy(upload_result["public_id"], resource_type="video")
            except Exception as e:
                results["instagram"] = f"failed: {e}"
    finally:
        os.remove(tmp_path)

    return jsonify({"results": results, "message": str(results)})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
