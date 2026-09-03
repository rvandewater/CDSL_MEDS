# Zigong MEDS ETL

Configuration-only [MEDS-Extract](https://github.com/mmcdermott/MEDS_extract) ETL for the
[Zigong Fourth People's Hospital ICU infection database v1.1](https://physionet.org/content/icu-infection-zigong-fourth/1.1/).

## Run

Obtain access to the PhysioNet release, then export its credentials:

```bash
export DATASET_DOWNLOAD_USERNAME="$PHYSIONET_USERNAME"
export DATASET_DOWNLOAD_PASSWORD="$PHYSIONET_PASSWORD"
meds-extract-run spec=Zigong output_dir="$MEDS_OUTPUT_DIR"
```

To use files already downloaded from PhysioNet:

```bash
meds-extract-run spec=Zigong output_dir="$MEDS_OUTPUT_DIR" \
	do_download=false input_dir="$RAW_ZIGONG_DIR"
```

The input directory must contain the release's `dtBaseline.csv`, `dtDrugs.csv`, `dtICD.csv`,
`dtLab.csv`, `dtNursingChart.csv`, `dtOutCome.csv`, and `dtTransfer.csv` files.

## Mapping

`src/Zigong_MEDS/event_configs.yaml` converts the documented hour offsets from hospital admission
to MEDS pseudotime using `1970-01-01` as a shared anchor. It maps demographics, admissions,
transfers, diagnoses, laboratory results, prescriptions, nursing observations, death, and follow-up
SF-36 responses. No external terminology mapping is claimed where the source does not document one.

For a reusable mapping checklist, see [docs/MEDS_ETL_MAPPING.md](docs/MEDS_ETL_MAPPING.md).

## Synthetic validation

```bash
uv pip install git+https://github.com/rvandewater/MESSY_synth.git
MESSY-synth src/Zigong_MEDS/event_configs.yaml -o raw_synthetic/
meds-extract-run spec=Zigong output_dir=zigong_meds_synth/ \
	do_download=false input_dir=raw_synthetic/
```

The CI workflow runs this smoke test without accessing real data.
