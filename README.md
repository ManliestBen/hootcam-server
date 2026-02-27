# Hootcam Server

Backend for the Hootcam owl box camera system: **Raspberry Pi 5** dual CSI cameras, motion detection, and motion-triggered recording to SSD. REST API and SQLite for metadata. Use this repo together with a separate frontend (e.g. [**hootcam-ui**](https://github.com/ManliestBen/hootcam-ui)).

**Alternative: 3-part architecture.** If the Pi is overloaded, you can split into: **(1) [Hootcam Streamer](https://github.com/ManliestBen/hootcam-streamer)** on the Pi (RTSP only, no motion), **(2) [Hootcam Motion](https://github.com/ManliestBen/hootcam-motion)** on a NUC (consumes RTSP, runs motion + recording + API), and **(3) [Hootcam UI](https://github.com/ManliestBen/hootcam-ui)** (points at the NUC). This repo remains the **all-in-one** server for those who run everything on the Pi.

## Features

- **Dual cameras** – Uses both Pi 5 CSI ports (e.g. `Picamera2(0)` and `Picamera2(1)`).
- **Live streams** – MJPEG streams per camera for a frontend (`/cameras/0/stream`, `/cameras/1/stream`).
- **Motion detection** – Configurable threshold, noise level, event gap, pre/post capture.
- **Recording on motion** – Saves video (and optionally pictures) to SSD; metadata in SQLite.
- **REST API** – Configuration and control for cameras, detection, and recording.
- **SQLite** – Events, file records, and optional config storage; media files live on SSD.

## Requirements

- Raspberry Pi 5
- Two CSI cameras (with 15-pin FFC adapters if needed)
- SSD Pi HAT + SSD for recordings
- Raspberry Pi OS (Bookworm or later)

## Setup

On the Pi (in your clone of `hootcam-server`):

1. **Create a virtualenv with access to system packages** (required so the venv can use system-installed picamera2 from apt):

   ```bash
   cd /path/to/hootcam-server
   python3 -m venv .venv --system-site-packages
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Ensure picamera2 and ffmpeg are available.** Many Pi base images already include them. Check and install only if missing:

   ```bash
   # Check (optional)
   python3 -c "from picamera2 import Picamera2" 2>/dev/null && echo "picamera2 OK" || echo "need picamera2"
   ffmpeg -version &>/dev/null && echo "ffmpeg OK" || echo "need ffmpeg"

   # If either check fails, install:
   sudo apt update && sudo apt install -y python3-picamera2 ffmpeg
   ```

   Run the checks from the same environment you use to start the server (e.g. with the venv activated). If you see "need picamera2" even with the venv, ensure the venv was created with `--system-site-packages` (step 1).

3. **Storage (optional).** Recordings default to the current directory (SD card). To use an SSD: use the API (`GET /storage`, then `PATCH /storage` with `{ "use_auto_detected_ssd": true }` or `{ "path": "/mnt/ssd/hootcam-server" }`), or set `HOOTCAM_TARGET_DIR=/mnt/ssd/hootcam-server` before starting. Storage changes take effect after the next server restart.

## Running

```bash
source .venv/bin/activate
uvicorn hootcam_server.main:app --host 0.0.0.0 --port 8080
```

Use `--host 0.0.0.0` so the server accepts connections from other devices on the network (not just the Pi). Replace `<pi-ip>` below with the Pi's IP (e.g. `192.168.1.10`).

- **API & docs**: http://&lt;pi-ip&gt;:8080  
- **OpenAPI JSON**: http://&lt;pi-ip&gt;:8080/openapi.json  
- **Camera 0 stream**: http://&lt;pi-ip&gt;:8080/cameras/0/stream  
- **Camera 1 stream**: http://&lt;pi-ip&gt;:8080/cameras/1/stream  

All endpoints require **HTTP Basic auth**. Default user: `admin` / `admin`. Change the password via `PATCH /auth/password` (see [API docs](docs/API.md#authentication)).

## Running as a service (start on boot)

To run Hootcam Server automatically on the Pi after reboot:

**Prerequisite:** Complete [Setup](#setup) first, including creating the venv with `--system-site-packages` so that picamera2 is available when the service runs.

1. **Copy the systemd unit file** (from the repo root):
   ```bash
   sudo cp contrib/hootcam-server.service /etc/systemd/system/
   ```

2. **Edit the unit file** if the repo is not in `/home/pi/hootcam-server`:
   ```bash
   sudo nano /etc/systemd/system/hootcam-server.service
   ```
   Update `WorkingDirectory` and the path inside `ExecStart` to match your install (e.g. `/home/pi/hootcam-server`).

3. **Optional – recordings on SSD:** Recordings default to the SD card. To use an SSD, call `PATCH /storage` with `{ "use_auto_detected_ssd": true }` or set a path via the API; or create an environment file:
   ```bash
   echo 'HOOTCAM_TARGET_DIR=/mnt/ssd/hootcam-server' | sudo tee /etc/hootcam-server.env
   ```
   Then in `/etc/systemd/system/hootcam-server.service` uncomment the line:
   ```ini
   EnvironmentFile=/etc/hootcam-server.env
   ```

4. **Reload systemd, enable and start the service:**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable hootcam-server
   sudo systemctl start hootcam-server
   ```

5. **Check status and logs:**
   ```bash
   sudo systemctl status hootcam-server
   sudo journalctl -u hootcam-server -f
   ```

**Useful commands:**

| Command | Description |
|--------|-------------|
| `sudo systemctl stop hootcam-server` | Stop the service |
| `sudo systemctl start hootcam-server` | Start the service |
| `sudo systemctl restart hootcam-server` | Restart after config/code changes |
| `sudo systemctl disable hootcam-server` | Do not start on boot (service can still be started manually) |

The service runs as user `pi`. If you use a different user, change `User=` and `Group=` in the unit file and ensure that user can access the install directory and (if used) the SSD mount.  

## API overview

- **Global config** – `GET/PATCH /config` (target_dir, log_level, stream options, etc.)
- **Storage** – `GET /storage` (current path + auto-detected SSD), `PATCH /storage` (set path or use SSD)
- **Per-camera config** – `GET/PATCH /cameras/{id}/config` (width, height, framerate, threshold, movie_output, etc.)
- **Detection control** – `POST /cameras/{id}/detection/start`, `POST /cameras/{id}/detection/pause`
- **Events & files** – `GET /events`, `GET /events/{id}`, `GET /files` (from SQLite; media on SSD)
- **Streams** – `GET /cameras/{id}/stream`, `GET /cameras/{id}/current` (latest JPEG)

See the **OpenAPI documentation** at `/docs` for full parameter descriptions.

## Troubleshooting

### Can't reach the server from another PC (e.g. http://pi-ip:8080/cameras/0/stream)

If "site can't be reached" or connection refused from another machine:

1. **Start the server bound to all interfaces.** It must listen on `0.0.0.0`, not just localhost:
   ```bash
   uvicorn hootcam_server.main:app --host 0.0.0.0 --port 8080
   ```
   If you omit `--host 0.0.0.0`, uvicorn defaults to 127.0.0.1 and only the Pi can connect. The [systemd unit](contrib/hootcam-server.service) already uses `--host 0.0.0.0`.

2. **Allow port 8080 through the Pi's firewall.** On Raspberry Pi OS, if you use `ufw`:
   ```bash
   sudo ufw allow 8080/tcp
   sudo ufw status
   ```
   If you use `iptables` or another firewall, open TCP port 8080. (Many Pi setups have no firewall by default.)

3. **Check the Pi's IP** and that the other PC is on the same network:
   ```bash
   hostname -I
   ```
   From the other PC, try `ping <pi-ip>`. Then try `http://<pi-ip>:8080/` in a browser (you'll get a 401 or a JSON response if the server is reachable; the login prompt or API response means it's working).

4. **All endpoints require HTTP Basic auth.** In the browser, when you open the stream URL you'll be prompted for username and password (default `admin` / `admin`). For the UI, set `VITE_HOOTCAM_STREAMER_URL=http://<pi-ip>:8080` so the app talks to the Pi.

### "picamera2 not available; cameras disabled"

This means the Python process can't import `picamera2`. On the Pi, picamera2 is installed via `apt` (system-wide), but a **virtualenv does not see system packages** unless it was created with `--system-site-packages`.

**Fix:** Recreate the venv so it can use the system picamera2:

```bash
deactivate
rm -rf .venv
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -r requirements.txt
```

Then start the server again. Verify with:

```bash
python3 -c "from picamera2 import Picamera2; print('picamera2 OK')"
```

### Camera timeout / "RPI camera frontend has timed out"

If the sensor disconnects or the cable is loose, the camera stack can block and lock the app. The server now runs each capture in a thread with a **15 second timeout**. If a capture times out:

1. The app logs a warning and **attempts to restart that camera** once.
2. If timeouts continue (3 in a row), that camera is **marked failed** and the capture loop skips it so the rest of the app (and the other camera) keeps running.
3. Restart the server after fixing the cable/sensor to use that camera again.

### Low framerate on the live stream

- **Capture** uses per-camera `framerate` (default **15** fps).
- **Stream** (what you see in the UI) is limited by global **stream_maxrate** (default **15** fps). If the feed was very slow before, the saved config may have `stream_maxrate: 1`. In the UI go to **Config** and set "Stream max framerate" to 15 or higher (up to 100 for no limit).

## Project layout

```
hootcam-server/
├── README.md
├── requirements.txt
├── contrib/
│   ├── hootcam-server.service   # systemd unit (run on boot)
│   └── README.md
├── docs/
│   └── API.md
├── hootcam_server/
│   ├── __init__.py
│   ├── main.py          # FastAPI app
│   ├── config.py        # Config load/save, defaults
│   ├── database.py      # SQLite schema, events, files
│   ├── camera.py        # Dual camera capture (picamera2)
│   ├── motion.py        # Motion detection
│   ├── recording.py     # Record on motion to SSD
│   ├── streaming.py     # MJPEG streams
│   └── api/
│       ├── __init__.py
│       ├── routes.py    # All API routes
│       └── schemas.py   # Pydantic models for config options
```

## See also

- [**Hootcam UI**](https://github.com/ManliestBen/hootcam-ui) – Web frontend for this server (or for Hootcam Motion in the 3-part setup).
- [**Hootcam Streamer**](https://github.com/ManliestBen/hootcam-streamer) – RTSP-only streamer for the Pi; use with Hootcam Motion for the 3-part architecture.
- [**Hootcam Motion**](https://github.com/ManliestBen/hootcam-motion) – NUC app that consumes RTSP and runs motion + recording + API; alternative to running everything on the Pi.

## License

MIT
