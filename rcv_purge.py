"""
rcv_purge.py  —  RCV Tabulator with Candidate Purge Pre-processing
===================================================================
Identical to rcv.py in every respect, except that before any validation
or tabulation, all columns whose header is exactly "X" (case-insensitive)
are removed from every ballot.  The ranks are then re-compressed so that
the remaining candidates form a clean consecutive preference list.

Use this when candidates have been withdrawn or disqualified after ballots
were collected: rename their header column(s) to "X" in the CSV and run
this script instead of rcv.py.

Multiple X columns are all purged simultaneously before compression.

Usage:
    python rcv_purge.py ballots.csv
    python rcv_purge.py ballots.csv --output-dir results/
    python rcv_purge.py ballots.csv --seed 42
"""

import argparse
import csv
import random
import sys
from pathlib import Path

# Re-use all logic from rcv.py — no duplication.
from rcv import (
    parse_ranks,
    validate_ranks,
    ranks_to_ballot,
    ranks_to_csv_row,
    review_flagged,
    run_irv,
    write_cleaned_ballots,
    write_round_log,
    write_flagged,
    print_summary,
)


# ---------------------------------------------------------------------------
# Purge-aware ballot loader
# ---------------------------------------------------------------------------

def load_ballots_purged(
    path: str,
) -> tuple[list[str], list[str], list[tuple[list[str], dict[str, int]]], list[dict]]:
    """
    Load ballots, silently dropping all columns headed 'X' (case-insensitive).

    Returns:
        all_columns     — every column name in the original CSV (including X's),
                          used only for reporting
        candidates      — column names after purging X's; used for tabulation
        valid_ballots   — list of (preference_list, compressed_rank_dict) tuples
        flagged         — list of dicts describing invalid ballots
    """
    path = Path(path)
    if not path.exists():
        sys.exit(f"Error: file not found: {path}")

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        all_columns = [c.strip() for c in reader.fieldnames if c.strip()]
        raw_rows = [
            {k.strip(): v.strip() for k, v in row.items() if k.strip()}
            for row in reader
        ]
        raw_rows = [row for row in raw_rows if any(v for v in row.values())]
    def is_purged(name: str) -> bool:
        u = name.upper()
        return u == "X" or u.startswith("X ")

    purged = [c for c in all_columns if is_purged(c)]
    candidates = [c for c in all_columns if not is_purged(c)]

    if not purged:
        print("  Note: no columns named 'X' found — proceeding without purge.")
    else:
        print(f"  Purging {len(purged)} column(s) named 'X'.")

    valid_ballots: list[tuple[list[str], dict[str, int]]] = []
    flagged: list[dict] = []

    for i, row in enumerate(raw_rows, start=2):  # row 1 = header
        # Drop X-candidate entries from this row before any processing.
        pruned_row = {c: row[c] for c in candidates if c in row}

        ranks, parse_errors = parse_ranks(pruned_row, candidates)

        if parse_errors:
            flagged.append({
                "row": i,
                "reason": "parse error: " + "; ".join(parse_errors),
                "raw": pruned_row,
            })
            continue

        dupes = validate_ranks(ranks)
        if dupes:
            flagged.append({
                "row": i,
                "reason": f"duplicate rank(s): {dupes}",
                "raw": pruned_row,
            })
            continue

        ordered = ranks_to_ballot(ranks)
        compressed = {c: idx + 1 for idx, c in enumerate(ordered)}
        valid_ballots.append((ordered, compressed))

    return all_columns, candidates, valid_ballots, flagged


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="RCV tabulator with X-candidate purge pre-processing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("ballots", help="Path to input CSV file")
    parser.add_argument(
        "--output-dir", default=None,
        help="Directory for output CSVs (default: same as input file)",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducible tie-breaking",
    )
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    input_path = Path(args.ballots)
    out_dir = Path(args.output_dir) if args.output_dir else input_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = input_path.stem
    round_log_path = out_dir / f"{stem}_rounds.csv"
    flagged_path   = out_dir / f"{stem}_excluded.csv"
    cleaned_path   = out_dir / f"{stem}_cleaned.csv"

    print(f"\nLoading ballots from: {input_path}")
    all_columns, candidates, valid_ballots, flagged = load_ballots_purged(str(input_path))

    print(f"  Original columns : {len(all_columns)}  "
          f"({len(all_columns) - len(candidates)} purged)")
    print(f"  Candidates       : {len(candidates)}")
    print(f"  Valid ballots    : {len(valid_ballots)}")
    print(f"  Flagged ballots  : {len(flagged)}")

    # --- Interactive review of flagged ballots before tabulation ---
    rescued, excluded = review_flagged(flagged, candidates)
    all_ballots = valid_ballots + rescued

    if not all_ballots:
        sys.exit("Error: no valid ballots to tabulate.")

    winner, rounds = run_irv(all_ballots, candidates)

    print("\nWriting output:")
    write_cleaned_ballots(all_ballots, candidates, cleaned_path)
    write_round_log(rounds, candidates, round_log_path)
    write_flagged(excluded, candidates, flagged_path)

    print_summary(winner, rounds, excluded)


if __name__ == "__main__":
    main()
