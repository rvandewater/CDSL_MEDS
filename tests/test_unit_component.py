"""A lab code's unit component is `lab_06.ud_result` stripped, and `UNK` when missing or blank.

MEDS-Extract compares units stripped and drops blank ones when it writes `codes.parquet`, so a
code that kept the whitespace would split one unit over several codes (` mg`, `mg`, `mg `), and a
blank one would give a code ending in whitespace (or in `//`) with no unit. The expressions are
evaluated straight from the shipped config with dftly, as MEDS-Extract evaluates them.
"""

import polars as pl
import yaml
from conftest import CONFIG_FP
from dftly import Parser


def test_lab_unit_is_stripped_and_blank_is_unk():
    block = yaml.safe_load(CONFIG_FP.read_text())["lab_06"]
    units = ["mg", " mg", "mg ", "\tmg\n", "mL / h", "  ", "", None]
    raw = pl.DataFrame(
        {"ud_result": units, "item_lab": ["GLU"] * len(units)}, schema_overrides={"ud_result": pl.String}
    )
    helper = {"_value_unit": block["_table"]["cols"]["_value_unit"]}
    event = block["laboratory_result"]
    with_cols = raw.with_columns(**Parser.to_polars(helper))
    out = with_cols.select(
        **Parser.to_polars({"code": event["code"], "description": event["_metadata"]["_self"]["description"]})
    )
    expected = ["mg", "mg", "mg", "mg", "mL / h", "UNK", "UNK", "UNK"]
    assert out["code"].to_list() == [f"LAB//GLU//{u}" for u in expected]
    assert out["description"].to_list() == [
        f"Laboratory test: GLU; unit: {u} (CDSL lab_06.item_lab, lab_06.ud_result)" for u in expected
    ]
    # `unit` stays the raw column; MEDS-Extract strips it when it writes `codes.parquet`.
    assert event["_metadata"]["_self"]["unit"] == "$ud_result"
