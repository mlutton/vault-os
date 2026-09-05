from pathlib import Path

import docs_consistency


def fixture_tree(
    tmp_path: Path, root_count: int = 3, api_count: int = 3, file_count: int = 2
) -> Path:
    (tmp_path / "api" / "tests").mkdir(parents=True)
    (tmp_path / "README.md").write_text(f"The API has {root_count} tests.\n")
    (tmp_path / "api" / "README.md").write_text(
        f"The API has {api_count} tests in {file_count} test files.\n"
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
