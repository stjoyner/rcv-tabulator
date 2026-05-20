# Running a Ranked Choice Election — Quick Start Guide

---

## For Those Already Familiar with Python

```bash
# Standard election
python3 rcv.py MyBallots.csv

# With a fixed random seed (for reproducible tie-breaking)
python3 rcv.py MyBallots.csv --seed 42

# Election with withdrawn/disqualified candidates pre-removed
python3 rcv_purge.py MyBallots.csv

# Write output files to a specific folder
python3 rcv.py MyBallots.csv --output-dir results/
```

Both `rcv.py` and `rcv_purge.py` must be in the same folder. See `README.md` for full documentation.

---

## Step-by-Step Guide for New Users

### Step 1 — Install Python

Python is a free programming language required to run these scripts. To check whether it is already installed, open a terminal (see Step 3 for how to do this) and type:

```
python3 --version
```

If a version number appears (e.g. `Python 3.11.4`), Python is already installed — skip to Step 2.

If you see an error, install Python from [https://www.python.org/downloads](https://www.python.org/downloads). Download the installer for your operating system (Windows or macOS), run it, and follow the on-screen instructions. When prompted, check the box labelled **"Add Python to PATH"** before clicking Install.

If you get stuck, an AI assistant such as Claude can walk you through the installation for your specific computer.

---

### Step 2 — Prepare Your Ballot CSV File

Ballot data must be in a **CSV file** (Comma-Separated Values). This is a plain-text spreadsheet format that Excel, Numbers, and Google Sheets can all create.

**Format rules:**

- The **first row** contains candidate names, one per column.
- Each **subsequent row** is one voter's ballot.
- Each cell contains the rank that voter gave to that candidate (1 = first choice, 2 = second choice, etc.).
- Leave a cell **blank** if the voter did not rank that candidate.
- **Do not include any other sheets, charts, or formatting** — the file must contain only this single table.

**Example** (4 candidates, 4 voters):

| Alice | Bob | Carol | Dave |
|-------|-----|-------|------|
| 1     | 2   | 3     |      |
|       | 1   | 2     | 3    |
| 2     | 1   |       | 3    |
| 1     | 3   | 2     |      |

**To save as CSV from Excel:**
1. Open your spreadsheet in Excel.
2. Go to **File → Save As**.
3. In the file format dropdown, choose **CSV (Comma delimited) (*.csv)**.
4. Click **Save**. If Excel warns about features not supported in CSV, click **Yes/Keep**.

Note down the folder where you saved the file — you will need it in Step 3.

---

### Step 3 — Open a Terminal and Navigate to Your File

A **terminal** (also called a command prompt or shell) is a text-based window for running programs.

**On macOS:** Open **Finder → Applications → Utilities → Terminal**, or press `⌘ Space`, type `Terminal`, and press Enter.

**On Windows:** Press the Windows key, type `PowerShell`, and press Enter to open Windows PowerShell.

Once the terminal is open, you need to navigate to the folder containing your CSV file and the two script files (`rcv.py` and `rcv_purge.py`). Use the `cd` command (short for "change directory"):

```
cd path/to/your/folder
```

**Example** — if your files are in a folder called `Elections` inside `Documents`:

- On macOS: `cd Documents/Elections`
- On Windows: `cd Documents\Elections`

Press Enter after typing the command. The terminal prompt will update to show your current location.

**Tip:** You can drag and drop a folder from Finder (macOS) or File Explorer (Windows) into the terminal window, and the path will be typed for you automatically.

---

### Step 4 — Run the Election

Once you are in the correct folder, run the election by typing the following and pressing Enter, replacing `MyBallots.csv` with the actual name of your file:

```
python3 rcv.py MyBallots.csv
```

The program will:
1. Load and validate all ballots, reporting how many are valid and how many have errors.
2. If any ballots have errors (e.g. duplicate ranks), display them one at a time and ask you to either correct them or exclude them — follow the on-screen prompts.
3. Run the ranked choice election and display the round-by-round results in the terminal.
4. Write three output files to the same folder as your CSV:
   - `MyBallots_cleaned.csv` — all valid ballots used in the count
   - `MyBallots_rounds.csv` — the full round-by-round vote tally
   - `MyBallots_excluded.csv` — any ballots that were excluded after review

---

### Step 5 — Re-running with Candidates Removed (if needed)

If one or more candidates need to be withdrawn or disqualified after ballots were collected, use `rcv_purge.py` instead of `rcv.py`. Their votes will automatically transfer to each voter's next-ranked remaining candidate.

**How to mark a candidate for removal:**

Open your CSV file in Excel and find the candidate's name in the first row. Add `X ` (the letter X followed by a single space) in front of their name. For example:

- `Bob` becomes `X Bob`
- `Carol` becomes `X Carol`

You can add a note after the name for your own records, e.g. `X Bob withdrawn`. Save the file as CSV again.

**We recommend using `MyBallots_cleaned.csv`** (produced by a previous run) as the starting point, since it already has all ballots validated and gaps compressed. Rename it if you wish to keep the original run's output intact.

Then run:

```
python3 rcv_purge.py MyBallots_cleaned.csv
```

Any column whose header is `X`, `x`, or begins with `X ` (case-insensitive) will be removed before the election is run.

---

### Reproducible Tie-Breaking

In rare cases where a tie cannot be broken from the ballot data alone, the program resolves it randomly. To ensure the same result if you need to re-run the election, add `--seed` followed by any whole number:

```
python3 rcv.py MyBallots.csv --seed 42
```

Record the seed you used alongside your results so the election can be independently verified.
