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

The defaults suit running on your own computer. The development server and in-process
background thread are not meant for a shared production server.

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
