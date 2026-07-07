"""
app.py — Brain Expert Annotation Platform (Flask)

A single-study annotation app for brain (glioma / GBM) histopathology patches:
  Task 1 — Rate 50 images as Real or Synthetic (blinded, one at a time).
  Task 2 — Review 50 transitions (original -> synthetic counterfactual) and
           mark morphological features on the first and last image of each row.

Routes:
  GET  /                        Landing / login page
  POST /start                   Create or resume a rater session
  GET  /intro                   Study intro card
  GET  /task1/<img_idx>         Rate image Real/Synthetic
  POST /task1/<img_idx>         Save Task 1 rating
  GET  /task1/image/<img_idx>   Blinded image bytes for Task 1
  GET  /task1_complete          Task 1 done interstitial
  GET  /task2/<t_idx>           Morphological features for a transition
  POST /task2/<t_idx>           Save Task 2 features
  GET  /task2/image/<t_idx>/<pos>   Image bytes for a transition position
  GET  /complete                Study complete + summary
  GET  /admin/results           Zip download of all result files (admin key)
"""

import io
import json
import os
import urllib.request
import zipfile
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from config import (
    ADMIN_KEY,
    BRAIN_FEATURES,
    BRAIN_SCALE_ASPECTS,
    MAX_NEW_ACCOUNTS_PER_DAY,
    RATER_PASSWORDS,
    RESULTS_DIR,
    SECRET_KEY,
    STUDY_CONFIG_PATH,
)

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ---------------------------------------------------------------------------
# Load study config at startup
# ---------------------------------------------------------------------------
if not os.path.exists(STUDY_CONFIG_PATH):
    raise FileNotFoundError(
        f"study_config.json not found at {STUDY_CONFIG_PATH}. "
        "Run build_config.py first (it can produce an empty but valid config)."
    )

with open(STUDY_CONFIG_PATH) as f:
    STUDY = json.load(f)

STUDY_TITLE = STUDY.get("title", "Brain User Study")
TASK1_IMAGES = STUDY.get("task1", {}).get("images", [])
TRANSITIONS = STUDY.get("task2", {}).get("transitions", [])
N_TASK1 = len(TASK1_IMAGES)
N_TASK2 = len(TRANSITIONS)

os.makedirs(RESULTS_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Results helpers
# ---------------------------------------------------------------------------

def utcnow():
    return datetime.now(timezone.utc).isoformat()


def results_path(rater_id):
    safe_id = "".join(c for c in rater_id if c.isalnum() or c in "-_.")
    return os.path.join(RESULTS_DIR, f"rater_{safe_id}.json")


def load_results(rater_id):
    path = results_path(rater_id)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_results(rater_id, data):
    """Atomic write to prevent corrupt JSON on crash."""
    path = results_path(rater_id)
    tmp = path + ".tmp"
    data["last_updated"] = utcnow()
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def ensure_shape(results, rater_id):
    """Guarantee the top-level keys exist."""
    results.setdefault("rater_id", rater_id)
    results.setdefault("task1", {})   # img_id -> {rating, rated_at}
    results.setdefault("task2", {})   # transition_id -> {left, right, rated_at}
    return results


# Images are always served same-origin: templates point <img> at Flask routes
# (/task1/image/…, /task2/image/…), and Flask fetches from HuggingFace here,
# server-side, then caches the bytes in memory. The browser never requests an
# external URL, so nginx/CSP rules that block cross-origin image loads cannot
# break image display (this was the failure mode in the earlier study UI).
_hf_cache: dict = {}


def _proxy_hf(hf_url):
    if hf_url not in _hf_cache:
        try:
            req = urllib.request.Request(hf_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                _hf_cache[hf_url] = resp.read()
        except Exception:
            app.logger.warning("HF image fetch failed: %s", hf_url)
            return "Image not found", 404
    response = send_file(io.BytesIO(_hf_cache[hf_url]), mimetype="image/png")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


def _serve_image(img):
    """Serve an image dict via local_path (preferred) or hf_url fallback."""
    if not img:
        return "Image not found", 404
    local_path = img.get("local_path")
    if local_path and os.path.exists(local_path):
        return send_file(local_path, mimetype="image/png")
    hf_url = img.get("hf_url")
    if hf_url:
        return _proxy_hf(hf_url)
    return "Image not found", 404


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def task1_done_count(results):
    t1 = results.get("task1", {})
    return sum(1 for img in TASK1_IMAGES if t1.get(img["img_id"]) is not None)


def first_unrated_task1(results):
    """Index of the first Task 1 image without a rating (0 if all rated)."""
    t1 = results.get("task1", {})
    for idx, img in enumerate(TASK1_IMAGES):
        if t1.get(img["img_id"]) is None:
            return idx
    return 0


def task2_done_count(results):
    t2 = results.get("task2", {})
    return sum(1 for tr in TRANSITIONS if t2.get(tr["transition_id"]) is not None)


def overall_pct(results):
    total = N_TASK1 + N_TASK2
    if not total:
        return 0
    done = task1_done_count(results) + task2_done_count(results)
    return int(done / total * 100)


@app.context_processor
def inject_globals():
    """Sidebar progress injected into every template for logged-in raters."""
    rater_id = session.get("rater_id")
    if not rater_id:
        return {"nav": None, "study_title": STUDY_TITLE}
    results = load_results(rater_id)
    t1_done = task1_done_count(results)
    t2_done = task2_done_count(results)
    nav = {
        "task1_done": t1_done,
        "task1_total": N_TASK1,
        "task1_complete": N_TASK1 > 0 and t1_done >= N_TASK1,
        "task2_done": t2_done,
        "task2_total": N_TASK2,
        "task2_complete": N_TASK2 > 0 and t2_done >= N_TASK2,
        "overall_pct": overall_pct(results),
    }
    return {"nav": nav, "study_title": STUDY_TITLE}


# ---------------------------------------------------------------------------
# Resume logic
# ---------------------------------------------------------------------------

def compute_redirect(rater_id):
    """Send the rater to the first unanswered item."""
    results = load_results(rater_id)
    t1 = results.get("task1", {})
    for img_idx, img in enumerate(TASK1_IMAGES):
        if t1.get(img["img_id"]) is None:
            return redirect(url_for("task1", img_idx=img_idx))

    t2 = results.get("task2", {})
    if N_TASK2 and not any(t2.get(tr["transition_id"]) for tr in TRANSITIONS):
        # Task 1 finished but Task 2 not started — show interstitial
        return redirect(url_for("task1_complete"))
    for t_idx, tr in enumerate(TRANSITIONS):
        if t2.get(tr["transition_id"]) is None:
            return redirect(url_for("task2", t_idx=t_idx))

    return redirect(url_for("study_complete"))


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("rater_id"):
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", protected_ids=list(RATER_PASSWORDS.keys()))


@app.route("/start", methods=["POST"])
def start():
    rater_id = request.form.get("rater_id", "").strip()
    protected_ids = list(RATER_PASSWORDS.keys())
    if not rater_id:
        return render_template("index.html", error="Please enter your rater ID.",
                               protected_ids=protected_ids)

    if rater_id in RATER_PASSWORDS:
        password = request.form.get("password", "").strip()
        if password != RATER_PASSWORDS[rater_id]:
            session.pop("rater_id", None)
            return render_template("index.html", error="Incorrect password.",
                                   protected_ids=protected_ids, prefill_id=rater_id)

    session["rater_id"] = rater_id

    results = load_results(rater_id)
    if not results or not results.get("started_at"):
        # New session — enforce a soft daily account creation limit.
        if rater_id not in RATER_PASSWORDS:
            today = datetime.now(timezone.utc).date().isoformat()
            new_today = 0
            for fn in os.listdir(RESULTS_DIR):
                if not fn.endswith(".json") or fn.endswith(".tmp"):
                    continue
                try:
                    with open(os.path.join(RESULTS_DIR, fn)) as _f:
                        d = json.load(_f)
                    if d.get("started_at", "")[:10] == today:
                        new_today += 1
                except Exception:
                    pass
            if new_today >= MAX_NEW_ACCOUNTS_PER_DAY:
                return render_template("index.html",
                    error="Registration is temporarily limited. Please try again "
                          "tomorrow or contact the study coordinator.",
                    protected_ids=protected_ids)
        results = ensure_shape(results, rater_id)
        results["started_at"] = utcnow()
        save_results(rater_id, results)
        session["started_at"] = results["started_at"]
        return redirect(url_for("intro"))

    flash("Welcome back! Resuming from where you left off.", "resume")
    session["started_at"] = results.get("started_at", utcnow())
    return compute_redirect(rater_id)


@app.route("/intro")
@login_required
def intro():
    return render_template("intro.html", n_task1=N_TASK1, n_task2=N_TASK2)


@app.route("/task1/<int:img_idx>", methods=["GET", "POST"])
@login_required
def task1(img_idx):
    if img_idx < 0 or img_idx >= N_TASK1:
        return redirect(url_for("task1_complete"))
    img = TASK1_IMAGES[img_idx]
    rater_id = session["rater_id"]

    if request.method == "POST":
        rating = request.form.get("rating")
        if rating not in ("real", "synthetic"):
            return redirect(url_for("task1", img_idx=img_idx))
        results = ensure_shape(load_results(rater_id), rater_id)
        results["task1"][img["img_id"]] = {"rating": rating, "rated_at": utcnow()}
        if results.get("task1_started_at") is None:
            results["task1_started_at"] = utcnow()
        if task1_done_count(results) >= N_TASK1:
            results.setdefault("task1_completed_at", utcnow())
        save_results(rater_id, results)

        next_idx = img_idx + 1
        if next_idx < N_TASK1:
            return redirect(url_for("task1", img_idx=next_idx))
        return redirect(url_for("task1_complete"))

    # GET — blinded image URL (position only, never filename / img_id)
    image_url = url_for("task1_image", img_idx=img_idx)
    # Preload the next few images so clicking Next is smooth (also warms the
    # server-side HF cache).
    preload_urls = [
        url_for("task1_image", img_idx=img_idx + k)
        for k in range(1, 4) if img_idx + k < N_TASK1
    ]

    results = load_results(rater_id)
    existing = results.get("task1", {}).get(img["img_id"], {})
    existing_rating = existing.get("rating") if isinstance(existing, dict) else None

    return render_template(
        "task1.html",
        img_idx=img_idx,
        total_images=N_TASK1,
        image_url=image_url,
        preload_urls=preload_urls,
        overall_pct=overall_pct(results),
        existing_rating=existing_rating,
    )


@app.route("/task1/image/<int:img_idx>")
@login_required
def task1_image(img_idx):
    """Blinded image serving: URL reveals only position, never filename."""
    if img_idx < 0 or img_idx >= N_TASK1:
        return "Not found", 404
    return _serve_image(TASK1_IMAGES[img_idx])


@app.route("/task1_complete")
@login_required
def task1_complete():
    results = load_results(session["rater_id"])
    done = task1_done_count(results)
    return render_template(
        "task1_complete.html",
        n_images=N_TASK1,
        n_pairs=N_TASK2,
        task1_done=done,
        task1_complete=(N_TASK1 > 0 and done >= N_TASK1),
        resume_idx=first_unrated_task1(results),
    )


@app.route("/task2/<int:t_idx>", methods=["GET", "POST"])
@login_required
def task2(t_idx):
    if t_idx < 0 or t_idx >= N_TASK2:
        return redirect(url_for("study_complete"))
    tr = TRANSITIONS[t_idx]
    imgs = tr["images"]
    last = len(imgs) - 1
    rater_id = session["rater_id"]

    # Task 1 should be finished before Task 2. If it isn't, bounce to the
    # interstitial (which explains this) unless the rater has explicitly chosen
    # to proceed anyway — that choice is remembered for the rest of the session.
    if request.args.get("force"):
        session["allow_task2_early"] = True
    if task1_done_count(load_results(rater_id)) < N_TASK1 \
            and not session.get("allow_task2_early"):
        return redirect(url_for("task1_complete"))

    if request.method == "POST":
        results = ensure_shape(load_results(rater_id), rater_id)
        if results.get("task2_started_at") is None:
            results["task2_started_at"] = utcnow()

        def parse_deltas():
            """One signed change per aspect: -2..+2 (- = higher in Original,
            + = higher in Counterfactual, 0 = no difference)."""
            deltas = {}
            for _group, aspects in BRAIN_SCALE_ASPECTS:
                for key, _label in aspects:
                    v = request.form.get(f"delta_{key}", "").strip()
                    try:
                        iv = int(v)
                    except ValueError:
                        continue
                    if -2 <= iv <= 2:
                        deltas[key] = iv
            return deltas

        results["task2"][tr["transition_id"]] = {
            "left": {
                "features": request.form.getlist("features_0"),
                "artifact_text": request.form.get("artifact_text_0", "").strip(),
                "other_text": request.form.get("other_text_0", "").strip(),
            },
            "right": {
                "features": request.form.getlist(f"features_{last}"),
                "artifact_text": request.form.get(f"artifact_text_{last}", "").strip(),
                "other_text": request.form.get(f"other_text_{last}", "").strip(),
            },
            "deltas": parse_deltas(),
            "rated_at": utcnow(),
        }
        if task2_done_count(results) >= N_TASK2:
            results.setdefault("task2_completed_at", utcnow())
        save_results(rater_id, results)

        next_idx = t_idx + 1
        if next_idx < N_TASK2:
            return redirect(url_for("task2", t_idx=next_idx))
        return redirect(url_for("study_complete"))

    # GET
    group_urls = [url_for("task2_image", t_idx=t_idx, pos=pos) for pos in range(len(imgs))]
    # Preload the next transition's frames so advancing is smooth.
    preload_urls = []
    if t_idx + 1 < N_TASK2:
        n_next = len(TRANSITIONS[t_idx + 1]["images"])
        preload_urls = [
            url_for("task2_image", t_idx=t_idx + 1, pos=p) for p in range(n_next)
        ]
    results = load_results(rater_id)
    saved = results.get("task2", {}).get(tr["transition_id"])
    existing = [None] * len(imgs)
    if saved:
        existing[0] = saved.get("left")
        existing[last] = saved.get("right")
    deltas = saved.get("deltas", {}) if saved else {}

    return render_template(
        "task2.html",
        t_idx=t_idx,
        n_pairs=N_TASK2,
        group=imgs,
        group_urls=group_urls,
        preload_urls=preload_urls,
        existing=existing,
        deltas=deltas,
        features=BRAIN_FEATURES,
        scale_groups=BRAIN_SCALE_ASPECTS,
        overall_pct=overall_pct(results),
    )


@app.route("/task2/image/<int:t_idx>/<int:pos>")
@login_required
def task2_image(t_idx, pos):
    if t_idx < 0 or t_idx >= N_TASK2:
        return "Not found", 404
    imgs = TRANSITIONS[t_idx]["images"]
    if pos < 0 or pos >= len(imgs):
        return "Not found", 404
    return _serve_image(imgs[pos])


@app.route("/complete")
@login_required
def study_complete():
    rater_id = session["rater_id"]
    results = load_results(rater_id)
    t1 = results.get("task1", {})
    n_real = sum(1 for v in t1.values() if v and v.get("rating") == "real")
    n_synth = sum(1 for v in t1.values() if v and v.get("rating") == "synthetic")

    incomplete = {
        "task1": task1_done_count(results) < N_TASK1,
        "task2": task2_done_count(results) < N_TASK2,
    }
    return render_template(
        "study_complete.html",
        rater_id=rater_id,
        n_task1=N_TASK1,
        n_task2=N_TASK2,
        task1_done=task1_done_count(results),
        task2_done=task2_done_count(results),
        n_real=n_real,
        n_synthetic=n_synth,
        incomplete=incomplete,
        results_file=f"rater_{rater_id}.json",
    )


@app.route("/admin/results")
def admin_results():
    if not ADMIN_KEY or request.args.get("key") != ADMIN_KEY:
        return "Unauthorized", 403
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in os.listdir(RESULTS_DIR):
            if fname.endswith(".json"):
                zf.write(os.path.join(RESULTS_DIR, fname), fname)
    buf.seek(0)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"brain_results_{timestamp}.zip")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6081,
            debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")
