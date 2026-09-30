"""
Video Publisher backend.

Handles OAuth for YouTube + Instagram, stages the video on Cloudinary
(Instagram's API requires a public URL, not a raw upload), then publishes
to both platforms.

Instagram uses the "Instagram Login" flow (Business Login for Instagram) --
simpler than Facebook Login for Business for a single personal account:
no Facebook Page required, no business verification, token comes straight
from instagram.com.

Token storage is a single JSON file keyed by user id ("me" for now).
That's intentional: swap load_tokens()/save_tokens() for real DB calls
later without touching any of the route logic above them.
"""
import json
import os
import tempfile
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
CORS(app)  # tighten this to your PWA's origin once deployed
app.secret_key = os.environ["FLASK_SECRET_KEY"]
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


def load_tokens():
    if TOKENS_PATH.exists():
        return json.loads(TOKENS_PATH.read_text())
    return {}


def save_tokens(data):
    TOKENS_PATH.write_text(json.dumps(data, indent=2))


# ---------------- Status ----------------

@app.route("/api/status")
def status():
    tokens = load_tokens().get(USER_ID, {})
    return jsonify({
        "youtube_connected": "youtube" in tokens,
        "instagram_connected": "instagram" in tokens,
    })


# ---------------- YouTube OAuth ----------------

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
    # PKCE: the verifier generated here must survive until the callback,
    # since each request creates a brand new Flow object.
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
    return "YouTube connected. You can close this tab."


def get_youtube_client():
    yt_tokens = load_tokens()[USER_ID]["youtube"]
    creds = Credentials(**yt_tokens)
    return build("youtube", "v3", credentials=creds)


# ---------------- Instagram OAuth (Instagram Login flow) ----------------
# Uses instagram.com directly -- no Facebook Page, no Business Login,
# no business verification. Requires the Instagram account to be a
# Business or Creator account, and the app's "Instagram API setup with
# Instagram login" product to be configured with this redirect URI.

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

    # Exchange for a long-lived token (~60 days) so you're not reconnecting often.
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
    return "Instagram connected. You can close this tab."


# ---------------- Publish ----------------

@app.route("/api/upload", methods=["POST"])
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
                # Instagram needs a public URL, not the raw file -> stage on Cloudinary first.
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

                publish_res = requests.post(
                    f"https://graph.instagram.com/v19.0/{ig_user_id}/media_publish",
                    data={"creation_id": creation_id, "access_token": ig_token},
                ).json()
                results["instagram"] = "ok" if "id" in publish_res else publish_res

                # Clean up the staged copy now that Instagram has ingested it.
                cloudinary.uploader.destroy(upload_result["public_id"], resource_type="video")
            except Exception as e:
                results["instagram"] = f"failed: {e}"
    finally:
        os.remove(tmp_path)

    return jsonify({"results": results, "message": str(results)})


if __name__ == "__main__":
    app.run(debug=True, port=5000)