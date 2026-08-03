# Geomaps — Street View recorder + Route Companion PWA

Tools for a motorbike (or car) trip from **Slawi / Tegal → Yogyakarta**: record Street View into video, find SPBU, and track yourself on the road with nearby places.

## Prerequisites

1. **Python 3.10+**
2. **FFmpeg** on `PATH` (for video) — Windows: `winget install ffmpeg`
3. **Google Cloud** project with:
   - [Routes API](https://console.cloud.google.com/apis/library/routes.googleapis.com) enabled
   - [Places API (New)](https://console.cloud.google.com/apis/library/places.googleapis.com) enabled (Route Companion nearby places)
   - [Street View Static API](https://console.cloud.google.com/apis/library/street-view-image-backend.googleapis.com) enabled (Street View recorder only)
   - An API key in `.env` (allow Routes + Places + Street View if the key is restricted)

## Setup

```bash
pip install -r requirements.txt
copy .env.example .env
```

Set `GOOGLE_MAPS_API_KEY` in `.env`.

---

## Route Companion PWA (GPS + nearby places)

Phone-friendly app: load the motorbike route, track GPS, and list nearby food, markets, beaches, SPBU, and interesting spots via **Google Places API**.

```bash
python app.py
```

Or with **PM2** (keeps the server running):

```bash
pm2 start ecosystem.config.cjs
pm2 status
pm2 logs geomaps
pm2 restart geomaps
pm2 stop geomaps
```

### Deploy / auto-restart on server

After you push to GitHub (or your remote), on the **server**:

```bash
cd /path/to/geomaps
chmod +x deploy.sh
./deploy.sh
```

That runs `git pull`, installs deps if needed, then `pm2 restart ecosystem.config.cjs`.

To restart automatically whenever you `git pull` on the server (one-time setup):

```bash
chmod +x scripts/install-git-hooks.sh
bash scripts/install-git-hooks.sh
```

Then:

```bash
git pull   # post-merge hook → pm2 restart geomaps
```

Open **http://127.0.0.1:5000** on your PC, or `http://<your-pc-lan-ip>:5000` on your phone (same Wi‑Fi).

1. Tap ⚙ → confirm origin / destination → **Load route & places**
2. **Start GPS** on the phone (or **Simulate** on desktop)
3. Filter categories and radius (1 / 2 / 5 km) in the bottom sheet
4. Install as a PWA from the browser menu when prompted

### GPS / HTTPS note

Browsers often require **HTTPS** (or `localhost`) for geolocation. For on-road phone use over LAN:

- Use a tunnel (Cloudflare Tunnel, ngrok, etc.), or
- Serve with a local trusted cert (mkcert)

Route + places are cached in `localStorage` for weak signal after the first successful load.

---

## Street View recorder

```bash
python record_streetview.py
```

Default: motorbike route, sample every **250 m**, first **100 km**, then FFmpeg → `output/streetview.mp4`.

| Flag | Default | Description |
|------|---------|-------------|
| `--mode` | `motorbike` | `motorbike` or `car` |
| `--interval` | `250` | Sample spacing (m) |
| `--max-distance` | `100000` | Max route distance (m) |
| `--preview-only` | off | Show route / HTML map, then exit |
| `--yes` | off | Skip confirm prompt |
| `--skip-existing` | off | Resume frame downloads |
| `--frames-only` / `--video-only` | off | Partial pipeline |

```bash
python record_streetview.py --preview-only
python record_streetview.py --yes --max-distance 230000 --skip-existing
```

Slow video from existing frames (1.5 s each):

```bash
python make_video.py
```

## SPBU along route

```bash
python find_spbu.py --source osm
```

Writes `output/spbu.json` and `output/spbu_map.html`.

## Cost note

Street View Static API, Routes API, and Places API (New) are billed by Google. Loading the companion trip samples Places Nearby Search along the route — avoid repeated full reloads on long trips.
