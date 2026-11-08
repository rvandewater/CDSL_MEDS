import json

import polars as pl
import pyarrow.parquet as pq
import pytest
import yaml
from conftest import ATC7_NAME, COMMERCIAL_NAME, ROOT
from meds import CodeMetadataSchema, DatasetMetadataSchema
from MEDS_extract.config import MessyConfig


def test_config_loads_and_excludes_images():
    config = MessyConfig.load("./src/CDSL_MEDS/event_configs.yaml")
    needed = config.needed_source_columns()

    assert {"patient_01", "lab_06", "medication_05", "vital_signs_04"} <= set(needed)
    assert {"lab_id", "medication_id", "vital_sign_id"} <= set().union(*needed.values())
    raw = yaml.safe_load((ROOT / "src/CDSL_MEDS/event_configs.yaml").read_text())
    assert raw["sources"]["dataset"][0]["exclude"] == ["IMAGES/**"]


def test_etl_runs_on_a_minimal_valid_fixture(plain_output):
    """``skip_stages=[sumeds_enrich]``: the ETL and its own descriptions, without suMEDS."""
    output = plain_output
    assert (output / "metadata/dataset.json").is_file()
    assert list((output / "data").glob("**/*.parquet"))

    # Every code but the ICD-10-PCS procedures (titled by suMEDS) is described, the ETL writes no
    # vocabulary parent codes (suMEDS adds them), and the file is valid MEDS.
    codes = pq.read_table(output / "metadata/codes.parquet")
    CodeMetadataSchema.validate(codes)
    metadata = {r["code"]: r for r in codes.to_pylist()}
    undescribed = {c for c, r in metadata.items() if not r["description"]}
    assert undescribed == {"PROCEDURE_ICD10PCS//5A1955Z"}
    assert not [c for c, r in metadata.items() if r.get("parent_codes")]
    assert metadata["DIAGNOSIS_ICD10//I10"]["description"] == "Essential (primary) hypertension"
    assert metadata["NEOPLASM_ICDO3//8140/3"]["description"].startswith("Neoplasm, ICD-O-3 morphology")
    assert metadata["MEDICATION//ATC5//H03AA//ATC7//H03AA01"]["description"] == ATC7_NAME
    assert metadata["MEDICATION_END//ATC5//H03AA//ATC7//H03AA01"]["description"].startswith(
        f"End of medication: {ATC7_NAME}"
    )


def test_latin1_sources_are_decoded(plain_output, raw_dir):
    """The fixture's Latin-1 tables are not UTF-8, yet their text arrives intact (`etl.encoding`)."""
    events = pl.concat([pl.read_parquet(fp) for fp in (plain_output / "data").rglob("*.parquet")])
    medications = events.filter(pl.col("code").str.starts_with("MEDICATION//"))
    assert medications.height and set(medications["text_value"]) == {COMMERCIAL_NAME}
    with pytest.raises(UnicodeDecodeError):
        (raw_dir / "atc7.csv").read_text(encoding="utf-8")


def test_dataset_json_describes_the_release_and_the_extra_columns(plain_output):
    """The `etl:` descriptors reach `metadata/dataset.json`, and every extra column is accounted for."""
    metadata = json.loads((plain_output / "metadata/dataset.json").read_text())
    DatasetMetadataSchema.validate(metadata)
    assert metadata["license"] == "PhysioNet Contributor Review Health Data License 1.5.0"
    assert metadata["description_uri"] == "https://physionet.org/content/covid-data-shared-learning/1.0.0/"
    assert metadata["raw_source_id_columns"] == [
        "lab_id",
        "medication_id",
        "vital_sign_id",
        "study_id",
        "image_id",
    ]
    events = pl.concat([pl.read_parquet(fp) for fp in (plain_output / "data").rglob("*.parquet")])
    core = {"subject_id", "time", "code", "numeric_value", "text_value"}
    listed = set(metadata["raw_source_id_columns"]) | set(metadata["other_extension_columns"])
    assert listed == set(events.columns) - core
