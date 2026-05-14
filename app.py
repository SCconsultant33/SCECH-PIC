#!/usr/bin/env python3
"""Streamlit UI for the MOECS PIC lookup agent."""

from __future__ import annotations

import base64
import csv
import html
import io
import os
from pathlib import Path
import time

import streamlit as st

from moecs_pic_agent import MatchReview, NameRecord, parse_names_from_reader, run_lookup


st.set_page_config(page_title="MOECS PIC Lookup", layout="wide")
CHECKPOINT_PATH = Path("/tmp/moecs_last_results.csv")

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
chunk_size = st.number_input("Chunk size (entries per run)", min_value=5, max_value=100, value=20, step=5)
run_clicked = st.button("Run Lookup", type="primary", disabled=uploaded_file is None)

if "last_results" not in st.session_state:
    st.session_state.last_results = []
if "current_run_results" not in st.session_state:
    st.session_state.current_run_results = []
if "last_results_csv" not in st.session_state:
    st.session_state.last_results_csv = b""
if "pending_names" not in st.session_state:
    st.session_state.pending_names = []
if "total_names" not in st.session_state:
    st.session_state.total_names = 0
if "auto_continue" not in st.session_state:
    st.session_state.auto_continue = False


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


def csv_data_uri(csv_bytes: bytes) -> str:
    encoded = base64.b64encode(csv_bytes).decode("ascii")
    return f"data:text/csv;charset=utf-8;base64,{encoded}"


def write_checkpoint(rows: list[dict[str, str]]) -> None:
    try:
        CHECKPOINT_PATH.write_bytes(to_csv_bytes(rows))
    except Exception:
        pass


def load_checkpoint() -> list[dict[str, str]]:
    if not CHECKPOINT_PATH.exists():
        return []
    try:
        text = CHECKPOINT_PATH.read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(text))
        return list(reader)
    except Exception:
        return []


if not st.session_state.last_results:
    restored = load_checkpoint()
    if restored:
        st.session_state.last_results = restored
        st.session_state.last_results_csv = to_csv_bytes(restored)


def run_chunk(chunk: list[dict[str, str]]) -> None:
    names = [NameRecord(first_name=r["first_name"], last_name=r["last_name"]) for r in chunk]
    progress = st.progress(0)
    status = st.empty()
    completed_before = len(st.session_state.last_results)
    total = st.session_state.total_names or (completed_before + len(names))

    def on_progress(current: int, _: int, result: MatchReview) -> None:
        overall_done = completed_before + current
        progress.progress(overall_done / total)
        status.info(f"{overall_done}/{total} complete: {result.first_name} {result.last_name} -> {result.status} {result.pic}")
        st.session_state.current_run_results.append(review_to_dict(result))
        st.session_state.last_results = st.session_state.current_run_results[:]
        st.session_state.last_results_csv = to_csv_bytes(st.session_state.last_results)
        write_checkpoint(st.session_state.last_results)

    try:
        with st.spinner("Running lookup automation..."):
            run_lookup(names, headful=headful, slow_mo_ms=int(slow_mo_ms), progress_callback=on_progress)
            st.session_state.last_results = st.session_state.current_run_results[:]
            st.session_state.last_results_csv = to_csv_bytes(st.session_state.last_results)
            write_checkpoint(st.session_state.last_results)
        status.success("Chunk complete.")
    except Exception as exc:
        status.error(f"Run interrupted: {exc}")
        if st.session_state.current_run_results:
            st.warning("Showing partial results collected before interruption.")
            st.session_state.last_results = st.session_state.current_run_results[:]
            st.session_state.last_results_csv = to_csv_bytes(st.session_state.last_results)
            write_checkpoint(st.session_state.last_results)


if run_clicked and uploaded_file is not None:
    try:
        text = uploaded_file.getvalue().decode("utf-8-sig")
        names = parse_names_from_reader(csv.DictReader(io.StringIO(text)))
    except Exception as exc:
        st.error(f"Could not parse CSV: {exc}")
    else:
        try:
            CHECKPOINT_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        st.session_state.current_run_results = []
        st.session_state.last_results = []
        st.session_state.last_results_csv = b""
        st.session_state.pending_names = [{"first_name": n.first_name, "last_name": n.last_name} for n in names]
        st.session_state.total_names = len(names)
        st.session_state.auto_continue = True

        next_chunk = st.session_state.pending_names[: int(chunk_size)]
        st.session_state.pending_names = st.session_state.pending_names[int(chunk_size) :]
        run_chunk(next_chunk)
        st.rerun()

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
    csv_bytes = st.session_state.last_results_csv or to_csv_bytes(st.session_state.last_results)
    st.download_button(
        "Download full results CSV",
        data=csv_bytes,
        file_name="pic_lookup_results.csv",
        mime="text/csv",
        key="download_results_csv",
        on_click="ignore",
    )
    st.markdown(
        f'<a download="pic_lookup_results.csv" href="{html.escape(csv_data_uri(csv_bytes))}">'
        "Direct CSV download link"
        "</a>",
        unsafe_allow_html=True,
    )
    st.caption("Rows marked REVIEW_REQUIRED should be manually checked before use.")

if st.session_state.pending_names:
    st.info(
        f"{len(st.session_state.last_results)}/{st.session_state.total_names} processed. "
        f"{len(st.session_state.pending_names)} entries remain."
    )

if st.session_state.auto_continue and st.session_state.pending_names:
    st.caption("Continuing with next chunk automatically...")
    time.sleep(0.8)
    next_chunk = st.session_state.pending_names[: int(chunk_size)]
    st.session_state.pending_names = st.session_state.pending_names[int(chunk_size) :]
    run_chunk(next_chunk)
    st.rerun()
elif st.session_state.auto_continue and not st.session_state.pending_names:
    st.session_state.auto_continue = False
