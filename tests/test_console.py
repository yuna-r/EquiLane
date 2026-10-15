from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import unittest


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.console import CheckProgress  # noqa: E402


class CheckProgressTests(unittest.TestCase):
    def test_piped_progress_preserves_counts_and_throttles_without_stdout_output(self) -> None:
        stderr = io.StringIO()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            progress = CheckProgress(enabled=True, stream=stderr)
            with progress.stage(2, "Generate test vectors", total=100) as update:
                for current in range(1, 101):
                    update(current)
            disabled = CheckProgress(enabled=False, stream=stderr)
            before_quiet = stderr.getvalue()
            with disabled.stage(3, "Quiet fixture", total=100) as update:
                update(50)
                update(100)
        self.assertEqual(stderr.getvalue(), before_quiet)
        self.assertEqual(stdout.getvalue(), "")
        lines = stderr.getvalue().splitlines()
        self.assertEqual(lines[0], "[2/7] Generate test vectors...")
        self.assertTrue(lines[-1].startswith("[2/7] Generate test vectors: done ("))
        bars = [line for line in lines if "%" in line]
        self.assertEqual(len(bars), 11)
        self.assertIn("  0% 0/100", bars[0])
        self.assertIn(" 10% 10/100", bars[1])
        self.assertIn("[======================] 100% 100/100", bars[-1])
        self.assertNotIn("\r", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
