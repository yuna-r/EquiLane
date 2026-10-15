from __future__ import annotations

from contextlib import chdir
import importlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.errors import EquiLaneError, ValidationError
from equilane.project import load_project


class BrandingCompatibilityTests(unittest.TestCase):
    def _write_project(self, path: Path, table: str, *, suite: str = "cases") -> None:
        path.write_text(
            f'[{table}]\nschema_version = 1\nsuite = "{suite}"\n'
            'isa_registry = "isa.json"\n',
            encoding="utf-8",
        )

    def test_new_project_file_resolves_paths_and_prefers_equilane(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, chdir(temporary):
            self._write_project(Path("equilane.toml"), "equilane", suite="new")
            self._write_project(Path("ioitf.toml"), "ioitf", suite="old")
            project = load_project()
            self.assertEqual(project.path, Path("equilane.toml"))
            self.assertEqual(project.suite, Path("new"))

    def test_legacy_default_project_still_loads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, chdir(temporary):
            self._write_project(Path("ioitf.toml"), "ioitf")
            self.assertEqual(load_project().suite, Path("cases"))

    def test_explicit_missing_project_and_invalid_preferred_file_do_not_fall_back(self):
        with tempfile.TemporaryDirectory() as temporary, chdir(temporary):
            self._write_project(Path("ioitf.toml"), "ioitf")
            with self.assertRaises(ValidationError):
                load_project("missing.toml")
            Path("equilane.toml").write_text("invalid TOML", encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_project()

    def test_mixed_project_tables_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "equilane.toml"
            self._write_project(path, "equilane")
            with path.open("a", encoding="utf-8") as stream:
                stream.write("[ioitf]\n")
            with self.assertRaises(ValidationError):
                load_project(path)

    def test_legacy_imports_share_modules_exceptions_and_model_cache(self) -> None:
        for name in ("errors", "development", "casepack_families", "cli", "quality_runner"):
            with self.subTest(module=name):
                self.assertIs(
                    importlib.import_module(f"ioitf.{name}"),
                    importlib.import_module(f"equilane.{name}"),
                )
        from ioitf.errors import IOITFError

        self.assertIs(IOITFError, EquiLaneError)
        self.assertTrue(issubclass(ValidationError, IOITFError))

    def test_new_and_legacy_module_commands_show_the_same_help(self) -> None:
        results = []
        for name in ("equilane", "ioitf"):
            completed = subprocess.run(
                [sys.executable, "-m", name, "--help"],
                cwd=PROJECT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("usage: equilane", completed.stdout)
            self.assertIn("EquiLane", completed.stdout)
            results.append(completed.stdout)
        self.assertEqual(*results)


if __name__ == "__main__":
    unittest.main()
