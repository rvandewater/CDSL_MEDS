"""Code metadata that MEDS-Extract cannot attach through ``_metadata`` blocks.

Most CDSL code families are described in ``event_configs.yaml``: the ``atc7`` dictionary join
for medications, and ``_self`` templates for the codes built from one event's columns. Two kinds
of codes cannot be described there, so ``MEDS_extract-CDSL`` fills them into the finished
``metadata/codes.parquet`` with :func:`apply_code_metadata`:

* **Literal codes** (``HOSPITAL_ADMISSION``, ``VITAL//HEART_RATE``, ...) have no code components
  for ``_metadata`` to join on. MEDS-Extract documents a pre-existing ``codes.parquet`` for this
  case, but the standard extraction pipeline never reads it. Their descriptions are in
  ``metadata/static_code_descriptions.csv``, with the source of each row.
* **Slot-coded vocabulary codes** (``DIAGNOSIS_ICD10//``, ``PROCEDURE_ICD10PCS//``,
  ``NEOPLASM_ICDO3//``) come from one event per column of a wide table (``dia_ppal``,
  ``dia_02``, ...). A code seen in two columns then has two code templates, which the
  MEDS-Extract metadata reducer rejects, so those events cannot carry ``_metadata`` either.
  Their parent codes follow from the code itself; diagnosis descriptions come from the
  release's own ICD-10-CM dictionary, ``icd10_codes_dict.txt``.

Only missing values are filled: an extracted description or parent code always wins, no code
is added, and running the fill twice changes nothing.
"""

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from meds import CodeMetadataSchema

logger = logging.getLogger(__name__)

METADATA_DIR = Path(__file__).parent / "metadata"
STATIC_DESCRIPTIONS_CSV = METADATA_DIR / "static_code_descriptions.csv"

# The release's ICD-10 dictionary ("Additional tables" on the PhysioNet page). It is the CMS
# ICD-10-CM FY2021 order file with its fixed-width fields joined by "|", without a header
# (theonesp/covidhm-code, CovidHM_V3-code/buildcovidhm/data_wrangling/V3_icd10_data_wrangling.py).
ICD10_DICTIONARY = "icd10_codes_dict.txt"
ICD10_DICTIONARY_FIELDS = (
    "order_number",
    "code",
    "valid_for_submission",
    "short_description",
    "long_description",
)

# Code shapes. CDSL writes ICD-10-CM codes with the dot after the category, which is also how OMOP
# writes ICD10CM concept codes. ICD-O-3 morphology is MMMM/B, optionally followed by the 6th-digit
# grade or differentiation code; OMOP ICDO3 histology concepts carry MMMM/B only.
ICD10CM_CODE = re.compile(r"^[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$")
ICD10PCS_CODE = re.compile(r"^[0-9A-HJ-NP-Z]{7}$")
ICDO3_MORPHOLOGY_CODE = re.compile(r"^([0-9]{4}/[0-9])([0-9])?$")

DIAGNOSIS_SOURCE = "CDSL diagnosis_er_02 / diagnosis_hosp_03 dia_* columns"
PROCEDURE_SOURCE = "CDSL diagnosis_er_02 / diagnosis_hosp_03 proc_* columns"
NEOPLASM_SOURCE = "CDSL diagnosis_hosp_03 neo_* columns"


def read_static_code_descriptions(path: Path = STATIC_DESCRIPTIONS_CSV) -> pl.DataFrame:
    """Read the packaged descriptions of literal codes as ``code``, ``description``.

    The ``source`` column documents the table itself and is not written into the dataset.
    """
    return pl.read_csv(path, infer_schema=False).select("code", "description")


def read_icd10_dictionary(path: Path) -> dict[str, str]:
    """Map undotted ICD-10-CM codes to their long titles from the release's dictionary.

    Each line holds the five order-file fields joined by ``|``. The long title is the last field
    and is split off with a bounded split, so a ``|`` inside it would survive.
    """
    titles = {}
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            fields = line.rstrip("\n").split("|", len(ICD10_DICTIONARY_FIELDS) - 1)
            if len(fields) != len(ICD10_DICTIONARY_FIELDS):
                continue
            row = dict(zip(ICD10_DICTIONARY_FIELDS, fields, strict=True))
            titles[row["code"].strip()] = row["long_description"].strip()
    return titles


def _icdo3_parent(value: str) -> str | None:
    match = ICDO3_MORPHOLOGY_CODE.match(value)
    return f"ICDO3/{match.group(1)}" if match else None


def vocabulary_code_metadata(code: str, icd10_titles: Mapping[str, str]) -> tuple[str | None, str | None]:
    """Return ``(description, parent_code)`` for a slot-coded vocabulary code, else ``(None, None)``.

    Examples:
        >>> titles = {"I10": "Essential (primary) hypertension"}
        >>> vocabulary_code_metadata("DIAGNOSIS_ICD10//I10", titles)
        ('Essential (primary) hypertension', 'ICD10CM/I10')
        >>> vocabulary_code_metadata("DIAGNOSIS_ICD10//J18.9", {})  # not in the dictionary
        ... # doctest: +NORMALIZE_WHITESPACE
        ('ICD-10-CM diagnosis J18.9 (CDSL diagnosis_er_02 / diagnosis_hosp_03 dia_* columns)',
         'ICD10CM/J18.9')
        >>> vocabulary_code_metadata("DIAGNOSIS_ICD10//8140/3", titles)[1]
        'ICDO3/8140/3'
        >>> vocabulary_code_metadata("PROCEDURE_ICD10PCS//5A1955Z", titles)[1]
        'ICD10PCS/5A1955Z'
        >>> vocabulary_code_metadata("NEOPLASM_ICDO3//8140/32", titles)[1]
        'ICDO3/8140/3'
        >>> vocabulary_code_metadata("LAB//Glucose//mg/dL", titles)
        (None, None)
    """
    family, sep, value = code.partition("//")
    if not sep:
        return None, None
    if family == "DIAGNOSIS_ICD10":
        if ICD10CM_CODE.match(value):
            title = icd10_titles.get(value.replace(".", ""))
            return title or f"ICD-10-CM diagnosis {value} ({DIAGNOSIS_SOURCE})", f"ICD10CM/{value}"
        if parent := _icdo3_parent(value):
            return (
                f"ICD-O-3 morphology code {value} in an ICD-10 diagnosis column ({DIAGNOSIS_SOURCE})",
                parent,
            )
        return f"Diagnosis code {value} ({DIAGNOSIS_SOURCE})", None
    if family == "PROCEDURE_ICD10PCS":
        parent = f"ICD10PCS/{value}" if ICD10PCS_CODE.match(value) else None
        return f"ICD-10-PCS procedure {value} ({PROCEDURE_SOURCE})", parent
    if family == "NEOPLASM_ICDO3":
        return f"Neoplasm, ICD-O-3 morphology code {value} ({NEOPLASM_SOURCE})", _icdo3_parent(value)
    return None, None


def apply_code_metadata(
    codes_fp: Path, icd10_dictionary: Path | None = None, static: pl.DataFrame | None = None
) -> dict[str, int]:
    """Fill missing descriptions and parent codes in a finished ``codes.parquet``, in place.

    Args:
        codes_fp: The ``metadata/codes.parquet`` of a finished extraction.
        icd10_dictionary: The release's ``icd10_codes_dict.txt``. Without it, ICD-10-CM codes
            still get their parent code and a description naming the code.
        static: Override of the packaged literal-code table (``code``, ``description``), for tests.

    Returns:
        The number of codes whose description and whose parent codes were filled.

    The file is validated against the MEDS code-metadata schema and replaced atomically. Polars
    writes strings as Arrow ``large_string``, which that schema rejects, so the MEDS columns are
    cast back to the types the pipeline itself writes (``string``, ``list<string>``).
    """
    codes_fp = Path(codes_fp)
    static = read_static_code_descriptions() if static is None else static
    titles = {}
    if icd10_dictionary is not None and Path(icd10_dictionary).is_file():
        titles = read_icd10_dictionary(icd10_dictionary)
    else:
        logger.warning(f"{ICD10_DICTIONARY} not found; ICD-10-CM codes are described by their code only.")

    codes = pl.from_arrow(pq.read_table(codes_fp))
    if "description" not in codes.columns:
        codes = codes.with_columns(pl.lit(None, dtype=pl.String).alias("description"))
    if "parent_codes" not in codes.columns:
        codes = codes.with_columns(pl.lit(None, dtype=pl.List(pl.String)).alias("parent_codes"))

    vocabulary = [vocabulary_code_metadata(code, titles) for code in codes["code"]]
    filled = (
        codes.with_columns(
            pl.Series("_vocab_description", [d for d, _ in vocabulary], dtype=pl.String),
            pl.Series("_vocab_parent", [[p] if p else None for _, p in vocabulary], dtype=pl.List(pl.String)),
        )
        .join(static.rename({"description": "_static"}), on="code", how="left", maintain_order="left")
        .with_columns(
            pl.coalesce("description", "_static", "_vocab_description").alias("description"),
            pl.when(pl.col("parent_codes").list.len().fill_null(0) > 0)
            .then(pl.col("parent_codes"))
            .otherwise(pl.col("_vocab_parent"))
            .alias("parent_codes"),
        )
        .select(codes.columns)
    )

    def n_parents(df: pl.DataFrame) -> int:
        return int((df["parent_codes"].list.len().fill_null(0) > 0).sum())

    counts = {
        "descriptions": int(
            filled["description"].is_not_null().sum() - codes["description"].is_not_null().sum()
        ),
        "parent_codes": n_parents(filled) - n_parents(codes),
    }

    out = filled.to_arrow()
    meds_types = {"code": pa.string(), "description": pa.string(), "parent_codes": pa.list_(pa.string())}
    out = out.cast(pa.schema([pa.field(f.name, meds_types.get(f.name, f.type)) for f in out.schema]))
    CodeMetadataSchema.validate(out)

    tmp_fp = codes_fp.with_suffix(".parquet.tmp")
    pq.write_table(out, tmp_fp)
    os.replace(tmp_fp, codes_fp)
    return counts
