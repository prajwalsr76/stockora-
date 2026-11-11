# Putting Stockora online for free

Two free services, no credit card:

| Part | Where | What you get |
|---|---|---|
| Website (React) | **Cloudflare Pages** | `https://stockora.pages.dev`, always on, fast everywhere |
| Server (FastAPI + forecaster) | **Render**, free web service | `https://stockora-api.onrender.com` |

Both build straight from the **GitHub repository**, so every change pushed there goes live by itself.

## What to expect from the free plan

- The Render server **sleeps after 15 minutes** without visitors. The next visitor sees a "Starting the demo server" screen for about a minute, then everything is fast.
- The server has 512 MB of memory and a tenth of a CPU. Stockora uses about 230–290 MB. Training would take minutes there, so the demo shop and its forecasts are built **while Render builds the image** and saved inside it.
- **Visitors' changes are not kept.** Each time the server wakes it starts again from the saved demo shop. That is right for a public demo; a real shop needs paid hosting with a disk or a database.
- A GitHub Action rebuilds the server every night (02:15 India time), so the demo's sample sales always end on today's date.
- The demo is public. Anyone with the link can use the demo shop and try the setup wizard.

## One-time setup (about 20 minutes)

### 1. GitHub (where the code lives)

1. Go to <https://github.com/signup> and create a free account (skip if you have one).
2. Click **+** (top right) → **New repository**, give it a name (this project uses `stockora-`), and leave "Add a README" unticked. Public or private both work with Render and Cloudflare.
3. Push the project to it (the repository already contains everything below).

The repository contains no passwords or keys: `.env`, databases and data folders are excluded by `.gitignore`.

### 2. Render (the server)

1. Go to <https://dashboard.render.com/register> and sign up with **GitHub** (the same account).
2. Click **New → Blueprint**. Pick the `stockora-` repository (click **Configure GitHub** if it isn't listed and allow access to it).
3. Render reads `render.yaml` and shows one service, `stockora-api`, plan **Free**, region **Singapore**. For `GROQ_API_KEY`, leave it **empty** for now (see "AI answers" below). Click **Deploy Blueprint**.
4. The first build takes about 5–10 minutes (it installs packages and builds the demo shop). When the service shows **Live**, open its URL and add `/health` to the end, for example `https://stockora-api.onrender.com/health`. You should see `{"ok":true,...}`.
5. Copy the service URL from the top of the service page. If it is not exactly `https://stockora-api.onrender.com`, note the real one for step 3.

### 3. Cloudflare Pages (the website)

1. Go to <https://dash.cloudflare.com/sign-up> and create a free account.
2. In the left menu open **Workers & Pages**, click **Create**, choose the **Pages** tab, then **Connect to Git** (the menu names can move a little; look for "Pages" and "Connect to Git"). Connect GitHub and pick `stockora-`.
3. Build settings:
   - Project name: `stockora`
   - Production branch: `main`
   - Framework preset: **None**
   - Build command: `npm run build`
   - Build output directory: `dist`
   - Root directory (Advanced): `frontend`
   - Environment variables: `VITE_API_URL` = your Render URL from step 2.5 (no slash at the end), and `NODE_VERSION` = `22`
4. Click **Save and Deploy**. After 1–2 minutes the site is at `https://stockora.pages.dev` (or the name Cloudflare shows if `stockora` was taken).
5. **If your Pages address is not `stockora.pages.dev`:** in Render → `stockora-api` → **Environment**, change `CORS_ORIGINS` to your Pages address and `CORS_ORIGIN_REGEX` to match it (for `stockora-abc.pages.dev`: `https://[a-z0-9-]+\.stockora-abc\.pages\.dev`). Save; Render restarts by itself.

### 4. Nightly demo refresh (optional, recommended)

1. Render → `stockora-api` → **Settings → Deploy Hook** → copy the URL. It is secret; don't paste it into chats.
2. GitHub → `stockora-` repository → **Settings → Secrets and variables → Actions → New repository secret**. Name: `RENDER_DEPLOY_HOOK`, value: the URL. Save.
3. To test: **Actions** tab → **Refresh online demo** → **Run workflow**. Render starts a new build.

## AI answers on the public demo

Without `GROQ_API_KEY` the chatbot uses its offline answers, and bill photo reading is off. Adding the key on Render (Environment → `GROQ_API_KEY`) turns the full AI on, but **every visitor then uses your free Groq quota**. For a public link, leave it off, or turn it on only while you show the demo to someone.

## Checking that it works

1. Open the Pages address. The welcome page appears straight away.
2. Click **Try the demo shop**. If the server was asleep you see "Starting the demo server" for up to about a minute, then the overview.
3. Switch to Hindi, make a bill, try **Set up my shop** with `frontend/e2e/fixtures/vyapar_items.csv`.

## Updating the live site

Changes pushed to the `main` branch of the GitHub repository go live by themselves: the website in about 2 minutes, the server in about 5–10 (only when files under `backend/` changed).

## Costs

Everything above is on free plans: Cloudflare Pages (unlimited bandwidth, 500 builds a month), Render (750 free instance hours a month, enough for one service all month), GitHub (private repositories and about 2,000 Action minutes a month; the nightly refresh uses well under a minute a day). Free plans can change; check the providers' pricing pages if something stops working.
