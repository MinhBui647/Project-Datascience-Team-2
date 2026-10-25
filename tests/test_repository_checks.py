"""Release checks must reject broken source contracts before CI can pass."""
import ast
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.verification import check_repository as checks


class RepositoryCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "docs").mkdir()
        (self.root / "README.md").write_text("# Test\n", encoding="utf-8")

    def workflow(self, text):
        directory = self.root / ".github/workflows"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "ci.yml").write_text(text, encoding="utf-8")

    def test_markdown_resolves_encoded_source_path_and_ignores_fenced_examples(self):
        (self.root / "docs/My Doc.md").write_text("# Document\n", encoding="utf-8")
        (self.root / "README.md").write_text(
            "[Doc](docs/My%20Doc.md) [Site](https://example.com/missing) [Anchor](#a)\n"
            "```markdown\n[Example](not-an-actual-file.md)\n```\n", encoding="utf-8")
        self.assertEqual(checks.check_markdown(self.root), 2)

    def test_missing_navigation_link_is_rejected(self):
        (self.root / "README.md").write_text("[Missing](docs/missing.md)\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing link target"):
            checks.check_markdown(self.root)

    def test_link_to_existing_ignored_receipt_is_rejected_for_source_release(self):
        runtime = self.root / "reports/receipt.json"
        runtime.parent.mkdir()
        runtime.write_text("{}", encoding="utf-8")
        (self.root / "README.md").write_text("[Receipt](reports/receipt.json)", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "runtime artifact"):
            checks.check_markdown(self.root)

    def test_navigation_cannot_escape_repository(self):
        (self.root / "README.md").write_text("[Outside](../outside.md)", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "escapes repository"):
            checks.check_markdown(self.root)

    def test_yaml_on_key_is_preserved_and_duplicate_job_keys_fail(self):
        self.workflow("on:\n  push:\njobs:\n  tests:\n    runs-on: ubuntu-latest\n    steps: []\n")
        self.assertEqual(checks.check_workflows(self.root), 1)
        self.workflow("on:\n  push:\njobs:\n  tests: {}\n  tests: {}\n")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            checks.check_workflows(self.root)

    def test_external_action_tags_fail_but_full_commit_pins_pass(self):
        for reference in ("v7", "a" * 40):
            self.workflow("on:\n  push:\njobs:\n  tests:\n    steps:\n      - uses: actions/checkout@" + reference + "\n")
            if reference == "v7":
                with self.assertRaisesRegex(ValueError, "full commit SHA"):
                    checks.check_workflows(self.root)
            else:
                self.assertEqual(checks.check_workflows(self.root), 1)

    def test_real_m4_source_corruption_fails_without_updating_any_pin(self):
        self.assertEqual(checks.check_pins(checks.ROOT), 6)
        relative = "scripts/orchestration/guarded_preparation_executor.py"
        guard = checks.ROOT / relative
        destination = self.root / relative
        destination.parent.mkdir(parents=True)
        shutil.copyfile(guard, destination)
        tree = ast.parse(guard.read_text(encoding="utf-8-sig"))
        value = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "M4_PINNED_SHA256" for target in node.targets))
        files = [name for stage in ast.literal_eval(value).values() for name, _ in stage]
        for name in files:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(checks.ROOT / name, target)
        self.assertEqual(checks.check_pins(self.root), 6)
        with (self.root / files[0]).open("ab") as handle:
            handle.write(b"\n# accidental format edit\n")
        with self.assertRaisesRegex(ValueError, "bytes changed"):
            checks.check_pins(self.root)

    def test_invalid_python_and_nonobject_config_are_rejected(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        (scripts / "invalid.py").write_text("def broken(\n", encoding="utf-8")
        with self.assertRaises(SyntaxError):
            checks.check_python(self.root)
        config = self.root / "config"
        config.mkdir()
        (config / "bad.json").write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "JSON object"):
            checks.check_config(self.root)


if __name__ == "__main__":
    unittest.main()
