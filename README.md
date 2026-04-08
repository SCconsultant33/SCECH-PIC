# MOECS PIC Lookup Agent

This project provides two ways to run the same lookup automation:

1. **Web app (no command-line workflow after startup)** via Streamlit.
2. **CLI script** for terminal users.

## What it does

- Accepts a CSV input file with `first_name,last_name` headers.
- Looks up each person on `https://mdoe.state.mi.us/MOECS/PublicCredentialSearch.aspx`.
- For duplicate name matches, evaluates each returned record and attempts to prioritize rows that appear to show:
  - an **active** school counseling credential, or
  - a counseling endorsement with **(NT)** indicators.
- Exports all results to CSV, including a confidence-like status and reason.

## Important note

Because duplicate-name disambiguation is heuristic (text-based), you should manually review any row marked `REVIEW_REQUIRED`.

## Setup (one time)

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## Hosted standalone web app (recommended for no local setup)

You can deploy this once to a cloud service (for example Render) and then use it from your browser without installing Python locally.

### Deploy on Render

1. Push this repo to GitHub.
2. In Render, create a new **Web Service** from the repo.
3. Render will detect `render.yaml` + `Dockerfile` and build automatically.
4. Set an environment variable `APP_ACCESS_KEY` in Render (so only you can use it).
5. Open your Render URL and enter the access key.

After deployment, your day-to-day usage is just: open URL -> upload CSV -> run lookup -> download results.

### Render error: "Looks like you launched a headed browser without having a XServer running"

On Render, the app must run Playwright in headless mode (no visible browser). This repo now auto-forces headless mode in hosted environments, even if a headful toggle was previously enabled.

### Large batch runs (30+ names)

The lookup engine now uses shorter waits and periodically refreshes the browser session during long jobs to reduce hangs in hosted environments.
If you still hit runtime limits on your hosting plan, run in smaller batches (for example 20-30 names per file) and combine the CSV outputs.

## Option A: Web app (recommended)

Start the app:

```bash
python -m streamlit run app.py
```

Then open the URL Streamlit prints (usually `http://localhost:8501`), upload your CSV, and click **Run Lookup**.

### Windows shortcut launcher

If you're on Windows, you can also double-click `launch_ui.bat`.

### If `launch_ui.bat` says "Python was not found"

That means Python is not installed (or not on PATH) on Windows yet. Install Python 3, then run:

```bash
py -3 -m pip install -r requirements.txt
py -3 -m playwright install chromium
```

After that, run `launch_ui.bat` again.


## Option B: CLI usage

1. Create your input file, for example `names.csv`:

```csv
first_name,last_name
Alex,Smith
Jamie,Johnson
```

2. Run:

```bash
python moecs_pic_agent.py --input names.csv --output results.csv --headful
```

Arguments:

- `--input` (required): source CSV.
- `--output` (optional): output CSV path (default `pic_lookup_results.csv`).
- `--headful` (optional): shows browser while it runs.
- `--slow-mo-ms` (optional): slows browser actions for debugging.

## Output

The output CSV includes:

- `first_name`
- `last_name`
- `status` (`LIKELY_MATCH`, `NO_FULL_NAME_MATCH`, `REVIEW_REQUIRED`, `NOT_FOUND`, or `ERROR`)
- `pic`
- `reason`
- `matched_entry`
