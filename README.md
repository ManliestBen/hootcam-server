# Hootcam Server

Backend for the Hootcam owl box camera system: **Raspberry Pi 5** dual CSI cameras, motion detection, and motion-triggered recording to SSD. REST API and SQLite for metadata. Use this repo together with a separate frontend (e.g. **hootcam** or **hootcam-ui**).

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

```bash
# On the Pi (clone as hootcam-server)
cd /path/to/hootcam-server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Install picamera2 (Pi only) and ffmpeg (for encoding motion-triggered movies)
sudo apt update && sudo apt install -y python3-picamera2 ffmpeg

# Recordings default to the current directory (SD card). To use an SSD instead:
#   - Use the API: GET /storage (see auto-detected SSD), then PATCH /storage with
#     { "use_auto_detected_ssd": true } or { "path": "/mnt/ssd/hootcam-server" }
#   - Or set HOOTCAM_TARGET_DIR=/mnt/ssd/hootcam-server before starting
# Storage changes take effect after the next server restart.
```

## Running

```bash
source .venv/bin/activate
uvicorn hootcam_server.main:app --host 0.0.0.0 --port 8080
```

- **API & docs**: http://&lt;pi-ip&gt;:8080  
- **OpenAPI JSON**: http://&lt;pi-ip&gt;:8080/openapi.json  
- **Camera 0 stream**: http://&lt;pi-ip&gt;:8080/cameras/0/stream  
- **Camera 1 stream**: http://&lt;pi-ip&gt;:8080/cameras/1/stream  

All endpoints require **HTTP Basic auth**. Default user: `admin` / `admin`. Change the password via `PATCH /auth/password` (see [API docs](docs/API.md#authentication)).

## Running as a service (start on boot)

To run Hootcam Server automatically on the Pi after reboot:

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

## License

MIT
