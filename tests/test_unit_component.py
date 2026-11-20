"""CDSL codes that end in a unit, tested with suMEDS's shared `UnitComponentContract`.

The contract finds every code of the spec whose last component is a unit and evaluates it with
dftly, as MEDS-Extract does: the component is the unit stripped, `UNK` where a row records none
or a blank one, and the `unit` metadata stays the raw column. `expected_unit_events` pins the
events it finds, so a new code that ends in a unit is a conscious change here. The lab
description, which the contract does not check, names the stripped unit too.
"""

import polars as pl
import yaml
from conftest import CONFIG_FP
from dftly import Parser
from sumeds.testing.contracts import UnitComponentContract


class TestUnitComponents(UnitComponentContract):
    spec = "CDSL"
    expected_unit_events = ("lab_06/laboratory_result",)


def test_lab_description_names_the_stripped_unit():
    block = yaml.safe_load(CONFIG_FP.read_text())["lab_06"]
    units = ["mg", " mg", "mg ", "\tmg\n", "mL / h", "  ", "", None]
    raw = pl.DataFrame(
        {"ud_result": units, "item_lab": ["GLU"] * len(units)}, schema_overrides={"ud_result": pl.String}
    )
    helper = {"_value_unit": block["_table"]["cols"]["_value_unit"]}
    description = {"description": block["laboratory_result"]["_metadata"]["_self"]["description"]}
    out = raw.with_columns(**Parser.to_polars(helper)).select(**Parser.to_polars(description))
    expected = ["mg", "mg", "mg", "mg", "mL / h", "UNK", "UNK", "UNK"]
    assert out["description"].to_list() == [
        f"Laboratory test: GLU; unit: {u} (CDSL lab_06.item_lab, lab_06.ud_result)" for u in expected
    ]
