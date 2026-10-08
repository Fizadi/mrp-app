"""
audit_numeric_casts.py

Scans app/modules/project/api.py for:
  - SUM(...) calls without CAST
  - ISNULL(SUM(...), ...) on nvarchar columns
  - Python comparisons on raw SQL row values (heuristic)
  
Reports likely problem lines.
"""

import re
from pathlib import Path

API_FILE = Path("app/modules/project/api.py")

def audit():
    content = API_FILE.read_text(encoding="utf-8")
    lines = content.splitlines()

    print("=" * 70)
    print("  NUMERIC CAST AUDIT — app/modules/project/api.py")
    print("=" * 70)

    # Pattern 1: SUM(...) without CAST inside
    sum_without_cast = re.compile(r'\bSUM\s*\(\s*(?!CAST)[A-Za-z_]', re.IGNORECASE)

    # Pattern 2: ISNULL(SUM(...)) without CAST inside
    isnull_sum_no_cast = re.compile(
        r'\bISNULL\s*\(\s*SUM\s*\(\s*(?!CAST)[A-Za-z_]',
        re.IGNORECASE
    )

    # Pattern 3: numeric comparison in Python: r[digit] > number  or  row[digit] > number
    py_numeric_cmp = re.compile(r'\b(r|row)\[\d+\]\s*[<>]=?\s*\d+')

    issues = []

    for i, line in enumerate(lines, start=1):
        if sum_without_cast.search(line) and 'CAST' not in line:
            issues.append((i, 'SUM without CAST', line.strip()))
        if isnull_sum_no_cast.search(line) and 'CAST' not in line:
            issues.append((i, 'ISNULL(SUM) without CAST', line.strip()))
        if py_numeric_cmp.search(line):
            issues.append((i, 'Python numeric comparison on raw row value', line.strip()))

    if not issues:
        print("\n✅ No issues found.")
        return

    print(f"\n⚠️  Found {len(issues)} potential issue(s):\n")
    for lineno, category, snippet in issues:
        print(f"  Line {lineno:5d}  [{category}]")
        print(f"              {snippet[:100]}")
        print()

if __name__ == '__main__':
    audit()