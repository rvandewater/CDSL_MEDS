"""``meds-extract-run spec=CDSL`` builds what the removed ``MEDS_extract-CDSL`` runner built.

Up to 2f08708, ``MEDS_extract-CDSL`` ran ``meds-extract-run`` and then filled two kinds of descriptions
into the finished ``codes.parquet``: those of the literal codes, from a packaged CSV, and those of the
slot-coded diagnosis and neoplasm codes. The spec now does both itself, with constant ``_self`` blocks
and the ``cdsl_describe_slot_codes`` post-stage. ``regression/runner_2f08708_fixture.json`` holds what
the runner built from the synthetic release of ``conftest.py`` (with ``--no-enrich``): every code, its
description and its number of events. The one difference is intended: literal codes now also carry
their ``code_template``, as every other described code does.
"""

import json
from pathlib import Path

import polars as pl
import pytest

GOLDEN = Path(__file__).parent / "regression" / "runner_2f08708_fixture.json"


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN.read_text())


def test_same_codes_descriptions_and_event_counts(plain_output: Path, golden: dict):
    events = pl.concat([pl.read_parquet(fp) for fp in (plain_output / "data").rglob("*.parquet")])
    counts = dict(events.group_by("code").len().iter_rows())
    codes = pl.read_parquet(plain_output / "metadata/codes.parquet")
    built = {
        code: {"description": description, "events": counts[code]}
        for code, description in codes.select("code", "description").iter_rows()
    }
    assert events.height == golden["events"]
    assert built == golden["codes"]


def test_literal_codes_carry_their_code_template(plain_output: Path):
    codes = {
        r["code"]: r for r in pl.read_parquet(plain_output / "metadata/codes.parquet").iter_rows(named=True)
    }
    assert codes["HOSPITAL_ADMISSION"]["code_template"] == "HOSPITAL_ADMISSION"
    assert codes["VITAL//HEART_RATE"]["code_template"] == '"VITAL//HEART_RATE"'
    # Slot-coded codes have one template per column, so they still carry none.
    assert codes["DIAGNOSIS_ICD10//I10"]["code_template"] is None
