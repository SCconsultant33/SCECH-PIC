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
- `status` (`LIKELY_MATCH`, `SINGLE_MATCH`, `REVIEW_REQUIRED`, `NOT_FOUND`, or `ERROR`)
- `pic`
- `reason`
- `matched_entry`
