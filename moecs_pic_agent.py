#!/usr/bin/env python3
"""
MOECS PIC lookup helper.

Given a CSV of first/last names, this script automates the Michigan MOECS
Public Credential Search and attempts to identify a PIC number for each person.

For duplicate name matches, it opens each result and checks for evidence of:
  1) an active school counseling license, or
  2) a counseling endorsement (NT).

The script is intentionally conservative and prints a review status so you can
manually verify any uncertain records.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

ACTIVE_HINTS = ["active", "valid", "current"]
COUNSELING_HINTS = ["school counselor", "school counselling", "counselor", "counselling", "guidance counselor"]
ENDORSEMENT_HINTS = ["endorsement", "(nt)", " nt ", "nt)", "(nt"]
PIC_REGEX = re.compile(r"PIC\s*[:#-]?\s*(\d{4,})", re.IGNORECASE)


@dataclass
class NameRecord:
    first_name: str
    last_name: str


@dataclass
class MatchReview:
    first_name: str
    last_name: str
    status: str
    pic: str
    matched_entry: str
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lookup PIC numbers from MOECS.")
    parser.add_argument("--input", required=True, help="Path to CSV file with headers: first_name,last_name")
    parser.add_argument("--output", default="pic_lookup_results.csv", help="Path to output CSV file.")
    parser.add_argument("--headful", action="store_true", help="Show browser while running.")
    parser.add_argument("--slow-mo-ms", type=int, default=0, help="Delay Playwright operations (ms). Useful for debugging.")
    return parser.parse_args()


def parse_names_from_reader(reader: csv.DictReader) -> List[NameRecord]:
    records: List[NameRecord] = []
    expected = {"first_name", "last_name"}
    if not expected.issubset({(h or "").strip() for h in reader.fieldnames or []}):
        raise ValueError("Input CSV must contain headers: first_name,last_name")

    for row in reader:
        first = (row.get("first_name") or "").strip()
        last = (row.get("last_name") or "").strip()
        if not first or not last:
            continue
        records.append(NameRecord(first_name=first, last_name=last))

    if not records:
        raise ValueError("No valid names found in input CSV")

    return records


def load_names(csv_path: Path) -> List[NameRecord]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        return parse_names_from_reader(csv.DictReader(f))


def first_visible(page: Page, selectors: Iterable[str]) -> Optional[Locator]:
    for selector in selectors:
        loc = page.locator(selector)
        try:
            if loc.first.is_visible(timeout=1200):
                return loc.first
        except Exception:
            continue
    return None


def fill_search_form(page: Page, first_name: str, last_name: str) -> None:
    first_input = first_visible(
        page,
        [
            "input[name*='FirstName' i]",
            "input[id*='FirstName' i]",
            "input[placeholder*='First' i]",
            "#txtFirstName",
            "input[type='text'] >> nth=0",
        ],
    )
    last_input = first_visible(
        page,
        [
            "input[name*='LastName' i]",
            "input[id*='LastName' i]",
            "input[placeholder*='Last' i]",
            "#txtLastName",
            "input[type='text'] >> nth=1",
        ],
    )
    if not first_input or not last_input:
        raise RuntimeError("Could not find first/last name fields on search form")

    first_input.fill(first_name)
    last_input.fill(last_name)


def run_search(page: Page) -> None:
    search_button = first_visible(
        page,
        [
            "input[type='submit'][value*='Search' i]",
            "button:has-text('Search')",
            "a:has-text('Search')",
            "input[name*='Search' i]",
            "#btnSearch",
        ],
    )
    if not search_button:
        raise RuntimeError("Could not find Search button")

    search_button.click()
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        page.wait_for_load_state("domcontentloaded", timeout=15000)


def get_result_rows(page: Page) -> List[Locator]:
    table = first_visible(page, ["table:has(th:has-text('PIC'))", "table:has(th:has-text('Credential'))", "table:has-text('PIC')"])
    if not table:
        rows = page.locator("table tr").all()
        return [r for r in rows if r.locator("td").count() > 1]

    rows = table.locator("tr").all()
    return [r for r in rows if r.locator("td").count() > 1]


def extract_pic(text: str) -> str:
    m = PIC_REGEX.search(text)
    if m:
        return m.group(1)
    candidates = re.findall(r"\b\d{5,}\b", text)
    return candidates[0] if candidates else ""


def score_detail(detail_text: str) -> tuple[int, str]:
    t = f" {detail_text.lower()} "
    score = 0
    reasons: List[str] = []

    if any(h in t for h in COUNSELING_HINTS):
        score += 3
        reasons.append("counseling keyword found")
    if any(h in t for h in ENDORSEMENT_HINTS):
        score += 2
        reasons.append("endorsement/NT hint found")
    if any(h in t for h in ACTIVE_HINTS):
        score += 2
        reasons.append("active status hint found")

    reason = "; ".join(reasons) if reasons else "no counseling/license hint found"
    return score, reason


def open_and_score_detail(page: Page, row: Locator) -> tuple[int, str, str]:
    row_text = row.inner_text().strip()
    detail_score, detail_reason = score_detail(row_text)

    link = row.locator("a").first
    if link.count() == 0:
        return detail_score, detail_reason, row_text

    before_url = page.url
    try:
        link.click()
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass

    time.sleep(0.2)
    full_text = page.locator("body").inner_text(timeout=4000)
    score2, reason2 = score_detail(full_text)

    score = max(detail_score, score2)
    reason = reason2 if score2 >= detail_score else detail_reason
    try:
        if page.url != before_url:
            page.go_back(timeout=15000)
            page.wait_for_load_state("networkidle", timeout=15000)
        else:
            page.go_back(timeout=8000)
    except Exception:
        back_button = first_visible(
            page,
            [
                "input[type='submit'][value='Back']",
                "button:has-text('Back')",
                "input[type='button'][value='Back']",
            ],
        )
        if back_button:
            back_button.click()
            page.wait_for_load_state("domcontentloaded", timeout=10000)

    return score, reason, row_text


def choose_best_match(page: Page, first_name: str, last_name: str) -> MatchReview:
    rows = get_result_rows(page)
    if not rows:
        return MatchReview(first_name, last_name, "NOT_FOUND", "", "", "No matching rows returned")

    ranked: list[tuple[int, str, str]] = []
    for row in rows:
        try:
            score, reason, row_text = open_and_score_detail(page, row)
        except Exception as exc:
            row_text = row.inner_text().strip()
            score, reason = score_detail(row_text)
            reason = f"{reason}; detail check error: {exc}"
        pic = extract_pic(row_text)
        ranked.append((score, reason, f"{row_text}\nPIC={pic}"))

    ranked.sort(key=lambda x: x[0], reverse=True)
    best_score, best_reason, best_entry = ranked[0]
    best_pic = extract_pic(best_entry)

    status = "REVIEW_REQUIRED"
    if best_score >= 5 and best_pic:
        status = "LIKELY_MATCH"
    elif best_pic and len(ranked) == 1:
        status = "SINGLE_MATCH"

    return MatchReview(first_name, last_name, status, best_pic, best_entry, best_reason)


def lookup_name(page: Page, name: NameRecord) -> MatchReview:
    page.goto("https://mdoe.state.mi.us/MOECS/PublicCredentialSearch.aspx", wait_until="domcontentloaded")
    fill_search_form(page, name.first_name, name.last_name)
    run_search(page)
    return choose_best_match(page, name.first_name, name.last_name)


def run_lookup(
    names: List[NameRecord],
    *,
    headful: bool = False,
    slow_mo_ms: int = 0,
    progress_callback: Optional[Callable[[int, int, MatchReview], None]] = None,
) -> List[MatchReview]:
    # Cloud Linux runtimes (like Render) usually have no DISPLAY/X server,
    # so force headless mode there even if a UI toggle was set to headful.
    has_display = bool(os.environ.get("DISPLAY"))
    effective_headful = headful and (os.name == "nt" or has_display)

    results: List[MatchReview] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not effective_headful,
            slow_mo=slow_mo_ms,
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        ctx = browser.new_context()
        page = ctx.new_page()

        total = len(names)
        for idx, name in enumerate(names, start=1):
            try:
                result = lookup_name(page, name)
            except Exception as exc:
                result = MatchReview(
                    first_name=name.first_name,
                    last_name=name.last_name,
                    status="ERROR",
                    pic="",
                    matched_entry="",
                    reason=str(exc),
                )

            results.append(result)
            if progress_callback:
                progress_callback(idx, total, result)

        browser.close()

    return results


def save_results(path: Path, rows: List[MatchReview]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["first_name", "last_name", "status", "pic", "reason", "matched_entry"])
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def main() -> int:
    args = parse_args()
    names = load_names(Path(args.input))
    results = run_lookup(names, headful=args.headful, slow_mo_ms=args.slow_mo_ms)

    for result in results:
        print(f"{result.first_name} {result.last_name}: {result.status} {result.pic} ({result.reason})")

    save_results(Path(args.output), results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
