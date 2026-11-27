import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from cerebro.adapters.inbound.browser_api import browser_api
from cerebro.adapters.inbound.browser_history import install_browser_history
from cerebro.application.browser import BrowserBinding
from cerebro.application.history import MAX_DIFF_CHARS, ChangeUnavailable, HistoryRequest
from cerebro.application.ports.notes import NoteChange
from cerebro.bootstrap.browser import owner_credentials
from cerebro.bootstrap.container import build_container
from cerebro.domain.access import AccessCeiling, AccessContext
from cerebro.domain.commit_message import OwnerCommit
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Sensitivity
from cerebro.domain.note import NoteId
from tests.integration.content_kit import DOCKER_ID, VAULT_ID, make_sample_repo


@pytest.mark.parametrize("listener", ["private", "public"])
@pytest.mark.parametrize("ceiling", [Sensitivity.RESTRICTED, Sensitivity.INTERNAL])
def test_only_a_restricted_owner_session_can_list_and_read_an_encrypted_diff(
    tmp_path: Path, listener: str, ceiling: Sensitivity
) -> None:
    sample = make_sample_repo(tmp_path)
    container = build_container(sample.settings)
    before = container.notes.get(VAULT_ID)
    assert before is not None
    malicious = "<script>synthetic()</script>"
    changed = replace(before, body=f"{malicious}\n[[missing|unavailable metadata]]\n" + ("large sample\n" * 3000))
    revision = container.notes.apply(
        [NoteChange(note=changed, expected_hash=content_hash(before))],
        OwnerCommit.for_update(VAULT_ID),
    )
    owner_credentials(sample.settings).provision(b"history-fixture")
    origin = f"https://{listener}.example.invalid"
    binding = BrowserBinding(listener, origin)
    app = browser_api(container.browser_auth, binding, AccessCeiling(ceiling))
    install_browser_history(app, container.use_cases.history)
    with TestClient(app, base_url=origin) as client:
        endpoints = ("/api/changes", f"/api/changes/{revision}", f"/api/changes?cursor={revision}")
        for endpoint in endpoints:
            assert client.get(endpoint).status_code == 401
        assert (
            client.post(
                "/api/login", headers={"Origin": origin, "X-Cerebro-Request": "1"}, json={"password": "history-fixture"}
            ).status_code
            == 200
        )
        if ceiling is not Sensitivity.RESTRICTED:
            for endpoint in endpoints:
                assert client.get(endpoint, headers={"X-Ceiling": "restricted"}).status_code == 403
            return
        assert client.get(endpoints[2]).status_code == 404
        assert client.get("/api/changes", params={"cursor": "é"}).status_code == 404
        assert client.get("/api/changes?cursor=x&cursor=y").status_code == 422
        rows = client.get(endpoints[0]).json()["changes"]
        assert [row["revision"] for row in rows] == [revision]
        assert rows[0]["actor"] == "owner"
        detail = client.get(endpoints[1])
        assert detail.status_code == 200
        assert detail.headers["cache-control"] == "no-store"
        payload = detail.json()
        assert malicious in payload["diff"]
        assert "missing" not in payload["diff"]
        assert "unavailable metadata" not in payload["diff"]
        assert payload["truncated"]
        assert len(payload["diff"]) <= MAX_DIFF_CHARS
        assert payload["operation"] == "update"
        for invalid in ("HEAD", "HEAD~1", "--all", "f" * 40, revision[:12]):
            assert client.get("/api/changes", params={"cursor": invalid}).status_code == 404
            assert client.get(f"/api/changes/{invalid}").status_code == 404
        clean = container.notes.get(DOCKER_ID)
        assert clean
        readable = container.notes.apply(
            [
                NoteChange(
                    note=replace(clean, body=f"See [[{VAULT_ID.value}|alias]]."), expected_hash=content_hash(clean)
                )
            ],
            OwnerCommit.for_update(DOCKER_ID),
        )
        (sample.root / "notes" / f"{VAULT_ID.value}.md").write_bytes(b"invalid note")
        assert client.get(endpoints[1]).json() == {"error": "Change unavailable."}
        assert client.get(endpoints[1]).status_code == 404
        clean_detail = client.get(f"/api/changes/{readable}")
        assert clean_detail.status_code == 200
        assert VAULT_ID.value not in clean_detail.json()["diff"]
        assert [row["revision"] for row in client.get(endpoints[0]).json()["changes"]] == [readable]
        (sample.root / "notes" / f"{VAULT_ID.value}.md").unlink()
        assert client.get(endpoints[1]).status_code == 404
        assert [row["revision"] for row in client.get(endpoints[0]).json()["changes"]] == [readable]


def test_unattributed_commits_are_not_listed_and_corrupt_history_fails_closed(tmp_path: Path) -> None:
    sample = make_sample_repo(tmp_path)
    container = build_container(sample.settings)
    base = container.notes.get(VAULT_ID)
    assert base
    created = replace(base, id=NoteId("manual-record"))
    owner = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))
    history = container.use_cases.history
    # The owner never creates notes, and trailers of the retired Inbox are not an attribution (ADR-0023).
    added = container.notes.apply([NoteChange(note=created)], OwnerCommit(subject="Add note: manual-record"))
    path = f"notes/{created.id.value}.md"
    stored = (sample.root / path).read_bytes()
    git = sample.git()
    (sample.root / path).write_bytes(stored + b"Retired trailers.\n")
    retired = git.commit(
        "Update note: manual-record\n\nProposed-by: claude-code\nCurated-by: curator/null\nApproved-by: owner\n"
        "Proposal-Id: retired-0001",
        [path],
    )
    listed = {row.revision for row in history.list(owner).changes}
    assert added not in listed
    assert retired not in listed
    # A malformed historical parent is never treated as a newly created note, even when the latest file is readable.
    (sample.root / path).write_bytes(b"invalid format")
    git.commit("Manual repair in progress", [path])
    (sample.root / path).write_bytes(stored)
    repaired = git.commit(OwnerCommit.for_update(created.id).render(), [path])
    assert repaired not in {row.revision for row in history.list(owner).changes}
    with pytest.raises(ChangeUnavailable):
        history.detail(HistoryRequest(repaired), owner)


def test_history_pages_cross_both_limits_and_open_older_details(tmp_path: Path) -> None:
    sample = make_sample_repo(tmp_path)
    container = build_container(sample.settings)
    note = container.notes.get(DOCKER_ID)
    assert note
    revisions = []
    for i in range(22):
        changed = replace(note, body=f"Revision {i}")
        revisions.append(
            container.notes.apply(
                [NoteChange(note=changed, expected_hash=content_hash(note))], OwnerCommit.for_update(DOCKER_ID)
            )
        )
        note = changed
    for i in range(32):
        (sample.root / "manual").write_text(str(i))
        sample.git().stage(["manual"])
        sample.git().commit("Unattributed", ["manual"])
    history = container.use_cases.history
    owner = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))
    first = history.list(owner)
    assert not first.changes
    assert first.next_cursor
    second = history.list(owner, first.next_cursor)
    assert [row.revision for row in second.changes] == revisions[:-21:-1]
    assert second.next_cursor
    last = history.list(owner, second.next_cursor)
    assert [row.revision for row in last.changes] == revisions[1::-1]
    assert last.next_cursor is None
    assert history.detail(HistoryRequest(revisions[0], second.next_cursor), owner).change.revision == revisions[0]


def test_private_api_follows_an_opaque_cursor_after_an_empty_window(tmp_path: Path) -> None:
    sample = make_sample_repo(tmp_path)
    container = build_container(sample.settings)
    base = container.notes.get(DOCKER_ID)
    assert base
    revision = container.notes.apply(
        [NoteChange(note=replace(base, body="Earlier entry"), expected_hash=content_hash(base))],
        OwnerCommit.for_update(DOCKER_ID),
    )
    git = sample.git()
    history = container.use_cases.history
    owner = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))
    initial = history.list(owner).page_cursor
    for i in range(34):
        (sample.root / "manual").write_text(str(i))
        git.stage(["manual"])
        git.commit("Unattributed", ["manual"])
    assert history.detail(HistoryRequest(revision, initial), owner).change.revision == revision
    origin = "https://private.example.invalid"
    owner_credentials(sample.settings).provision(b"history-fixture")
    app = browser_api(container.browser_auth, BrowserBinding("private", origin), AccessCeiling(Sensitivity.RESTRICTED))
    install_browser_history(app, container.use_cases.history)
    with TestClient(app, base_url=origin) as client:
        client.post(
            "/api/login", headers={"Origin": origin, "X-Cerebro-Request": "1"}, json={"password": "history-fixture"}
        )
        first = client.get("/api/changes").json()
        assert first["changes"] == []
        assert revision not in first["next_cursor"]
        last = client.get("/api/changes", params={"cursor": first["next_cursor"]}).json()
        assert [row["revision"] for row in last["changes"]] == [revision]
        assert last["next_cursor"] is None
        assert client.get(f"/api/changes/{revision}").status_code == 404
        assert client.get(f"/api/changes/{revision}", params={"cursor": first["next_cursor"]}).status_code == 200
        subprocess.run(["git", "-C", str(sample.root), "reset", "--hard", revision], check=True, capture_output=True)
        assert client.get("/api/changes", params={"cursor": first["next_cursor"]}).status_code == 404


@pytest.mark.parametrize("listener", ["private", "public"])
@pytest.mark.parametrize("ceiling", [Sensitivity.RESTRICTED, Sensitivity.INTERNAL])
def test_a_note_history_requires_a_restricted_owner_and_lists_that_note_only(
    tmp_path: Path, listener: str, ceiling: Sensitivity
) -> None:
    sample = make_sample_repo(tmp_path)
    container = build_container(sample.settings)
    revisions = {}
    for note_id in (VAULT_ID, DOCKER_ID):
        note = container.notes.get(note_id)
        assert note
        revisions[note_id] = container.notes.apply(
            [NoteChange(note=replace(note, body="Changed."), expected_hash=content_hash(note))],
            OwnerCommit.for_update(note_id),
        )
    owner_credentials(sample.settings).provision(b"history-fixture")
    origin = f"https://{listener}.example.invalid"
    app = browser_api(container.browser_auth, BrowserBinding(listener, origin), AccessCeiling(ceiling))
    install_browser_history(app, container.use_cases.history)
    path = f"/api/notes/{VAULT_ID.value}/changes"
    with TestClient(app, base_url=origin) as client:
        assert client.get(path).status_code == 401
        login = client.post(
            "/api/login", headers={"Origin": origin, "X-Cerebro-Request": "1"}, json={"password": "history-fixture"}
        )
        assert login.status_code == 200
        response = client.get(path)
        if ceiling is not Sensitivity.RESTRICTED:
            assert response.status_code == 403
            assert revisions[VAULT_ID] not in response.text
            return
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert [row["revision"] for row in body["changes"]] == [revisions[VAULT_ID]]
        assert body["next_cursor"] is None
        # The page cursor opens the diff of a listed change, like the global history's.
        detail = client.get(f"/api/changes/{revisions[VAULT_ID]}", params={"cursor": body["page_cursor"]})
        assert detail.status_code == 200
        assert client.get("/api/notes/absent-note/changes").json()["changes"] == []
        assert client.get("/api/notes/Not_A_Slug/changes").status_code == 422
        assert client.get(path, params={"cursor": "forged"}).status_code == 404
        assert client.get(path + "?cursor=a&cursor=b").status_code == 422
        assert client.get(path, params={"history": "true"}).status_code == 422
        assert client.post(path).status_code == 403
        assert client.head(path + "%0A").status_code == 422
        # A cursor carries only a position: authorization applies again wherever it is used.
        other = client.get(f"/api/notes/{DOCKER_ID.value}/changes", params={"cursor": body["page_cursor"]})
        assert [row["revision"] for row in other.json()["changes"]] == [revisions[DOCKER_ID]]


@pytest.mark.parametrize("listener", ["private", "public"])
def test_a_trailing_line_break_cannot_skip_the_restricted_history_rule(tmp_path: Path, listener: str) -> None:
    sample = make_sample_repo(tmp_path)
    container = build_container(sample.settings)
    note = container.notes.get(DOCKER_ID)
    assert note
    revision = container.notes.apply(
        [NoteChange(note=replace(note, body="Changed."), expected_hash=content_hash(note))],
        OwnerCommit.for_update(DOCKER_ID),
    )
    owner_credentials(sample.settings).provision(b"history-fixture")
    origin = f"https://{listener}.example.invalid"
    app = browser_api(container.browser_auth, BrowserBinding(listener, origin), AccessCeiling(Sensitivity.INTERNAL))
    install_browser_history(app, container.use_cases.history)
    with TestClient(app, base_url=origin) as client:
        login = client.post(
            "/api/login", headers={"Origin": origin, "X-Cerebro-Request": "1"}, json={"password": "history-fixture"}
        )
        assert login.status_code == 200
        # The router's end anchor also matches before a final line break, which an exact path comparison misses.
        for path in (
            "/api/changes%0A",
            "/api/changes%0A?cursor=x&history=true",
            f"/api/changes/{revision}%0A",
            "/api/session%0A",
            "/api/changes%0D",
            "/api/changes%00",
            "/api/changes%7F",
            f"/api/notes/{DOCKER_ID.value}/changes%0A",
        ):
            response = client.get(path)
            assert response.status_code == 422, path
            assert revision not in response.text
            assert response.headers["cache-control"] == "no-store"
