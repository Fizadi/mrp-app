# =====================================================================
# RAHKARAN EXCEL EXTRACTOR -> rahkaranTables.txt
# Reads D:\IT-2024-02-02\Rahkaran\RahkaranTables.xlsx
# Writes D:\projects\mrp\mrp-app\scripts\rahkaranTables.txt
# Style mirrors generate_db_info.py (SQL Server report)
# =====================================================================

import pandas as pd
from pathlib import Path
from datetime import datetime
import re

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
EXCEL_PATH = Path(r"D:\IT-2024-02-02\Rahkaran\RahkaranTables.xlsx")
OUTPUT_PATH = Path(r"D:\projects\mrp\mrp-app\scripts\rahkaranTables.txt")

SAMPLE_ROWS = 4
TOP_DISTINCT_VALUES = 5

# Sheets to skip by name pattern (case-insensitive)
SKIP_SHEET_PATTERNS = [
    r"^_",           # starts with underscore
    r"^sheet\d*$",   # Sheet, Sheet1, sheet2...
    r"^temp",        # Temp...
    r"^tmp",
    r"^copy of",
]

# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------
def should_skip_sheet(name: str) -> bool:
    n = name.strip().lower()
    for pat in SKIP_SHEET_PATTERNS:
        if re.search(pat, n):
            return True
    return False


def is_empty_sheet(df: pd.DataFrame) -> bool:
    """A sheet counts as empty if it has 0 rows OR every cell is NaN/NULL."""
    if df is None or df.shape[0] == 0 or df.shape[1] == 0:
        return True
    # drop fully-empty columns first
    d = df.dropna(how="all", axis=1)
    if d.shape[1] == 0:
        return True
    # if every remaining cell is NaN
    if d.dropna(how="all", axis=0).shape[0] == 0:
        return True
    return False


def looks_like_table(df: pd.DataFrame) -> bool:
    """
    Heuristic: a real table sheet has at least 2 non-null columns
    and headers that are strings (not numbers/dates).
    """
    if df.shape[1] < 2:
        return False
    non_null_cols = df.dropna(how="all", axis=1).shape[1]
    if non_null_cols < 2:
        return False
    # at least half of the header names should be strings
    str_headers = sum(1 for c in df.columns if isinstance(c, str) and c.strip() != "")
    if str_headers < max(2, df.shape[1] // 2):
        return False
    return True


def guess_type(series: pd.Series) -> str:
    """Infer a coarse data type from a pandas Series."""
    s = series.dropna()
    if s.empty:
        return "unknown"

    # bool
    if s.dtype == bool:
        return "bool"

    # numeric
    if pd.api.types.is_integer_dtype(s):
        return "int"
    if pd.api.types.is_float_dtype(s):
        return "float"

    # try datetime
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"

    # try parse as datetime on sample
    sample = s.astype(str).head(50)
    try:
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
        if parsed.notna().mean() >= 0.8:
            return "datetime"
    except Exception:
        pass

    # numeric strings?
    try:
        num = pd.to_numeric(sample, errors="coerce")
        if num.notna().mean() >= 0.8:
            return "numeric(text)"
    except Exception:
        pass

    return "text"


def profile_column(series: pd.Series):
    total = len(series)
    non_null = int(series.notna().sum())
    null = total - non_null
    distinct = int(series.dropna().astype(str).nunique())

    dtype = guess_type(series)

    top_vals = []
    if distinct > 0 and distinct <= 50:
        try:
            vc = series.dropna().astype(str).value_counts().head(TOP_DISTINCT_VALUES)
            top_vals = [f"{k} ({v})" for k, v in vc.items()]
        except Exception:
            top_vals = []

    min_v = max_v = None
    if dtype in ("int", "float", "numeric(text)"):
        try:
            num = pd.to_numeric(series, errors="coerce").dropna()
            if not num.empty:
                min_v, max_v = num.min(), num.max()
        except Exception:
            pass
    elif dtype == "datetime":
        try:
            dt = pd.to_datetime(series, errors="coerce").dropna()
            if not dt.empty:
                min_v, max_v = dt.min(), dt.max()
        except Exception:
            pass

    return {
        "total": total,
        "non_null": non_null,
        "null": null,
        "distinct": distinct,
        "dtype": dtype,
        "top": top_vals,
        "min": min_v,
        "max": max_v,
    }


def cell_to_str(v) -> str:
    if v is None:
        return "NULL"
    try:
        if pd.isna(v):
            return "NULL"
    except Exception:
        pass
    return str(v)


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------
def main():
    print("=" * 60)
    print("RAHKARAN EXCEL EXTRACTOR")
    print("=" * 60)
    print(f"Input : {EXCEL_PATH}")
    print(f"Output: {OUTPUT_PATH}")
    print()

    if not EXCEL_PATH.exists():
        print(f"ERROR: file not found -> {EXCEL_PATH}")
        return

    # Read all sheets at once (header=row1). Use openpyxl engine for .xlsx.
    print("Loading workbook (this may take a bit for 15 MB)...")
    try:
        xls = pd.read_excel(EXCEL_PATH, sheet_name=None, header=0,
                            engine="openpyxl", dtype=object)
    except Exception as e:
        print(f"ERROR reading workbook: {e}")
        return

    print(f"Sheets found: {len(xls)}")
    print()

    # classify sheets
    kept = []  # list of (name, df)
    skipped = []  # list of (name, reason)

    for name, df in xls.items():
        if should_skip_sheet(name):
            skipped.append((name, "name pattern"))
            continue
        if is_empty_sheet(df):
            skipped.append((name, "empty"))
            continue
        if not looks_like_table(df):
            skipped.append((name, "not a table"))
            continue
        # make column names unique & clean
        cols = []
        seen = {}
        for i, c in enumerate(df.columns):
            cname = str(c).strip() if c is not None else f"col{i+1}"
            if cname == "" or cname.lower() == "nan":
                cname = f"col{i+1}"
            if cname in seen:
                seen[cname] += 1
                cname = f"{cname}_{seen[cname]}"
            else:
                seen[cname] = 0
            cols.append(cname)
        df.columns = cols
        kept.append((name, df))

    kept.sort(key=lambda x: x[0].lower())
    skipped.sort(key=lambda x: x[0].lower())

    # -----------------------------------------------------------------
    # WRITE REPORT
    # -----------------------------------------------------------------
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:

        # HEADER
        f.write("=" * 100 + "\n")
        f.write("RAHKARAN EXCEL FULL REPORT\n")
        f.write("=" * 100 + "\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Source   : {EXCEL_PATH}\n")
        f.write(f"Total sheets in workbook: {len(xls)}\n")
        f.write(f"Table sheets kept       : {len(kept)}\n")
        f.write(f"Sheets skipped          : {len(skipped)}\n")
        f.write("=" * 100 + "\n\n")

        # TABLE DIGEST
        f.write("=" * 100 + "\n")
        f.write("TABLE DIGEST (A-Z)\n")
        f.write("=" * 100 + "\n\n")
        for i, (name, df) in enumerate(kept, 1):
            f.write(f"{i:>3}. {name:<50} rows: {len(df):>6}   cols: {df.shape[1]}\n")
        f.write("\n\n")

        # SKIPPED SHEETS
        if skipped:
            f.write("=" * 100 + "\n")
            f.write("SKIPPED SHEETS\n")
            f.write("=" * 100 + "\n\n")
            for name, reason in skipped:
                f.write(f"  - {name:<50} ({reason})\n")
            f.write("\n\n")

        # DETAILED SECTIONS
        for name, df in kept:
            f.write("=" * 100 + "\n")
            f.write(f"TABLE: {name}\n")
            f.write("=" * 100 + "\n")
            f.write(f"Total Rows: {len(df)}\n")
            f.write(f"Total Cols: {df.shape[1]}\n\n")

            # COLUMNS
            f.write("COLUMNS\n")
            f.write("-" * 100 + "\n")
            f.write(f"{'Name':<35} | {'Type':<14} | {'NonNull':>8} | {'Null':>6} | {'Distinct':>8}\n")
            f.write("-" * 100 + "\n")
            profiles = {}
            for col in df.columns:
                p = profile_column(df[col])
                profiles[col] = p
                f.write(
                    f"{col:<35} | {p['dtype']:<14} | {p['non_null']:>8} | "
                    f"{p['null']:>6} | {p['distinct']:>8}\n"
                )
            f.write("\n")

            # COLUMN PROFILE DETAILS (min/max/top values)
            f.write("COLUMN PROFILES\n")
            f.write("-" * 100 + "\n")
            for col in df.columns:
                p = profiles[col]
                bits = []
                if p["min"] is not None or p["max"] is not None:
                    bits.append(f"min={p['min']}  max={p['max']}")
                if p["top"]:
                    bits.append("top: " + " | ".join(p["top"]))
                if bits:
                    f.write(f"  {col}:\n    " + "\n    ".join(bits) + "\n")
            f.write("\n")

            # SAMPLE ROWS
            f.write(f"SAMPLE ROWS (First {SAMPLE_ROWS} records)\n")
            f.write("-" * 100 + "\n")
            sample = df.head(SAMPLE_ROWS)
            if len(sample) == 0:
                f.write("  (No sample data)\n")
            else:
                f.write(" | ".join(df.columns) + "\n")
                f.write("-" * 100 + "\n")
                for _, row in sample.iterrows():
                    f.write(" | ".join(cell_to_str(v) for v in row.values) + "\n")
            f.write("\n\n")

    print("=" * 60)
    print("EXPORT COMPLETED")
    print("=" * 60)
    print(f"Kept sheets   : {len(kept)}")
    print(f"Skipped sheets: {len(skipped)}")
    print(f"Report saved  : {OUTPUT_PATH}")
    print(f"File size     : {OUTPUT_PATH.stat().st_size / 1024:.2f} KB")


if __name__ == "__main__":
    main()