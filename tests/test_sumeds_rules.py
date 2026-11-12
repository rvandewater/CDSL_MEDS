"""The packaged suMEDS rules load in suMEDS and match the code layouts of ``event_configs.yaml``.

suMEDS validates the rules file itself (``sumeds.load_rules``) and resolves codes with the rule
objects its enrichment uses, so these tests only state what CDSL's codes must resolve to. The
example codes are built from the code templates in ``event_configs.yaml`` with synthetic (public
example) values, so a change to a code layout that the rules no longer match fails here.
"""

import json
import re
from importlib.resources import files
from pathlib import Path

import polars as pl
import pytest
import yaml
from sumeds import EnrichmentConfig, OntologyRules, enrich_metadata, load_rules, registered_rules
from sumeds.ontology import INDEX_SCHEMA

PACKAGE_DIR = Path(__file__).parents[1] / "src" / "CDSL_MEDS"
RULES_FP = PACKAGE_DIR / "sumeds_rules.yaml"
CONFIG_FP = PACKAGE_DIR / "event_configs.yaml"

# Synthetic values for the config's columns, keyed by column name or `prefix*`. `J18`, `S72.0`,
# `5A19` and `0BH` are shorter codes of other examples, for the hierarchy rules.
DIAGNOSES = ["I10", "J18", "J18.9", "U07.1", "S72.0", "S72.001A", "8140/3", "8000/31"]
PROCEDURES = ["5A1955Z", "0BH17EZ", "5A19", "0BH"]
NEOPLASMS = ["8140/3", "8140/32"]
MEDICATIONS = [  # (id_atc5, id_atc7)
    ("H03AA", "H03AA01"),  # ATC7 under its ATC5 code
    ("N02BE", None),  # no ATC7 code: rendered as UNKNOWN
    ("J01CR", "J01CA04"),  # ATC7 not under the row's ATC5 code
]

# The first ontology rule, in file order, that resolves each example code (suMEDS ranks the rules
# that way), and the vocabulary code it resolves to. Codes not listed match no rule.
EXPECTED_ONTOLOGY = {
    "DIAGNOSIS_ICD10//I10": ("cdsl-icd10cm-diagnoses", "I10"),
    "DIAGNOSIS_ICD10//J18": ("cdsl-icd10cm-diagnoses", "J18"),
    "DIAGNOSIS_ICD10//J18.9": ("cdsl-icd10cm-diagnoses", "J18.9"),
    "DIAGNOSIS_ICD10//U07.1": ("cdsl-icd10cm-diagnoses", "U07.1"),
    "DIAGNOSIS_ICD10//S72.0": ("cdsl-icd10cm-diagnoses", "S72.0"),
    "DIAGNOSIS_ICD10//S72.001A": ("cdsl-icd10cm-diagnoses", "S72.001A"),
    "DIAGNOSIS_ICD10//8140/3": ("cdsl-icdo3-diagnoses", "8140/3"),
    "DIAGNOSIS_ICD10//8000/31": ("cdsl-icdo3-diagnoses", "8000/3"),
    "PROCEDURE_ICD10PCS//5A1955Z": ("cdsl-icd10pcs-procedures", "5A1955Z"),
    "PROCEDURE_ICD10PCS//0BH17EZ": ("cdsl-icd10pcs-procedures", "0BH17EZ"),
    "NEOPLASM_ICDO3//8140/3": ("cdsl-icdo3-neoplasms", "8140/3"),
    "NEOPLASM_ICDO3//8140/32": ("cdsl-icdo3-neoplasms", "8140/3"),
    **{
        f"{family}//ATC5//{atc5}//ATC7//{atc7}": (rule_id, value)
        for family in ("MEDICATION", "MEDICATION_END")
        for atc5, atc7, rule_id, value in [
            ("H03AA", "H03AA01", "cdsl-atc7-medications", "H03AA01"),
            ("N02BE", "UNKNOWN", "cdsl-atc5-medications", "N02BE"),
            ("J01CR", "J01CA04", "cdsl-atc5-medications", "J01CR"),
        ]
    },
}
# Hierarchy rule id -> example code -> the parent suMEDS adds from the other example codes.
EXPECTED_HIERARCHY = {
    "cdsl-diagnosis-prefixes": {
        "DIAGNOSIS_ICD10//J18.9": ["DIAGNOSIS_ICD10//J18"],
        "DIAGNOSIS_ICD10//S72.001A": ["DIAGNOSIS_ICD10//S72.0"],
    },
    "cdsl-procedure-prefixes": {
        "PROCEDURE_ICD10PCS//5A1955Z": ["PROCEDURE_ICD10PCS//5A19"],
        "PROCEDURE_ICD10PCS//0BH17EZ": ["PROCEDURE_ICD10PCS//0BH"],
    },
}


def code_templates() -> list[str]:
    cfg = yaml.safe_load(CONFIG_FP.read_text())
    templates = []
    for table, events in cfg.items():
        if table in ("_defaults", "etl", "sources"):
            continue
        templates += [str(e["code"]) for name, e in events.items() if not name.startswith("_")]
    return templates


def render(template: str, values: dict[str, str | None]) -> str:
    """Render a config code expression: a literal, or an f-string of column fields with defaults."""
    head = template.split(" if ")[0].strip()
    if not head.startswith('f"'):
        return head.strip('"')

    def value(match: re.Match) -> str:
        column, default = match.group(1), match.group(2)
        got = values.get(column, next((v for k, v in values.items() if column.startswith(k)), "X"))
        return default if got is None else got

    return re.sub(r"\{\$(\w+)(?: \?\? '([^']*)')?\}", value, head[2:-1])


def example_codes() -> set[str]:
    """Every code template of the config, rendered with each synthetic value set."""
    value_sets = [
        {"dia_": d, "proc_": p, "neo_": n}
        for d, p, n in zip(DIAGNOSES, PROCEDURES * 2, NEOPLASMS * 4, strict=True)
    ]
    value_sets += [{"id_atc5": atc5, "id_atc7": atc7} for atc5, atc7 in MEDICATIONS]
    return {render(t, values) for t in code_templates() for values in value_sets}


@pytest.fixture
def empty_ontology_source(tmp_path) -> str:
    """A local ontology source without packs, so enrichment fetches nothing over the network."""
    (tmp_path / "index.json").write_text(json.dumps({"schema": INDEX_SCHEMA, "vocabularies": {}}))
    return str(tmp_path)


def test_rules_file_is_packaged():
    assert "*.yaml" in Path(__file__).parents[1].joinpath("pyproject.toml").read_text()
    assert files("CDSL_MEDS").joinpath("sumeds_rules.yaml").is_file()


def test_rules_are_registered_under_the_spec_name():
    """The spec's `sumeds_enrich: {rules: CDSL}` and `suMEDS-enrich --rules CDSL` find them here."""
    assert registered_rules()["CDSL"] == "CDSL_MEDS:sumeds_rules.yaml"
    assert load_rules("CDSL") == load_rules(RULES_FP)


def test_rules_load_in_sumeds():
    """The strict suMEDS loader accepts every rule; each pattern must also span a whole code."""
    rules = load_rules(RULES_FP)
    assert not rules.crosswalk
    for rule in (*rules.vocabulary_rules, *rules.hierarchy):
        if rule.pattern is not None:
            assert rule.pattern.pattern.startswith("^"), rule.id
            assert rule.pattern.pattern.endswith("$"), rule.id


def test_ontology_rules_match_the_config_code_layouts():
    rules = load_rules(RULES_FP).ontology
    examples = example_codes()
    assert set(EXPECTED_ONTOLOGY) <= examples, set(EXPECTED_ONTOLOGY) - examples

    resolved = {}
    for code in examples:
        hit = next(((r.id, value) for r in rules if (value := r.resolve(code)) is not None), None)
        if hit:
            resolved[code] = hit
    assert resolved == EXPECTED_ONTOLOGY
    # Every rule is exercised by at least one example.
    assert {rule_id for rule_id, _ in resolved.values()} == {rule.id for rule in rules}


def test_hierarchy_rules_add_the_expected_parents(empty_ontology_source):
    """Run suMEDS's own enrichment, offline, with each string hierarchy rule over the examples."""
    hierarchy = tuple(r for r in load_rules(RULES_FP).hierarchy if r.mode != "ontology")
    assert {rule.id for rule in hierarchy} == set(EXPECTED_HIERARCHY)
    examples = sorted(example_codes())
    for rule in hierarchy:
        enriched = enrich_metadata(
            pl.LazyFrame({"code": examples}),
            EnrichmentConfig(rules=OntologyRules(hierarchy=(rule,)), ontology_source=empty_ontology_source),
        ).collect()
        parents = {
            row["code"]: row["parent_codes"]
            for row in enriched.select("code", "parent_codes").to_dicts()
            if row["parent_codes"]
        }
        # Exactly these codes get a parent, so the rule matches no code of another family.
        assert parents == EXPECTED_HIERARCHY[rule.id], rule.id
