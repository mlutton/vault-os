from pathlib import Path

import docs_consistency

REGISTRY_TEXT = """\
README.md	tests	The API has (?P<count>\\d+) tests
api/README.md	tests	The API has (?P<count>\\d+) tests
api/README.md	files	(?P<count>\\d+) test files
"""


def fixture_tree(
    tmp_path: Path, root_count: int = 3, api_count: int = 3, file_count: int = 2
) -> Path:
    (tmp_path / "api" / "tests").mkdir(parents=True)
    (tmp_path / "scripts" / "preflight").mkdir(parents=True)
    (tmp_path / "README.md").write_text(f"The API has {root_count} tests.\n")
    (tmp_path / "api" / "README.md").write_text(
        f"The API has {api_count} tests in {file_count} test files.\n"
    )
    (tmp_path / "scripts" / "preflight" / "docs-consistency-documents.txt").write_text(
        REGISTRY_TEXT,
        encoding="utf-8",
    )
    (tmp_path / "api" / "tests" / "test_one.py").write_text("")
    (tmp_path / "api" / "tests" / "test_two.py").write_text("")
    return tmp_path


def test_matching_fixture_readmes_pass(tmp_path: Path):
    root = fixture_tree(tmp_path)
    assert docs_consistency.check(root, expected_tests=3, expected_files=2) == []


def test_stale_test_count_fails_with_expected_value(tmp_path: Path):
    root = fixture_tree(tmp_path, api_count=2)
    assert docs_consistency.check(root, expected_tests=3, expected_files=2) == [
        "api/README.md: states 2 tests; expected 3"
    ]


def test_stale_test_file_count_fails_with_expected_value(tmp_path: Path):
    root = fixture_tree(tmp_path, file_count=1)
    assert docs_consistency.real_test_file_count(root) == 2
    assert docs_consistency.check(root, expected_tests=3, expected_files=2) == [
        "api/README.md: states 1 test files; expected 2"
    ]


def test_registered_architecture_test_count_is_checked(tmp_path: Path):
    root = fixture_tree(tmp_path)
    (root / "docs" / "architecture").mkdir(parents=True)
    (root / "docs" / "architecture" / "vaultos-system.architecture.json").write_text(
        '{"nodes":[{"description":"Built: the spine - 2 API tests"}]}\n',
        encoding="utf-8",
    )
    (root / "scripts" / "preflight" / "docs-consistency-documents.txt").write_text(
        REGISTRY_TEXT
        + "docs/architecture/vaultos-system.architecture.json\ttests\t(?P<count>\\d+) API tests\n",
        encoding="utf-8",
    )

    assert docs_consistency.check(root, expected_tests=3, expected_files=2) == [
        "docs/architecture/vaultos-system.architecture.json: states 2 tests; expected 3"
    ]


def test_registered_document_ignores_prose_that_only_resembles_a_count(tmp_path: Path):
    root = fixture_tree(tmp_path)
    (root / "README.md").write_text(
        "A stale document once had 90 tests for a week.\nThe API has 3 tests.\n",
        encoding="utf-8",
    )

    assert docs_consistency.check(root, expected_tests=3, expected_files=2) == []
