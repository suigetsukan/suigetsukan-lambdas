"""Tests for scripts/validate_mdc.py."""

from pathlib import Path

import pytest

# Import from parent (add project root to path)
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.validate_mdc import extract_frontmatter, validate_mdc


def test_extract_frontmatter_valid():
    content = "---\ndescription: foo\nalwaysApply: true\n---\n\n# Body"
    fm, body = extract_frontmatter(content)
    assert fm is not None
    assert "description: foo" in fm
    assert "# Body" in body


def test_extract_frontmatter_empty_body():
    content = "---\ndescription: foo\n---\n"
    fm, body = extract_frontmatter(content)
    assert fm is not None
    assert body == ""


def test_extract_frontmatter_no_closing():
    content = "---\ndescription: foo\n\n# No closing ---"
    fm, body = extract_frontmatter(content)
    assert fm is None
    assert body == content


def test_validate_mdc_valid(tmp_path):
    (tmp_path / "rules").mkdir()
    mdc = tmp_path / "rules" / "test.mdc"
    mdc.write_text("---\ndescription: A rule\nalwaysApply: true\n---\n\n# Content")
    errs = validate_mdc(mdc)
    assert errs == []


def test_validate_mdc_missing_description(tmp_path):
    (tmp_path / "rules").mkdir()
    mdc = tmp_path / "rules" / "test.mdc"
    mdc.write_text("---\nalwaysApply: true\n---\n\n# Content")
    errs = validate_mdc(mdc)
    assert any("description" in e for e in errs)


def test_validate_mdc_empty_description(tmp_path):
    (tmp_path / "rules").mkdir()
    mdc = tmp_path / "rules" / "test.mdc"
    mdc.write_text("---\ndescription: \nalwaysApply: true\n---\n\n# Content")
    errs = validate_mdc(mdc)
    assert any("non-empty" in e for e in errs)


def test_validate_mdc_no_always_apply_or_globs(tmp_path):
    (tmp_path / "rules").mkdir()
    mdc = tmp_path / "rules" / "test.mdc"
    mdc.write_text("---\ndescription: A rule\n---\n\n# Content")
    errs = validate_mdc(mdc)
    assert any("alwaysApply" in e or "globs" in e for e in errs)


def test_validate_mdc_invalid_yaml(tmp_path):
    (tmp_path / "rules").mkdir()
    mdc = tmp_path / "rules" / "test.mdc"
    mdc.write_text("---\ndescription: foo\n  bad: indent\n---\n\n# Content")
    errs = validate_mdc(mdc)
    assert len(errs) > 0


def test_validate_mdc_globs_only(tmp_path):
    """Globs without alwaysApply is valid."""
    (tmp_path / "rules").mkdir()
    mdc = tmp_path / "rules" / "test.mdc"
    mdc.write_text("---\ndescription: Python rule\nglobs: \"**/*.py\"\n---\n\n# Content")
    errs = validate_mdc(mdc)
    assert errs == []


def test_validate_mdc_empty_globs(tmp_path):
    """Empty globs when present is invalid."""
    (tmp_path / "rules").mkdir()
    mdc = tmp_path / "rules" / "test.mdc"
    mdc.write_text("---\ndescription: A rule\nglobs: \n---\n\n# Content")
    errs = validate_mdc(mdc)
    assert any("globs" in e and "non-empty" in e and "currently empty" in e for e in errs)


def test_validate_mdc_globs_as_array(tmp_path):
    """Globs as YAML array is valid (Cursor supports both string and list)."""
    (tmp_path / "rules").mkdir()
    mdc = tmp_path / "rules" / "test.mdc"
    mdc.write_text("---\ndescription: TS rule\nglobs:\n  - \"**/*.ts\"\n  - \"**/*.tsx\"\n---\n\n# Content")
    errs = validate_mdc(mdc)
    assert errs == []


def test_validate_mdc_globs_empty_array(tmp_path):
    """Globs as empty array is invalid."""
    (tmp_path / "rules").mkdir()
    mdc = tmp_path / "rules" / "test.mdc"
    mdc.write_text("---\ndescription: A rule\nglobs: []\n---\n\n# Content")
    errs = validate_mdc(mdc)
    assert any("globs" in e and "currently empty" in e for e in errs)


def test_main_validates_custom_rules_dir(tmp_path):
    """main() validates rules from --rules-dir when provided."""
    rules_dir = tmp_path / "custom_rules"
    rules_dir.mkdir()
    (rules_dir / "valid.mdc").write_text(
        "---\ndescription: Custom rule\nalwaysApply: true\n---\n\n# OK"
    )
    from scripts.validate_mdc import main
    import sys
    orig_argv = sys.argv[:]
    try:
        sys.argv = ["validate_mdc.py", "--rules-dir", str(rules_dir)]
        result = main()
        assert result == 0
    finally:
        sys.argv = orig_argv
