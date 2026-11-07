"""``MEDS_extract-CDSL`` runs the ETL, fills CDSL's own descriptions, then enriches with suMEDS, offline.

This package registers its rules in suMEDS's ``sumeds.rules`` entry-point group under the same name
as its MEDS-Extract spec, ``CDSL``, and the runner enriches with them as its last step. The
ontology source is a tiny pack repository in suMEDS's ``sumeds-ontologies/0.1`` layout, built here
with made-up titles for the synthetic codes of ``conftest.py`` (and their ancestors), so the test
reads nothing over the network and does not depend on the published packs. An empty pack source
stands in for a site without internet access, where the enrichment soft-fails.
"""

import csv
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest
from conftest import run_etl
from meds import CodeMetadataSchema, DatasetMetadataSchema

# vocabulary -> release -> code -> parent (None for a root).
PACKS = {
    "ICD10CM": ("fy2016-fy2027", {"I10": None}),
    "ICD10PCS": (
        "fy2016-fy2027",
        {
            "5": None,
            "5A": "5",
            "5A1": "5A",
            "5A19": "5A1",
            "5A195": "5A19",
            "5A1955": "5A195",
            "5A1955Z": "5A1955",
        },
    ),
}


def title(vocabulary: str, code: str) -> str:
    return f"Synthetic {vocabulary} title {code}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="session")
def ontology_source(tmp_path_factory) -> Path:
    """A local `sumeds-ontologies/0.1` repository holding the synthetic ICD packs."""
    root = tmp_path_factory.mktemp("ontologies")
    columns = ["code", "description", "parent", "first_year", "last_year"]
    vocabularies = {}
    for vocabulary, (release, rows) in PACKS.items():
        directory = root / vocabulary / release
        directory.mkdir(parents=True)
        by_shard: dict[str, list[list[str]]] = {}
        for code, parent in rows.items():
            by_shard.setdefault(code[:1], []).append([code, title(vocabulary, code), parent or "", "", ""])
        shards = {}
        for key, shard_rows in by_shard.items():
            buffer = io.StringIO()
            csv.writer(buffer, lineterminator="\n").writerows([columns, *shard_rows])
            data = buffer.getvalue().encode()
            (directory / f"{key}.csv").write_bytes(data)
            shards[key] = {
                "file": f"{key}.csv",
                "rows": len(shard_rows),
                "bytes": len(data),
                "sha256": _sha256(data),
            }
        manifest = {
            "schema": "sumeds-ontology-pack/0.1",
            "vocabulary": vocabulary,
            "release": release,
            "columns": columns,
            "fields": {"code": "code", "description": "description", "parent": "parent"},
            "shard_key_length": 1,
            "rows": len(rows),
            "bytes": sum(s["bytes"] for s in shards.values()),
            "shards": shards,
        }
        manifest_bytes = json.dumps(manifest).encode()
        (directory / "manifest.json").write_bytes(manifest_bytes)
        vocabularies[vocabulary] = {
            "manifest": f"{vocabulary}/{release}/manifest.json",
            "sha256": _sha256(manifest_bytes),
            "release": release,
            "rows": manifest["rows"],
            "bytes": manifest["bytes"],
        }
    (root / "index.json").write_text(
        json.dumps({"schema": "sumeds-ontologies/0.1", "vocabularies": vocabularies})
    )
    return root


@pytest.fixture(scope="session")
def enriched_output(tmp_path_factory, raw_dir: Path, ontology_source: Path) -> Path:
    output = tmp_path_factory.mktemp("cdsl_enriched") / "meds"
    result = run_etl(output, raw_dir, "--", "--ontology-source", str(ontology_source))
    assert result.returncode == 0, result.stderr[-2000:]
    return output


def read_codes(output: Path) -> dict[str, dict]:
    """``codes.parquet`` by code, read with pyarrow, so a names map is a list of (key, value) pairs."""
    return {row["code"]: row for row in pq.read_table(output / "metadata/codes.parquet").to_pylist()}


def read_record(output: Path) -> dict:
    """The enrichment record suMEDS writes into ``metadata/dataset.json``, which must stay valid MEDS."""
    metadata = json.loads((output / "metadata/dataset.json").read_text())
    DatasetMetadataSchema.validate(metadata)
    return metadata["sumeds_enrichment"]


def test_codes_parquet_is_replaced_by_valid_meds_metadata(enriched_output: Path):
    metadata = enriched_output / "metadata"
    CodeMetadataSchema.validate(pq.read_table(metadata / "codes.parquet"))
    assert not list(metadata.glob(".*enriched*")), "the temporary enrichment output was left behind"


def test_data_matches_the_plain_etl(enriched_output: Path, plain_output: Path):
    def events(output: Path) -> pl.DataFrame:
        key = ["subject_id", "time", "code"]
        return (
            pl.concat([pl.read_parquet(fp) for fp in (output / "data").rglob("*.parquet")])
            .select(key)
            .sort(key)
        )

    assert events(enriched_output).equals(events(plain_output))


def test_enrichment_keeps_the_dataset_metadata(enriched_output: Path, plain_output: Path):
    """suMEDS only fills nulls: every description the ETL wrote is kept, and existing parents lead."""
    plain, enriched = read_codes(plain_output), read_codes(enriched_output)
    assert set(enriched) == set(plain)
    for code, row in plain.items():
        if row["description"] is not None:
            assert enriched[code]["description"] == row["description"], code
        parents = row.get("parent_codes") or []
        assert (enriched[code]["parent_codes"] or [])[: len(parents)] == parents, code


def test_vocabulary_codes_get_titles_and_parents(enriched_output: Path):
    rows = read_codes(enriched_output)

    # No description from the ETL: the ICD-10-PCS pack title fills it, and its ancestors are parents.
    procedure = rows["PROCEDURE_ICD10PCS//5A1955Z"]
    pcs = ["5A1955Z", "5A1955", "5A195", "5A19", "5A1", "5A", "5"]
    assert procedure["description"] == title("ICD10PCS", "5A1955Z")
    assert procedure["parent_codes"] == [f"ICD10PCS//{code}" for code in pcs]
    assert dict(procedure["parent_names"]) == {f"ICD10PCS//{code}": title("ICD10PCS", code) for code in pcs}

    # The release's own ICD-10-CM title is kept; the vocabulary code and its name are added.
    diagnosis = rows["DIAGNOSIS_ICD10//I10"]
    assert diagnosis["description"] == "Essential (primary) hypertension"
    assert diagnosis["parent_codes"] == ["ICD10CM//I10"]
    assert dict(diagnosis["parent_names"]) == {"ICD10CM//I10": title("ICD10CM", "I10")}

    # ICD-O-3 and ATC have no suMEDS pack: their codes are tagged, without a name.
    assert rows["NEOPLASM_ICDO3//8140/3"]["parent_codes"] == ["ICDO3//8140/3"]
    for family in ("MEDICATION", "MEDICATION_END"):
        medication = rows[f"{family}//ATC5//H03AA//ATC7//H03AA01"]
        assert medication["parent_codes"] == ["ATC//H03AA01"]
        assert not medication["parent_names"]

    # Codes that no rule matches are left alone.
    assert not rows["HOSPITAL_ADMISSION"]["parent_codes"]


def test_bad_sumeds_options_fail_before_the_pipeline(tmp_path, raw_dir: Path):
    output = tmp_path / "meds"
    result = run_etl(output, raw_dir, "--", "--no-such-option")
    assert result.returncode != 0
    assert "--no-such-option" in result.stderr
    assert not output.exists()


def test_dataset_json_records_the_enrichment(enriched_output: Path):
    record = read_record(enriched_output)
    assert record["enriched"] is True
    assert record["rules"] == "CDSL"
    assert record["ontology_source"] == "local"
    coverage = record["coverage"]
    assert coverage["codes"] == len(read_codes(enriched_output))
    assert coverage["described"][1] > coverage["described"][0]
    assert coverage["with_parent_codes"][1] > coverage["with_parent_codes"][0]


def test_a_failed_enrichment_keeps_the_build_and_can_be_retried(
    tmp_path, raw_dir: Path, plain_output: Path, enriched_output: Path, ontology_source: Path
):
    """Without packs (as without internet) the run still succeeds; ``suMEDS-enrich OUT`` finishes it."""
    output, no_packs = tmp_path / "meds", tmp_path / "no-packs"
    no_packs.mkdir()
    result = run_etl(output, raw_dir, "--", "--ontology-source", str(no_packs))
    assert result.returncode == 0, result.stderr[-2000:]
    assert "once the cause is fixed, enrich it with" in result.stderr
    codes = output / "metadata/codes.parquet"
    assert codes.read_bytes() == (plain_output / "metadata/codes.parquet").read_bytes()
    record = read_record(output)
    assert record["enriched"] is False
    assert record["rules"] == "CDSL"
    assert record["retry"].startswith("suMEDS-enrich <MEDS_ROOT>")

    # The packs directory is a path, which the record never holds, so it is given again.
    sumeds_enrich = str(Path(sys.executable).parent / "suMEDS-enrich")
    retry = [sumeds_enrich, str(output), "--ontology-source", str(ontology_source)]
    result = subprocess.run(retry, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
    assert read_record(output)["enriched"] is True
    assert pq.read_table(codes).equals(pq.read_table(enriched_output / "metadata/codes.parquet"))


def test_strict_makes_a_failed_enrichment_an_error(tmp_path, raw_dir: Path):
    no_packs = tmp_path / "no-packs"
    no_packs.mkdir()
    result = run_etl(tmp_path / "meds", raw_dir, "--", "--ontology-source", str(no_packs), "--strict")
    assert result.returncode == 1
    assert "suMEDS-enrich: error:" in result.stderr
