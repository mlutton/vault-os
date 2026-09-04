from vaultos.api.jobs import deliverable_link


def test_deliverable_link_none_when_path_missing():
    assert deliverable_link(None, None) is None
    assert deliverable_link(None, "") is None


def test_deliverable_link_reads_frontmatter_link_field(tmp_path):
    vault_root = tmp_path
    inbox = vault_root / "inbox"
    inbox.mkdir()
    (inbox / "x.md").write_text('---\ndate: 2026-08-09\nlink: "https://example.com/draft"\n---\nbody\n')
    assert deliverable_link(vault_root, "inbox/x.md") == "https://example.com/draft"


def test_deliverable_link_none_when_no_link_field(tmp_path):
    vault_root = tmp_path
    inbox = vault_root / "inbox"
    inbox.mkdir()
    (inbox / "x.md").write_text("---\ndate: 2026-08-09\n---\nbody\n")
    assert deliverable_link(vault_root, "inbox/x.md") is None


def test_deliverable_link_none_when_prefix_not_allowlisted(tmp_path):
    vault_root = tmp_path
    (vault_root / "secrets").mkdir()
    (vault_root / "secrets" / "x.md").write_text('---\nlink: "https://evil.example.com"\n---\n')
    assert deliverable_link(vault_root, "secrets/x.md") is None


def test_deliverable_link_rejects_path_traversal(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text('---\nlink: "https://evil.example.com"\n---\n')
    assert deliverable_link(vault_root, "inbox/../../outside.md") is None


def test_deliverable_link_none_not_raise_on_non_utf8_content(tmp_path):
    # Deliverables ingest scraped web/email content -- a non-UTF-8 byte
    # anywhere in the file must degrade to None, not 500 the whole endpoint.
    vault_root = tmp_path
    inbox = vault_root / "inbox"
    inbox.mkdir()
    (inbox / "x.md").write_bytes(b"---\nlink: \xff\xfe not valid utf-8\n---\n")
    assert deliverable_link(vault_root, "inbox/x.md") is None


def test_deliverable_link_finds_link_field_past_800_bytes(tmp_path):
    # A long voice-ask `prompt:` field can push `link:` well past the old
    # 800-byte cutoff -- e.g. a lengthy transcribed utterance in the frontmatter.
    vault_root = tmp_path
    inbox = vault_root / "inbox"
    inbox.mkdir()
    long_prompt = "x" * 1000
    content = f'---\ndate: 2026-08-09\nskill: voice-ask\nprompt: "{long_prompt}"\nlink: "https://example.com/draft"\n---\nbody\n'
    (inbox / "x.md").write_text(content)
    assert deliverable_link(vault_root, "inbox/x.md") == "https://example.com/draft"
