#!/usr/bin/env python3
"""Streamlit UI for the MOECS PIC lookup agent."""

from __future__ import annotations

import csv
import io
import os

import streamlit as st

from moecs_pic_agent import MatchReview, parse_names_from_reader, run_lookup


st.set_page_config(page_title="MOECS PIC Lookup", layout="wide")

required_key = os.getenv("APP_ACCESS_KEY", "").strip()
if required_key:
    entered_key = st.text_input("Access key", type="password")
    if entered_key != required_key:
        st.warning("Enter your access key to use this app.")
        st.stop()

st.title("MOECS PIC Lookup")
st.caption("Upload a CSV with first_name,last_name and run lookups without using the terminal.")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
is_hosted = bool(os.getenv("RENDER")) or bool(os.getenv("PORT"))
if is_hosted:
    st.info("Hosted mode detected: browser UI is disabled, running in headless mode.")
    headful = False
else:
    headful = st.checkbox("Show browser while running", value=False)
slow_mo_ms = st.number_input("Slow motion (milliseconds)", min_value=0, max_value=3000, value=0, step=100)
run_clicked = st.button("Run Lookup", type="primary", disabled=uploaded_file is None)

if "last_results" not in st.session_state:
    st.session_state.last_results = []
if "current_run_results" not in st.session_state:
    st.session_state.current_run_results = []
if "last_results_csv" not in st.session_state:
    st.session_state.last_results_csv = b""


def review_to_dict(row: MatchReview) -> dict[str, str]:
    return {
        "first_name": row.first_name,
        "last_name": row.last_name,
        "status": row.status,
        "pic": row.pic,
        "reason": row.reason,
        "matched_entry": row.matched_entry,
    }


def to_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["first_name", "last_name", "status", "pic", "reason", "matched_entry"])
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


if run_clicked and uploaded_file is not None:
    try:
        text = uploaded_file.getvalue().decode("utf-8-sig")
        names = parse_names_from_reader(csv.DictReader(io.StringIO(text)))
    except Exception as exc:
        st.error(f"Could not parse CSV: {exc}")
    else:
        st.session_state.current_run_results = []
        progress = st.progress(0)
        status = st.empty()

        def on_progress(current: int, total: int, result: MatchReview) -> None:
            progress.progress(current / total)
            status.info(f"{current}/{total} complete: {result.first_name} {result.last_name} -> {result.status} {result.pic}")
            st.session_state.current_run_results.append(review_to_dict(result))
            st.session_state.last_results = st.session_state.current_run_results[:]
            st.session_state.last_results_csv = to_csv_bytes(st.session_state.last_results)

        try:
            with st.spinner("Running lookup automation..."):
                rows = run_lookup(names, headful=headful, slow_mo_ms=int(slow_mo_ms), progress_callback=on_progress)
                st.session_state.last_results = [review_to_dict(r) for r in rows]
                st.session_state.last_results_csv = to_csv_bytes(st.session_state.last_results)
            status.success("Lookup run complete.")
        except Exception as exc:
            status.error(f"Run interrupted: {exc}")
            if st.session_state.current_run_results:
                st.warning("Showing partial results collected before interruption.")
                st.session_state.last_results = st.session_state.current_run_results[:]

        st.session_state.current_run_results = []

if st.session_state.last_results:
    table_rows = [
        {
            "first_name": r["first_name"],
            "last_name": r["last_name"],
            "status": r["status"],
            "pic": r["pic"],
            "reason": r["reason"],
        }
        for r in st.session_state.last_results
    ]
    st.subheader("Results")
    st.dataframe(table_rows, use_container_width=True)
    st.download_button(
        "Download full results CSV",
        data=st.session_state.last_results_csv or to_csv_bytes(st.session_state.last_results),
        file_name="pic_lookup_results.csv",
        mime="text/csv",
        key="download_results_csv",
    )
    st.caption("Rows marked REVIEW_REQUIRED should be manually checked before use.")
