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
        if code in counts
    }
    codes_by_name = dict(codes.select("code", "description").iter_rows())
    # The fixture has since gained `ROW_OVERRIDES` (conftest.py): subject 2's padded lab unit " X "
    # still gives `LAB//X//X`, while subject 3's lab moved to item "Y" with a blank unit, `LAB//Y//UNK`.
    expected = json.loads(json.dumps(golden["codes"]))
    expected["LAB//X//X"]["events"] -= 1
    expected["LAB//Y//UNK"] = {
        "description": "Laboratory test: Y; unit: UNK (CDSL lab_06.item_lab, lab_06.ud_result)",
        "events": 1,
    }
    # Subject 1's stay ends in death: its discharge is `HOSPITAL_DISCHARGE//Death`, and the death is
    # also `MEDS_DEATH` at that discharge (which the runner predates).
    expected["HOSPITAL_DISCHARGE//X"]["events"] -= 1
    expected["HOSPITAL_DISCHARGE//Death"] = {
        "description": "Hospital discharge; discharge destination: Death (CDSL patient_01.destin_discharge)",
        "events": 1,
    }
    expected["MEDS_DEATH"] = {"description": codes_by_name["MEDS_DEATH"], "events": 1}
    # The only codes without events are unit-free stems (suMEDS's `sumeds_link_unit_siblings`, which
    # the runner predates): each is another code without its unit component.
    stems = set(codes["code"]) - set(counts)
    assert all(any(c.startswith(f"{stem}//") for c in counts) for stem in stems), stems
    assert events.height == golden["events"] + 1  # MEDS_DEATH
    assert built == expected


def test_literal_codes_carry_their_code_template(plain_output: Path):
    codes = {
        r["code"]: r for r in pl.read_parquet(plain_output / "metadata/codes.parquet").iter_rows(named=True)
    }
    assert codes["HOSPITAL_ADMISSION"]["code_template"] == "HOSPITAL_ADMISSION"
    assert codes["VITAL//HEART_RATE"]["code_template"] == '"VITAL//HEART_RATE"'
    # Slot-coded codes have one template per column, so they still carry none.
    assert codes["DIAGNOSIS//ICD//10CM//I10"]["code_template"] is None
