# MOECS PIC Lookup Agent

This project provides a small automation script that can process a list of names and attempt to find each person's PIC number from the Michigan MOECS public credential search.

## What it does

- Accepts a CSV input file with `first_name,last_name` headers.
- Looks up each person on `https://mdoe.state.mi.us/MOECS/PublicCredentialSearch.aspx`.
- For duplicate name matches, evaluates each returned record and attempts to prioritize rows that appear to show:
  - an **active** school counseling credential, or
  - a counseling endorsement with **(NT)** indicators.
- Exports all results to CSV, including a confidence-like status and reason.

## Important note

Because duplicate-name disambiguation is heuristic (text-based), you should manually review any row marked `REVIEW_REQUIRED`.

## Usage

1. Install dependencies:

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

2. Create your input file, for example `names.csv`:

```csv
first_name,last_name
Alex,Smith
Jamie,Johnson
```

3. Run the script:

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
