#!/usr/bin/env python3
"""
Cross-workstream enum compatibility check.

Compares canonical enum values across:
- SQL DDL (`agent-01-data-architecture/sql/001_core_schema.sql`)
- OpenAPI (`agent-05-platform-api/api/openapi.yaml`)
- Ingestion constants (`agent-02-data-acquisition/scripts/ingest_template.py`)

Usage:
    python agent-07-integration/enum_contract_check.py
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "agent-01-data-architecture/sql/001_core_schema.sql"
OPENAPI_PATH = ROOT / "agent-05-platform-api/api/openapi.yaml"
INGEST_PATH = ROOT / "agent-02-data-acquisition/scripts/ingest_template.py"
CONTRACT_PATH = ROOT / "agent-07-integration/contracts/canonical_vocabulary.json"


def read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")
    return path.read_text(encoding="utf-8")


def read_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_sql_enum(sql_text: str, enum_name: str) -> set[str]:
    pattern = re.compile(
        rf"create\s+type\s+{re.escape(enum_name)}\s+as\s+enum\s*\((.*?)\);",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(sql_text)
    if not match:
        raise ValueError(f"Could not find SQL enum '{enum_name}'")

    body = match.group(1)
    return set(re.findall(r"'([^']+)'", body))


def parse_python_string_set(python_text: str, constant_name: str) -> set[str]:
    tree = ast.parse(python_text)

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != constant_name:
            continue

        value = node.value
        if isinstance(value, ast.Set):
            return {elt.value for elt in value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)}
        if isinstance(value, ast.List):
            return {elt.value for elt in value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)}
        raise ValueError(f"Constant '{constant_name}' is not a string set/list")

    raise ValueError(f"Could not find Python constant '{constant_name}'")


def parse_python_string_dict_values(python_text: str, constant_name: str) -> set[str]:
    tree = ast.parse(python_text)

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != constant_name:
            continue

        value = node.value
        if not isinstance(value, ast.Dict):
            raise ValueError(f"Constant '{constant_name}' is not a dict")

        out: set[str] = set()
        for v in value.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                out.add(v.value)
        return out

    raise ValueError(f"Could not find Python constant '{constant_name}'")


def parse_python_string_constant(python_text: str, constant_name: str) -> str:
    tree = ast.parse(python_text)

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != constant_name:
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
        raise ValueError(f"Constant '{constant_name}' is not a string")

    raise ValueError(f"Could not find Python constant '{constant_name}'")


def parse_openapi_enum_lists(openapi_text: str) -> list[set[str]]:
    results: list[set[str]] = []

    # Inline style: enum: [a, b, c]
    for match in re.finditer(r"enum:\s*\[([^\]]+)\]", openapi_text):
        raw_items = match.group(1).split(",")
        values = {item.strip().strip("\"'") for item in raw_items if item.strip()}
        if values:
            results.append(values)

    # Block style:
    # enum:
    #   - a
    #   - b
    block_pattern = re.compile(r"enum:\s*\n((?:\s*-\s*[^\n]+\n)+)")
    for match in block_pattern.finditer(openapi_text):
        block = match.group(1)
        values = {
            line.strip().removeprefix("-").strip().strip("\"'")
            for line in block.splitlines()
            if line.strip().startswith("-")
        }
        if values:
            results.append(values)

    return results


def extract_openapi_domain_enum(openapi_enums: list[set[str]], domain_values: set[str], domain_name: str) -> set[str]:
    candidates: list[set[str]] = []
    for enum_values in openapi_enums:
        overlap = enum_values & domain_values
        if overlap:
            candidates.append(enum_values)

    exact_matches = [e for e in candidates if e == domain_values]
    if exact_matches:
        return exact_matches[0]

    if candidates:
        # Provide the most relevant candidate in error messaging.
        largest_overlap = max(candidates, key=lambda e: len(e & domain_values))
        raise ValueError(
            f"OpenAPI {domain_name} enum mismatch. "
            f"Expected {sorted(domain_values)}, found {sorted(largest_overlap)}"
        )

    raise ValueError(f"Could not find OpenAPI enum for domain '{domain_name}'")


def compare_sets(label: str, left_name: str, left: set[str], right_name: str, right: set[str]) -> list[str]:
    errors: list[str] = []
    if left != right:
        missing_in_right = sorted(left - right)
        extra_in_right = sorted(right - left)
        errors.append(
            f"{label} mismatch between {left_name} and {right_name}: "
            f"missing_in_{right_name}={missing_in_right}, "
            f"extra_in_{right_name}={extra_in_right}"
        )
    return errors


def main() -> int:
    sql_text = read_text(SQL_PATH)
    openapi_text = read_text(OPENAPI_PATH)
    ingest_text = read_text(INGEST_PATH)
    contract = read_json(CONTRACT_PATH)

    contract_facility = set(contract["enums"]["facility_type"])
    contract_operational = set(contract["enums"]["operational_status"])
    contract_validation = set(contract["enums"]["validation_level"])

    sql_facility = parse_sql_enum(sql_text, "facility_type")
    sql_operational = parse_sql_enum(sql_text, "operational_status")
    sql_validation = parse_sql_enum(sql_text, "validation_level")

    ingest_allowed_facility = parse_python_string_set(ingest_text, "ALLOWED_FACILITY_TYPES")
    ingest_allowed_operational = parse_python_string_set(ingest_text, "ALLOWED_OPERATIONAL_STATUSES")
    ingest_facility_map_values = parse_python_string_dict_values(ingest_text, "FACILITY_TYPE_MAP")
    ingest_l0 = parse_python_string_constant(ingest_text, "VALIDATION_LEVEL_L0")

    openapi_enums = parse_openapi_enum_lists(openapi_text)
    openapi_facility = extract_openapi_domain_enum(openapi_enums, sql_facility, "facility_type")
    openapi_operational = extract_openapi_domain_enum(openapi_enums, sql_operational, "operational_status")
    openapi_validation = extract_openapi_domain_enum(openapi_enums, sql_validation, "validation_level")

    errors: list[str] = []
    errors += compare_sets("facility_type", "Contract", contract_facility, "SQL", sql_facility)
    errors += compare_sets("operational_status", "Contract", contract_operational, "SQL", sql_operational)
    errors += compare_sets("validation_level", "Contract", contract_validation, "SQL", sql_validation)
    errors += compare_sets("facility_type", "SQL", sql_facility, "OpenAPI", openapi_facility)
    errors += compare_sets("facility_type", "SQL", sql_facility, "Ingestion(ALLOWED_FACILITY_TYPES)", ingest_allowed_facility)
    errors += compare_sets("operational_status", "SQL", sql_operational, "OpenAPI", openapi_operational)
    errors += compare_sets(
        "operational_status",
        "SQL",
        sql_operational,
        "Ingestion(ALLOWED_OPERATIONAL_STATUSES)",
        ingest_allowed_operational,
    )
    errors += compare_sets("validation_level", "SQL", sql_validation, "OpenAPI", openapi_validation)

    # Ingestion should only emit DB-safe facility type values.
    if not ingest_facility_map_values.issubset(sql_facility):
        errors.append(
            "Ingestion FACILITY_TYPE_MAP contains values outside SQL facility_type enum: "
            f"{sorted(ingest_facility_map_values - sql_facility)}"
        )

    # Ingestion's initial emitted level must be an SQL/OpenAPI legal value.
    if ingest_l0 not in sql_validation:
        errors.append(f"Ingestion VALIDATION_LEVEL_L0='{ingest_l0}' not in SQL validation_level")
    if ingest_l0 not in openapi_validation:
        errors.append(f"Ingestion VALIDATION_LEVEL_L0='{ingest_l0}' not in OpenAPI validation_level")

    if errors:
        print("Enum compatibility check FAILED")
        for idx, err in enumerate(errors, start=1):
            print(f"{idx}. {err}")
        return 1

    print("Enum compatibility check PASSED")
    print(f"- contract version: {contract.get('version', 'unknown')}")
    print(f"- facility_type: {sorted(contract_facility)}")
    print(f"- operational_status: {sorted(contract_operational)}")
    print(f"- validation_level: {sorted(contract_validation)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
