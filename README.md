# CDSL MEDS Extraction ETL

<!-- Placeholders until available: the PyPI badges resolve once CDSL-MEDS is published; replace
CODECOV_TOKEN_PLACEHOLDER with the repo's Codecov badge token and zenodo.XXXXXXX with its Zenodo DOI. -->

[![PyPI - Version](https://img.shields.io/pypi/v/CDSL-MEDS)](https://pypi.org/project/CDSL-MEDS/)
[![codecov](https://codecov.io/gh/rvandewater/CDSL_MEDS/graph/badge.svg?token=CODECOV_TOKEN_PLACEHOLDER)](https://codecov.io/gh/rvandewater/CDSL_MEDS)
[![tests](https://github.com/rvandewater/CDSL_MEDS/actions/workflows/synthetic-etl.yaml/badge.svg)](https://github.com/rvandewater/CDSL_MEDS/actions/workflows/synthetic-etl.yaml)
[![code-quality](https://github.com/rvandewater/CDSL_MEDS/actions/workflows/code-quality-main.yaml/badge.svg)](https://github.com/rvandewater/CDSL_MEDS/actions/workflows/code-quality-main.yaml)
![python](https://img.shields.io/badge/python-%3E%3D3.11.4%2C%20%3C3.14-blue?logo=python&logoColor=white)
![Static Badge](https://img.shields.io/badge/MEDS-0.4.1-blue)
[![license](https://img.shields.io/badge/License-MIT-green.svg?labelColor=gray)](https://github.com/rvandewater/CDSL_MEDS#license)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/rvandewater/CDSL_MEDS/pulls)
[![contributors](https://img.shields.io/github/contributors/rvandewater/CDSL_MEDS.svg)](https://github.com/rvandewater/CDSL_MEDS/graphs/contributors)
[![DOI](https://zenodo.org/badge/1355887072.svg)](https://doi.org/10.5281/zenodo.23676595)

This pipeline extracts the
[COVID Data for Shared Learning (CDSL) database v1.0.0](https://physionet.org/content/covid-data-shared-learning/1.0.0/)
from PhysioNet into the [MEDS format](https://medical-event-data-standard.github.io/). CDSL holds 4,479
patients hospitalized with confirmed or suspected COVID-19 in the Spanish HM Hospitales group
(December 2019 – February 2021). It was built from the
[ETL_MEDS_Template](https://github.com/Medical-Event-Data-Standard/ETL_MEDS_Template) and uses the
config-only layout introduced by [MEDS-Extract](https://github.com/mmcdermott/MEDS_extract) 0.7.

The ETL maps the tabular EHR data and the DICOM metadata table. It never downloads or reads the `IMAGES/`
tree.

## Usage

CDSL is a restricted-access dataset. You need a credentialed PhysioNet account that has completed the
required training, signed the data use agreement, and had a project approved by the data contributors.

```bash
pip install git+https://github.com/rvandewater/CDSL_MEDS.git
export DATASET_DOWNLOAD_USERNAME=$PHYSIONET_USERNAME
export DATASET_DOWNLOAD_PASSWORD=$PHYSIONET_PASSWORD
meds-extract-download spec=CDSL output_dir=$RAW_INPUT_DIR
MEDS_extract-CDSL spec=CDSL output_dir=$MEDS_OUTPUT_DIR do_download=false input_dir=$RAW_INPUT_DIR
```

When you run this, the program will:

1. Download the CDSL tables and DICOM metadata from PhysioNet into `$RAW_INPUT_DIR`, skipping `IMAGES/`.
2. Stage a UTF-8 view of the input in `$MEDS_OUTPUT_DIR/.meds_extract_run/pre_meds` (see
    [Input encoding](#input-encoding)). The downloaded files are not modified.
3. Build the MEDS cohort from the staged files and write it to `$MEDS_OUTPUT_DIR` (`data/` and
    `metadata/`). All transformations and metadata extraction are declared in
    [`src/CDSL_MEDS/event_configs.yaml`](src/CDSL_MEDS/event_configs.yaml).
4. Fill in the code metadata that MEDS-Extract cannot attach on its own (see
    [Code metadata](#code-metadata)).

If you already have the PhysioNet release on disk, skip the first command and point `input_dir` at the
directory that directly contains `patient_01.csv`.

`MEDS_extract-CDSL` takes the same arguments as `meds-extract-run`. To parallelize the stages, pass a
runner file:

```bash
printf 'parallelize:\n  n_workers: 8\n  launcher: joblib\n' >runner.yaml
MEDS_extract-CDSL spec=CDSL output_dir=$MEDS_OUTPUT_DIR do_download=false \
	input_dir=$RAW_INPUT_DIR stage_runner_fp=runner.yaml
```

Run `meds-extract-run --help` for every option.

## How this ETL is defined

The whole ETL is one MESSY (MEDS-Extract Specification Syntax YAML) file,
[`src/CDSL_MEDS/event_configs.yaml`](src/CDSL_MEDS/event_configs.yaml). It declares:

- **`sources:`**: where the raw data lives (the PhysioNet release, excluding `IMAGES/**`).
- **`etl:`**: the dataset name and sharding options.
- **One block per raw table**: how each row becomes one or more MEDS events, and which dictionary
    tables describe the resulting codes.

`pyproject.toml` registers the file with MEDS-Extract's `MEDS_extract.pipelines` entry-point group under
the name `CDSL`. That registration is what makes `spec=CDSL` work.

### Input encoding

`atc7.csv`, `medication_05.csv` and `vital_signs_04.csv` are Latin-1 encoded, while MEDS-Extract's CSV
reader requires UTF-8. [`src/CDSL_MEDS/pre_meds.py`](src/CDSL_MEDS/pre_meds.py) handles this:
`MEDS_extract-CDSL` transcodes those three files into a staging directory, hard-links every other file,
and then runs `meds-extract-run` on the staged copy. Because of this step and the
[code metadata](#code-metadata) step that follows the pipeline, run the ETL with `MEDS_extract-CDSL`
rather than `meds-extract-run` directly.

## Events

| Table                       | Event codes                                                                                                                                                       | Notes                                                                                     |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `patient_01`                | `HOSPITAL_ADMISSION`, `HOSPITAL_DISCHARGE//{destination}`, `ICU_ADMISSION`, `ICU_DISCHARGE`, `AGE_AT_ADMISSION`, `SEX//{sex}`, `INPATIENT_DIAGNOSIS//{diagnosis}` | Age is the `numeric_value`. Sex and inpatient diagnosis are placed at hospital admission. |
|                             | `EMERGENCY_DEPARTMENT_VISIT`, `EMERGENCY_DEPARTMENT//{department}`, `EMERGENCY_DIAGNOSIS//{diagnosis}`                                                            | Placed at the ED admission time.                                                          |
|                             | `MECHANICAL_VENTILATION`                                                                                                                                          | Emitted at hospital admission when `mechvent` is `Yes`.                                   |
|                             | `VITAL//{measurement}`                                                                                                                                            | The first and last ED vital signs, at their recorded times.                               |
| `diagnosis_er_02`           | `DIAGNOSIS_ICD10//{code}`, `PROCEDURE_ICD10PCS//{code}`                                                                                                           | One event per populated diagnosis (12) and procedure (5) slot.                            |
| `diagnosis_hosp_03`         | `DIAGNOSIS_ICD10//{code}`, `PROCEDURE_ICD10PCS//{code}`, `NEOPLASM_ICDO3//{code}`                                                                                 | One event per populated diagnosis (19), procedure (20) and neoplasm (6) slot.             |
| `vital_signs_04`            | `VITAL//{measurement}`                                                                                                                                            | Blood pressure, temperature, heart rate, oxygen saturation and glucose.                   |
| `medication_05`             | `MEDICATION//ATC5//{atc5}//ATC7//{atc7}`, `MEDICATION_END//ATC5//{atc5}//ATC7//{atc7}`                                                                            | The daily average dose is the start event's `numeric_value`; the commercial name is text. |
| `lab_06`                    | `LAB//{item}//{unit}`                                                                                                                                             | `numeric_value`, `text_value` and the reference range are kept.                           |
| `CDSL-1.0.0-dicom-metadata` | `IMAGING//{modality}//{study description}//{body part}`                                                                                                           | Study and image IDs, view and patient position, and spatial resolution are kept.          |

`{measurement}` is one of `SYSTOLIC_BLOOD_PRESSURE`, `DIASTOLIC_BLOOD_PRESSURE`, `TEMPERATURE`,
`HEART_RATE`, `OXYGEN_SATURATION`, `BLOOD_GLUCOSE` and, for the ED only, `URINE_OUTPUT`. The measured
value is the `numeric_value`.

A missing discharge destination, ATC7 code or lab unit becomes `UNKNOWN` in the code. A row with a
missing time produces no event. Source row IDs (`medication_id`, `lab_id`, `vital_sign_id`) are kept as
columns, so repeated identical measurements are not collapsed.

See [`docs/MEDS_ETL_MAPPING.md`](docs/MEDS_ETL_MAPPING.md) for the table-level mapping in more detail.

### Code metadata

Every code in `metadata/codes.parquet` has a **`description`**, and vocabulary-coded codes have
**`parent_codes`** written as `<OMOP vocabulary_id>/<code>` (`ICD10CM/J18.9`, `ATC/H03AA01`), which
OHDSI Athena-based tools can resolve. Descriptions name the CDSL column each value comes from. Spanish
source values, such as the ATC names, are kept as they are, not translated.

| Code family                                                                                                                                                   | Route                                                                                                                     | Description                                                                                                                                                     | Parent codes                                                                                                                                 |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `DIAGNOSIS_ICD10//{code}`                                                                                                                                     | filled after the pipeline ([`code_descriptions.py`](src/CDSL_MEDS/code_descriptions.py))                                  | the ICD-10-CM long title from the release's `icd10_codes_dict.txt`; a template naming the code otherwise                                                        | `ICD10CM/{code}`; ICD-O-3 morphology codes (`MMMM/B`) found in diagnosis columns get `ICDO3/{MMMM/B}` instead (inferred from the code shape) |
| `PROCEDURE_ICD10PCS//{code}`                                                                                                                                  | filled after the pipeline                                                                                                 | a template naming the code                                                                                                                                      | `ICD10PCS/{code}`                                                                                                                            |
| `NEOPLASM_ICDO3//{code}`                                                                                                                                      | filled after the pipeline                                                                                                 | a template naming the code                                                                                                                                      | `ICDO3/{MMMM/B}`; a sixth (grade) digit is dropped, since OMOP's ICD-O-3 histology codes have none                                           |
| `MEDICATION//…`, `MEDICATION_END//…`                                                                                                                          | `atc7` join and `_self` block in `event_configs.yaml`                                                                     | the ATC7 name from the release's `atc7.csv` (prefixed "End of medication" for `MEDICATION_END`); for rows without an ATC7 code, a template naming the ATC5 code | `ATC/{id_atc7}` when it is a well-formed level-5 code under the row's ATC5 code, else `ATC/{id_atc5}`                                        |
| `HOSPITAL_DISCHARGE`, `SEX`, `INPATIENT_DIAGNOSIS`, `EMERGENCY_DEPARTMENT`, `EMERGENCY_DIAGNOSIS`, `LAB`, `IMAGING`                                           | `_self` templates in `event_configs.yaml`                                                                                 | the code's values with the CDSL columns they come from                                                                                                          | none                                                                                                                                         |
| literal codes: `HOSPITAL_ADMISSION`, `ICU_ADMISSION`, `ICU_DISCHARGE`, `AGE_AT_ADMISSION`, `EMERGENCY_DEPARTMENT_VISIT`, `MECHANICAL_VENTILATION`, `VITAL//…` | [`metadata/static_code_descriptions.csv`](src/CDSL_MEDS/metadata/static_code_descriptions.csv), filled after the pipeline | the meaning of the source columns in the CDSL documentation, with the source of each row in the table's `source` column                                         | none                                                                                                                                         |

Two kinds of codes cannot be described in the config. MEDS-Extract attaches `_metadata` by joining on a
code's components, so a literal code has nothing to join on, and the pre-existing `codes.parquet` that
MEDS-Extract documents for this case is not read by the standard pipeline. The diagnosis, procedure and
neoplasm codes come from one event per column of a wide table (`dia_ppal`, `dia_02`, ...), so a code seen
in two columns has two code templates, which the MEDS-Extract metadata stage rejects. `MEDS_extract-CDSL`
fills both into the finished `codes.parquet`: it only fills missing values, never adds a code, can be
rerun safely, and validates the file against `meds.CodeMetadataSchema`.

Sources:

- The [CDSL v1.0.0 documentation](https://physionet.org/content/covid-data-shared-learning/1.0.0/)
    on PhysioNet, for the meaning of each column (paraphrased; `cdsl-docs:` in the `source` column).
- The release's own dictionary tables, `atc7.csv` and `icd10_codes_dict.txt`, read at ETL time and never
    copied into this repository. `icd10_codes_dict.txt` is the public-domain CMS ICD-10-CM FY2021 order
    file with its fields joined by `|`, as built by the dataset authors'
    [covidhm-code](https://github.com/theonesp/covidhm-code) (MIT,
    `CovidHM_V3-code/buildcovidhm/data_wrangling/V3_icd10_data_wrangling.py`).
- The ICD-10-CM, ICD-10-PCS, ICD-O-3 and ATC code systems, for the parent codes. Only codes are
    written; their titles come from the vocabulary.

**Which ICD-10.** The CDSL documentation says only "ICD-10". The dictionary shipped with the release is
the US ICD-10-CM (FY2021), and Spain's CIE-10-ES is the Spanish edition of the ICD-10 Clinical
Modification
([Ministerio de Sanidad](https://www.sanidad.gob.es/areas/saludDigital/interoperabilidadSemantica/factoriaRecursos/recursosEU/estandarNacional.htm)),
so diagnosis parents use OMOP's `ICD10CM` vocabulary. CDSL already writes the codes with the dot where
OMOP puts it.

### Data notes

- **Deidentified dates.** Dates are shifted but preserve chronology within a patient, and are emitted as
    supplied.
- **Pseudotime.** `diagnosis_er_02` and `diagnosis_hosp_03` have no timestamps. Their diagnosis,
    procedure and neoplasm events use the fixed time `1970-01-01 00:00:00` rather than a guessed
    admission or discharge time.
- **One admission per subject.** `patient_id` is CDSL's admission identifier and becomes the MEDS
    `subject_id`.
- **No units for vitals.** The release does not document units for the vital signs, so none are added
    to the codes. Zero values are kept because the release defines no missing-value sentinel.
- **Unmapped columns.** The present-on-admission flags in `diagnosis_hosp_03` have no MEDS mapping and
    are not extracted.
- **Spanish values.** Most content is in English, but some columns, such as the medication commercial
    name, contain Spanish text.

## Testing

`tests/test_cdsl_etl.py` checks that the config loads and excludes `IMAGES/`, builds a minimal raw
fixture from the columns the config needs, runs `MEDS_extract-CDSL` over it (every code described, valid
MEDS code metadata), and checks the Latin-1 staging step. `tests/test_code_descriptions.py` checks that
the literal-code table is complete and sourced, that every other code family has a description route,
and that the post-pipeline fill keeps existing values. The [synthetic ETL workflow](.github/workflows/synthetic-etl.yaml) also generates raw files
straight from the config with [MESSY-synth](https://github.com/rvandewater/MESSY_synth) and fails if any
event block yields no events.

```bash
uv sync --group dev
uv run pytest tests/ -v
uv run pre-commit run --all-files
```

## Citation

If you use this ETL, please cite the CDSL database:

```bibtex
@article{PhysioNet-covid-data-shared-learning-1.0.0,
  author = {Ritoré, Álvaro and Oprescu, Andreea M and {Estirado Bronchalo}, Alberto and {Armengol de la Hoz}, Miguel Ángel},
  title = {{COVID Data for Shared Learning (CDSL): A comprehensive, multimodal COVID-19 dataset from HM Hospitales}},
  journal = {{PhysioNet}},
  year = {2024},
  month = oct,
  note = {Version 1.0.0},
  doi = {10.13026/1176-6c44},
  url = {https://doi.org/10.13026/1176-6c44}
}
```

And this ETL:

```bibtex
@software{Robin_van_de_Water_rvandewater_CDSL_MEDS_Zenodo_2026,
author = {Robin van de Water},
doi = {10.5281/zenodo.23676596},
license = {["mit"]},
month = oct,
title = {{rvandewater/CDSL\_MEDS: Zenodo}},
url = {https://github.com/rvandewater/CDSL_MEDS/,
year = {2026}
}
```

See also [`CITATION.cff`](CITATION.cff).
