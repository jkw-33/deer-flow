from api.ci_verification import candidate, verify

SHA = "a" * 40
LOG = "./scripts/check.sh: line 4: syntax error: unexpected end of file"


def test_candidate_requires_unambiguous_checkout_and_diagnostic():
    assert candidate(LOG, (SHA,)) == (SHA, "scripts/check.sh", 4)
    assert candidate(LOG, ()) is None
    assert candidate(LOG, (SHA, "b" * 40)) is None
    assert candidate(LOG + "\n" + LOG.replace("check", "other"), (SHA,)) is None
    assert candidate(LOG.replace("./scripts/", "../"), (SHA,)) is None


def test_verify_preserves_bytes_and_checks_both_runs():
    source = b"if true; then\r\n echo ok\r\nfi\r\n"
    calls = []

    def check(data):
        calls.append(data)
        return (2, 4) if data == source else (0, None)

    result = verify(source, "scripts/check.sh", 4, check)
    assert result["status"] == "verified"
    assert calls == [source, source.replace(b"\r\n", b"\n")]
    assert result["patch"].count("\r\n") == 3
    assert verify(source, "scripts/check.sh", 5, check)["status"] == "unconfirmed"
    assert verify(source, "scripts/check.sh", 4, lambda _: (0, None))["status"] == "unconfirmed"
    assert verify(source, "scripts/check.sh", 4, lambda _: (2, 4))["status"] == "unconfirmed"


def test_node_target_requires_cr_path_and_unambiguous_script():
    from api.ci_verification import node_target

    log = "2026-01-01T00:00:00Z ##[group]Run bash ./scripts/check.sh\n2026-01-01T00:00:01Z Error: Cannot find module '/probe/service.mjs\r'\n2026-01-01T00:00:01Z   code: 'MODULE_NOT_FOUND',\n"
    assert node_target(log, (SHA,)) == (SHA, "scripts/check.sh", "service.mjs")
    assert node_target(log.replace("\r", "\n2026-01-01T00:00:01.123Z "), (SHA,)) == (SHA, "scripts/check.sh", "service.mjs")
    assert node_target(log.replace("\r", ""), (SHA,)) is None
    assert node_target(log + log.replace("check.sh", "other.sh"), (SHA,)) is None


def test_node_verification_requires_matching_lookup_and_success():
    original = b"node /probe/service.mjs\r\n"

    def check(source):
        return (1, "service.mjs") if source == original else (0, None)

    result = verify(original, "check.sh", "service.mjs", check, mode="node_lookup")
    assert result["status"] == "verified"
    assert verify(original, "check.sh", "other.mjs", check, mode="node_lookup")["status"] == "unconfirmed"


def test_node_probe_rejects_unapproved_shell_syntax_before_docker(monkeypatch):
    import pytest

    from api import ci_verification

    monkeypatch.setattr(ci_verification, "_container_check", lambda *_: pytest.fail("Unexpected Docker invocation"))
    with pytest.raises(ValueError, match="Unsupported"):
        ci_verification.node_check("fixture", b"node ./service.mjs; touch /tmp/unexpected\r\n", "service.mjs")
