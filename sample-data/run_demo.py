#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — End-to-End Demo

Runs the full pipeline on sample data:
  1. Ingest: CSV → canonical records (L0)
  2. Deduplicate: pairwise comparison within same state
  3. Report: print match results grouped by decision

Usage:
    python sample-data/run_demo.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from itertools import combinations
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap imports for hyphenated directories
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent


def _import_from_path(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Import ingestion pipeline
ingest = _import_from_path(
    "ingest_template",
    ROOT / "agent-02-data-acquisition" / "scripts" / "ingest_template.py",
)

# Import deduplication modules (need to wire up the subpackage)
alg_path = ROOT / "agent-03-deduplication" / "algorithms"

# Register parent package so relative imports in __init__.py work
parent_spec = importlib.util.spec_from_file_location(
    "algorithms",
    alg_path / "__init__.py",
    submodule_search_locations=[str(alg_path)],
)
parent_mod = importlib.util.module_from_spec(parent_spec)
sys.modules["algorithms"] = parent_mod

# Import individual modules first (they have no relative imports)
name_sim = _import_from_path(
    "algorithms.name_similarity",
    alg_path / "name_similarity.py",
)
geo_prox = _import_from_path(
    "algorithms.geo_proximity",
    alg_path / "geo_proximity.py",
)
composite = _import_from_path(
    "algorithms.composite_scorer",
    alg_path / "composite_scorer.py",
)

# Now exec the parent __init__ so it can re-export
parent_spec.loader.exec_module(parent_mod)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def step_ingest(csv_path: Path, template_path: Path) -> list[dict]:
    """Run the ingestion pipeline and return canonical records."""
    print("=" * 65)
    print("STEP 1: INGESTION")
    print("=" * 65)

    records = ingest.read_csv_source(str(csv_path))
    template = ingest.load_template(str(template_path))

    results = ingest.process_batch(
        records,
        template,
        source_id="src-grid3-health",
        actor="demo:run_demo",
    )

    stats = results["stats"]
    print(f"  Source file : {csv_path.name}")
    print(f"  Records in : {stats['total_input']}")
    print(f"  Accepted   : {stats['accepted']} (all assigned L0 — Mapped)")
    print(f"  Rejected   : {stats['rejected']}")
    if results["rejected_records"]:
        for rej in results["rejected_records"]:
            print(f"    Row {rej['record_index']}: {'; '.join(rej['errors'])}")
    print()

    return results["canonical_records"]


def step_deduplicate(records: list[dict]) -> list:
    """Run pairwise deduplication and return MatchResults."""
    print("=" * 65)
    print("STEP 2: DEDUPLICATION")
    print("=" * 65)

    config_path = ROOT / "agent-03-deduplication" / "config" / "merge_rules.yaml"
    config = composite.ScorerConfig.from_yaml(config_path)

    pairs = list(combinations(records, 2))
    print(f"  Records    : {len(records)}")
    print(f"  Pairs      : {len(pairs)}")

    results = composite.score_candidate_pairs(
        [(a, b) for a, b in pairs],
        config=config,
    )

    # Filter to interesting results (anything above no_match threshold)
    matches = [r for r in results if r.decision != "no_match"]
    print(f"  Matches    : {len(matches)} (above no_match threshold)")
    print()

    return results


def step_report(match_results: list, records: list[dict]) -> None:
    """Print a human-readable deduplication report."""
    print("=" * 65)
    print("STEP 3: MATCH REPORT")
    print("=" * 65)

    # Build a lookup for names
    name_lookup = {r["pharmacy_id"]: r["facility_name"] for r in records}

    auto_merges = [r for r in match_results if r.decision == "auto_merge"]
    reviews = [r for r in match_results if r.decision == "review"]

    if auto_merges:
        print(f"\n  AUTO-MERGE ({len(auto_merges)} pairs)")
        print("  " + "-" * 63)
        for m in auto_merges:
            name_a = name_lookup.get(m.record_a_id, "?")
            name_b = name_lookup.get(m.record_b_id, "?")
            print(f"  {m.match_confidence:.2f}  {name_a}")
            print(f"         vs  {name_b}")
            if m.override_reason:
                print(f"         reason: {m.override_reason}")
            print(f"         signals: {', '.join(m.signals_used)}")
            print()
    else:
        print("\n  No auto-merge candidates.\n")

    if reviews:
        print(f"  REVIEW QUEUE ({len(reviews)} pairs)")
        print("  " + "-" * 63)
        for m in reviews:
            name_a = name_lookup.get(m.record_a_id, "?")
            name_b = name_lookup.get(m.record_b_id, "?")
            print(f"  {m.match_confidence:.2f}  {name_a}")
            print(f"         vs  {name_b}")
            signals = []
            signals.append(f"name={m.name_score:.2f}")
            if m.geo_score is not None:
                signals.append(f"geo={m.geo_score:.2f} ({m.geo_distance_km:.3f}km)")
            if m.phone_score is not None:
                signals.append(f"phone={'match' if m.phone_score == 1.0 else 'diff'}")
            print(f"         [{', '.join(signals)}]")
            print()
    else:
        print("  No review-queue candidates.\n")

    # Summary
    total_no_match = sum(1 for r in match_results if r.decision == "no_match")
    print("  " + "-" * 63)
    print(f"  SUMMARY: {len(auto_merges)} auto-merge, {len(reviews)} review, {total_no_match} no-match")
    print("=" * 65)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    csv_path = ROOT / "sample-data" / "lagos_pharmacies.csv"
    template_path = ROOT / "agent-02-data-acquisition" / "templates" / "generic_pharmacy_import.json"

    if not csv_path.exists():
        print(f"Sample data not found: {csv_path}")
        sys.exit(1)

    print()
    print("  Nigeria Pharmacy Registry — End-to-End Demo")
    print()

    # 1. Ingest
    canonical = step_ingest(csv_path, template_path)

    # 2. Deduplicate
    match_results = step_deduplicate(canonical)

    # 3. Report
    step_report(match_results, canonical)


if __name__ == "__main__":
    main()
