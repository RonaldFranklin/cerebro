import hashlib
from dataclasses import fields, replace
from datetime import date, timedelta
from typing import Any

import pytest

from cerebro.application.dto import (
    AuthenticateTokenRequest,
    HealthCode,
    HealthReportRequest,
    HealthReportResult,
    IssueAgentTokenRequest,
    ListAgentTokensRequest,
    RebuildIndexRequest,
    RevokeAgentTokenRequest,
)
from cerebro.application.errors import (
    AlreadyExists,
    AuthenticationFailed,
    NoteReadError,
    PermissionDenied,
    TokenNotFound,
)
from cerebro.application.index_state import advance_revision, index_is_stale
from cerebro.application.ports.notes import FileProblem, NoteChange
from cerebro.application.ports.search import SearchQuery
from cerebro.application.use_cases import (
    AuthenticateToken,
    HealthReport,
    IssueAgentToken,
    ListAgentTokens,
    RebuildIndex,
    RevokeAgentToken,
)
from cerebro.application.use_cases.health_report import _finding_issue
from cerebro.domain.access import AccessCeiling, Scope
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.report import Finding, Severity, ViolationCode
from cerebro.domain.secrets import secret_message
from cerebro.domain.vocabulary import CORE, HUB
from tests.fakes.stores import InMemoryTokenStore
from tests.fakes.system import FixedClock, SequentialIdGenerator
from tests.unit.application.world import MESSAGE, NID, World, ctx
from tests.unit.application.write_kit import SECRET
from tests.unit.domain.builders import VOCABULARY, make_note

PUB, INT, RES = Sensitivity.PUBLIC, Sensitivity.INTERNAL, Sensitivity.RESTRICTED
READ = frozenset({Scope.READ})


def owner(level: Sensitivity = RES) -> Any:
    return ctx(level, owner=True)


def agent(level: Sensitivity = RES) -> Any:
    return ctx(level, frozenset(Scope))


# -- RebuildIndex ---------------------------------------------------------------------------------------------------


class TestRebuildIndex:
    def make(self, world: World) -> RebuildIndex:
        return RebuildIndex(world.notes, world.search, world.graph)

    def test_rebuilds_both_indexes_from_the_repository(self) -> None:
        w = World()
        w.add(id=NID("infrastructure"), type=HUB, priority=Priority.P1, hub=None)
        w.add(id=NID("ssh"), hub=NID("infrastructure"), sensitivity=RES)
        w.search.rebuild([])  # an index update failed after the commit
        w.graph.rebuild([])
        assert w.graph.graph(ceiling=AccessCeiling(RES)).nodes == ()

        result = self.make(w).execute(RebuildIndexRequest(), owner(PUB))  # even from a low-ceiling listener

        assert result.notes_indexed == 2
        assert {r.id.value for r in w.graph.graph(ceiling=AccessCeiling(RES)).nodes} == {"infrastructure", "ssh"}
        assert len(w.search.search(SearchQuery(ceiling=AccessCeiling(RES)))) == 2

    def test_removes_entries_of_notes_that_no_longer_exist_and_is_idempotent(self) -> None:
        w = World()
        w.add(id=NID("kept"), hub=None)
        ghost = World()
        ghost.add(id=NID("ghost"), hub=None)
        for note in ghost.notes.list_notes():
            w.search.upsert(note)
        use_case = self.make(w)
        use_case.execute(RebuildIndexRequest(), owner())
        use_case.execute(RebuildIndexRequest(), owner())
        hits = w.search.search(SearchQuery(ceiling=AccessCeiling(RES)))
        assert [h.id.value for h in hits] == ["kept"]

    def test_only_the_owner(self) -> None:
        w = World()
        with pytest.raises(PermissionDenied):
            self.make(w).execute(RebuildIndexRequest(), agent())

    def test_a_stable_problem_file_is_left_out_and_the_index_is_fresh(self) -> None:
        w = World()
        w.add(id=NID("kept"), hub=None)
        w.notes.problems.append(FileProblem("notes/broken.md", "invalid frontmatter", stable=True))
        w.search.set_revision("rev-old")
        result = self.make(w).execute(RebuildIndexRequest(), owner())
        assert (result.notes_indexed, result.files_excluded) == (1, 1)
        assert not index_is_stale(w.notes, w.search, w.graph)  # the next read does not rebuild again

    def test_an_unstable_problem_fails_and_never_marks_the_index_fresh(self) -> None:
        w = World()
        w.add(id=NID("kept"), hub=None)
        w.notes.problems += [
            FileProblem("notes/broken.md", "invalid frontmatter", stable=True),
            FileProblem("notes/edited.md", "differs from the last commit", stable=False),
        ]
        w.search.set_revision("rev-old")
        with pytest.raises(NoteReadError) as info:
            self.make(w).execute(RebuildIndexRequest(), owner())
        assert info.value.problems == (("notes/edited.md", "differs from the last commit"),)
        assert w.search.built_from() == "rev-old"
        assert index_is_stale(w.notes, w.search, w.graph)


# -- tokens ---------------------------------------------------------------------------------------------------------


class TokenKit:
    def __init__(self) -> None:
        self.store = InMemoryTokenStore()
        self.clock = FixedClock()
        self.ids = SequentialIdGenerator()
        self.issue_uc = IssueAgentToken(self.store, self.clock, self.ids)
        self.revoke_uc = RevokeAgentToken(self.store, self.clock)
        self.auth_uc = AuthenticateToken(self.store, self.clock)

    def issue(self, c: Any = None, **overrides: Any) -> Any:
        fields: dict[str, Any] = {"agent": "claude-code@workstation", "ceiling": AccessCeiling(INT), "scopes": READ}
        fields.update(overrides)
        return self.issue_uc.execute(IssueAgentTokenRequest(**fields), c or owner())

    def authenticate(self, secret: str, listener: Sensitivity = RES) -> Any:
        return self.auth_uc.execute(AuthenticateTokenRequest(secret=secret, listener=AccessCeiling(listener)))


@pytest.fixture
def tk() -> TokenKit:
    return TokenKit()


class TestListAgentTokens:
    def test_returns_only_owner_metadata(self, tk: TokenKit) -> None:
        issued = tk.issue()
        tk.revoke_uc.execute(RevokeAgentTokenRequest(token_id=issued.token_id), owner())
        (row,) = ListAgentTokens(tk.store).execute(ListAgentTokensRequest(), owner())
        assert {f.name for f in fields(row)} == {
            "token_id",
            "agent",
            "scopes",
            "ceiling",
            "created_at",
            "expires_at",
            "revoked_at",
        }
        assert (row.token_id, row.agent, row.scopes, row.ceiling) == (
            issued.token_id,
            issued.agent,
            issued.scopes,
            issued.ceiling,
        )
        assert row.revoked_at == tk.clock.now()

    def test_denies_an_agent_before_reading_credentials(self, tk: TokenKit, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tk.store, "all", lambda: pytest.fail("agent read the store"))
        with pytest.raises(PermissionDenied):
            ListAgentTokens(tk.store).execute(ListAgentTokensRequest(), agent())


class TestIssueAgentToken:
    def test_stores_only_the_hash_and_returns_the_secret_once(self, tk: TokenKit) -> None:
        issued = tk.issue()
        stored = tk.store.get(issued.token_id)
        assert stored is not None
        assert stored.token_hash.hexdigest == hashlib.sha256(issued.secret.encode()).hexdigest()
        assert issued.secret not in repr(stored)
        assert issued.secret not in stored.token_hash.hexdigest
        assert issued.secret not in repr(tk.store.all())
        assert issued.secret not in repr(issued)
        assert issued.secret not in str(issued)
        # Nothing the store (or a later call) exposes carries the plaintext.
        assert all(issued.secret not in str(getattr(stored, name)) for name in stored.__slots__)
        assert (issued.agent, issued.ceiling, issued.scopes) == ("claude-code@workstation", AccessCeiling(INT), READ)
        assert issued.created_at == tk.clock.now()

    def test_each_token_gets_its_own_secret_and_id(self, tk: TokenKit) -> None:
        a, b = tk.issue(), tk.issue(agent="codex@workstation")
        assert a.secret != b.secret
        assert a.token_id != b.token_id

    def test_the_issued_token_authenticates(self, tk: TokenKit) -> None:
        issued = tk.issue(scopes=frozenset({Scope.READ, Scope.WRITE}))
        context = tk.authenticate(issued.secret)
        assert context.principal.name == "claude-code@workstation"
        assert context.scopes == frozenset({Scope.READ, Scope.WRITE})

    @pytest.mark.parametrize("name", ["owner", "owner@workstation", "Not Valid", ""])
    def test_agent_name_is_validated(self, tk: TokenKit, name: str) -> None:
        with pytest.raises(InvalidValueError):
            tk.issue(agent=name)
        assert tk.store.all() == ()

    @pytest.mark.parametrize(
        "scopes",
        [
            frozenset[Scope](),
            {Scope.READ},
            frozenset({"read"}),
            frozenset({Scope.READ, Scope.CORE}),
        ],
    )
    def test_scopes_are_validated(self, tk: TokenKit, scopes: Any) -> None:
        with pytest.raises(InvalidValueError):
            tk.issue(scopes=scopes)

    def test_ceiling_is_validated(self, tk: TokenKit) -> None:
        with pytest.raises(InvalidValueError):
            tk.issue(ceiling=INT)

    def test_expiry_must_be_aware_and_in_the_future(self, tk: TokenKit) -> None:
        with pytest.raises(InvalidValueError):
            tk.issue(expires_at=tk.clock.now().replace(tzinfo=None))
        with pytest.raises(InvalidValueError):
            tk.issue(expires_at=tk.clock.now() - timedelta(days=1))
        assert tk.store.all() == ()

    def test_ceiling_cannot_exceed_the_owners_context(self, tk: TokenKit) -> None:
        with pytest.raises(PermissionDenied):
            tk.issue(owner(INT), ceiling=AccessCeiling(RES))
        assert tk.store.all() == ()
        tk.issue(owner(INT), ceiling=AccessCeiling(INT))  # equal is fine

    def test_only_the_owner_even_with_every_scope(self, tk: TokenKit) -> None:
        with pytest.raises(PermissionDenied):
            tk.issue(agent_ctx_all())
        assert tk.store.all() == ()

    def test_a_weak_generated_secret_is_refused_without_echoing_it(self, tk: TokenKit) -> None:
        tk.ids.new_token_secret = lambda: "tiny-weak"  # type: ignore[method-assign]
        with pytest.raises(InvalidValueError) as error:
            tk.issue()
        assert "tiny-weak" not in str(error.value)
        assert tk.store.all() == ()

    def test_a_secret_collision_is_not_stored(self, tk: TokenKit) -> None:
        tk.ids.new_token_secret = lambda: "x" * 40  # type: ignore[method-assign]
        tk.issue()
        with pytest.raises(AlreadyExists):
            tk.issue(agent="codex@workstation")
        assert len(tk.store.all()) == 1


def agent_ctx_all() -> Any:
    return ctx(RES, frozenset(Scope))


class TestRevokeAgentToken:
    def test_revokes_and_the_token_stops_authenticating(self, tk: TokenKit) -> None:
        issued = tk.issue()
        tk.authenticate(issued.secret)
        tk.clock.advance(timedelta(hours=1))
        result = tk.revoke_uc.execute(RevokeAgentTokenRequest(token_id=issued.token_id), owner())
        assert (result.token_id, result.revoked_at, result.already_revoked) == (
            issued.token_id,
            tk.clock.now(),
            False,
        )
        with pytest.raises(AuthenticationFailed):
            tk.authenticate(issued.secret)

    def test_is_idempotent_and_keeps_the_first_revocation_time(self, tk: TokenKit) -> None:
        issued = tk.issue()
        first = tk.revoke_uc.execute(RevokeAgentTokenRequest(token_id=issued.token_id), owner())
        tk.clock.advance(timedelta(days=2))
        again = tk.revoke_uc.execute(RevokeAgentTokenRequest(token_id=issued.token_id), owner())
        assert again.already_revoked is True
        assert again.revoked_at == first.revoked_at

    def test_other_tokens_are_untouched(self, tk: TokenKit) -> None:
        a, b = tk.issue(), tk.issue(agent="codex@workstation")
        tk.revoke_uc.execute(RevokeAgentTokenRequest(token_id=a.token_id), owner())
        assert tk.authenticate(b.secret).principal.name == "codex@workstation"

    def test_unknown_token_is_not_found_for_the_owner_and_denied_for_everyone_else(self, tk: TokenKit) -> None:
        with pytest.raises(TokenNotFound):
            tk.revoke_uc.execute(RevokeAgentTokenRequest(token_id="nope"), owner())
        issued = tk.issue()
        for token_id in ("nope", issued.token_id):  # existing and unknown ids look the same to a non-owner
            with pytest.raises(PermissionDenied) as error:
                tk.revoke_uc.execute(RevokeAgentTokenRequest(token_id=token_id), agent_ctx_all())
            assert token_id not in str(error.value)
        tk.authenticate(issued.secret)  # still valid

    def test_request_is_validated(self) -> None:
        with pytest.raises(InvalidValueError):
            RevokeAgentTokenRequest(token_id="")


class TestAuthenticateToken:
    def test_effective_ceiling_is_the_minimum_of_listener_and_token(self, tk: TokenKit) -> None:
        issued = tk.issue(ceiling=AccessCeiling(INT))
        assert tk.authenticate(issued.secret, listener=RES).ceiling == AccessCeiling(INT)
        assert tk.authenticate(issued.secret, listener=PUB).ceiling == AccessCeiling(PUB)

    def test_a_token_never_widens_the_listener(self, tk: TokenKit) -> None:
        issued = tk.issue(ceiling=AccessCeiling(RES))
        assert tk.authenticate(issued.secret, listener=INT).ceiling == AccessCeiling(INT)

    def test_the_context_is_an_agent_context_never_the_owner(self, tk: TokenKit) -> None:
        context = tk.authenticate(tk.issue().secret)
        assert not context.is_owner
        assert context.principal.name == "claude-code@workstation"

    def test_unknown_malformed_and_wrong_secrets_fail_identically(self, tk: TokenKit) -> None:
        issued = tk.issue()
        messages = set()
        for secret in ("nope", "", issued.secret + "x", issued.secret[:-1], issued.secret.upper(), 5, None):
            with pytest.raises(AuthenticationFailed) as error:
                tk.authenticate(secret)  # type: ignore[arg-type]
            messages.add(str(error.value))
        assert messages == {"authentication failed"}

    def test_unencodable_and_oversized_secrets_fail_like_any_other(self, tk: TokenKit) -> None:
        tk.issue()
        for secret in ("\ud800" * 40, "a" * 100_000):
            with pytest.raises(AuthenticationFailed) as error:
                tk.authenticate(secret)
            assert str(error.value) == "authentication failed"
            assert error.value.__cause__ is None

    def test_revoked_and_expired_tokens_fail_like_unknown_ones(self, tk: TokenKit) -> None:
        revoked = tk.issue()
        expiring = tk.issue(agent="codex@workstation", expires_at=tk.clock.now() + timedelta(hours=1))
        tk.revoke_uc.execute(RevokeAgentTokenRequest(token_id=revoked.token_id), owner())
        tk.authenticate(expiring.secret)
        tk.clock.advance(timedelta(hours=1))  # now == expires_at: no longer valid
        for secret in (revoked.secret, expiring.secret, "unknown"):
            with pytest.raises(AuthenticationFailed) as error:
                tk.authenticate(secret)
            assert str(error.value) == "authentication failed"
            assert error.value.__cause__ is None

    def test_a_token_created_in_the_future_fails_like_an_unknown_one(self, tk: TokenKit) -> None:
        issued = tk.issue()
        issued_at = tk.clock.now()
        tk.clock.set(issued_at - timedelta(minutes=5))  # verifier's clock is 5 min behind: tolerated
        assert tk.authenticate(issued.secret).principal.name == "claude-code@workstation"
        tk.clock.set(issued_at - timedelta(minutes=5, seconds=1))
        with pytest.raises(AuthenticationFailed) as error:
            tk.authenticate(issued.secret)
        assert str(error.value) == "authentication failed"
        assert error.value.__cause__ is None

    def test_the_secret_is_never_in_the_request_repr_or_the_error(self, tk: TokenKit) -> None:
        issued = tk.issue()
        request = AuthenticateTokenRequest(secret=issued.secret, listener=AccessCeiling(RES))
        assert issued.secret not in repr(request)
        tk.revoke_uc.execute(RevokeAgentTokenRequest(token_id=issued.token_id), owner())
        with pytest.raises(AuthenticationFailed) as error:
            tk.auth_uc.execute(request)
        assert issued.secret not in str(error.value)
        assert issued.secret not in repr(error.value)

    def test_the_listener_must_be_a_ceiling(self) -> None:
        with pytest.raises(InvalidValueError):
            AuthenticateTokenRequest(secret="s", listener=INT)  # type: ignore[arg-type]


# -- HealthReport ---------------------------------------------------------------------------------------------------


def healthy_world() -> World:
    w = World()
    w.add(id=NID("core-rules"), type=CORE, priority=Priority.P0, hub=None, body="Be kind.\n")
    w.add(id=NID("infrastructure"), type=HUB, priority=Priority.P1, hub=None, body="Intro.\n")
    w.add(id=NID("ssh"), hub=NID("infrastructure"), sensitivity=RES, body="Use the key. See [[core-rules]].\n")
    return w


class HealthKit:
    def __init__(self, world: World | None = None) -> None:
        self.world = world or healthy_world()
        self.clock = FixedClock()

    def run(self, c: Any = None) -> HealthReportResult:
        use_case = HealthReport(self.world.notes, self.world.search, self.world.graph, self.clock, VOCABULARY)
        return use_case.execute(HealthReportRequest(), c or owner())


def codes(report: HealthReportResult) -> set[Any]:
    return {i.code for i in report.issues}


class TestHealthReport:
    def test_a_healthy_base_has_no_issues(self) -> None:
        report = HealthKit().run()
        assert report.issues == ()
        assert report.ok
        assert (report.notes_checked, report.search_index_checked) == (3, True)

    def test_problem_files_are_listed_and_the_other_notes_still_checked(self) -> None:
        kit = HealthKit()
        kit.world.notes.problems.append(FileProblem("notes/broken.md", "invalid frontmatter", stable=True))
        report = kit.run()
        (issue,) = report.issues
        assert (issue.subject, issue.code, issue.severity) == (
            "notes/broken.md",
            HealthCode.NOTE_FILE_EXCLUDED,
            Severity.ERROR,
        )
        assert issue.message.startswith("invalid frontmatter (")
        assert (report.notes_checked, report.search_index_checked) == (3, True)

    def test_unstable_problem_files_block_and_skip_the_entry_comparison(self) -> None:
        kit = HealthKit()
        kit.world.notes.problems.append(FileProblem("notes/edited.md", "differs from the last commit", stable=False))
        kit.world.search.rebuild([], revision=kit.world.notes.head())  # would differ entry by entry
        report = kit.run()
        assert [(i.subject, i.code) for i in report.issues] == [("notes/edited.md", HealthCode.NOTE_FILE_BLOCKING)]
        assert not report.search_index_checked

    def test_a_scan_that_cannot_run_is_reported_not_raised(self) -> None:
        kit = HealthKit()

        def fail() -> Any:
            raise NoteReadError(problems=[("notes", "is not a real directory (symlink or file)")])

        kit.world.notes.scan = fail  # type: ignore[method-assign]
        report = kit.run()
        assert [(i.subject, i.code) for i in report.issues] == [("notes", HealthCode.NOTE_FILE_BLOCKING)]
        assert (report.ok, report.notes_checked) == (False, 0)

    def test_only_the_owner_and_only_with_full_view(self) -> None:
        kit = HealthKit()
        for c in (agent(), ctx(INT, frozenset(Scope)), owner(INT), owner(PUB)):
            with pytest.raises(PermissionDenied):
                kit.run(c)

    def test_broken_links_warn_and_name_the_note(self) -> None:
        kit = HealthKit()
        kit.world.add(id=NID("lonely"), hub=NID("infrastructure"), body="See [[nowhere]].\n")
        report = kit.run()
        issue = next(i for i in report.issues if i.code is ViolationCode.BROKEN_LINK)
        assert (issue.subject, issue.severity) == ("lonely", Severity.WARNING)
        assert report.ok
        assert report.warnings == tuple(i for i in report.issues if i.severity is Severity.WARNING)
        assert issue in report.warnings

    def test_hub_chain_problems(self) -> None:
        kit = HealthKit()
        w = kit.world
        w.add(id=NID("hub-a"), type=HUB, priority=Priority.P1, hub=NID("infrastructure"))
        w.add(id=NID("hub-b"), type=HUB, priority=Priority.P1, hub=NID("hub-a"))
        w.add(id=NID("hub-c"), type=HUB, priority=Priority.P1, hub=NID("hub-b"))
        w.add(id=NID("loop-a"), type=HUB, priority=Priority.P1, hub=NID("loop-b"))
        w.add(id=NID("loop-b"), type=HUB, priority=Priority.P1, hub=NID("loop-a"))
        found = codes(kit.run())
        assert ViolationCode.HUB_CHAIN_TOO_DEEP in found
        assert ViolationCode.HUB_CHAIN_CYCLE in found
        assert not kit.run().ok

    def test_missing_hub_and_per_note_rules(self) -> None:
        kit = HealthKit()
        kit.world.add(id=NID("stray"), hub=NID("ghost-hub"), summary="too short")
        assert {ViolationCode.HUB_NOT_FOUND, ViolationCode.SUMMARY_LENGTH} <= codes(kit.run())

    def test_budget_overruns(self) -> None:
        kit = HealthKit()
        kit.world.add(id=NID("big-core"), type=CORE, priority=Priority.P0, hub=None, body="word " * 1600)
        kit.world.add(id=NID("big-hub"), type=HUB, priority=Priority.P1, hub=None, body="word " * 900)
        kit.world.add(id=NID("big-note"), hub=NID("infrastructure"), body="word " * 3100)
        kit.world.add(id=NID("long-note"), hub=NID("infrastructure"), body="word " * 1600)
        report = kit.run()
        assert {
            ViolationCode.CORE_OVER_BUDGET,
            ViolationCode.HUB_OVER_BUDGET,
            ViolationCode.BODY_OVER_HARD_LIMIT,
            ViolationCode.BODY_OVER_SOFT_LIMIT,
        } <= codes(report)
        soft = next(i for i in report.issues if i.code is ViolationCode.BODY_OVER_SOFT_LIMIT)
        assert soft.severity is Severity.WARNING

    def test_history_does_not_count_towards_the_core_budget(self) -> None:
        kit = HealthKit()
        kit.world.add(
            id=NID("old-core"), type=CORE, priority=Priority.P0, hub=None, status=Status.ARCHIVED, body="word " * 1600
        )
        assert ViolationCode.CORE_OVER_BUDGET not in codes(kit.run())

    def test_secrets_in_existing_notes_are_reported_without_any_part_of_the_value(self) -> None:
        kit = HealthKit()
        kit.world.add(id=NID("leaky"), hub=NID("infrastructure"), body=f"token: {SECRET}\n", summary="x" * 50)
        kit.world.add(id=NID("leaky-title"), hub=NID("infrastructure"), title=f"key {SECRET}")
        report = kit.run()
        leaks = [i for i in report.issues if i.code is ViolationCode.SECRET_DETECTED]
        assert {i.subject for i in leaks} == {"leaky", "leaky-title"}
        assert all(i.severity is Severity.ERROR for i in leaks)
        assert not report.ok
        text = repr(report)
        assert SECRET not in text
        assert SECRET[:2] not in " ".join(i.message for i in leaks)
        assert "starts with" not in text
        assert {i.field for i in leaks} >= {"body", "title"}

    def test_ids_of_stored_notes_are_not_reported_as_secrets(self) -> None:
        kit = HealthKit()
        first = NID("configuring-postgresql-16-backups-with-pgbackrest-on-ubuntu-24-04-k7q2")
        kit.world.add(id=first, hub=NID("infrastructure"))
        kit.world.add(
            id=NID("quarterly-review-2026-q4-of-zigbee2mqtt-and-mosquitto-upgrade-x9m3"),
            hub=NID("infrastructure"),
            related=(first,),
            body=f"Backups: {first.value}.\n",
        )
        assert ViolationCode.SECRET_DETECTED not in codes(kit.run())

    def test_the_report_ignores_the_message_text_of_secret_findings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        junk = Finding(
            code=ViolationCode.SECRET_DETECTED,
            field="body",
            message=f"starts with {SECRET[:6]!r} whatever; notes must? {SECRET}",
            detector="bearer token",
            line=3,
        )
        monkeypatch.setattr("cerebro.domain.validation.scan_note", lambda note, **_: (junk,))
        kit = HealthKit()
        kit.world.add(id=NID("any"), hub=NID("infrastructure"))
        leaks = [i for i in kit.run().issues if i.code is ViolationCode.SECRET_DETECTED]
        assert leaks
        assert {(i.message, i.field) for i in leaks} == {(secret_message("bearer token", 3), "body")}
        text = repr(kit.run())
        assert SECRET[:6] not in text
        assert "starts with" not in text

    def test_orphan_notes_and_overdue_reviews(self) -> None:
        kit = HealthKit()
        kit.world.add(id=NID("alone"), type=CORE, priority=Priority.P0, hub=None, body="No links.\n")
        kit.world.add(id=NID("due"), hub=NID("infrastructure"), review_by=date(2026, 11, 24))
        kit.world.add(id=NID("later"), hub=NID("infrastructure"), review_by=date(2026, 11, 26))
        kit.world.add(
            id=NID("due-but-archived"), hub=NID("infrastructure"), review_by=date(2020, 1, 1), status=Status.ARCHIVED
        )
        report = kit.run()
        by_code = {c: {i.subject for i in report.issues if i.code is c} for c in codes(report)}
        assert by_code[HealthCode.ORPHAN_NOTE] == {"alone"}
        assert by_code[HealthCode.REVIEW_OVERDUE] == {"due"}

    def test_stale_indexes_are_errors(self) -> None:
        kit = HealthKit()
        w = kit.world
        # a note committed but never indexed, an indexed note that was deleted, and an outdated entry
        w.notes.apply([NoteChange(note=_unindexed())], MESSAGE)
        w.search.remove(NID("ssh"))
        w.graph.remove(NID("core-rules"))
        report = kit.run()
        stale = {(i.code, i.subject) for i in report.issues if i.severity is Severity.ERROR}
        assert (HealthCode.SEARCH_INDEX_STALE, "fresh") in stale
        assert (HealthCode.GRAPH_INDEX_STALE, "fresh") in stale
        assert (HealthCode.SEARCH_INDEX_STALE, "ssh") in stale
        assert (HealthCode.GRAPH_INDEX_STALE, "core-rules") in stale
        assert not report.ok

    def test_rebuild_fixes_stale_indexes(self) -> None:
        kit = HealthKit()
        kit.world.search.rebuild([])
        assert not kit.run().ok
        RebuildIndex(kit.world.notes, kit.world.search, kit.world.graph).execute(RebuildIndexRequest(), owner())
        assert kit.run().ok

    def test_a_note_edited_behind_the_index_is_out_of_date(self) -> None:
        kit = HealthKit()
        note = kit.world.notes.get(NID("ssh"))
        assert note is not None
        kit.world.search.upsert(replace(note, title="Old title"))
        report = kit.run()
        assert (HealthCode.SEARCH_INDEX_STALE, "ssh") in {(i.code, i.subject) for i in report.issues}

    def test_a_large_repository_checks_only_the_graph_index(self) -> None:
        kit = HealthKit()
        for n in range(100):
            kit.world.add(id=NID(f"note-{n:03d}"), hub=NID("infrastructure"))
        report = kit.run()
        assert report.search_index_checked is False
        assert report.notes_checked == 103
        assert not any(i.code is HealthCode.SEARCH_INDEX_STALE for i in report.issues)


def _unindexed() -> Any:
    return make_note(id=NID("fresh"), related=(), hub=NID("infrastructure"), sensitivity=PUB)


class TestFindingIssue:
    def test_secret_findings_are_described_from_detector_and_line_only(self) -> None:
        finding = Finding(
            code=ViolationCode.SECRET_DETECTED, field="body", message=f"junk {SECRET}", detector="bearer token", line=7
        )
        issue = _finding_issue("some-note", finding)
        assert issue.message == secret_message("bearer token", 7)
        assert (issue.subject, issue.field, issue.severity) == ("some-note", "body", Severity.ERROR)
        assert SECRET not in repr(issue)

    def test_other_findings_keep_their_message(self) -> None:
        finding = Finding(code=ViolationCode.CORE_OVER_BUDGET, field=None, message="too big")
        assert _finding_issue("core-budget", finding).message == "too big"


# -- index revision -------------------------------------------------------------------------------------------------


class TestIndexRevision:
    def test_rebuild_records_the_head_in_both_indexes(self) -> None:
        w = healthy_world()
        w.search.set_revision(None)
        w.graph.set_revision("rev-old")
        RebuildIndex(w.notes, w.search, w.graph).execute(RebuildIndexRequest(), owner())
        assert w.search.built_from() == w.graph.built_from() == w.notes.head()

    def test_rebuild_of_an_empty_repository_records_none(self) -> None:
        w = World()
        w.search.set_revision("rev-old")
        RebuildIndex(w.notes, w.search, w.graph).execute(RebuildIndexRequest(), owner())
        assert (w.search.built_from(), w.graph.built_from(), w.notes.head()) == (None, None, None)

    def test_rebuild_reads_the_head_before_the_notes(self) -> None:
        w = healthy_world()
        seen = w.notes.head()
        original = w.notes.scan

        def commit_then_scan() -> Any:
            scan = original()
            w.notes.apply([NoteChange(note=_unindexed())], MESSAGE)  # a commit lands while the notes are read
            return scan

        w.notes.scan = commit_then_scan  # type: ignore[method-assign]
        RebuildIndex(w.notes, w.search, w.graph).execute(RebuildIndexRequest(), owner())
        assert w.search.built_from() == seen != w.notes.head()
        assert index_is_stale(w.notes, w.search, w.graph)

    def test_is_stale_compares_each_index_with_the_head(self) -> None:
        w = healthy_world()
        assert not index_is_stale(w.notes, w.search, w.graph)
        w.search.set_revision("rev-other")
        assert index_is_stale(w.notes, w.search, w.graph)
        w.search.set_revision(w.notes.head())
        w.graph.set_revision(None)
        assert index_is_stale(w.notes, w.search, w.graph)

    def test_fresh_empty_indexes_of_an_empty_repository_are_not_stale(self) -> None:
        w = World()
        assert not index_is_stale(w.notes, w.search, w.graph)
        w.search.set_revision("rev-gone")
        assert index_is_stale(w.notes, w.search, w.graph)

    def test_advance_skips_an_index_that_was_already_stale(self) -> None:
        w = World()
        w.search.set_revision("rev-1")
        w.graph.set_revision("rev-0")
        advance_revision(w.search, w.graph, previous="rev-1", current="rev-2")
        assert (w.search.built_from(), w.graph.built_from()) == ("rev-2", "rev-0")

    def test_health_report_flags_a_stale_revision_even_when_the_content_matches(self) -> None:
        kit = HealthKit()
        kit.world.graph.set_revision("rev-other")
        report = kit.run()
        assert [(i.code, i.severity, i.subject) for i in report.issues] == [
            (HealthCode.INDEX_STALE, Severity.ERROR, "index")
        ]
        assert "rev-" not in report.issues[0].message
        assert not report.ok

    def test_health_report_flags_an_index_that_was_never_built(self) -> None:
        kit = HealthKit()
        kit.world.search.set_revision(None)
        assert HealthCode.INDEX_STALE in codes(kit.run())

    def test_a_healthy_base_has_no_stale_index_issue(self) -> None:
        assert HealthCode.INDEX_STALE not in codes(HealthKit().run())

    def test_rebuild_clears_the_stale_flag(self) -> None:
        kit = HealthKit()
        kit.world.search.set_revision("rev-other")
        RebuildIndex(kit.world.notes, kit.world.search, kit.world.graph).execute(RebuildIndexRequest(), owner())
        assert kit.run().ok


def test_health_report_lists_the_revision_issue_and_the_per_note_differences_together() -> None:
    kit = HealthKit()
    kit.world.notes.apply([NoteChange(note=_unindexed())], MESSAGE)  # committed, never indexed
    found = {(i.code, i.subject) for i in kit.run().issues}
    assert (HealthCode.INDEX_STALE, "index") in found
    assert (HealthCode.SEARCH_INDEX_STALE, "fresh") in found
    assert (HealthCode.GRAPH_INDEX_STALE, "fresh") in found
