# Brain Expert Annotation Platform

A Flask web app for expert annotation of synthetic vs. real **brain (glioma / GBM)**
histopathology images. Two tasks:

- **Task 1 — Authenticity:** rate 50 images as *Real* or *Synthetic* (blinded, one at a time).
- **Task 2 — Transitions:** for each of 50 transitions (original → synthetic counterfactual),
  tick the morphological features present in the original and counterfactual, and rate the
  change of several graded aspects on a −2…+2 scale.

Images are served from the public Hugging Face dataset
[`zigulaura/IDHBrainDiff`](https://huggingface.co/datasets/zigulaura/IDHBrainDiff) and fetched
at runtime, so no image files are needed to host the app.

## Setup

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
cd BrainUI
uv sync
cp .env.example .env      # then edit values
```

`.env` values:

| Variable | Purpose |
|----------|---------|
| `FLASK_SECRET_KEY` | Flask session secret |
| `ADMIN_KEY` | key for the results-download endpoint |

## Running

```bash
uv run -m flask --app app run --host=0.0.0.0 --port=6006
```

Then open `http://<server-ip>:6006`. (`--app app` names `app.py`; any free port works.)

For a long-lived deployment use gunicorn instead of the dev server:

```bash
uv run gunicorn -b 0.0.0.0:6006 app:app
```

## Results

Per-rater answers are saved to `results/rater_<id>.json`, written atomically after every
response. Download all of them as a zip:

```
GET /admin/results?key=<ADMIN_KEY>
```

## Project structure

```
BrainUI/
├── app.py               # Flask application (2 tasks, single study)
├── config.py            # Config (loads .env) + Task 2 feature list & graded aspects
├── study_config.json    # Study design: image URLs and task1/task2 order
├── pyproject.toml       # Python dependencies (uv)
├── results/             # Per-rater result JSON files
├── static/              # CSS, JS
└── templates/           # Jinja2 HTML templates
```
