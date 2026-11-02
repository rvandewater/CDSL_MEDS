"""The packaged code metadata and the config blocks that describe codes stay in step."""

import doctest
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import yaml
from meds import CodeMetadataSchema

from CDSL_MEDS import code_descriptions
from CDSL_MEDS.code_descriptions import (
    STATIC_DESCRIPTIONS_CSV,
    apply_code_metadata,
    read_icd10_dictionary,
    read_static_code_descriptions,
    slot_code_description,
)

CONFIG_FP = Path(__file__).parents[1] / "src" / "CDSL_MEDS" / "event_configs.yaml"
# Code families emitted by one event per wide-table column, described by `slot_code_description`
# (procedure codes are left to suMEDS).
SLOT_FAMILIES = {"DIAGNOSIS_ICD10", "PROCEDURE_ICD10PCS", "NEOPLASM_ICDO3"}


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


def test_static_table_is_complete_and_sourced():
    table = pl.read_csv(STATIC_DESCRIPTIONS_CSV, infer_schema=False)
    assert table.columns == ["code", "description", "source"]
    assert not table["code"].is_duplicated().any()
    assert table.select(pl.all().str.len_chars().min()).row(0) > (0, 0, 0)


def test_static_table_covers_exactly_the_literal_codes():
    literal = {c for _, _, e in _events() if (c := _literal_code(str(e["code"])))}
    assert literal, "no literal codes found; the detection is stale"
    static = set(read_static_code_descriptions()["code"])
    assert literal == static, {"undescribed": literal - static, "stale": static - literal}


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
                "HOSPITAL_ADMISSION",
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

    assert apply_code_metadata(fp, dictionary) == {"descriptions": 2}
    out = pl.read_parquet(fp)
    assert out.columns == codes.columns and out["code"].to_list() == codes["code"].to_list()
    rows = {r["code"]: r for r in out.iter_rows(named=True)}
    assert rows["VITAL//HEART_RATE"]["description"].startswith("Heart rate")
    assert rows["HOSPITAL_ADMISSION"]["description"] == "already extracted"
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
