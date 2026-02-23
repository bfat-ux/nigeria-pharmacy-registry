#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — Cross-Source Deduplication

Loads all canonical records from output/, compares records across different
data sources using the composite scorer, and produces a deduplicated master
registry with match reports.

Strategy:
    1. Load all canonical records
    2. Block by state (records in different states are never compared)
    3. Within each state, find cross-source candidate pairs using geo proximity
    4. Score candidates with the composite scorer
    5. Output: deduplicated registry + match report

Usage:
    python agent-03-deduplication/scripts/cross_source_dedup.py \
        --output-dir output/deduped/

Dependencies:
    pip install rapidfuzz pyyaml
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add project root to path for imports
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Import bootstrapping for hyphenated directory
# ---------------------------------------------------------------------------

def _bootstrap_imports():
    """Register hyphenated agent directories as importable Python modules."""
    import importlib.util

    aliases = {
        "agent_03_dedup": ROOT / "agent-03-deduplication",
    }

    for alias, pkg_path in aliases.items():
        if alias in sys.modules:
            continue

        alg_path = pkg_path / "algorithms"

        # Register parent package
        spec = importlib.util.spec_from_file_location(
            alias,
            alg_path / "__init__.py",
            submodule_search_locations=[str(pkg_path)],
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[alias] = mod

        # Register algorithms subpackage
        alg_spec = importlib.util.spec_from_file_location(
            f"{alias}.algorithms",
            alg_path / "__init__.py",
            submodule_search_locations=[str(alg_path)],
        )
        alg_mod = importlib.util.module_from_spec(alg_spec)
        sys.modules[f"{alias}.algorithms"] = alg_mod
        alg_spec.loader.exec_module(alg_mod)


# Bootstrap BEFORE importing dedup modules
_bootstrap_imports()

from agent_03_dedup.algorithms.composite_scorer import (  # noqa: E402
    ScorerConfig,
    compute_match,
)
from agent_03_dedup.algorithms.geo_proximity import (  # noqa: E402
    Coordinate,
    find_nearby_candidates,
)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

OUTPUT_DIR = ROOT / "output"


def load_all_canonical() -> list[dict[str, Any]]:
    """Load all canonical_*.json files from the output directory tree."""
    records = []
    pattern = str(OUTPUT_DIR / "**" / "canonical_*.json")
    files = glob.glob(pattern, recursive=True)

    for fpath in files:
        # Skip deduped output to avoid circular loading
        if "deduped" in fpath:
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            batch = json.load(f)
        if isinstance(batch, list):
            records.extend(batch)
        logger.info("Loaded %d records from %s", len(batch) if isinstance(batch, list) else 0, fpath)

    # Deduplicate by pharmacy_id (in case of overlapping batches)
    seen = set()
    unique = []
    for r in records:
        pid = r.get("pharmacy_id")
        if pid and pid not in seen:
            seen.add(pid)
            unique.append(r)

    logger.info("Total unique records loaded: %d", len(unique))
    return unique


# ---------------------------------------------------------------------------
# Cross-source candidate generation
# ---------------------------------------------------------------------------


def group_by_state(records: list[dict]) -> dict[str, list[dict]]:
    """Group records by state for blocking."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        state = (r.get("state") or "Unknown").strip()
        groups[state].append(r)
    return dict(groups)


def group_by_source(records: list[dict]) -> dict[str, list[dict]]:
    """Group records by source_id."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        src = r.get("source_id", "unknown")
        groups[src].append(r)
    return dict(groups)


def find_cross_source_candidates(
    state_records: list[dict],
    search_radius_km: float = 2.0,
) -> list[tuple[dict, dict]]:
    """
    Find candidate pairs from different sources within a state.

    For each record, find nearby records from other sources using
    bounding-box + Haversine filtering. Only generates each pair once.
    """
    by_source = group_by_source(state_records)
    sources = sorted(by_source.keys())

    if len(sources) < 2:
        return []

    pairs = []
    seen_pairs: set[tuple[str, str]] = set()

    # Compare each source against every other source
    for i, src_a in enumerate(sources):
        for src_b in sources[i + 1:]:
            records_a = by_source[src_a]
            records_b = by_source[src_b]

            for rec_a in records_a:
                lat_a = rec_a.get("latitude")
                lon_a = rec_a.get("longitude")

                if lat_a is None or lon_a is None:
                    continue

                target = Coordinate(latitude=float(lat_a), longitude=float(lon_a))

                nearby = find_nearby_candidates(
                    target,
                    records_b,
                    radius_km=search_radius_km,
                )

                for rec_b in nearby:
                    id_a = rec_a["pharmacy_id"]
                    id_b = rec_b["pharmacy_id"]

                    # Avoid duplicate pairs
                    pair_key = tuple(sorted([id_a, id_b]))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)

                    # Clean up augmented fields from find_nearby_candidates
                    clean_b = {k: v for k, v in rec_b.items() if not k.startswith("_")}
                    pairs.append((rec_a, clean_b))

    return pairs


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

# Source trust order (index 0 = highest priority)
SOURCE_PRIORITY = [
    "src-pcn-premises",
    "src-nafdac-retail",
    "src-nhia-facility",
    "src-state-moh",
    "src-crowdsource-field",
    "src-google-places",
    "src-grid3-health",
    "src-osm-pharmacy",
    "src-flutterwave-agent",
]


def source_rank(source_id: str) -> int:
    """Lower rank = higher priority."""
    try:
        return SOURCE_PRIORITY.index(source_id)
    except ValueError:
        return len(SOURCE_PRIORITY)


def merge_records(survivor: dict, absorbed: dict) -> dict:
    """
    Merge two records, preferring the higher-priority source's data.
    Returns a new merged record.
    """
    # Determine which record comes from the higher-priority source
    if source_rank(absorbed.get("source_id", "")) < source_rank(survivor.get("source_id", "")):
        primary, secondary = absorbed, survivor
    else:
        primary, secondary = survivor, absorbed

    merged = dict(primary)

    # Fill in nulls from the secondary record
    fill_fields = [
        "facility_name", "address_line", "ward", "lga", "state",
        "latitude", "longitude", "phone", "email", "contact_person",
        "operational_status",
    ]
    for field in fill_fields:
        if not merged.get(field) or merged.get(field) == "unknown":
            secondary_val = secondary.get(field)
            if secondary_val and secondary_val != "unknown":
                merged[field] = secondary_val

    # Merge external identifiers
    primary_ids = merged.get("external_identifiers", {}) or {}
    secondary_ids = secondary.get("external_identifiers", {}) or {}
    combined_ids = {**secondary_ids, **primary_ids}  # primary wins on conflict
    merged["external_identifiers"] = combined_ids

    # Track merge provenance
    merged["_merged_from"] = [
        primary.get("pharmacy_id"),
        secondary.get("pharmacy_id"),
    ]
    merged["_merge_sources"] = sorted(set([
        primary.get("source_id", ""),
        secondary.get("source_id", ""),
    ]))

    # Keep the primary's pharmacy_id as the survivor
    merged["pharmacy_id"] = primary["pharmacy_id"]
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()

    return merged


# ---------------------------------------------------------------------------
# Union-Find for transitive merges
# ---------------------------------------------------------------------------


class UnionFind:
    """Simple union-find for grouping transitive merge chains."""

    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        if x not in self.parent:
            self.parent[x] = x
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # path compression
            x = self.parent[x]
        return x

    def union(self, x: str, y: str):
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[ry] = rx

    def groups(self) -> dict[str, list[str]]:
        """Return groups as {root_id: [member_ids]}."""
        result: dict[str, list[str]] = defaultdict(list)
        for x in self.parent:
            result[self.find(x)].append(x)
        return dict(result)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cross-source deduplication for the Nigeria Pharmacy Registry",
    )
    parser.add_argument(
        "--output-dir",
        default="output/deduped",
        help="Directory for deduplicated output (default: output/deduped/)",
    )
    parser.add_argument(
        "--search-radius",
        type=float,
        default=2.0,
        help="Geo search radius in km for candidate pairs (default: 2.0)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to merge_rules.yaml (default: auto-detected)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show candidate counts without running scorer.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Load scorer config
    config_path = args.config or str(ROOT / "agent-03-deduplication" / "config" / "merge_rules.yaml")
    config = ScorerConfig.from_yaml(config_path)
    logger.info("Loaded scorer config from %s", config_path)

    # Load all records
    all_records = load_all_canonical()
    if not all_records:
        logger.error("No records found. Exiting.")
        sys.exit(1)

    # Source breakdown
    source_counts: dict[str, int] = defaultdict(int)
    for r in all_records:
        source_counts[r.get("source_id", "unknown")] += 1
    logger.info("Records by source:")
    for src, count in sorted(source_counts.items()):
        logger.info("  %-25s %d", src, count)

    # Group by state
    by_state = group_by_state(all_records)
    logger.info("Records grouped into %d states", len(by_state))

    # Find cross-source candidate pairs
    logger.info("Finding cross-source candidates (radius: %.1f km)...", args.search_radius)
    all_candidates: list[tuple[dict, dict]] = []

    for state, state_records in sorted(by_state.items()):
        state_sources = set(r.get("source_id") for r in state_records)
        if len(state_sources) < 2:
            continue  # Only one source in this state — no cross-source pairs

        candidates = find_cross_source_candidates(state_records, args.search_radius)
        if candidates:
            logger.info("  %-20s %d records → %d candidate pairs", state, len(state_records), len(candidates))
            all_candidates.extend(candidates)

    logger.info("Total cross-source candidate pairs: %d", len(all_candidates))

    if args.dry_run:
        print(f"\nDRY RUN: {len(all_candidates)} candidate pairs found. "
              f"Use without --dry-run to score them.")
        return

    if not all_candidates:
        logger.info("No cross-source candidates found. Writing registry as-is.")
        write_output(all_records, [], [], args.output_dir)
        return

    # Score all candidates
    logger.info("Scoring %d candidate pairs...", len(all_candidates))
    t0 = time.time()

    auto_merges: list[dict] = []
    reviews: list[dict] = []
    no_matches = 0

    for rec_a, rec_b in all_candidates:
        result = compute_match(rec_a, rec_b, config)

        if result.decision == "auto_merge":
            auto_merges.append(result.to_dict())
        elif result.decision == "review":
            reviews.append(result.to_dict())
        else:
            no_matches += 1

    elapsed = time.time() - t0
    logger.info("Scoring complete in %.1fs", elapsed)
    logger.info("  Auto-merge : %d", len(auto_merges))
    logger.info("  Review     : %d", len(reviews))
    logger.info("  No match   : %d", no_matches)

    # Build merge groups using Union-Find (handles transitive chains: A=B, B=C → A=B=C)
    uf = UnionFind()
    for match in auto_merges:
        uf.union(match["record_a_id"], match["record_b_id"])

    merge_groups = uf.groups()
    multi_groups = {k: v for k, v in merge_groups.items() if len(v) > 1}
    logger.info("Merge groups: %d (involving %d records)",
                len(multi_groups),
                sum(len(v) for v in multi_groups.values()))

    # Apply merges
    record_index = {r["pharmacy_id"]: r for r in all_records}
    absorbed_ids: set[str] = set()
    merged_records: list[dict] = []

    for root_id, member_ids in multi_groups.items():
        # Sort members by source priority (best source first)
        members = [record_index[mid] for mid in member_ids if mid in record_index]
        members.sort(key=lambda r: source_rank(r.get("source_id", "")))

        if not members:
            continue

        # Iteratively merge: start with best-priority record, fold in the rest
        survivor = dict(members[0])
        for other in members[1:]:
            survivor = merge_records(survivor, other)
            absorbed_ids.add(other["pharmacy_id"])

        merged_records.append(survivor)
        # Mark the original survivor as absorbed too (replaced by merged version)
        absorbed_ids.add(members[0]["pharmacy_id"])

    # Build final registry: merged records + unaffected records
    final_records = []
    final_records.extend(merged_records)
    for r in all_records:
        if r["pharmacy_id"] not in absorbed_ids:
            final_records.append(r)

    logger.info("Final registry: %d records (was %d, merged %d duplicates)",
                len(final_records), len(all_records),
                len(all_records) - len(final_records))

    # Write output
    write_output(final_records, auto_merges, reviews, args.output_dir)


def write_output(
    records: list[dict],
    auto_merges: list[dict],
    reviews: list[dict],
    output_dir: str,
) -> None:
    """Write deduplicated registry and match reports."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Deduplicated canonical records
    canonical_path = out_path / f"canonical_deduped_{ts}.json"
    with open(canonical_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    logger.info("Wrote %d records to %s", len(records), canonical_path)

    # Auto-merge report
    if auto_merges:
        merges_path = out_path / f"auto_merges_{ts}.json"
        with open(merges_path, "w", encoding="utf-8") as f:
            json.dump(auto_merges, f, indent=2, ensure_ascii=False)
        logger.info("Wrote %d auto-merge matches to %s", len(auto_merges), merges_path)

    # Review queue
    if reviews:
        reviews_path = out_path / f"review_queue_{ts}.json"
        with open(reviews_path, "w", encoding="utf-8") as f:
            json.dump(reviews, f, indent=2, ensure_ascii=False)
        logger.info("Wrote %d review candidates to %s", len(reviews), reviews_path)

    # Summary stats
    source_counts: dict[str, int] = defaultdict(int)
    state_counts: dict[str, int] = defaultdict(int)
    for r in records:
        source_counts[r.get("source_id", "unknown")] += 1
        state_counts[r.get("state") or "Unknown"] += 1

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_records": len(records),
        "auto_merges": len(auto_merges),
        "review_queue": len(reviews),
        "by_source": dict(sorted(source_counts.items())),
        "by_state": dict(sorted(state_counts.items(), key=lambda x: -x[1])),
    }
    summary_path = out_path / f"dedup_summary_{ts}.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("Wrote summary to %s", summary_path)


if __name__ == "__main__":
    main()
