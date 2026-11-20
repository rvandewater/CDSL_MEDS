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
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23676595.svg)](https://doi.org/10.5281/zenodo.23676595)

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
meds-extract-run spec=CDSL output_dir=$MEDS_OUTPUT_DIR download_dest_dir=$RAW_INPUT_DIR
```

This one command gives the complete build. It will:

1. Download the CDSL tables and DICOM metadata from PhysioNet into `$RAW_INPUT_DIR`, skipping `IMAGES/`.
2. Build the MEDS cohort and write it to `$MEDS_OUTPUT_DIR` (`data/` and `metadata/`). All
    transformations, the code descriptions, the encodings of the input files (see
    [Input encoding](#input-encoding)) and the dataset descriptors of `metadata/dataset.json` are
    declared in [`src/CDSL_MEDS/event_configs.yaml`](src/CDSL_MEDS/event_configs.yaml). The downloaded
    files are not modified.
3. Run the four post-stages the config declares: suMEDS's `sumeds_link_unit_siblings` links codes
    that differ only in their unit (see [Code metadata](#code-metadata)), suMEDS's
    `sumeds_normalize_units` writes `unit` in UCUM, `cdsl_describe_slot_codes` describes the
    slot-coded diagnosis and neoplasm codes from the release's own dictionary, then `sumeds_enrich` adds
    official vocabulary titles and parent codes with suMEDS (see
    [Enriching code metadata with suMEDS](#enriching-code-metadata-with-sumeds)).

If you already have the PhysioNet release on disk, skip the download and point `input_dir` at the
directory that directly contains `patient_01.csv`:

```bash
meds-extract-run spec=CDSL output_dir=$MEDS_OUTPUT_DIR do_download=false input_dir=$RAW_INPUT_DIR
```

To parallelize the stages, pass a runner file:

```bash
printf 'parallelize:\n  n_workers: 8\n  launcher: joblib\n' >runner.yaml
meds-extract-run spec=CDSL output_dir=$MEDS_OUTPUT_DIR do_download=false \
	input_dir=$RAW_INPUT_DIR stage_runner_fp=runner.yaml
```

Run `meds-extract-run --help` for every option. Rerunning the same command on an existing output only
runs the stages that have not finished (each finished stage leaves `.logs/<stage>.done` in the output).

Intermediate stage outputs are deleted after a successful run, by default: the spec sets
MEDS-Extract's `etl.cleanup_intermediates: true`, so a finished `$MEDS_OUTPUT_DIR` holds only `data/`,
`metadata/`, `.logs/` and `.meds_extract_run/`. A failed run keeps them, so the same command resumes
where it stopped, and the same command on a finished output still does nothing. To keep them (to look
at a stage's output; `$MEDS_OUTPUT_DIR/.logs/pipeline.log` keeps the stage logs either way), add
`"overrides=['cleanup_intermediates=false']"`. Cleanup never deletes raw data: a download into the
default `$MEDS_OUTPUT_DIR/.meds_extract_run/raw_input` stays, and `download_dest_dir=` elsewhere lets other
output directories reuse it.

## How this ETL is defined

The whole ETL is one MESSY (MEDS-Extract Specification Syntax YAML) file,
[`src/CDSL_MEDS/event_configs.yaml`](src/CDSL_MEDS/event_configs.yaml). It declares:

- **`sources:`**: where the raw data lives (the PhysioNet release, excluding `IMAGES/**`).
- **`etl:`**: the dataset name, sharding options, the codecs of the Latin-1 input files, the
    descriptors written to `metadata/dataset.json` (the release's license and PhysioNet page, and which
    extra columns are raw row identifiers), and the post-stages that run after the canonical pipeline.
- **One block per raw table**: how each row becomes one or more MEDS events, and which dictionary
    tables describe the resulting codes.

`pyproject.toml` registers the file with MEDS-Extract's `MEDS_extract.pipelines` entry-point group under
the name `CDSL`. That registration is what makes `spec=CDSL` work. A second entry point registers
[`src/CDSL_MEDS/sumeds_rules.yaml`](src/CDSL_MEDS/sumeds_rules.yaml) with suMEDS's `sumeds.rules` group
under the same name, which is how the config's `sumeds_enrich: {rules: CDSL}` and `--rules CDSL` find
the enrichment rules. One more registers the `cdsl_describe_slot_codes` post-stage
([`src/CDSL_MEDS/code_descriptions.py`](src/CDSL_MEDS/code_descriptions.py)) with MEDS-Transforms's
`MEDS_transforms.stages` group; the other post-stages come with suMEDS.

### Input encoding

`atc7.csv`, `medication_05.csv` and `vital_signs_04.csv` are Latin-1 encoded; every other file the ETL
reads is UTF-8. The `etl.encoding` block of the config names the codec of those three files, and
MEDS-Extract (0.8 or later) decodes them as it reads them, so no transcoded copy of the input is needed.
They are declared rather than left to MEDS-Extract's encoding detection, which would decode them as
cp1252.

## Events

| Table                       | Event codes                                                                                                                                                       | Notes                                                                                     |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `patient_01`                | `HOSPITAL_ADMISSION`, `HOSPITAL_DISCHARGE//{destination}`, `ICU_ADMISSION`, `ICU_DISCHARGE`, `AGE_AT_ADMISSION`, `SEX//{sex}`, `INPATIENT_DIAGNOSIS//{diagnosis}` | Age is the `numeric_value`. Sex and inpatient diagnosis are placed at hospital admission. |
|                             | `EMERGENCY_DEPARTMENT_VISIT`, `EMERGENCY_DEPARTMENT//{department}`, `EMERGENCY_DIAGNOSIS//{diagnosis}`                                                            | Placed at the ED admission time.                                                          |
|                             | `MECHANICAL_VENTILATION`                                                                                                                                          | Emitted at hospital admission when `mechvent` is `Yes`.                                   |
|                             | `VITAL//{measurement}`                                                                                                                                            | The first and last ED vital signs, at their recorded times.                               |
| `diagnosis_er_02`           | `DIAGNOSIS//ICD//10CM//{code}`, `PROCEDURE//ICD//10PCS//{code}`                                                                                                   | One event per populated diagnosis (12) and procedure (5) slot.                            |
| `diagnosis_hosp_03`         | `DIAGNOSIS//ICD//10CM//{code}`, `PROCEDURE//ICD//10PCS//{code}`, `NEOPLASM//ICDO//3//{code}`                                                                      | One event per populated diagnosis (19), procedure (20) and neoplasm (6) slot.             |
| `vital_signs_04`            | `VITAL//{measurement}`                                                                                                                                            | Blood pressure, temperature, heart rate, oxygen saturation and glucose.                   |
| `medication_05`             | `MEDICATION//START//ATC5//{atc5}//ATC7//{atc7}`, `MEDICATION//END//ATC5//{atc5}//ATC7//{atc7}`                                                                    | The daily average dose is the start event's `numeric_value`; the commercial name is text. |
| `lab_06`                    | `LAB//{item}//{unit}`                                                                                                                                             | `numeric_value`, `text_value` and the reference range are kept.                           |
| `CDSL-1.0.0-dicom-metadata` | `IMAGING//{modality}//{study description}//{body part}`                                                                                                           | Study and image IDs, view and patient position, and spatial resolution are kept.          |

`{measurement}` is one of `SYSTOLIC_BLOOD_PRESSURE`, `DIASTOLIC_BLOOD_PRESSURE`, `TEMPERATURE`,
`HEART_RATE`, `OXYGEN_SATURATION`, `BLOOD_GLUCOSE` and, for the ED only, `URINE_OUTPUT`. The measured
value is the `numeric_value`. The `diagnosis_er_02` and `diagnosis_hosp_03` events, which have no time of
their own, are placed at the stay's hospital discharge (see [Data notes](#data-notes)).

A missing discharge destination or ATC7 code becomes `UNKNOWN` in the code, and a missing lab unit `UNK`
(`LAB//{item}//UNK`), as in the other MEDS ETLs. The lab unit in a code is stripped of surrounding
whitespace; missing or blank is `UNK`. MEDS-Extract compares units the same way when it writes `unit`
(stripped, blanks dropped), so ` mg` and `mg` give one code. A row with a
missing time produces no event. Source row IDs (`medication_id`, `lab_id`, `vital_sign_id`) are kept as
columns, so repeated identical measurements are not collapsed.

See [`docs/MEDS_ETL_MAPPING.md`](docs/MEDS_ETL_MAPPING.md) for the table-level mapping in more detail.

### Code metadata

The ETL writes a **`description`** for every code whose meaning the release itself documents, and no
**`parent_codes`**: vocabulary parents (ICD-10-CM, ICD-10-PCS, ICD-O-3, ATC), and the official titles of
the ICD-10-PCS procedure codes, come from suMEDS (see
[Enriching code metadata with suMEDS](#enriching-code-metadata-with-sumeds)). Descriptions name the CDSL
column each value comes from. Spanish source values, such as the ATC names, are kept as they are, not
translated.

| Code family                                                                                                                                                   | Route                                                                                                | Description                                                                                                                                                       |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DIAGNOSIS//ICD//10CM//{code}`                                                                                                                                | `cdsl_describe_slot_codes` post-stage ([`code_descriptions.py`](src/CDSL_MEDS/code_descriptions.py)) | the ICD-10-CM long title from the release's `icd10_codes_dict.txt`; ICD-O-3 morphology codes (`MMMM/B`) found in diagnosis columns get a template naming the code |
| `PROCEDURE//ICD//10PCS//{code}`                                                                                                                               | none in the ETL                                                                                      | the CMS ICD-10-PCS title, added by suMEDS                                                                                                                         |
| `NEOPLASM//ICDO//3//{code}`                                                                                                                                   | `cdsl_describe_slot_codes` post-stage                                                                | a template naming the code (ICD-O-3 titles are licensed)                                                                                                          |
| `MEDICATION//START//…`, `MEDICATION//END//…`                                                                                                                  | `atc7` join and `_self` block in `event_configs.yaml`                                                | the ATC7 name from the release's `atc7.csv` (prefixed "End of medication" for `MEDICATION//END`); for rows without an ATC7 code, a template naming the ATC5 code  |
| `HOSPITAL_DISCHARGE`, `SEX`, `INPATIENT_DIAGNOSIS`, `EMERGENCY_DEPARTMENT`, `EMERGENCY_DIAGNOSIS`, `LAB`, `IMAGING`                                           | `_self` templates in `event_configs.yaml`                                                            | the code's values with the CDSL columns they come from                                                                                                            |
| literal codes: `HOSPITAL_ADMISSION`, `ICU_ADMISSION`, `ICU_DISCHARGE`, `AGE_AT_ADMISSION`, `EMERGENCY_DEPARTMENT_VISIT`, `MECHANICAL_VENTILATION`, `VITAL//…` | constant `_self` blocks in `event_configs.yaml`                                                      | the meaning of the source columns in the CDSL documentation, with the source of each in a `cdsl-docs:` comment                                                    |

A literal code has no components to join a dictionary on, so its description is a constant `_self`
block on the events that emit it (MEDS-Extract 0.8.1 or later); the events that emit the same vital sign
share one block through a YAML alias. The diagnosis, procedure and neoplasm codes cannot be described in
the config: they come from one event per column of a wide table (`dia_ppal`, `dia_02`, ...), so a code
seen in two columns has two code templates, which the MEDS-Extract metadata stage rejects. The
`cdsl_describe_slot_codes` post-stage describes them in the finished `codes.parquet`: it reads
`icd10_codes_dict.txt` from the run's `input_dir`, only fills missing descriptions, never adds a code or
a parent, can be rerun safely, and validates the file against `meds.CodeMetadataSchema`. Without the
dictionary it logs a warning and leaves the ICD-10-CM codes to suMEDS.

Sources:

- The [CDSL v1.0.0 documentation](https://physionet.org/content/covid-data-shared-learning/1.0.0/)
    on PhysioNet, for the meaning of each column (paraphrased; the `cdsl-docs:` comments in the config).
- The release's own dictionary tables, `atc7.csv` and `icd10_codes_dict.txt`, read at ETL time and never
    copied into this repository. `icd10_codes_dict.txt` is the CMS ICD-10-CM FY2021 order
    file with its fields joined by `|`, as built by the dataset authors'
    [covidhm-code](https://github.com/theonesp/covidhm-code) (MIT,
    `CovidHM_V3-code/buildcovidhm/data_wrangling/V3_icd10_data_wrangling.py`).

**Units.** Lab codes also carry their unit as **`unit`**, from the row's `lab_06.ud_result`, which is
already the code's last part; a `LAB//{item}//UNK` code has none. The release and its PhysioNet documentation give
no unit for the vital signs, age, urine output or medication dose, so those codes carry none. `unit` is a
String: the unit of the code's numeric values, one per code, written in [UCUM](https://ucum.org/) by
suMEDS's `sumeds_normalize_units` post-stage (`mg/dl` would become `mg/dL`). The spelling as recorded
stays in `unit_source_value`, a spelling suMEDS does not know is kept as it is, and the codes keep their
names (`LAB//{item}//{unit}` keeps the unit as recorded). MEDS-Extract (since 0.9.0) writes it as one String per code and fails if a code has more than
one unit. No CDSL code does, because the lab unit is part of the code.

**Unit siblings.** The first post-stage, suMEDS's shared `sumeds_link_unit_siblings`
([docs](https://github.com/rvandewater/suMEDS/blob/main/docs/unit_siblings.md)), links lab
codes that differ only in their unit (`LAB//{item}//{unit}` codes of one item with two or more units):
each gets the code without its unit, `LAB//{item}`, in its `parent_codes` (suMEDS then appends any
vocabulary parents). This parent is an in-dataset code, not a vocabulary code. It gets a row of its own in `codes.parquet` (no events carry it): the siblings'
description if they all share one (or, when they differ only in the `; unit: …` part, that description
without it), the vocabulary parents they all share, and no unit. A lab code whose item has a single
unit gets no such parent. A `LAB//{item}//UNK` code (no unit) joins its item's unit siblings and gets the
stem too, without narrowing the stem's shared parents; on its own it gets none. CDSL uses the stage's
default options (its default `unknown_unit_components`, `UNK`, covers these codes)
plus `description_unit_pattern`, the `; unit: …` span of the lab descriptions this config writes. The
post-stages run in this order: `sumeds_link_unit_siblings`, `sumeds_normalize_units`,
`cdsl_describe_slot_codes`, `sumeds_enrich`.

**Units in descriptions.** The stage can also fill a missing `unit` from a unit the code's
description states (never overriding a unit from the data). CDSL has no dataset dictionary that states
units: the release's descriptions give none, and the lab descriptions are written by this ETL from the
row's own `lab_06.ud_result`. So no code gains a unit this way, and no code is renamed.

**Which ICD-10.** The CDSL documentation says only "ICD-10". The dictionary shipped with the release is
the US ICD-10-CM (FY2021), and Spain's CIE-10-ES is the Spanish edition of the ICD-10 Clinical
Modification
([Ministerio de Sanidad](https://www.sanidad.gob.es/areas/saludDigital/interoperabilidadSemantica/factoriaRecursos/recursosEU/estandarNacional.htm)),
so the codes carry the edition token `10CM` (`DIAGNOSIS//ICD//10CM//{code}`) and the suMEDS rules map
them to OMOP's `ICD10CM` vocabulary. CDSL already writes the codes with the dot where OMOP puts it.
Procedures are CIE-10-ES Procedimientos, the Spanish edition of ICD-10-PCS (`PROCEDURE//ICD//10PCS//{code}`),
and neoplasms are ICD-O-3 morphology codes (`NEOPLASM//ICDO//3//{code}`). Some diagnosis columns also hold
ICD-O-3 morphology codes (`MMMM/B`); they keep the diagnosis layout, and suMEDS gives them `ICDO3` parents.

### Enriching code metadata with suMEDS

Vocabulary work is maintained once, in [suMEDS](https://github.com/rvandewater/SumMEDS) (installed with
this package), rather than in every ETL. This package ships the rules for it,
[`src/CDSL_MEDS/sumeds_rules.yaml`](src/CDSL_MEDS/sumeds_rules.yaml), registered in suMEDS's
`sumeds.rules` entry-point group under the same name as the MEDS-Extract spec, `CDSL`. The config's
post-stage `sumeds_enrich` enriches `metadata/codes.parquet` with them after the
release's own descriptions are filled, and replaces the file only once the enriched table is complete.
By default the ICD-10-CM and ICD-10-PCS data come from suMEDS's ontology packs, fetched from GitHub on
first use and cached; no Athena download is needed.

Enrichment is on by default and soft-fails: if it cannot finish, for example on a server without
internet access, `metadata/codes.parquet` is left as the ETL wrote it and the run still succeeds. The
warning, with the command that enriches it later, is in `$MEDS_OUTPUT_DIR/.logs/pipeline.log`.
`metadata/dataset.json` tells whether a build was enriched: `sumeds_enrichment.enriched` is `true` or
`false`, next to the rules, options and coverage used, and the error if it failed.

```bash
jq .sumeds_enrichment.enriched "$MEDS_OUTPUT_DIR/metadata/dataset.json"
```

**Finish the enrichment later.** MEDS-Extract marks the stage done even when it soft-fails, so rerunning
`meds-extract-run` does not try again. Run `suMEDS-enrich` on the build instead; it reuses the rules and
options recorded in `dataset.json`:

```bash
suMEDS-enrich "$MEDS_OUTPUT_DIR"
# Air-gapped: from a copy of the sumeds-ontologies packs directory instead of GitHub
suMEDS-enrich "$MEDS_OUTPUT_DIR" --ontology-source /path/to/sumeds-ontologies/ontologies --offline
```

**Skip it.** `skip_stages=[sumeds_enrich]` builds without the enrichment and without a record;
`suMEDS-enrich "$MEDS_OUTPUT_DIR" --rules CDSL` adds it later:

```bash
meds-extract-run spec=CDSL output_dir=$MEDS_OUTPUT_DIR do_download=false input_dir=$RAW_INPUT_DIR \
	skip_stages=[sumeds_enrich]
```

**Stage options for one run.** `meds-extract-run` passes `overrides=` to every stage, so set a
`sumeds_enrich` option (the `suMEDS-enrich` options, spelled with underscores) with `++stage_cfg.`: the
`++` adds the key to the stages that do not declare it, which ignore it. `strict` makes a failed
enrichment fail the run (exit code 1, and no `.done` marker, so the same command retries the stage):

```bash
# Fail the run if the enrichment fails
meds-extract-run spec=CDSL ... "overrides=['++stage_cfg.strict=true']"
# Also validate ATC and ICD-O-3 codes against your own OHDSI Athena download
meds-extract-run spec=CDSL ... "overrides=['++stage_cfg.athena_csv=/path/to/athena']"
# Use only already cached packs (no network), from a local pack directory
meds-extract-run spec=CDSL ... "overrides=['++stage_cfg.offline=true','++stage_cfg.ontology_source=/path/to/packs']"
```

`SUMEDS_ONTOLOGY_SOURCE=/path/to/packs` in the environment sets the pack source too.

**Clusters without internet on compute nodes.** Fill a shared cache once, from a login node, then run
offline on the compute nodes with the same `XDG_CACHE_HOME`:

```bash
export XDG_CACHE_HOME=/shared/project/cache
suMEDS-enrich --prefetch --rules CDSL # login node: downloads the pinned pack release
meds-extract-run spec=CDSL output_dir=$MEDS_OUTPUT_DIR do_download=false input_dir=$RAW_INPUT_DIR \
	"overrides=['++stage_cfg.offline=true']" # compute node: no network requests
```

ICD-10-CM and ICD-10-PCS codes are validated against suMEDS's versioned ontology data
(sumeds-ontologies, pinned release), built from the CMS and CDC files. The rules add:

- `DIAGNOSIS//ICD//10CM` codes shaped like ICD-10-CM: parents `ICD10CM//{code}` and its ancestors; the CMS
    title where the release's dictionary has none.
- `PROCEDURE//ICD//10PCS` codes: parents `ICD10PCS//{code}` and its ancestors; the CMS title.
- ICD-O-3 morphology codes (`NEOPLASM//ICDO//3`, and `MMMM/B` codes in diagnosis columns): parent
    `ICDO3//{MMMM/B}`, the sixth (grade) digit dropped. ICD-O-3 is licensed, so suMEDS only tags these
    codes unless Athena validates them.
- `MEDICATION//START` and `MEDICATION//END`: parent `ATC//{id_atc7}` when the ATC7 code is well formed and lies
    under the row's ATC5 code, else `ATC//{id_atc5}`. ATC is licensed too: tagged only, or validated with
    its ancestors by Athena.
- Same-dataset parents: a diagnosis or procedure code gets the nearest shorter code of the same family
    that CDSL contains (`DIAGNOSIS//ICD//10CM//J18` for `DIAGNOSIS//ICD//10CM//J18.9`).

suMEDS fills only missing descriptions, so the release's own descriptions are kept, and it never removes
or rewrites existing metadata. It writes parents as `VOCABULARY//CODE`, and the titles of the parents it
knows in `parent_names`, a map from parent code to title. A build with `skip_stages=[sumeds_enrich]` has
none of these parents or the ICD-10-PCS descriptions.

### Data notes

- **Deidentified dates.** Dates are shifted but preserve chronology within a patient, and are emitted as
    supplied.
- **Coded diagnoses at discharge.** `diagnosis_er_02` and `diagnosis_hosp_03` have no timestamps.
    Their diagnosis, procedure and neoplasm codes are the stay's coded diagnoses, which are final when
    the stay ends, so their events are placed at the stay's hospital discharge
    (`patient_01.discharge_date`, joined on `patient_id`), as MIMIC-IV and SICdb place coded diagnoses.
    A prediction made during the stay therefore does not see them. Both the emergency-department and
    the hospital table are placed there, since the release does not say when the ED codes were
    finalised. A stay without a `discharge_date` has no discharge event, and its rows in these two
    tables produce no event either; MEDS-Extract logs how many it drops per event
    (`dropped N/M rows with null time`).
- **One admission per subject.** `patient_id` is CDSL's admission identifier and becomes the MEDS
    `subject_id`.
- **No units for vitals.** The release does not document units for the vital signs, so none are added
    to the codes. Zero values are kept because the release defines no missing-value sentinel.
- **Unmapped columns.** The present-on-admission flags in `diagnosis_hosp_03` have no MEDS mapping and
    are not extracted.
- **Spanish values.** Most content is in English, but some columns, such as the medication commercial
    name, contain Spanish text.

## Testing

`tests/conftest.py` builds a minimal raw fixture from the columns the config needs, with the Latin-1
tables written in Latin-1. `tests/test_cdsl_etl.py` checks that the config loads and excludes `IMAGES/`,
and runs `meds-extract-run spec=CDSL skip_stages=[sumeds_enrich]` over the fixture (every code but the
ICD-10-PCS procedures described, no parent codes, valid MEDS code metadata, Latin-1 text decoded, the
`dataset.json` descriptors, the diagnosis, procedure and neoplasm codes at hospital discharge and no event
before admission). `tests/test_runner_equivalence.py` checks that this build has the codes,
descriptions and event counts that the removed `MEDS_extract-CDSL` runner built from the same fixture.
`tests/test_run_with_enrichment.py` runs the full `meds-extract-run spec=CDSL`, offline against a tiny
synthetic ontology pack, and checks that suMEDS adds titles and parents without changing the release's
own descriptions or the events, and records the enrichment in `dataset.json`; that
`skip_stages=[sumeds_enrich]` leaves no record; that without packs the run soft-fails with
`codes.parquet` unchanged, a rerun does not retry, and `suMEDS-enrich` on the build finishes it; and that
`strict` makes the failure an error. `tests/test_code_descriptions.py` checks that every literal-code event
has one constant description, that every other code family has a description route, and that the
slot-code stage keeps existing values. `tests/test_sumeds_rules.py` checks that the rules are registered
as `CDSL` and load in suMEDS, and what example codes built from the config's code templates resolve to.
The [synthetic ETL workflow](.github/workflows/synthetic-etl.yaml) also generates raw files straight
from the config with [MESSY-synth](https://github.com/rvandewater/MESSY_synth) and fails if any event
block yields no events.

```bash
uv sync --group dev
uv run pytest tests/ -v
uv run pre-commit run --all-files
```

## Summary statistics

[`tests/regression/full_summary_stats.json`](tests/regression/full_summary_stats.json) is a
[meds_summary_stats](https://github.com/Medical-Event-Data-Standard/meds_summary_stats) fingerprint of
the MEDS dataset this ETL builds from the full CDSL 1.0.0 release with `meds-extract-run spec=CDSL`, so
its code-metadata coverage includes the suMEDS enrichment. It covers subject, event and measurement counts,
digests of the code vocabulary, per-subject, value and time distributions, code-metadata coverage, the
splits and the schema. It names no code and reports no cell of fewer than 20 subjects and
no minimum or maximum, which is what makes it safe to publish. Because it is committed next to the ETL,
its diff shows what an ETL change did to the output, and two ETL versions can be compared without
access to the data.

Regenerate it in every pull request that changes the output, from a build of that branch:

```bash
# Build the release with this checkout, then summarize it
scripts/summary_stats.sh full --input-dir $RAW_INPUT_DIR
# Or summarize a build of this checkout you already have
scripts/summary_stats.sh full --meds-dir $MEDS_OUTPUT_DIR
```

Both print how the statistics changed against the committed baseline before overwriting it. Add
`--compare` to check a build without writing anything; it exits 1 when
[`tests/regression/policy.yaml`](tests/regression/policy.yaml) flags an error. Rebuilding the same
commit reproduces the baseline exactly, so any finding is a change in the output. CI cannot rebuild the
credentialed release, so the [Summary stats workflow](.github/workflows/summary-stats.yaml) only warns
when a pull request changes `src/` (the config, the post-stages or the suMEDS rules) without regenerating the
baseline. A MEDS-Extract, suMEDS or polars upgrade can also move the output, so check it with
`--compare`.

## Breaking changes

Changes that alter codes, columns or metadata that cohorts, predicates or downstream mappings may depend on,
newest release first.

### Unreleased (next release after 0.1.0)

- **ICD codes name the vocabulary and its edition apart.** Every MEDS ETL of this family now writes
    `<FAMILY>//<VOCABULARY>//<EDITION>//<code>`. `DIAGNOSIS_ICD10//{code}` is now `DIAGNOSIS//ICD//10CM//{code}`,
    `PROCEDURE_ICD10PCS//{code}` is `PROCEDURE//ICD//10PCS//{code}` and `NEOPLASM_ICDO3//{code}` is
    `NEOPLASM//ICDO//3//{code}`; the code after the edition and its parents are unchanged. Migrate by replacing
    `^DIAGNOSIS_ICD10//` with `DIAGNOSIS//ICD//10CM//`, `^PROCEDURE_ICD10PCS//` with `PROCEDURE//ICD//10PCS//`
    and `^NEOPLASM_ICDO3//` with `NEOPLASM//ICDO//3//`.
- **Diagnosis, procedure and neoplasm events are timed at hospital discharge** (were `1970-01-01 00:00:00`).
    The `DIAGNOSIS//ICD//10CM//…`, `PROCEDURE//ICD//10PCS//…` and `NEOPLASM//ICDO//3//…` events from
    `diagnosis_er_02` and `diagnosis_hosp_03` move to the stay's `HOSPITAL_DISCHARGE` time
    (`patient_01.discharge_date`). Their counts are unchanged (their codes are renamed as in the entry above), and the move itself
    leaves `codes.parquet` unchanged. A
    filter on `time.year == 1970` no longer finds them; select them by code prefix instead. Rebuild any
    predictions, task labels or cached cohorts made from an earlier build: the 1970 placement put these
    codes in the history of every prediction made during the stay.
- **Medication courses are `MEDICATION//START//ATC5//…` and `MEDICATION//END//ATC5//…`**
    ([alignEHR#53](https://github.com/rvandewater/alignEHR/issues/53)). The start was the unmarked
    `MEDICATION//ATC5//{atc5}//ATC7//{atc7}` and the end `MEDICATION_END//ATC5//{atc5}//ATC7//{atc7}`. Both now
    carry the token in the second part, the spelling all MEDS ETLs move to for the two ends of an interval, so a
    start pairs with its end by swapping `START` for `END`. Events, times, values and code metadata (descriptions,
    ATC parents) are otherwise unchanged, and the packaged suMEDS rules match the new codes.

    | Old                                          | New                                             |
    | -------------------------------------------- | ----------------------------------------------- |
    | `MEDICATION//ATC5//{atc5}//ATC7//{atc7}`     | `MEDICATION//START//ATC5//{atc5}//ATC7//{atc7}` |
    | `MEDICATION_END//ATC5//{atc5}//ATC7//{atc7}` | `MEDICATION//END//ATC5//{atc5}//ATC7//{atc7}`   |

    To migrate, rewrite old codes with the regexes `^MEDICATION//ATC5//` → `MEDICATION//START//ATC5//` and
    `^MEDICATION_END//ATC5//` → `MEDICATION//END//ATC5//`.
- **A lab result without a unit is `LAB//{item}//UNK`** (was `LAB//{item}//UNKNOWN`), as in the other MEDS
    ETLs. Migrate by replacing `^(LAB//.+)//UNKNOWN$` with `\1//UNK`. The discharge destination and ATC7
    fallbacks are not units and stay `UNKNOWN`.
- **The unit in a lab code is stripped of surrounding whitespace, and a blank one is `UNK`**
    (`LAB//{item}//{unit}`). Before, `lab_06.ud_result` was copied as recorded: a padded unit (` mg`) gave
    a code of its own, and a blank or empty one gave a code ending in whitespace or in `//`, with no
    `unit`. No output change on the synthetic data; codes change only where a unit was padded or blank.
    To migrate, strip the last part of an old lab code, and write `UNK` where it is then empty:
    `^(LAB//.+?//)\s*(\S(?:.*\S)?)?\s*$` → `\1\2`.
- **`codes.parquet` `unit` is one UCUM string per code** (MEDS-Extract 0.9 and suMEDS
    `sumeds_normalize_units`; it was a list of the units as recorded). The unit as recorded is in the new
    column `unit_source_value`.
- **New rows in `codes.parquet` without events:** codes that differ only in their unit component get the code
    without the unit (`LAB//{item}`) as a parent, which has a row of its own.
- **Intermediate stage outputs are no longer kept.** Up to 0.1.0, a run left each stage's output in
    `<output_dir>/<stage>/` (`convert_to_parquet/`, `convert_to_MEDS_events/`, `cdsl_describe_slot_codes/`, `sumeds_enrich/`, …). The spec now sets MEDS-Extract's
    `etl.cleanup_intermediates: true`, so a successful run deletes those directories and keeps only
    `data/`, `metadata/`, `.logs/` and `.meds_extract_run/`. The MEDS data and metadata are
    unchanged. To migrate, add `"overrides=['cleanup_intermediates=false']"` to `meds-extract-run`
    wherever you read a stage directory after the run.

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
@software{van_de_Water_CDSL_MEDS_ETL_2026,
author = {van de Water, Robin},
doi = {10.5281/zenodo.23676595},
license = {MIT},
month = nov,
title = {{CDSL MEDS ETL}},
url = {https://github.com/rvandewater/CDSL_MEDS},
year = {2026}
}
```

See also [`CITATION.cff`](CITATION.cff).
