# Hootcam Server / Hootcam Motion API Reference

This document describes the REST API exposed by **Hootcam Server** (all-in-one on the Pi) and by **Hootcam Motion** (NUC app that consumes RTSP streams). The API is the same; only the video source differs (direct cameras vs RTSP). For Hootcam Motion, configure each camera’s `stream_url` (RTSP) instead of hardware resolution.

- **Swagger UI**: `GET /docs`
- **ReDoc**: `GET /redoc`
- **OpenAPI JSON**: `GET /openapi.json`

---

## Authentication

All API endpoints (including `/`, `/config`, `/cameras`, streams, and file content) require **HTTP Basic authentication**. There is a single user; the app does not support multiple accounts.

- **Default credentials (first run):** username `admin`, password `admin`. Change the password after first login.
- **How to send credentials:** Include the header `Authorization: Basic <base64(username:password)>`. In the browser, Swagger UI (`/docs`) has an **Authorize** button where you enter username and password. For `fetch` or curl, use `-u admin:password` or set the header manually.

### Auth endpoints

| Method | Path | Description |
|--------|------|-------------|
| PATCH | `/auth/password` | Change the user’s password. Send **current** credentials in the `Authorization` header (Basic) and the **new** password in the request body. Returns 204 on success. |

**Change password example**

```http
PATCH /auth/password
Authorization: Basic YWRtaW46YWRtaW4=
Content-Type: application/json

{
  "new_password": "your-new-secure-password"
}
```

(Use your current username and password for the `Authorization` header; the body contains only `new_password`.)

---

## Setting configuration via the API

All config is **partial update**: send only the keys you want to change. Omitted keys are left unchanged.

### Global config

- **Read**: `GET /config` → returns `global_config` and `cameras`.
- **Update**: `PATCH /config` with a JSON body containing any of the global options below.

### Storage (recording location)

- **Read**: `GET /storage` → returns `current_path` (where recordings are stored) and `auto_detected_ssd_path` (if an SSD is attached).
- **Update**: `PATCH /storage` with either:
  - `{ "path": "/absolute/path" }` — set storage to this directory (must be absolute; created if missing).
  - `{ "use_auto_detected_ssd": true }` — set storage to the auto-detected SSD mount path.
  Storage changes are saved to config and **take effect after the next server restart**.

Example — switch storage to auto-detected SSD:

```http
PATCH /storage
Content-Type: application/json

{
  "use_auto_detected_ssd": true
}
```

Example — set storage to a specific path:

```http
PATCH /storage
Content-Type: application/json

{
  "path": "/mnt/ssd/hootcam-server"
}
```

Example — set target directory and stream quality:

```http
PATCH /config
Content-Type: application/json

{
  "target_dir": "/mnt/ssd/hootcam-server",
  "stream_quality": 75
}
```

Example — allow stream from other machines and cap stream at 10 fps:

```http
PATCH /config
Content-Type: application/json

{
  "stream_localhost": false,
  "stream_maxrate": 10
}
```

### Per-camera config

- **Read**: `GET /cameras/0/config` or `GET /cameras/1/config`.
- **Update**: `PATCH /cameras/0/config` or `PATCH /cameras/1/config` with a JSON body containing any per-camera options.

Example — camera 0: resolution, framerate, and motion sensitivity:

```http
PATCH /cameras/0/config
Content-Type: application/json

{
  "camera_name": "owl-nest",
  "width": 1280,
  "height": 720,
  "framerate": 15,
  "threshold": 2000,
  "noise_level": 32,
  "event_gap": 45,
  "post_capture": 75
}
```

Example — camera 0: enable pictures and set movie quality:

```http
PATCH /cameras/0/config
Content-Type: application/json

{
  "picture_output": "first",
  "movie_quality": 70,
  "movie_max_time": 180
}
```

Example — camera 1: pause detection at start:

```http
PATCH /cameras/1/config
Content-Type: application/json

{
  "pause": true
}
```

Changes take effect immediately for new events and streams; in-progress events use the config that was active when they started.

---

## Global configuration options

Apply to the whole system (streams, logging, target directory). Set via `PATCH /config`.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| **target_dir** | string | current directory (SD) | Full path where pictures and movies are saved. Default is the current working directory (SD card). Use `GET /storage` and `PATCH /storage` to switch to SSD (auto-detect or manual path). |
| **log_level** | integer | 6 | Log verbosity 1–9 (1=minimal, 9=all). Use 7 (INF) when reporting issues. |
| **log_file** | string \| null | null | Full path for log file. If null, logs go to stderr/syslog. |
| **stream_localhost** | boolean | true | If true, stream URLs are only accessible from the same machine. Set false to view streams from other devices. |
| **stream_quality** | integer | 50 | JPEG quality (1–100) for live MJPEG streams. Lower = less bandwidth. |
| **stream_maxrate** | integer | 1 | Maximum stream framerate (fps). 1–100; 100 means effectively unlimited. |
| **stream_grey** | boolean | false | If true, stream is grayscale to reduce bandwidth. |
| **stream_motion** | boolean | false | If true, stream runs at 1 fps when no motion and at stream_maxrate when motion is detected. |
| **database_busy_timeout** | integer | 0 | SQLite only: max milliseconds to wait for a locked table. 0 = fail immediately. |

---

## Per-camera configuration options

Apply to one camera (0 or 1). Set via `PATCH /cameras/0/config` or `PATCH /cameras/1/config`.

### Identity

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| **camera_name** | string \| null | "camera0" / "camera1" | Name used in filenames (%$) and UI. |
| **camera_id** | integer \| null | 1 / 2 | Numeric id (1–32000), unique per camera. Used in DB and format specifiers (%t). |

### Image capture

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| **width** | integer | 640 | Frame width in pixels. Must be multiple of 8; valid range depends on camera. |
| **height** | integer | 480 | Frame height in pixels. Must be multiple of 8. |
| **framerate** | integer | 15 | Max frames per second (2–100). Higher = more CPU and more frames in recordings. |
| **minimum_frame_time** | integer | 0 | Minimum seconds between frames. 0 = use framerate. Use for capture slower than 2 fps. |
| **rotate** | integer | 0 | Rotate image: 0, 90, 180, or 270. 180 for upside-down camera; 90/270 swap width/height. |
| **flip_axis** | string | "none" | Flip image: "none", "v" (vertical), or "h" (horizontal). |

### Motion detection

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| **threshold** | integer | 1500 | Number of changed pixels (after noise/despeckle) needed to trigger motion. Lower = more sensitive; larger resolutions need higher values. |
| **threshold_maximum** | integer | 0 | If set &gt; 0, motion triggers only when changed pixels are between threshold and this value. 0 = disabled. |
| **threshold_tune** | boolean | false | If true, threshold is auto-tuned; manual threshold is ignored. |
| **noise_level** | integer | 32 | Pixel intensity must change by more than ± this (1–255) to count. Reduces camera noise. |
| **noise_tune** | boolean | true | If true, noise level is auto-tuned. |
| **despeckle_filter** | string \| null | null | Despeckle steps: E/e (erode), D/d (dilate), optional trailing "l" (labeling). Example: "EedDl". |
| **minimum_motion_frames** | integer | 1 | Consecutive frames with motion required to start an event. 1–5 is typical. |
| **event_gap** | integer | 60 | Seconds with no motion that end the event. -1 = no events (one continuous movie, no pre_capture). |
| **pre_capture** | integer | 0 | Number of frames to buffer and include *before* motion (0–100). 0–5 typical. |
| **post_capture** | integer | 0 | Frames to record *after* motion stops. Use e.g. framerate×5 for ~5 seconds of tail. |
| **pause** | boolean | false | If true, motion detection starts paused; use POST .../detection/start to resume. |
| **emulate_motion** | boolean | false | If true, save images/movies even when no motion (continuous recording). |

### Pictures (stills on motion)

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| **picture_output** | string | "off" | When to save stills: "on", "off", "first" (one per event), "best" (most changed pixels per event). |
| **picture_output_motion** | boolean | false | If true, also save debug images showing changed pixels (for tuning). |
| **picture_type** | string | "jpeg" | Format: "jpeg", "webp", "ppm", "grey". |
| **picture_quality** | integer | 75 | JPEG/WebP quality 1–100. |
| **picture_filename** | string | "%v-%Y%m%d%H%M%S-%q" | Filename pattern (relative to target_dir). Use %v, %Y%m%d%H%M%S, %q, %t, %$. |
| **snapshot_interval** | integer | 0 | Seconds between periodic snapshots. 0 = disabled. |
| **snapshot_filename** | string | "%v-%Y%m%d%H%M%S-snapshot" | Filename pattern for snapshots. |

### Movies (motion-triggered video)

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| **movie_output** | boolean | true | If true, encode and save a movie per motion event. |
| **movie_output_motion** | boolean | false | If true, also save motion-pixel (debug) movies. |
| **movie_max_time** | integer | 120 | Max length of one movie in seconds. 0 = unlimited. |
| **movie_bps** | integer | 400000 | Bitrate in bits/sec. Ignored if movie_quality ≠ 0. |
| **movie_quality** | integer | 60 | Variable bitrate quality 1–100. 0 = use movie_bps. |
| **movie_codec** | string | "mkv" | Container/codec: "mpeg4", "msmpeg4", "swf", "flv", "ffv1", "mov", "mp4", "mkv", "hevc". |
| **movie_filename** | string | "%v-%Y%m%d%H%M%S" | Filename pattern; extension added by codec. |

### Overlays / locate motion

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| **locate_motion_mode** | string | "off" | Draw box around motion: "on", "off", "preview" (preview only). |
| **locate_motion_style** | string | "box" | Box style: "box", "redbox", "cross", "redcross". |
| **text_left** | string \| null | null | Text overlay lower-left. Supports conversion specifiers and \\n. |
| **text_right** | string | "%Y-%m-%d\\n%T" | Text overlay lower-right (default: date and time). |
| **text_changes** | boolean | false | If true, show changed-pixel count on image (for tuning). |
| **text_scale** | integer | 1 | Scale for overlay text (1–10). |
| **text_event** | string | "%Y%m%d%H%M%S" | Defines %C for filenames/text; timestamp of first image in event. |

### Script hooks

Full path to executable (or script with shebang). Optional conversion specifiers in arguments. Run asynchronously; no guarantee of order.

| Option | Type | Default | When run |
|--------|------|---------|----------|
| **on_event_start** | string \| null | null | Start of a motion event. |
| **on_event_end** | string \| null | null | End of event (after event_gap). |
| **on_motion_detected** | string \| null | null | When motion is first detected. |
| **on_picture_save** | string \| null | null | When a picture is saved; filename passed as argument. |
| **on_movie_start** | string \| null | null | When movie recording starts. |
| **on_movie_end** | string \| null | null | When movie file is closed; filename passed as argument. |

### Database logging (SQLite)

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| **sql_log_picture** | boolean | false | If true, log each picture save to the database. |
| **sql_log_movie** | boolean | true | If true, log each movie file to the database. |
| **sql_log_snapshot** | boolean | true | If true, log snapshots to the database. |

---

## Other endpoints

Summary of all endpoints. Details and examples are in the sections above.

### Root

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | API name, version, links to docs, cameras, config, events. |

### Configuration

| Method | Path | Description |
|--------|------|-------------|
| GET | `/config` | Global config and per-camera config. |
| PATCH | `/config` | Update global config (partial body). |
| GET | `/storage` | Current storage path and auto-detected SSD path (if any). |
| PATCH | `/storage` | Set storage path manually or to auto-detected SSD. Takes effect after restart. |
| GET | `/cameras/{id}/config` | One camera’s config. |
| PATCH | `/cameras/{id}/config` | Update one camera’s config (partial body). |

### Cameras and streams

| Method | Path | Description |
|--------|------|-------------|
| GET | `/cameras` | List cameras: id, name, camera_id, detection_paused, stream_url. |
| GET | `/cameras/{id}/stream` | MJPEG live stream (multipart/x-mixed-replace). Use in `<img src="...">`. |
| GET | `/cameras/{id}/current` | Latest frame as a single JPEG. |
| GET | `/cameras/{id}/status` | Connection status (connected true/false). |

### Detection control

| Method | Path | Description |
|--------|------|-------------|
| POST | `/cameras/{id}/detection/start` | Resume motion detection. |
| POST | `/cameras/{id}/detection/pause` | Pause motion detection. |
| GET | `/cameras/{id}/detection/status` | Paused, in_event, event_id. |
| POST | `/cameras/{id}/action/snapshot` | Request a single snapshot. |

### Events and files

Media files live in `target_dir`; SQLite stores metadata only.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/events` | List events. Query: `camera_index` (optional), `limit` (default 100, max 500), `offset` (default 0). |
| GET | `/events/{event_id}` | One event. |
| GET | `/files` | List file records. Query: `event_id`, `camera_index`, `file_type` (all optional filters), `limit` (default 200, max 1000), `offset` (default 0). |
| GET | `/files/{file_id}/content` | **Download the picture or video file.** Returns the file bytes with the correct Content-Type. Use the `id` from a file in GET /files. Safe for `<img src>`, `<video src>`, or direct download. |

`file_type`: `picture`, `movie`, `snapshot`, `timelapse`. `file_path` in the list is relative to `target_dir`.

**Frontend usage for recorded media:** Call `GET /files?event_id=…` (or by `camera_index` / `file_type`) to get file records. Each record has an `id`. Use `GET /files/{id}/content` as the URL for images or videos, e.g. `<img src="/files/42/content">` or `<video src="/files/43/content">`.

---

## Conversion specifiers (filenames and text)

Use in `picture_filename`, `movie_filename`, `text_left`, `text_right`, `text_event`:

| Specifier | Meaning |
|-----------|---------|
| %Y %m %d | Year, month, day |
| %H %M %S | Hour, minute, second |
| %T | Time HH:MM:SS |
| %v | Event number |
| %q | Frame number |
| %t | Camera id |
| %$ | Camera name |
| %C | Value of text_event (timestamp of first image in event) |

---

## SQLite

- **Path**: `{target_dir}/.hootcam_server/hootcam_server.sqlite` (or from `HOOTCAM_TARGET_DIR`).
- **Tables**: `events`, `files`, `config`.
- **Config**: Global and per-camera config are stored in the `config` table when you use `PATCH /config` or `PATCH /cameras/{id}/config`.
