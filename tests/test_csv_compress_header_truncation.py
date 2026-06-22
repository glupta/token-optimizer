"""Regression test: _compress_csv must truncate the header to _CSV_MAX_COLS
columns, matching the truncation already applied to every data row.

Bug: `cols = header[:_CSV_MAX_COLS]` was computed but never used.
The header was output as `delimiter.join(header)` (all columns) while data
rows were correctly truncated to `row[:_CSV_MAX_COLS]`. For a wide CSV (>40
columns) this produced a header with more columns than the data rows, making
the output inconsistent and confusing.

Fix: change `delimiter.join(header)` → `delimiter.join(cols)` so the header
and data rows are aligned.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "token-optimizer"
    / "scripts"
    / "bash_compress.py"
)

spec = importlib.util.spec_from_file_location("bash_compress", _SCRIPT_PATH)
bash_compress = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bash_compress)

_CSV_MAX_COLS = bash_compress._CSV_MAX_COLS   # 40
_CSV_MIN_LINES = bash_compress._CSV_MIN_LINES  # 20


def _make_wide_csv(n_cols: int, n_rows: int) -> str:
    """Build a CSV with n_cols columns and n_rows data rows."""
    header = ",".join(f"col{i}" for i in range(n_cols))
    rows = [",".join(f"val{r}_{i}" for i in range(n_cols)) for r in range(n_rows)]
    return header + "\n" + "\n".join(rows)


class CsvCompressHeaderTruncationTest(unittest.TestCase):
    def test_header_col_count_matches_data_row_col_count_when_wide(self):
        """Wide CSV: header must be truncated to the same width as data rows.

        Pre-fix: header had n_cols columns, data rows had _CSV_MAX_COLS columns.
        Post-fix: both have _CSV_MAX_COLS columns.
        """
        n_cols = _CSV_MAX_COLS + 10  # 50 columns — well above the 40-col cap
        n_rows = _CSV_MIN_LINES + 5  # enough rows to trigger compression

        csv_input = _make_wide_csv(n_cols, n_rows)
        result = bash_compress._compress_csv(csv_input)

        # The function must have compressed (not returned raw).
        self.assertIn("more rows", result, "Expected compressed output with '... N more rows' marker")

        result_lines = result.split("\n")
        header_line = result_lines[0]
        data_line = result_lines[1]  # first preview row

        header_cols = len(header_line.split(","))
        data_cols = len(data_line.split(","))

        self.assertEqual(
            header_cols,
            data_cols,
            f"Header has {header_cols} cols but data row has {data_cols} cols — "
            f"header must be truncated to _CSV_MAX_COLS ({_CSV_MAX_COLS}), same as data rows.",
        )
        self.assertLessEqual(
            header_cols,
            _CSV_MAX_COLS,
            f"Header has {header_cols} cols, exceeding _CSV_MAX_COLS={_CSV_MAX_COLS}",
        )

    def test_narrow_csv_header_unchanged(self):
        """Narrow CSV (within cap): header must not be truncated."""
        n_cols = _CSV_MAX_COLS - 5  # 35 columns — under the cap
        n_rows = _CSV_MIN_LINES + 5

        csv_input = _make_wide_csv(n_cols, n_rows)
        result = bash_compress._compress_csv(csv_input)
        result_lines = result.split("\n")
        header_line = result_lines[0]

        header_cols = len(header_line.split(","))
        self.assertEqual(
            header_cols,
            n_cols,
            f"Narrow CSV: header should retain all {n_cols} cols, got {header_cols}",
        )

    def test_exact_cap_csv_header_unchanged(self):
        """CSV with exactly _CSV_MAX_COLS columns: header should show all columns."""
        n_cols = _CSV_MAX_COLS   # exactly 40
        n_rows = _CSV_MIN_LINES + 5

        csv_input = _make_wide_csv(n_cols, n_rows)
        result = bash_compress._compress_csv(csv_input)
        result_lines = result.split("\n")
        header_line = result_lines[0]

        header_cols = len(header_line.split(","))
        self.assertEqual(
            header_cols,
            n_cols,
            f"CSV with exactly {n_cols} cols: header should show all columns, got {header_cols}",
        )


if __name__ == "__main__":
    unittest.main()
