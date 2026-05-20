"""
rcv.py  —  Instant-Runoff Ranked Choice Voting Tabulator
=========================================================
Input CSV format:
    - One row per voter
    - One column per candidate (header = candidate name)
    - Cell value = rank assigned by voter (1 = first choice, 2 = second, ...)
    - Blank / missing = candidate not ranked by this voter

Usage:
    python rcv.py ballots.csv
    python rcv.py ballots.csv --output-dir results/
    python rcv.py ballots.csv --seed 42    (reproducible tie-breaking)
"""

import argparse
import csv
import random
import sys
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Ballot loading and validation
# ---------------------------------------------------------------------------

def parse_ranks(row: dict, candidates: list[str]) -> tuple[dict[str, int], list[str]]:
    """
    Parse one raw CSV row into a rank dict and a list of parse-error strings.
    Returns (ranks, errors).  ranks maps candidate -> rank integer.
    """
    ranks: dict[str, int] = {}
    errors: list[str] = []
    for cand in candidates:
        val = row.get(cand, "")
        if val == "":
            continue
        try:
            r = int(val)
            if r < 1:
                errors.append(f"{cand}={val} (non-positive rank)")
            else:
                ranks[cand] = r
        except ValueError:
            errors.append(f"{cand}={val!r} (not an integer)")
    return ranks, errors


def validate_ranks(ranks: dict[str, int]) -> list[str]:
    """Return list of duplicate rank values (empty = valid)."""
    seen, dupes = set(), set()
    for v in ranks.values():
        (dupes if v in seen else seen).add(v)
    return sorted(dupes)


def ranks_to_ballot(ranks: dict[str, int]) -> list[str]:
    """Compress gaps and return ordered preference list."""
    return sorted(ranks.keys(), key=lambda c: ranks[c])


def ranks_to_csv_row(ranks: dict[str, int], candidates: list[str]) -> dict[str, str]:
    """Convert a rank dict back to a CSV row dict (compressed ranks, blanks for unranked)."""
    ordered = ranks_to_ballot(ranks)
    compressed = {cand: str(i + 1) for i, cand in enumerate(ordered)}
    return {cand: compressed.get(cand, "") for cand in candidates}


def load_ballots(path: str) -> tuple[list[str], list[tuple[list[str], dict[str, int]]], list[dict]]:
    """
    Returns:
        candidates      — ordered list of candidate names (from header)
        valid_ballots   — list of (preference_list, compressed_rank_dict) tuples
        flagged         — list of dicts describing invalid ballots
    """
    path = Path(path)
    if not path.exists():
        sys.exit(f"Error: file not found: {path}")

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        candidates = [c.strip() for c in reader.fieldnames if c.strip()]
        raw_rows = [
            {k.strip(): v.strip() for k, v in row.items() if k.strip()}
            for row in reader
        ]

    valid_ballots: list[tuple[list[str], dict[str, int]]] = []
    flagged: list[dict] = []

    for i, row in enumerate(raw_rows, start=2):  # row 1 = header
        ranks, parse_errors = parse_ranks(row, candidates)

        if parse_errors:
            flagged.append({
                "row": i,
                "reason": "parse error: " + "; ".join(parse_errors),
                "raw": row,
            })
            continue

        dupes = validate_ranks(ranks)
        if dupes:
            flagged.append({
                "row": i,
                "reason": f"duplicate rank(s): {dupes}",
                "raw": row,
            })
            continue

        ordered = ranks_to_ballot(ranks)
        compressed = {c: i + 1 for i, c in enumerate(ordered)}
        valid_ballots.append((ordered, compressed))

    return candidates, valid_ballots, flagged


# ---------------------------------------------------------------------------
# IRV core
# ---------------------------------------------------------------------------

def active_first_choice(ballot: list[str], eliminated: set[str]) -> str | None:
    """Return the highest-ranked non-eliminated candidate on this ballot."""
    for cand in ballot:
        if cand not in eliminated:
            return cand
    return None  # ballot exhausted


def tally(ballots: list[tuple[list[str], dict]], eliminated: set[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for ballot, _ in ballots:
        top = active_first_choice(ballot, eliminated)
        if top is not None:
            counts[top] += 1
    return dict(counts)


def positional_tally(
    tied: list[str],
    ballots: list[tuple[list[str], dict]],
    active_set: set[str],
    n_survivors: int,
) -> dict[str, list[int]]:
    """
    For each candidate in `tied`, compute a positional vote vector of length
    `n_survivors`: index k holds the number of ballots on which that candidate
    ranks k-th (0-indexed) among all surviving candidates (those in `active_set`).
    """
    tied_set = set(tied)
    counts: dict[str, list[int]] = {c: [0] * n_survivors for c in tied}

    for ballot, _ in ballots:
        surviving_prefs = [c for c in ballot if c in active_set]
        for pos, cand in enumerate(surviving_prefs):
            if cand in tied_set:
                if pos >= n_survivors:
                    raise RuntimeError(
                        f"positional_tally: pos={pos} >= n_survivors={n_survivors}; "
                        f"cand={cand!r}, surviving_prefs={surviving_prefs}, "
                        f"active_set size={len(active_set)}"
                    )
                counts[cand][pos] += 1

    return counts


def resolve_tie(
    tied: list[str],
    history: list[dict[str, int]],
    ballots: list[tuple[list[str], dict]],
    active_set: set[str],
    n_survivors: int,
) -> str:
    """
    Choose which tied candidate to eliminate.  Three stages:

    1. Lexicographic positional comparison on current transferred-vote tallies:
       for each rank position among all surviving candidates, candidates with
       the most votes at that position are removed from elimination contention.
       This continues until one candidate remains — that candidate is eliminated.

    2. Historical round lookback (most-recent-first): among candidates still
       tied after stage 1, compare their first-choice totals in earlier rounds.
       The candidate with the fewest votes in the most recent round where they
       differ is eliminated.

    3. Random selection: if all prior stages leave more than one candidate
       tied, choose uniformly at random.

    Returns the candidate to ELIMINATE.
    """
    # --- Stage 1: lexicographic positional comparison ---
    pos_counts = positional_tally(tied, ballots, active_set, n_survivors)
    remaining = list(tied)
    pos = 0
    while pos < n_survivors:
        counts_at_pos = {c: pos_counts[c][pos] for c in remaining}
        if len(set(counts_at_pos.values())) == 1:
            pos += 1  # all equal at this position, advance
            continue
        # At this position candidates differ: eliminate those with fewest votes.
        min_at_pos = min(counts_at_pos.values())
        remaining = [c for c in remaining if counts_at_pos[c] == min_at_pos]
        if len(remaining) == 1:
            return remaining[0]
        # Multiple candidates share the minimum — re-examine this same position
        # with the reduced set before advancing.

    # --- Stage 2: historical round lookback ---
    for past_round in reversed(history[:-1]):
        min_votes = min(past_round.get(c, 0) for c in remaining)
        still_tied = [c for c in remaining if past_round.get(c, 0) == min_votes]
        if len(still_tied) == 1:
            return still_tied[0]
        remaining = still_tied

    # --- Stage 3: random fallback ---
    return random.choice(remaining)


def run_irv(
    ballots: list[tuple[list[str], dict]],
    candidates: list[str],
) -> tuple[str, list[dict]]:
    """
    Returns:
        winner      — name of the winning candidate
        rounds      — list of round-result dicts
    """
    eliminated: set[str] = set()
    remaining = [c for c in candidates]  # preserves original order
    history: list[dict[str, int]] = []
    rounds: list[dict] = []

    round_num = 0
    while True:
        round_num += 1
        counts = tally(ballots, eliminated)
        history.append(counts)

        active_ballots = sum(counts.values())
        threshold = active_ballots / 2  # strictly > 50 %

        round_info = {
            "round": round_num,
            "counts": dict(counts),
            "active_ballots": active_ballots,
            "eliminated": None,
            "winner": None,
            "tie_broken": False,
            "tie_candidates": [],
        }

        # Check for a majority winner
        for cand, votes in counts.items():
            if votes > threshold:
                round_info["winner"] = cand
                rounds.append(round_info)
                return cand, rounds

        # Single remaining candidate (shouldn't normally happen before majority,
        # but handles edge cases like all ballots exhausted)
        active_candidates = [c for c in remaining if c not in eliminated]
        if len(active_candidates) == 1:
            round_info["winner"] = active_candidates[0]
            rounds.append(round_info)
            return active_candidates[0], rounds

        # Find the candidate(s) with fewest votes
        min_votes = min(counts.get(c, 0) for c in active_candidates)
        to_eliminate = [c for c in active_candidates if counts.get(c, 0) == min_votes]

        if len(to_eliminate) > 1:
            active_set = set(active_candidates)
            n_survivors = len(active_candidates)
            loser = resolve_tie(to_eliminate, history, ballots, active_set, n_survivors)
            round_info["tie_broken"] = True
            round_info["tie_candidates"] = to_eliminate
        else:
            loser = to_eliminate[0]

        round_info["eliminated"] = loser
        eliminated.add(loser)
        rounds.append(round_info)


# ---------------------------------------------------------------------------
# Interactive flagged ballot review
# ---------------------------------------------------------------------------

def prompt_fix_ballot(
    entry: dict,
    candidates: list[str],
) -> tuple[list[str], dict[str, int]] | None:
    """
    Interactively ask the user to fix or discard a flagged ballot.
    Returns a valid (ordered, compressed_ranks) tuple, or None to exclude.
    """
    print(f"\n  {'─' * 56}")
    print(f"  Flagged ballot  (CSV row {entry['row']})")
    print(f"  Reason : {entry['reason']}")
    print(f"  Data   :")
    for cand in candidates:
        val = entry["raw"].get(cand, "")
        if val:
            print(f"             {cand}: {val}")

    while True:
        print()
        print("  Options:")
        print("    e  — enter corrected ranks manually")
        print("    x  — exclude this ballot from tabulation")
        choice = input("  Choice [e/x]: ").strip().lower()

        if choice == "x":
            return None

        if choice == "e":
            print()
            print("  Enter the rank for each candidate (blank = unranked):")
            new_raw: dict[str, str] = {}
            for cand in candidates:
                old = entry["raw"].get(cand, "")
                prompt = f"    {cand} (was {old!r}): " if old else f"    {cand}: "
                val = input(prompt).strip()
                new_raw[cand] = val

            ranks, errors = parse_ranks(new_raw, candidates)
            if errors:
                print(f"  ✗  Parse errors: {'; '.join(errors)}  — try again.")
                continue

            dupes = validate_ranks(ranks)
            if dupes:
                print(f"  ✗  Duplicate rank(s) still present: {dupes}  — try again.")
                continue

            ordered = ranks_to_ballot(ranks)
            compressed = {c: i + 1 for i, c in enumerate(ordered)}
            print(f"  ✓  Accepted: {' > '.join(ordered)}")
            return ordered, compressed

        print("  Please enter 'e' or 'x'.")


def review_flagged(
    flagged: list[dict],
    candidates: list[str],
) -> tuple[list[tuple[list[str], dict[str, int]]], list[dict]]:
    """
    Walk through all flagged ballots interactively.
    Returns:
        rescued   — corrected ballots to add to valid set
        excluded  — entries confirmed as excluded (for the flagged CSV)
    """
    if not flagged:
        return [], []

    print(f"\n{'═' * 60}")
    print(f"  FLAGGED BALLOT REVIEW  ({len(flagged)} ballot(s))")
    print(f"{'═' * 60}")

    rescued: list[tuple[list[str], dict[str, int]]] = []
    excluded: list[dict] = []

    for entry in flagged:
        result = prompt_fix_ballot(entry, candidates)
        if result is None:
            excluded.append(entry)
            print(f"  → Excluded (row {entry['row']})")
        else:
            rescued.append(result)
            print(f"  → Corrected ballot added (row {entry['row']})")

    print(f"\n  Review complete: {len(rescued)} corrected, {len(excluded)} excluded.")
    print(f"{'═' * 60}\n")
    return rescued, excluded


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_cleaned_ballots(
    ballots: list[tuple[list[str], dict[str, int]]],
    candidates: list[str],
    out_path: Path,
) -> None:
    """Write all valid (post-review) ballots as a clean CSV with compressed ranks."""
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=candidates)
        writer.writeheader()
        for _, ranks in ballots:
            writer.writerow(ranks_to_csv_row(ranks, candidates))
    print(f"  Cleaned ballots  →  {out_path}")

def write_round_log(rounds: list[dict], candidates: list[str], out_path: Path) -> None:
    """Write a CSV with one row per round."""
    # Collect all candidates that ever appeared in any round
    all_cands = candidates  # use original ordering

    fieldnames = ["Round", "Active Ballots"] + all_cands + ["Eliminated", "Winner", "Tie Broken", "Tie Candidates"]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rounds:
            row = {
                "Round": r["round"],
                "Active Ballots": r["active_ballots"],
                "Eliminated": r["eliminated"] or "",
                "Winner": r["winner"] or "",
                "Tie Broken": "Yes" if r["tie_broken"] else "",
                "Tie Candidates": "; ".join(r["tie_candidates"]) if r["tie_candidates"] else "",
            }
            for cand in all_cands:
                row[cand] = r["counts"].get(cand, 0)
            writer.writerow(row)

    print(f"  Round log  →  {out_path}")


def write_flagged(excluded: list[dict], candidates: list[str], out_path: Path) -> None:
    """Write a CSV of ballots excluded after review."""
    if not excluded:
        print("  No excluded ballots.")
        return

    fieldnames = ["Row", "Reason"] + candidates

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for entry in excluded:
            row = {"Row": entry["row"], "Reason": entry["reason"]}
            for cand in candidates:
                row[cand] = entry["raw"].get(cand, "")
            writer.writerow(row)

    print(f"  Excluded ballots →  {out_path}  ({len(excluded)} ballot(s))")


# ---------------------------------------------------------------------------
# Pretty console summary
# ---------------------------------------------------------------------------

def print_summary(winner: str, rounds: list[dict], flagged: list[dict]) -> None:
    print("\n" + "=" * 60)
    print("  RANKED CHOICE VOTING — RESULTS")
    print("=" * 60)

    if flagged:
        print(f"\n  ⚠  {len(flagged)} ballot(s) flagged and excluded (see flagged CSV).")

    print()
    for r in rounds:
        prefix = f"  Round {r['round']:>2} |"
        votes_str = "  ".join(
            f"{c}: {v}" for c, v in sorted(r["counts"].items(), key=lambda x: -x[1])
        )
        print(f"{prefix}  {votes_str}")
        if r["tie_broken"]:
            print(f"           |  Tie broken among: {', '.join(r['tie_candidates'])}")
        if r["eliminated"]:
            print(f"           |  ✗ Eliminated: {r['eliminated']}")
        if r["winner"]:
            print(f"           |  ✓ Winner: {r['winner']}")
        print()

    print(f"  🏆  Winner: {winner}")
    print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Instant-Runoff RCV tabulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("ballots", help="Path to input CSV file")
    parser.add_argument(
        "--output-dir", default=None,
        help="Directory for output CSVs (default: same as input file)"
    )
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducible tie-breaking")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    input_path = Path(args.ballots)
    out_dir = Path(args.output_dir) if args.output_dir else input_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = input_path.stem
    round_log_path   = out_dir / f"{stem}_rounds.csv"
    flagged_path     = out_dir / f"{stem}_excluded.csv"
    cleaned_path     = out_dir / f"{stem}_cleaned.csv"

    print(f"\nLoading ballots from: {input_path}")
    candidates, valid_ballots, flagged = load_ballots(str(input_path))

    print(f"  Candidates : {len(candidates)}")
    print(f"  Valid      : {len(valid_ballots)}")
    print(f"  Flagged    : {len(flagged)}")

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
