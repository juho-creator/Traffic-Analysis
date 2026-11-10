# Traffic-Analysis
Vehicle Counting Framework for Traffic Analysis in Complex Areas Using CCTV Systems

A Django web app that counts cars, motorcycles and trucks in road video from a fixed
camera, then shows traffic over time as an interactive graph and a PDF report. It was
built as a prototype for the Uganda Ministry of Works and Transport, to replace counting
every vehicle by hand.

## How it works

1. **Upload** a video from a fixed camera and drag a counting line across the road.
2. **Detect and track**: [Ultralytics YOLO26](https://docs.ultralytics.com/) (COCO weights)
   finds each vehicle in every frame, and ByteTrack follows it between frames.
3. **Count**: a vehicle is counted once when its centre crosses the line, with its type,
   direction and time. Every crossing is written to an event log.
4. **Report**: the log is grouped into time periods for the graph, a table, a PDF report
   and a CSV file. Times are East Africa Time.

The results page shows the counted video next to the totals. Hover over a column in the
graph for details, or click it to jump to that moment in the video. You can change the
period length, filter by direction and show or hide vehicle types.

## Requirements

- Python 3.10 or newer
- No GPU needed, but processing on a CPU takes a few minutes for a short clip

## Setup

```bash
git clone https://github.com/juho-creator/Traffic-Analysis.git
cd Traffic-Analysis
python -m venv .venv
.venv\Scripts\activate          # Windows; on macOS or Linux: source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
```

## Run

```bash
python manage.py runserver --noreload
```

Then open http://127.0.0.1:8000. `--noreload` stops Django from restarting when files
change, which would interrupt a video being processed. The first run of each speed setting
downloads its YOLO weights to `weights/`.

To browse past analyses in the Django admin at `/admin/`, create an account first with
`python manage.py createsuperuser`.

Uploaded videos and results are stored in `media/`, one folder per analysis, until you
delete them in the app.

### Speed settings

| Setting  | Model        | Image size | Use for                          |
|----------|--------------|------------|----------------------------------|
| Fast     | `yolo26n.pt` | 640        | Quick checks; may miss vehicles  |
| Balanced | `yolo26s.pt` | 960        | Default                          |
| Accurate | `yolo26m.pt` | 1280       | Crowded scenes; slowest          |

## Command line

The counting and report code also runs without the web app, including on live streams:

```bash
python -m traffic.count_vehicles road.mp4 --start-time 2026-11-08T08:00:00 --site "Kampala Road"
python -m traffic.count_vehicles rtsp://camera/stream --site "Kampala Road" --report-every 15 --no-video
python -m traffic.traffic_report output/road_events.csv --interval 15min
python -m traffic.simulate_traffic   # made-up data, clearly marked, to preview the report
```

Run any of them with `--help` for all options.

## Project structure

```
config/       Django settings and URLs
counter/      Web app: upload, background processing, results API and page
traffic/      Vehicle counting (count_vehicles), reports (traffic_report), demo data
manage.py
```

## Tests

```bash
python manage.py test
```

## Configuration

| Variable               | Default                | Purpose                          |
|------------------------|------------------------|----------------------------------|
| `DJANGO_SECRET_KEY`    | random on each start   | Set a fixed key on a server      |
| `DJANGO_DEBUG`         | `1`                    | Set to `0` on a server           |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost`  | Comma-separated host names       |
| `TRAFFIC_MEDIA_ROOT`   | `media/`               | Where videos and results go      |
| `TRAFFIC_WEIGHTS_DIR`  | `weights/`             | Where YOLO weights are saved     |

| `DJANGO_DB_PATH`       | `db.sqlite3`           | Where the database file goes     |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | none            | Extra allowed origins, e.g. `https://example.com` |
| `DJANGO_BEHIND_HTTPS_PROXY` | off               | Set to `1` behind a host's HTTPS proxy |

The defaults suit running on your own computer.

## Deploy

The app needs one long-running server with its own disk: processing a video takes
minutes, runs in a background thread, and writes the database, videos and results to
disk. **Serverless hosts such as Vercel cannot run it.** Their functions stop within
minutes, accept uploads of only a few MB, have no lasting disk, and have a 500 MB size
limit that PyTorch (needed by YOLO) exceeds on its own.

Use a host that runs a Docker container, such as Railway, Render, Fly.io, Hugging Face
Spaces (Docker) or your own server. The `Dockerfile` installs the CPU-only PyTorch,
downloads the YOLO weights, and starts the app with gunicorn on `$PORT` (default 8000).

```bash
docker build -t traffic-analysis .
docker run -p 8000:8000 -v traffic-data:/data -e DJANGO_SECRET_KEY=change-me traffic-analysis
```

- Give the container at least 1 GB of memory, and 2 GB for the Accurate setting. One
  analysis peaks at about 700 MB (Fast) or 800 MB (Balanced) on a 1080p video; with
  less memory the host stops the server mid-analysis. Free tiers with 512 MB are too small.
- Attach a volume at `/data` so analyses survive restarts.
- Set `DJANGO_SECRET_KEY` to a fixed secret value, and `DJANGO_ALLOWED_HOSTS` to your
  domain instead of the default `*`.

## Limitations

- Counts are automated estimates. Accuracy has only been checked on a short highway clip
  (10 of 10 vehicles) and has not yet been measured on Ugandan roads.
- Busy junctions with many motorcycles are not reliable yet: vehicles can be missed, or
  counted twice when tracking loses them.
- Buses, bicycles and pedestrians are not counted.
- The camera must not move, because the counting line is fixed in the picture.
- One video is processed at a time.

## Licence note

Ultralytics YOLO is licensed under AGPL-3.0. Check its licence terms, or the Ultralytics
Enterprise licence, before deploying this project.
