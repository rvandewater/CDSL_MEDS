"""Every code family of the config is described, and the ``cdsl_describe_slot_codes`` stage."""

import doctest
import re
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import yaml
from meds import CodeMetadataSchema

from CDSL_MEDS import code_descriptions
from CDSL_MEDS.code_descriptions import apply_code_metadata, read_icd10_dictionary, slot_code_description

CONFIG_FP = Path(__file__).parents[1] / "src" / "CDSL_MEDS" / "event_configs.yaml"
# Code families emitted by one event per wide-table column, described by `slot_code_description`
# (procedure codes are left to suMEDS).
SLOT_FAMILIES = {"DIAGNOSIS_ICD10", "PROCEDURE_ICD10PCS", "NEOPLASM_ICDO3"}
# The literal codes of the config, each described by a constant `_self` block.
LITERAL_CODES = {
    "HOSPITAL_ADMISSION",
    "ICU_ADMISSION",
    "ICU_DISCHARGE",
    "AGE_AT_ADMISSION",
    "EMERGENCY_DEPARTMENT_VISIT",
    "MECHANICAL_VENTILATION",
    *(
        f"VITAL//{name}"
        for name in (
            "SYSTOLIC_BLOOD_PRESSURE",
            "DIASTOLIC_BLOOD_PRESSURE",
            "TEMPERATURE",
            "HEART_RATE",
            "OXYGEN_SATURATION",
            "BLOOD_GLUCOSE",
            "URINE_OUTPUT",
        )
    ),
}
# A constant dftly string: a double-quoted string without column references.
CONSTANT = re.compile(r'^"[^"$]+"$')


def _events():
    cfg = yaml.safe_load(CONFIG_FP.read_text())
    for table, body in cfg.items():
        if table in ("_defaults", "etl", "sources"):
            continue
        for name, event in body.items():
            if not name.startswith("_"):
                yield table, name, event


def _literal_code(code: str) -> str | None:
    """The code of a literal code expression (``X``, ``"X"``, or ``"X" if ...``), else None."""
    head = code.split(" if ")[0].strip()
    return None if "$" in head else head.strip('"')


def test_every_literal_code_event_has_one_constant_description():
    """Each event that emits a literal code describes it with a constant ``_self`` block.

    Events that emit the same literal share one description (a YAML alias), so ``codes.parquet`` gets
    exactly one per code.
    """
    descriptions: dict[str, set[str]] = {}
    for table, name, event in _events():
        if not (code := _literal_code(str(event["code"]))):
            continue
        blocks = event.get("_metadata", {})
        assert set(blocks) == {"_self"}, f"{table}/{name}: literal codes take only a _self block"
        description = blocks["_self"]["description"]
        assert CONSTANT.match(description), f"{table}/{name}: {description!r} is not a constant string"
        descriptions.setdefault(code, set()).add(description)
    assert set(descriptions) == LITERAL_CODES
    assert all(len(d) == 1 for d in descriptions.values()), descriptions


def test_every_other_code_family_is_described():
    """Each component-coded event declares a description, or is a slot family filled after the pipeline."""
    undescribed = []
    for table, name, event in _events():
        code = str(event["code"])
        if _literal_code(code):
            continue
        family = code.split("//")[0].removeprefix('f"')
        blocks = [b for b in event.get("_metadata", {}).values() if "description" in b]
        if family in SLOT_FAMILIES:
            assert not event.get("_metadata"), f"{table}/{name}: slot events cannot carry _metadata"
        elif not blocks:
            undescribed.append(f"{table}/{name}")
    assert not undescribed, undescribed


def test_read_icd10_dictionary(tmp_path):
    # Two lines of the public CMS ICD-10-CM FY2021 order file, in the release's "|"-joined layout.
    fp = tmp_path / "icd10_codes_dict.txt"
    fp.write_text(
        "11096|I10|1|Essential (primary) hypertension|Essential (primary) hypertension\n"
        "00001|A00|0|Cholera|Cholera\n"
    )
    assert read_icd10_dictionary(fp) == {"I10": "Essential (primary) hypertension", "A00": "Cholera"}


def test_slot_codes_get_dataset_descriptions_only():
    titles = {"I10": "Essential (primary) hypertension"}
    cases = {
        "DIAGNOSIS_ICD10//I10": "Essential (primary) hypertension",
        "DIAGNOSIS_ICD10//E11.9": None,  # not in the release's dictionary: left to suMEDS
        "DIAGNOSIS_ICD10//8140/3": "ICD-O-3 morphology code 8140/3 in an ICD-10 diagnosis column",
        "DIAGNOSIS_ICD10//not a code": "Diagnosis code not a code",
        "PROCEDURE_ICD10PCS//5A1955Z": None,  # official title from suMEDS's ICD-10-PCS pack
        "NEOPLASM_ICDO3//8140/32": "Neoplasm, ICD-O-3 morphology code 8140/32",
        "LAB//Glucose//mg/dL": None,
    }
    for code, expected in cases.items():
        got = slot_code_description(code, titles)
        assert (got and got.split(" (CDSL")[0]) == expected, code


def test_apply_code_metadata_fills_only_missing_values(tmp_path):
    codes = pl.DataFrame(
        {
            "code": [
                "VITAL//HEART_RATE",
                "DIAGNOSIS_ICD10//8140/3",
                "DIAGNOSIS_ICD10//I10",
                "MEDICATION//ATC5//X",
            ],
            "description": [None, "already extracted", None, "kept"],
            "parent_codes": [None, None, None, ["ATC/H03AA"]],
            "code_template": [None, None, None, 'f"MEDICATION//ATC5//{$id_atc5}"'],
        }
    )
    dictionary = tmp_path / "icd10_codes_dict.txt"
    dictionary.write_text("11096|I10|1|Essential (primary) hypertension|Essential (primary) hypertension\n")
    fp = tmp_path / "codes.parquet"
    codes.write_parquet(fp)

    assert apply_code_metadata(fp, dictionary) == {"descriptions": 1}
    out = pl.read_parquet(fp)
    assert out.columns == codes.columns and out["code"].to_list() == codes["code"].to_list()
    rows = {r["code"]: r for r in out.iter_rows(named=True)}
    assert rows["VITAL//HEART_RATE"]["description"] is None  # a literal code: described by the spec
    assert rows["DIAGNOSIS_ICD10//8140/3"]["description"] == "already extracted"
    assert rows["DIAGNOSIS_ICD10//I10"]["description"] == "Essential (primary) hypertension"
    # Parent codes are suMEDS's job: existing ones are kept, none are added.
    assert rows["DIAGNOSIS_ICD10//I10"]["parent_codes"] is None
    assert rows["MEDICATION//ATC5//X"]["parent_codes"] == ["ATC/H03AA"]
    assert apply_code_metadata(fp, dictionary) == {"descriptions": 0}
    # Still valid MEDS code metadata as read from disk (string, not large_string).
    CodeMetadataSchema.validate(pq.read_table(fp))


def test_apply_code_metadata_leaves_procedures_and_parents_to_sumeds(tmp_path):
    fp = tmp_path / "codes.parquet"
    pl.DataFrame(
        {"code": ["PROCEDURE_ICD10PCS//5A1955Z", "NEOPLASM_ICDO3//8140/3"], "description": [None, None]},
        schema_overrides={"description": pl.String},
    ).write_parquet(fp)
    assert apply_code_metadata(fp) == {"descriptions": 1}
    out = pl.read_parquet(fp)
    assert out.columns == ["code", "description"]
    assert out["description"].is_null().to_list() == [True, False]
    CodeMetadataSchema.validate(pq.read_table(fp))


def test_docstring_examples():
    assert doctest.testmod(code_descriptions).failed == 0
