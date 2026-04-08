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
from datetime import date, datetime
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from playwright.sync_api import Error as PlaywrightError, Locator, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

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
        page.wait_for_load_state("domcontentloaded", timeout=7000)
    except PlaywrightTimeoutError:
        # Best effort: continue and let downstream selectors decide readiness.
        pass


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


def extract_expiration_date(text: str) -> Optional[date]:
    m = re.search(r"Expiration Date:\s*(\d{1,2}/\d{1,2}/\d{4})", text, flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%m/%d/%Y").date()
    except ValueError:
        return None


def analyze_credential(row_text: str, detail_text: str) -> tuple[int, str, bool]:
    blob = f"{row_text}\n{detail_text}".lower()
    expires_on = extract_expiration_date(detail_text)
    today = date.today()
    is_current = (expires_on is not None and expires_on >= today) or (" active " in f" {blob} " and "expired" not in blob)
    has_permanent = "permanent" in blob and expires_on is None
    has_nt = "(nt)" in blob or " nt " in f" {blob} "
    is_school_counseling = "school counselor license" in blob or "school counselor license renewal" in blob
    has_counseling_terms = any(k in blob for k in ["school counselor", "school counselling", "counseling", "counselling", "guidance counselor"])
    is_teaching_certificate = "teaching certificate" in blob or "certificate type" in blob

    score = 0
    reasons: List[str] = []
    counseling_related = False

    if is_school_counseling:
        counseling_related = True
        score += 140
        reasons.append("school counseling license/renewal")
    elif has_counseling_terms:
        counseling_related = True
        score += 95
        reasons.append("counseling-related credential")

    if has_nt:
        counseling_related = True
        score += 75
        reasons.append("NT endorsement found")

    if is_current:
        score += 45
        reasons.append("current/active credential")
    else:
        score -= 30
        reasons.append("not clearly current")
        if is_school_counseling:
            score -= 90
            reasons.append("school counseling record appears expired")

    if has_permanent:
        score -= 40
        reasons.append("permanent/no expiration")

    if is_teaching_certificate and is_current:
        score += 30
        reasons.append("active teaching certificate")

    if expires_on:
        # Prefer newer expiration dates among otherwise-similar records.
        score += min(expires_on.year - 2000, 50)
        reasons.append(f"expires {expires_on.isoformat()}")

    return score, "; ".join(reasons), counseling_related


def open_and_score_detail(page: Page, row: Locator) -> tuple[int, str, str, str, bool, str]:
    row_text = row.inner_text().strip()
    detail_score, detail_reason, counseling_related = analyze_credential(row_text=row_text, detail_text=row_text)

    link = row.locator("a").first
    row_pic = extract_pic(row_text)
    if link.count() == 0:
        return detail_score, detail_reason, row_text, row_pic, counseling_related, row_text

    before_url = page.url
    try:
        link.click()
        page.wait_for_load_state("domcontentloaded", timeout=6000)
    except PlaywrightTimeoutError:
        pass

    time.sleep(0.2)
    full_text = page.locator("body").inner_text(timeout=4000)
    detail_pic = extract_pic(full_text)
    score2, reason2, counseling_related2 = analyze_credential(row_text=row_text, detail_text=full_text)

    score = max(detail_score, score2)
    reason = reason2 if score2 >= detail_score else detail_reason
    counseling_related = counseling_related2 if score2 >= detail_score else counseling_related
    try:
        if page.url != before_url:
            page.go_back(timeout=7000)
            page.wait_for_load_state("domcontentloaded", timeout=7000)
        else:
            page.go_back(timeout=5000)
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

    return score, reason, row_text, (detail_pic or row_pic), counseling_related, full_text


def choose_best_match(page: Page, first_name: str, last_name: str) -> MatchReview:
    rows = get_result_rows(page)
    if not rows:
        return MatchReview(first_name, last_name, "NOT_FOUND", "", "", "No matching rows returned")

    ranked: list[tuple[int, str, str, str, bool]] = []
    for row in rows[:12]:
        try:
            score, reason, row_text, extracted_pic, counseling_related, _ = open_and_score_detail(page, row)
        except Exception as exc:
            row_text = row.inner_text().strip()
            score, reason, counseling_related = analyze_credential(row_text=row_text, detail_text=row_text)
            reason = f"{reason}; detail check error: {exc}"
            extracted_pic = extract_pic(row_text)

        pic = extracted_pic or extract_pic(row_text)
        if not pic:
            score -= 80
            reason = f"{reason}; no PIC on selected record"
        ranked.append((score, reason, row_text, pic, counseling_related))

        # Early return when we have a strong counseling-related hit with a PIC.
        if counseling_related and pic and score >= 220:
            return MatchReview(first_name, last_name, "LIKELY_MATCH", pic, f"{row_text}\nPIC={pic}", reason)

    ranked.sort(key=lambda x: (bool(x[3]), x[0]), reverse=True)
    _, best_reason, best_row_text, best_pic, best_is_counseling = ranked[0]

    if not best_pic:
        return MatchReview(first_name, last_name, "NOT_FOUND", "", best_row_text, best_reason)

    status = "REVIEW_REQUIRED"
    if best_pic and best_is_counseling:
        status = "LIKELY_MATCH"

    return MatchReview(first_name, last_name, status, best_pic, f"{best_row_text}\nPIC={best_pic}", best_reason)


def lookup_name(page: Page, name: NameRecord) -> MatchReview:
    for _ in range(2):
        try:
            page.goto("https://mdoe.state.mi.us/MOECS/PublicCredentialSearch.aspx", wait_until="domcontentloaded")
            break
        except PlaywrightError as exc:
            if "interrupted by another navigation" not in str(exc).lower():
                raise
            time.sleep(0.2)
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
        browser = None
        page = None
        total = len(names)
        restart_every = 15

        for idx, name in enumerate(names, start=1):
            needs_restart = page is None or (idx - 1) % restart_every == 0
            if needs_restart:
                if browser is not None:
                    browser.close()
                browser = p.chromium.launch(
                    headless=not effective_headful,
                    slow_mo=slow_mo_ms,
                    args=["--disable-dev-shm-usage", "--no-sandbox"],
                )
                ctx = browser.new_context()
            try:
                page = ctx.new_page()
                page.set_default_timeout(6000)
                page.set_default_navigation_timeout(10000)
                result = lookup_name(page, name)
                page.close()
            except Exception as exc:
                try:
                    page.close()
                except Exception:
                    pass
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

        if browser is not None:
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
