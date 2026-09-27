# Video Publisher — v1

Personal PWA for OnePlus 12R: share a video from your editor's Share sheet,
add a caption, publish to YouTube + Instagram. Zero cost at your volume
(see the free-tier math already worked out — this just implements it).

## What's here
- `webapp/` — the installable PWA (this is what you host and add to your
  home screen). Plain HTML/JS, no build step.
- `backend/` — Flask service that holds your OAuth tokens and talks to
  YouTube's and Instagram's APIs. Deploy this separately.

## One-time setup (do these in order)

### 1. Google Cloud (YouTube)
1. Create a project at console.cloud.google.com, enable "YouTube Data API v3".
2. Credentials -> Create OAuth client ID -> Web application.
3. Authorized redirect URI: `https://YOUR-BACKEND-URL/auth/youtube/callback`
   (you won't know this URL until step 4 — come back and fill it in after).
4. Copy the client ID and secret into `.env`.

### 2. Meta for Developers (Instagram)
1. Convert your Instagram to a **Business or Creator account** if it isn't
   already (Instagram app -> Settings -> Account type) — the API refuses
   personal accounts.
2. Link that Instagram account to a Facebook Page you control.
3. Create an app at developers.facebook.com, add the "Instagram Graph API"
   product.
4. Under App Roles, add yourself as a **Test user** — this lets you use the
   API on your own account indefinitely without Meta's app review process.
5. Use the Graph API Explorer to find your Instagram Business Account ID:
   `GET /me/accounts` (find your Page), then
   `GET /{page-id}?fields=instagram_business_account`.
6. Copy the App ID, App Secret, and the Instagram Business Account ID into `.env`.

### 3. Cloudinary (video staging — Instagram needs a public URL, not a raw file)
1. Sign up free at cloudinary.com.
2. Copy Cloud Name, API Key, API Secret from the dashboard into `.env`.

### 4. Deploy the backend
1. Push `backend/` to a GitHub repo.
2. Create a free Web Service on Render or Railway pointing at it.
   Build command: `pip install -r requirements.txt`
   Start command: `gunicorn app:app` (add `gunicorn` to requirements.txt for production)
3. Add all the `.env` values as environment variables in Render/Railway's
   dashboard (don't commit your real `.env` file).
4. Once deployed, copy the live URL and:
   - go back to Google Cloud and set the real redirect URI
   - set `BACKEND_URL` in your environment variables to this URL

### 5. Configure and host the webapp
1. Open `webapp/app.js` and set `API_BASE` to your backend's live URL.
2. Host `webapp/` anywhere that serves static files over HTTPS — GitHub
   Pages, Netlify, Vercel, or Render's static site option all have free
   tiers. HTTPS is mandatory — Share Target and service workers won't
   work over plain HTTP.

### 6. Install it on your phone
1. Open the hosted webapp URL in Chrome on your OnePlus 12R.
2. Chrome menu -> "Add to Home screen" / "Install app".
3. Open the app once, tap Connect for YouTube and Instagram, complete
   the OAuth flow for each.
4. From now on: edit your video -> Share -> "Video Publisher" shows up in
   the share sheet -> caption -> Publish.

## Known limitations of this v1 (intentional, not oversights)
- Single video at a time — batching was decided against for v1 (see the
  reasoning already discussed: adds queue/retry complexity for a workflow
  that's already ~10 seconds with the share target).
- Instagram token needs manual reconnect roughly every 60 days (long-lived
  token expiry) — there's no auto-refresh flow built yet.
- No post-publish history/log screen yet.
- `tokens.json` is a flat file, fine for one user. If this ever becomes
  multi-user, swap `load_tokens()`/`save_tokens()` in `app.py` for real
  database calls — nothing else in the route logic needs to change.
