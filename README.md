# rcv.py — Instant-Runoff Ranked Choice Voting Tabulator

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Input Format](#input-format)
4. [Data Cleaning and Validation](#data-cleaning-and-validation)
   - [Stage 1: Parsing](#stage-1-parsing)
   - [Stage 2: Duplicate Rank Detection](#stage-2-duplicate-rank-detection)
   - [Stage 3: Gap Compression](#stage-3-gap-compression)
   - [Stage 4: Interactive Flagged Ballot Review](#stage-4-interactive-flagged-ballot-review)
5. [Vote Tabulation Algorithm](#vote-tabulation-algorithm)
   - [The Instant-Runoff Method](#the-instant-runoff-method)
   - [Round Structure](#round-structure)
   - [Majority Threshold](#majority-threshold)
   - [Ballot Exhaustion](#ballot-exhaustion)
   - [Termination Conditions](#termination-conditions)
6. [Tie-Breaking](#tie-breaking)
   - [When a Tie Occurs](#when-a-tie-occurs)
   - [Step 1: Positional Tally Comparison](#step-1-positional-tally-comparison)
   - [Step 2: Historical Round Lookback](#step-2-historical-round-lookback)
   - [Step 3: Final Fallback](#step-3-final-fallback)
   - [Reproducibility and the `--seed` Flag](#reproducibility-and-the---seed-flag)
   - [Tie-Breaking Example](#tie-breaking-example)
7. [Candidate Purge: `rcv_purge.py`](#candidate-purge-rcv_purgepy)
   - [Purpose](#purpose)
   - [How to Mark Candidates for Removal](#how-to-mark-candidates-for-removal)
   - [What the Purge Does](#what-the-purge-does)
   - [Usage](#usage)
   - [Output](#output)
8. [Output Files](#output-files)
9. [Command-Line Reference](#command-line-reference)
10. [Design Decisions and Rationale](#design-decisions-and-rationale)

---

## Overview

`rcv.py` is a self-contained Python (≥ 3.10) implementation of **Instant-Runoff Voting (IRV)**, also known as the Alternative Vote or Ranked Choice Voting. It reads ballot data from a CSV file, validates and cleans the data interactively, runs the IRV algorithm, and produces structured CSV output suitable for auditing.

No third-party libraries are required — the implementation uses only the Python standard library (`csv`, `argparse`, `random`, `collections`, `pathlib`).

---

## Quick Start

```bash
# Basic usage
python rcv.py ballots.csv

# Write output to a specific directory
python rcv.py ballots.csv --output-dir results/

# Reproducible random tie-breaking
python rcv.py ballots.csv --seed 42
```

---

## Input Format

The input is a CSV file with the following structure:

- **Header row**: one column per candidate. Column names are the candidate names.
- **Data rows**: one row per voter.
- **Cell values**: the rank a voter assigned to that candidate (a positive integer). A blank cell means the voter did not rank that candidate.

Example:

```
Alice,Bob,Carol,Dave
1,2,3,
,1,2,3
2,1,,3
1,3,2,
```

Voter 1 ranked Alice first, Bob second, Carol third, and left Dave unranked.
Voter 3 ranked Bob first, Alice second, Dave third, and left Carol unranked.

The file is read with `utf-8-sig` encoding, which transparently handles the BOM marker that Excel sometimes prepends to UTF-8 CSV exports. Leading and trailing whitespace is stripped from all header names and cell values before any processing.

---

## Data Cleaning and Validation

Ballot cleaning happens in four sequential stages. The first three occur automatically during file loading; the fourth is interactive and runs before any tabulation.

### Stage 1: Parsing

Each cell for each candidate is read and subjected to the following checks, in order:

1. **Blank / missing**: A blank string (after whitespace stripping) is treated as "candidate not ranked." This is valid and requires no action.

2. **Integer conversion**: The value is passed to `int()`. If this raises `ValueError` (e.g., the cell contains `"1.5"`, `"yes"`, or `"--"`), a parse error is recorded for that cell. The entire ballot is flagged.

3. **Positivity check**: If the parsed integer is less than 1, a parse error is recorded. Rank values must be positive integers; `0` and negative values are disallowed because they have no meaningful interpretation in a ranked ballot.

If any parse error is found on a ballot, that ballot is flagged immediately and the remaining checks are skipped.

### Stage 2: Duplicate Rank Detection

After successful parsing, the set of assigned rank values is checked for duplicates. Specifically: if the multiset of rank values has any element appearing more than once, the ballot is flagged.

Example of a flagged ballot: `Alice=1, Bob=2, Carol=1` — rank `1` is assigned to two candidates. It is not possible to determine which of Alice and Carol the voter actually preferred, so the ballot cannot be used as-is.

Skipped ranks (e.g., `Alice=1, Bob=3` with no rank `2`) are **not** flagged at this stage; they are handled by gap compression (Stage 3).

The flag message records which rank values were duplicated, e.g.: `duplicate rank(s): [1, 3]`.

### Stage 3: Gap Compression

Every ballot that passes Stages 1 and 2 undergoes gap compression before being stored. This addresses cases where a voter skipped rank values (e.g., used 1, 3, 5 but not 2 or 4).

**Procedure**: the candidates are sorted by their assigned rank values, then re-numbered consecutively starting from 1. The relative ordering of candidates is fully preserved; only the specific integers are changed.

Example: `Alice=1, Carol=3, Dave=5` becomes `Alice=1, Carol=2, Dave=3`.

This is a lossless normalization — no preference information is destroyed — and it ensures that all stored ballots are in a canonical dense form. The compressed ranks are what appear in `_cleaned.csv`.

**Rationale for compressing rather than rejecting**: A voter who wrote `1, 3` almost certainly meant "first choice, second choice" and simply skipped `2` by accident or unfamiliarity with the form. Discarding such ballots would disenfranchise voters for a harmless clerical imprecision. Duplicate ranks, by contrast, represent a genuine ambiguity that cannot be resolved without re-contacting the voter.

### Stage 4: Interactive Flagged Ballot Review

After all ballots are loaded and the initial valid/flagged partition is determined, the program **halts** and presents each flagged ballot interactively before any tabulation occurs. This ensures that no election result is produced from data the administrator has not reviewed.

For each flagged ballot, the program displays:

- The CSV row number (1-indexed, with row 1 = header)
- The reason for flagging
- The raw data for that row (only non-blank candidate values)

The administrator then chooses one of two actions:

**`e` — Edit**: The program prompts for a corrected rank for each candidate in the original column order. For each candidate, the current (erroneous) value is shown alongside the prompt for reference. The corrected ballot is then subjected to the full Stage 1 + Stage 2 validation cycle. If errors remain, the prompt repeats until a clean ballot is entered. Gap compression (Stage 3) is applied automatically to the corrected ballot before it is accepted.

**`x` — Exclude**: The ballot is permanently excluded from tabulation and written to `_excluded.csv` for the record.

Ballots corrected via the `e` path are appended to the valid ballot set and are indistinguishable from originally-valid ballots during tabulation. They appear in `_cleaned.csv`.

---

## Vote Tabulation Algorithm

### The Instant-Runoff Method

IRV is an iterative elimination algorithm. In each round, every active ballot contributes exactly one vote to whichever remaining candidate appears highest on that ballot. The candidate with the fewest votes is eliminated, and the process repeats. This continues until a single candidate has a strict majority of the active vote, at which point that candidate is declared the winner.

The key property of IRV is that it simulates a sequence of runoff elections without requiring voters to return to the polls: a voter's ballot automatically "transfers" to their next-ranked candidate whenever their current top choice is eliminated.

### Round Structure

Each round proceeds as follows:

1. For each ballot, scan the preference list from first to last and find the first candidate not yet eliminated. That candidate receives one vote from this ballot. (If all candidates on a ballot have been eliminated, the ballot is exhausted and contributes no vote in this or any future round.)

2. Sum the votes for each remaining candidate, producing the round tally.

3. Check for a majority winner (see below). If found, the election is over.

4. Check whether only one candidate remains. If so, that candidate wins by default.

5. Otherwise, find the candidate(s) with the minimum vote count. If there is a unique minimum, that candidate is eliminated. If there is a tie for the minimum, the tie-breaking procedure is invoked (see [Tie-Breaking](#tie-breaking)).

6. The eliminated candidate is added to the `eliminated` set, their name is recorded in the round log, and the next round begins.

### Majority Threshold

A candidate wins when their vote count strictly exceeds half of the **active ballot count** for that round:

```
votes > active_ballots / 2
```

where `active_ballots` is the total number of ballots that contributed a vote in that round (i.e., ballots not yet exhausted).

This is a **dynamic threshold** — it adjusts downward as ballots become exhausted. This is the standard IRV convention and reflects the principle that a winner should command a majority of the voters who are still expressing a preference.

Note that this is not a threshold over the total number of ballots cast. A ballot that exhausts (all its ranked candidates are eliminated) no longer counts toward the denominator.

### Ballot Exhaustion

A ballot is exhausted when every candidate that voter ranked has been eliminated in prior rounds. Exhausted ballots are silently dropped from the active count; they do not contribute votes, and they do not count toward the majority threshold. The round tally contains only the votes that were actually cast in that round.

This is a deliberate and standard design choice. The alternative — counting exhausted ballots toward the threshold — would make it possible for no candidate ever to achieve a majority, even when only one candidate remains. The dynamic threshold avoids this pathology.

### Termination Conditions

The algorithm terminates in one of two ways:

1. **Majority achieved**: a candidate receives strictly more than half the active votes in some round. This is the normal termination path.

2. **Last candidate standing**: after eliminations, only one candidate remains. This is a degenerate case that in principle should not occur before a majority is achieved (since with two candidates, the one with more votes necessarily has a majority), but it is handled explicitly to guard against edge cases such as all ballots exhausting simultaneously or arithmetic peculiarities in small elections.

---

## Tie-Breaking

### When a Tie Occurs

A tie for elimination occurs when two or more candidates share the minimum first-choice vote count in a given round. The tie-breaking procedure is applied exclusively to the set of tied candidates and proceeds through three stages in order. Random selection is only ever reached if the first two stages are both exhausted.

### Step 1: Positional Tally Comparison

This is the primary tie-breaking mechanism and resolves the vast majority of ties deterministically from the ballot data alone.

For each tied candidate, a **positional vote vector** is computed. Entry *k* (0-indexed) of the vector holds the number of ballots on which that candidate ranks *k*-th among all **surviving candidates** at the moment of the tie. Surviving candidates are all those not yet eliminated — including any with zero first-choice votes in the current round. The positions are re-indexed relative to the surviving field: eliminated candidates are stripped from each ballot before positions are counted, so a voter's 5th choice may become their 2nd effective choice if candidates ranked 1st through 4th have all been eliminated.

This positional vector captures the full distribution of transferred support for each tied candidate across all preference ranks, not merely their current first-choice tally.

**The comparison proceeds as a strict lexicographic scan:**

Starting at position 0 (first-choice votes among survivors) and advancing one position at a time:

1. Compute each remaining candidate's vote count at the current position.
2. If all remaining candidates have equal counts at this position, advance to the next position.
3. If candidates differ, **narrow the remaining set to those with the fewest votes at this position** — those candidates are least preferred by voters at this rank level and are therefore the most appropriate to eliminate. Candidates with more votes at this position are removed from elimination contention entirely.
4. After narrowing: if exactly one candidate remains, eliminate them. If multiple candidates remain (i.e. they all share the same minimum at this position), **re-examine this same position** with the reduced set before advancing. This is essential: narrowing the set may reveal new differences at the current position that were previously hidden by candidates now removed.
5. Continue until either one candidate is identified for elimination, or all positions are exhausted.

**Key invariance property**: the positional vectors are computed identically whether non-surviving candidates were removed by the algorithm during earlier rounds or pre-emptively purged via `rcv_purge.py`. This ensures consistent tie-breaking regardless of how the election was set up.

**Example**: suppose three candidates A, B, C are tied for last place with the following positional vectors among 4 surviving candidates:

| Candidate | pos 0 | pos 1 | pos 2 | pos 3 |
|-----------|-------|-------|-------|-------|
| A         | 4     | 4     | 2     | 3     |
| B         | 4     | 2     | 1     | 0     |
| C         | 4     | 1     | 1     | 2     |

- pos 0: A=4, B=4, C=4 → all equal, advance.
- pos 1: A=4, B=2, C=1 → minimum is 1 (C). Narrow to {C}. One candidate remains → **C eliminated**.

Then B vs A continues in the next round. At pos 1: B=2, A=4 → minimum is 2 (B) → **B eliminated**. A wins.

**Second example** with a three-way tie and intermediate narrowing:

| Candidate | pos 0 | pos 1 | pos 2 | pos 3 |
|-----------|-------|-------|-------|-------|
| C         | 0     | 0     | 1     | 0     |
| D         | 0     | 0     | 1     | 2     |
| E         | 0     | 1     | 0     | 0     |

- pos 0: all 0 → equal, advance.
- pos 1: C=0, D=0, E=1 → minimum is 0. Narrow to {C, D}. Re-examine pos 1 with {C, D}: C=0, D=0 → equal, advance.
- pos 2: C=1, D=1 → equal, advance.
- pos 3: C=0, D=2 → minimum is 0 (C). Narrow to {C}. One candidate remains → **C eliminated**.

E survives because it had more second-choice support than either C or D.

### Step 2: Historical Round Lookback

If stage 1 exhausts all positions without resolving the tie (the remaining candidates have identical positional vectors), the program looks back through the first-choice vote tallies from all previous rounds, scanning most-recent-first.

At each historical round *k*:

- Compute the minimum first-choice count among the still-tied candidates in round *k* (using 0 for any candidate who received no votes in that round).
- Retain only those candidates who had that minimum count in round *k*.
- If exactly one candidate remains, eliminate them.
- If more than one remains, reduce to that subset and continue to round *k*−1.

**Why most-recent-first?** More recent rounds reflect the current state of the electorate after transfers. Earlier rounds may include votes from since-eliminated candidates whose supporters have since redistributed, making recent tallies more informative about current relative strength.

**Why is this stage 2 rather than stage 1?** Historical first-choice totals reflect only one dimension of support. The positional tally captures the full preference distribution across all ranks and is strictly more informative. The lookback is a fallback for the rare case where positional vectors are identical across all positions.

**Important note when using `rcv_purge.py`**: candidates purged before the election started have no historical round data. If a tie would have been resolved by the lookback using those early-round tallies, stage 2 will find less history to work with and may fall through to stage 3 sooner. Stage 1 is unaffected by purging (see invariance property above).

### Step 3: Final Fallback

If both stages 1 and 2 are exhausted, one of the remaining candidates is chosen uniformly at random using `random.choice`. This is the fairest resolution when the ballot data contain no information that differentiates the tied candidates.

### Reproducibility and the `--seed` Flag

Passing `--seed N` calls `random.seed(N)` at program startup, fixing Python's Mersenne Twister PRNG sequence and making any random tie-break fully reproducible given the same input data and seed. It is good practice to record the seed whenever a random tie-break is invoked. The `Tie Broken` and `Tie Candidates` columns in `_rounds.csv` make it straightforward to identify whether and when this occurred.

### Tie-Breaking Example

Suppose four candidates A, B, C, D with the following tallies. D is eliminated at the end of round 1 (strictly fewest first-choice votes), and B and C first tie in round 2:

| Round | A  | B  | C  | D  |
|-------|----|----|----|----|
| 1     | 12 | 6  | 5  | 3  |
| 2     | 14 | 6  | 6  | —  |

D's 3 votes transfer: 2 to A, 1 to C, giving totals 14+6+6 = 26 = 12+6+5+3. ✓

In round 2, B and C tie at 6 votes. Stage 1 (positional tally) is applied first. If the full positional vectors for B and C differ at any rank position among the 3 surviving candidates, the tie is resolved there. If they are identical across all positions, stage 2 (historical lookback) is applied: round 1 shows B=6, C=5 — C has the minimum, so **C is eliminated**.

For a case where random fallback is needed:

| Round | A  | B  | C  | D  |
|-------|----|----|----|----|
| 1     | 12 | 5  | 5  | 3  |
| 2     | 13 | 6  | 6  | —  |

D's 3 votes transfer: 1 to each of A, B, C, giving totals 13+6+6 = 25 = 12+5+5+3. ✓

If the positional vectors for B and C are identical, the lookback finds B=5, C=5 in round 1 — still tied. No prior rounds remain. Random selection is invoked.

---

## Candidate Purge: `rcv_purge.py`

### Purpose

`rcv_purge.py` handles the special case where one or more candidates must be removed from the election after ballots have already been collected — for example, due to withdrawal or disqualification. It is identical to `rcv.py` in every respect except that it first strips the designated columns from every ballot before any validation or tabulation occurs. Votes that would have gone to purged candidates are automatically transferred to the voter's next-ranked surviving candidate via the normal gap compression mechanism.

Both scripts must be in the same directory, as `rcv_purge.py` imports all of its logic directly from `rcv.py`.

### How to Mark Candidates for Removal

In the input CSV header row, rename any column to be purged by prepending `X ` (the letter X followed by a single space) to the candidate's name. Any column whose header is exactly `X` (case-insensitive) or begins with `X ` (case-insensitive) will be purged. The remainder of the name after `X ` is ignored and can be used to record who was removed for audit purposes.

Examples of headers that will be purged:

```
X
x
X Alice
X withdrawn
x Disqualified
```

Examples of headers that will **not** be purged (no space after X):

```
Xavier
Xena
XJ-9
```

A CSV prepared for purging might look like this:

```
Alice,X Bob,Carol,X Dave,Eve
1,2,3,,4
2,,1,3,
,1,2,,3
```

Here Bob and Dave are marked for removal. Every voter's ranks for Bob and Dave are discarded, and the remaining ranks (Alice, Carol, Eve) are gap-compressed to form a clean consecutive preference list.

### What the Purge Does

1. All columns whose header matches the purge pattern are identified and reported at startup.
2. Those columns are stripped from every ballot row before any parsing, validation, or gap compression occurs.
3. The remaining columns are treated exactly as they would be in `rcv.py`: parsed, validated for duplicates, gap-compressed, and subjected to interactive flagged ballot review if needed.
4. The cleaned output CSV contains only the surviving candidates as columns.

The purge is equivalent to the algorithmic elimination of those candidates in the earliest possible rounds, with one practical difference: purged candidates contribute no historical round data to stage 2 of tie-breaking. Stage 1 (positional tally) is unaffected.

### Usage

```bash
# Basic usage
python rcv_purge.py ballots.csv

# Write output to a specific directory
python rcv_purge.py ballots.csv --output-dir results/

# Reproducible random tie-breaking
python rcv_purge.py ballots.csv --seed 42
```

### Output

`rcv_purge.py` produces the same three output files as `rcv.py` (`_cleaned.csv`, `_rounds.csv`, `_excluded.csv`), with the purged candidates absent from all of them. The number of purged columns is reported at startup for verification.

---

## Output Files

Running `python rcv.py ballots.csv` produces up to three output files in the same directory as the input (or in `--output-dir` if specified). All files use the stem of the input filename as a prefix.

### `<stem>_cleaned.csv`

Contains all ballots that will be used in tabulation: the originally-valid ballots plus any ballots corrected during the interactive review. Each row is a voter; columns are candidates; values are compressed ranks (consecutive integers starting from 1, blanks for unranked candidates). This file is the canonical record of the data actually used to determine the winner and can be used to independently verify the result.

### `<stem>_rounds.csv`

One row per round of the IRV algorithm. Columns:

| Column | Content |
|---|---|
| `Round` | Round number (1-indexed) |
| `Active Ballots` | Number of non-exhausted ballots in this round |
| *(one column per candidate)* | First-choice vote count for that candidate in this round; 0 if eliminated or receiving no votes |
| `Eliminated` | Name of the candidate eliminated at the end of this round (blank in the final round) |
| `Winner` | Name of the winner (populated only in the final round) |
| `Tie Broken` | `Yes` if a tie-break procedure was invoked this round, otherwise blank |
| `Tie Candidates` | Semicolon-separated list of the candidates involved in a tie (blank if no tie) |

### `<stem>_excluded.csv`

Contains the raw data for every ballot that was excluded from tabulation — either flagged automatically and then confirmed excluded during interactive review, or flagged and not corrected. Columns:

| Column | Content |
|---|---|
| `Row` | Original CSV row number of the ballot |
| `Reason` | Human-readable explanation of why the ballot was flagged |
| *(one column per candidate)* | The original, uncorrected cell value for that candidate |

If no ballots were excluded, this file is not written.

---

## Command-Line Reference

```
python rcv.py <ballots> [--output-dir DIR] [--seed N]
```

| Argument | Default | Description |
|---|---|---|
| `ballots` | *(required)* | Path to the input CSV file |
| `--output-dir DIR` | Same directory as input | Directory where output CSVs are written |
| `--seed N` | *(none)* | Integer seed for the random number generator; makes random tie-breaks reproducible |

---

## Design Decisions and Rationale

**Why IRV rather than another RCV method?** IRV (single-winner, eliminate last place each round) is the simplest and most widely-used RCV method for single-seat elections. It was specified as the desired algorithm.

**Why is the majority threshold computed over active ballots rather than total cast?** Because once a ballot exhausts, the voter is no longer expressing a preference among the remaining candidates. Counting exhausted ballots in the denominator would penalize candidates for the ballots of voters who chose not to rank them — effectively treating abstention as opposition. The active-ballot threshold treats exhaustion neutrally.

**Why flag duplicates but compress gaps?** These two anomalies have fundamentally different natures. A duplicate rank (two candidates both ranked 1) is an *ambiguity*: there is no way to determine which candidate the voter preferred without additional information. A gap (ranks 1 and 3 with no 2) is merely a *formatting imprecision*: the relative ordering of all ranked candidates is still unambiguous. Flagging gaps would disenfranchise voters for a harmless clerical error; compressing them recovers the voter's clear intent.

**Why are flagged ballots reviewed before tabulation rather than after?** The outcome of the election can depend on whether corrected ballots are included, and corrected ballots should be treated identically to originally-valid ballots. Reviewing after tabulation would either require re-running the algorithm (confusing) or would relegate corrected ballots to a secondary status. Pre-tabulation review ensures a single, clean, complete dataset is used.

**Why is the positional tally the primary tie-breaking mechanism?** First-choice tallies alone tell you only how many voters rank a candidate highest among survivors — they say nothing about how broadly preferred that candidate is at lower rank positions. A candidate could have zero first-choice votes yet be ranked second or third by a large majority of voters, making them arguably less deserving of elimination than a candidate with similarly few first-choice votes but almost no lower-rank support. The positional tally captures this full picture. It is also invariant to whether non-surviving candidates were eliminated by the algorithm or pre-purged, ensuring consistent results between `rcv.py` and `rcv_purge.py`.

**Why does the positional comparison re-examine the same position after narrowing?** When the candidate set is narrowed at a given position (because some candidates shared the minimum), the remaining candidates' counts at that same position may now differ from each other — differences that were previously obscured by the candidates just removed. Advancing immediately to the next position would skip this information. The `while` loop with conditional increment ensures every relevant comparison is made at the correct position.

**Why are positional vectors computed among all surviving candidates rather than tied candidates only?** The position a candidate occupies on a ballot reflects their standing in the full remaining field. Re-indexing positions relative to the tied subset only would discard information about how many non-tied candidates a voter preferred over each tied candidate — information that is meaningful for assessing relative support.

**Why does the historical lookback scan most-recent-first?** More recent rounds reflect the current composition of the active ballot pool after transfers. Earlier rounds may include votes from since-eliminated candidates whose supporters have since redistributed, making recent tallies more informative about current relative strength.

**Why is the historical lookback stage 2 rather than stage 1?** The positional tally is strictly more informative: it uses the full current preference distribution across all rank positions, not just first-choice counts from prior rounds. The lookback is reserved for the rare case where positional vectors are genuinely identical.

**Why is random the only final fallback?** Alphabetical tie-breaking is deterministic but introduces systematic bias: candidates whose names sort early are always disadvantaged. Random tie-breaking is unbiased in expectation. For elections where legitimacy is important, an unbiased mechanism is preferable — and reproducibility is recovered by recording the `--seed`.

**Why does `rcv_purge.py` import from `rcv.py` rather than duplicating code?** All validation, IRV, tie-breaking, interactive review, and output logic lives in exactly one place. Changes to `rcv.py` are automatically inherited by `rcv_purge.py` with no risk of the two scripts diverging.
