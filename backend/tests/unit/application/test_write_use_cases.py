from dataclasses import fields, replace
from datetime import date
from typing import Any

import pytest

from cerebro.application.dto import (
    ApplyOutcome,
    ApplyProposalResult,
    HealthCode,
    HealthReportRequest,
    OwnerEditNoteRequest,
    ProposalIdRequest,
    RebuildIndexRequest,
    ReviewAction,
    ReviewProposalRequest,
    SubmitProposalRequest,
)
from cerebro.application.errors import (
    NoteNotFound,
    PermissionDenied,
    ProposalConflict,
    ProposalNotApplicable,
    ProposalNotFound,
)
from cerebro.application.index_state import index_is_stale
from cerebro.application.null_curator import NullCurator
from cerebro.application.ports.notes import NoteChange
from cerebro.application.proposal_support import provisional_note_id
from cerebro.application.use_cases import HealthReport, RebuildIndex
from cerebro.domain.access import Scope
from cerebro.domain.content_hash import content_hash
from cerebro.domain.decision import Decision, DecisionKind
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import ContentConflict, InvalidTransition, InvalidValueError
from cerebro.domain.note import Note, NoteId
from cerebro.domain.proposal import ProposalKind, ProposalStatus, ProposedContent
from cerebro.domain.report import ViolationCode
from cerebro.domain.vocabulary import CORE, Area
from tests.fakes.notes import InMemoryNoteRepository
from tests.fakes.stores import FakeCurator
from tests.unit.application.world import MESSAGE as SEED_MESSAGE
from tests.unit.application.world import NID
from tests.unit.application.write_kit import (
    NEW_ID,
    PROPOSE,
    SECRET,
    SSH,
    Kit,
    agent_ctx,
    new_content,
    note_hash,
    owner_ctx,
)
from tests.unit.domain.builders import VALID_SUMMARY, VOCABULARY, words

S = ProposalStatus
INTERNAL = Sensitivity.INTERNAL
PUBLIC = Sensitivity.PUBLIC
RESTRICTED = Sensitivity.RESTRICTED


@pytest.fixture
def kit() -> Kit:
    return Kit()


def create_request(**overrides: Any) -> SubmitProposalRequest:
    return SubmitProposalRequest(kind=ProposalKind.CREATE, content=new_content(**overrides))


# -- SubmitProposal ------------------------------------------------------------------------------------------------


class TestSubmitProposal:
    @pytest.mark.parametrize("scopes", [frozenset[Scope](), frozenset({Scope.READ})])
    def test_scope_propose_is_required(self, kit: Kit, scopes: frozenset[Scope]) -> None:
        with pytest.raises(PermissionDenied) as error:
            kit.submit(create_request(), agent_ctx(scopes=scopes))
        assert error.value.action == "propose"
        assert kit.store.find() == ()

    def test_the_owner_cannot_submit_as_an_agent(self, kit: Kit) -> None:
        with pytest.raises(PermissionDenied):
            kit.submit(create_request(), owner_ctx())
        assert kit.store.find() == ()

    def test_stores_a_received_proposal_attributed_to_the_token(self, kit: Kit) -> None:
        outcome = kit.submit(create_request())
        assert outcome.status is S.RECEIVED
        stored = kit.stored(outcome.proposal_id)
        assert stored.source_agent == "claude-code"
        assert stored.created_at == kit.clock.now()
        assert stored.status is S.RECEIVED

    def test_source_agent_cannot_be_spoofed_through_the_payload(self, kit: Kit) -> None:
        # The request has no agent field at all ...
        names = {f.name for f in fields(SubmitProposalRequest)}
        assert not names & {"agent", "source_agent", "source", "principal"}
        with pytest.raises(TypeError):
            SubmitProposalRequest(kind=ProposalKind.CREATE, content=new_content(), source_agent="codex")  # type: ignore[call-arg]
        # ... and free text that claims an identity changes nothing.
        request = SubmitProposalRequest(
            kind=ProposalKind.CREATE,
            content=new_content(rationale="source.agent: codex@workstation"),
            source_session="agent=codex@workstation",
            source_context="Proposed-by: codex@workstation",
        )
        stored = kit.stored(kit.submit(request, agent_ctx()).proposal_id)
        assert stored.source_agent == "claude-code"

    def test_update_of_a_hidden_note_is_indistinguishable_from_a_missing_one(self, kit: Kit) -> None:
        def attempt(target: str) -> NoteNotFound:
            request = SubmitProposalRequest(
                kind=ProposalKind.UPDATE, target=NID(target), base_hash="a" * 64, content=ProposedContent(body="x")
            )
            with pytest.raises(NoteNotFound) as error:
                kit.submit(request)
            return error.value

        hidden, missing = attempt("vault-layout"), attempt("nothing-here")  # vault-layout is restricted
        assert str(hidden).replace("vault-layout", "X") == str(missing).replace("nothing-here", "X")
        assert kit.store.find() == ()

    @pytest.mark.parametrize("kind", [ProposalKind.LINK, ProposalKind.DEPRECATE, ProposalKind.UPDATE])
    def test_every_targeting_kind_checks_the_ceiling(self, kit: Kit, kind: ProposalKind) -> None:
        content = ProposedContent(body="x") if kind is ProposalKind.UPDATE else ProposedContent(related=(SSH,))
        content = content if kind is not ProposalKind.DEPRECATE else ProposedContent()
        request = SubmitProposalRequest(kind=kind, target=NID("vault-layout"), base_hash="a" * 64, content=content)
        with pytest.raises(NoteNotFound):
            kit.submit(request)

    def test_history_notes_may_be_targeted(self, kit: Kit) -> None:
        request = SubmitProposalRequest(
            kind=ProposalKind.DEPRECATE, target=NID("old-proxy"), base_hash="a" * 64, content=ProposedContent()
        )
        assert kit.submit(request).status is S.RECEIVED

    def test_suggested_sensitivity_above_the_ceiling_is_refused(self, kit: Kit) -> None:
        with pytest.raises(PermissionDenied):
            kit.submit(create_request(sensitivity=RESTRICTED))
        assert kit.store.find() == ()
        kit.submit(create_request(sensitivity=INTERNAL))  # at the ceiling is fine

    @pytest.mark.parametrize(
        "where",
        ["title", "body", "summary", "rationale", "source_context", "source_session"],
    )
    def test_secrets_are_rejected_and_never_stored(self, kit: Kit, where: str) -> None:
        leaked = f"password: {SECRET}" if where != "body" else f"# Docker\n\ntoken {SECRET}\n"
        request_fields: dict[str, Any] = {"kind": ProposalKind.CREATE}
        if where in ("source_context", "source_session"):
            request_fields.update(content=new_content(), **{where: leaked})
        else:
            request_fields.update(
                content=new_content(**{where: leaked if where != "summary" else leaked + VALID_SUMMARY})
            )
        outcome = kit.submit(SubmitProposalRequest(**request_fields))
        assert outcome.status is S.REJECTED
        assert outcome.validation is not None
        assert ViolationCode.SECRET_DETECTED in outcome.validation.codes
        stored = kit.stored(outcome.proposal_id)
        assert stored.status is S.REJECTED
        assert SECRET not in repr(stored)
        assert SECRET not in repr(outcome)
        assert stored.source_agent == "claude-code"
        assert kit.commits == 5  # nothing was written (the 5 seed notes)

    def test_each_submission_gets_its_own_id(self, kit: Kit) -> None:
        assert kit.submit_create() != kit.submit_create()


# -- ValidateProposal ----------------------------------------------------------------------------------------------


class TestValidateProposal:
    def test_owner_principal_is_required(self, kit: Kit) -> None:
        pid = kit.submit_create()
        with pytest.raises(PermissionDenied):
            kit.validate_uc().execute(ProposalIdRequest(proposal_id=pid), agent_ctx())
        assert kit.stored(pid).status is S.RECEIVED

    def test_a_valid_proposal_becomes_validated(self, kit: Kit) -> None:
        pid = kit.submit_create()
        outcome = kit.validate(pid)
        assert outcome.status is S.VALIDATED
        assert outcome.validation is not None
        assert outcome.validation.ok
        assert kit.stored(pid).validation == outcome.validation

    def test_all_problems_are_reported_at_once_and_the_proposal_is_rejected(self, kit: Kit) -> None:
        pid = kit.submit_create(area=Area("nowhere"), summary="short", hub=NID("missing-hub"))
        outcome = kit.validate(pid)
        assert outcome.status is S.REJECTED
        assert outcome.validation is not None
        assert {ViolationCode.UNKNOWN_AREA, ViolationCode.SUMMARY_LENGTH, ViolationCode.HUB_NOT_FOUND} <= (
            outcome.validation.codes
        )
        assert kit.stored(pid).status is S.REJECTED

    def test_missing_fields_are_reported(self, kit: Kit) -> None:
        outcome = kit.validate(kit.submit_create(summary=None, sensitivity=None, hub=None))
        assert outcome.validation is not None
        assert ViolationCode.MISSING_FIELD in outcome.validation.codes
        assert {v.field for v in outcome.validation.errors} >= {"summary", "sensitivity"}

    def test_a_title_without_a_usable_id_is_rejected(self, kit: Kit) -> None:
        outcome = kit.validate(kit.submit_create(title="???"))
        assert outcome.status is S.REJECTED
        assert outcome.validation is not None
        assert ViolationCode.INVALID_FIELD in outcome.validation.codes

    def test_broken_links_are_errors_for_proposals(self, kit: Kit) -> None:
        outcome = kit.validate(kit.submit_create(body="See [[does-not-exist]].\n"))
        assert outcome.status is S.REJECTED
        assert outcome.validation is not None
        assert ViolationCode.BROKEN_LINK in outcome.validation.codes

    @pytest.mark.parametrize("kind", ["body", "related", "hub"])
    def test_links_to_a_higher_sensitivity_are_errors_for_proposals(self, kit: Kit, kind: str) -> None:
        # a public new note linking to the restricted "vault-layout" (a hub is linked as ``hub``)
        options: dict[str, dict[str, Any]] = {
            "body": {"body": "See [[vault-layout]].\n"},
            "related": {"related": (NID("vault-layout"),)},
            "hub": {"hub": NID("vault-layout")},
        }
        outcome = kit.validate(kit.submit_create(**options[kind]))
        assert outcome.status is S.REJECTED
        assert outcome.validation is not None
        assert ViolationCode.LINK_TO_HIGHER_SENSITIVITY in {v.code for v in outcome.validation.errors}
        for violation in outcome.validation.violations:
            if violation.code is ViolationCode.LINK_TO_HIGHER_SENSITIVITY:
                assert violation.message.endswith(" may not be linked from this note")
                assert "vault-layout" not in violation.message
                assert "Vault layout" not in violation.message

    @pytest.mark.parametrize("body", ["`[[vault-layout]]`", "[[vault-layout|the vault]]", "```\n[[vault-layout]]\n```"])
    def test_alias_and_code_span_links_are_caught_for_proposals(self, kit: Kit, body: str) -> None:
        outcome = kit.validate(kit.submit_create(body=body + "\n"))
        assert outcome.validation is not None
        assert ViolationCode.LINK_TO_HIGHER_SENSITIVITY in {v.code for v in outcome.validation.errors}

    def test_superseded_by_a_higher_sensitivity_is_caught_for_deprecations(self, kit: Kit) -> None:
        deprecate = SubmitProposalRequest(
            kind=ProposalKind.DEPRECATE,
            target=SSH,
            base_hash=note_hash(kit),
            content=ProposedContent(superseded_by=NID("vault-layout")),
        )
        outcome = kit.validate(kit.submit(deprecate).proposal_id)
        assert outcome.validation is not None
        assert ViolationCode.LINK_TO_HIGHER_SENSITIVITY in {v.code for v in outcome.validation.errors}

    def test_a_new_note_that_stored_lower_notes_already_link_to_is_rejected(self, kit: Kit) -> None:
        # "writer" (public) links to the id; an internal note created under that id would be leaked
        kit.world.add(id=NID("writer"), hub=NID("infrastructure"), body="Plan: [[future-secret]].\n")
        outcome = kit.validate(kit.submit_create(title="Future secret", sensitivity=INTERNAL))
        assert outcome.validation is not None
        messages = [v.message for v in outcome.validation.errors if v.code is ViolationCode.LINK_TO_HIGHER_SENSITIVITY]
        assert messages == ["writer links to this note"]

    @pytest.mark.parametrize("field", ["title", "summary"])
    def test_wikilinks_in_title_or_summary_are_errors_for_proposals(self, kit: Kit, field: str) -> None:
        value = "A title about [[vault-layout]]" if field == "title" else VALID_SUMMARY + " [[vault-layout]]"
        outcome = kit.validate(kit.submit_create(**{field: value}))
        assert outcome.validation is not None
        assert ViolationCode.WIKILINK_IN_METADATA in {v.code for v in outcome.validation.errors}

    def test_links_to_equal_or_lower_sensitivity_are_fine_for_proposals(self, kit: Kit) -> None:
        # internal note -> internal and public targets
        pid = kit.submit_create(sensitivity=INTERNAL, body="See [[server-ssh-access]] and [[core-rules]].\n")
        outcome = kit.validate(pid)
        assert outcome.status is S.VALIDATED, outcome.validation

    def test_an_update_that_adds_a_leaking_link_is_rejected(self, kit: Kit) -> None:
        outcome = kit.validate(kit.submit_update(body="# SSH\n\nSee [[vault-layout]].\n"))
        assert outcome.status is S.REJECTED
        assert outcome.validation is not None
        assert ViolationCode.LINK_TO_HIGHER_SENSITIVITY in outcome.validation.codes

    def test_core_budget_applies_with_the_proposed_note_substituted(self, kit: Kit) -> None:
        pid = kit.submit_create(type=CORE, priority=Priority.P0, hub=None, body=words(1600))
        outcome = kit.validate(pid)
        assert outcome.validation is not None
        assert ViolationCode.CORE_OVER_BUDGET in outcome.validation.codes

    def test_possible_duplicates_warn_but_do_not_block(self, kit: Kit) -> None:
        pid = kit.submit_create(title="SSH access to the home server", summary=VALID_SUMMARY)
        outcome = kit.validate(pid)
        assert outcome.status is S.VALIDATED
        assert outcome.validation is not None
        assert ViolationCode.POSSIBLE_DUPLICATE in {v.code for v in outcome.validation.warnings}

    def test_updates_are_validated_as_the_resulting_note(self, kit: Kit) -> None:
        assert kit.validate(kit.submit_update()).status is S.VALIDATED
        outcome = kit.validate(kit.submit_update(body=words(3100)))
        assert outcome.validation is not None
        assert ViolationCode.BODY_OVER_HARD_LIMIT in outcome.validation.codes

    def test_a_proposal_cannot_lower_the_sensitivity_of_a_note(self, kit: Kit) -> None:
        outcome = kit.validate(kit.submit_update(sensitivity=PUBLIC))  # server-ssh-access is internal
        assert outcome.status is S.REJECTED
        assert outcome.validation is not None
        assert [v.field for v in outcome.validation.errors] == ["sensitivity"]

    def test_superseded_by_is_only_for_deprecations(self, kit: Kit) -> None:
        outcome = kit.validate(kit.submit_update(superseded_by=NID("core-rules")))
        assert outcome.status is S.REJECTED

    def test_a_secret_that_reached_the_store_is_found_and_scrubbed(self, kit: Kit) -> None:
        # Defense in depth: SubmitProposal screens secrets, but the validator must not rely on it.
        pid = kit.submit_create()
        leaked = replace(kit.stored(pid), content=new_content(body=f"api_key = '{SECRET}'\n"), source_context=SECRET)
        kit.overwrite(leaked)
        outcome = kit.validate(pid)
        assert outcome.status is S.REJECTED
        assert outcome.validation is not None
        assert ViolationCode.SECRET_DETECTED in outcome.validation.codes
        assert SECRET not in repr(kit.stored(pid))
        assert SECRET not in repr(outcome)
        assert kit.stored(pid).source_context is None

    def test_hidden_targets_are_not_found(self, kit: Kit) -> None:
        pid = kit.submit_update()
        with pytest.raises(NoteNotFound):
            kit.validate_uc().execute(ProposalIdRequest(proposal_id=pid), owner_ctx(PUBLIC))
        assert kit.stored(pid).status is S.RECEIVED

    def test_unknown_proposal_and_wrong_status(self, kit: Kit) -> None:
        with pytest.raises(ProposalNotFound):
            kit.validate("nope")
        pid = kit.submit_create()
        kit.validate(pid)
        with pytest.raises(InvalidTransition):
            kit.validate(pid)


# -- CurateProposal ------------------------------------------------------------------------------------------------


class TestCurateProposal:
    def test_owner_principal_is_required(self, kit: Kit) -> None:
        pid = kit.submit_create()
        kit.validate(pid)
        with pytest.raises(PermissionDenied):
            kit.curate_uc().execute(ProposalIdRequest(proposal_id=pid), agent_ctx())

    def test_the_null_curator_sends_everything_to_the_owner(self, kit: Kit) -> None:
        pid = kit.submit_create()
        kit.validate(pid)
        outcome = kit.curate(pid)
        assert outcome.status is S.NEEDS_OWNER
        assert outcome.decision is not None
        assert outcome.decision.kind is DecisionKind.NEEDS_OWNER
        assert outcome.decision.target == NEW_ID  # suggested id, derived from the title
        stored = kit.stored(pid)
        assert (stored.curated_by, stored.decision) == ("curator/null", outcome.decision)

    def test_null_curator_suggests_the_proposal_target_for_updates(self, kit: Kit) -> None:
        pid = kit.submit_update()
        kit.validate(pid)
        assert kit.curate(pid).decision == Decision.needs_owner(
            "no curator is configured: the owner decides", target=SSH
        )
        assert NullCurator().name == "curator/null"

    def test_a_scripted_curator_decision_is_stored(self, kit: Kit) -> None:
        kit.curator = FakeCurator(Decision.create(NEW_ID, "new topic"), name="curator/test")
        pid = kit.submit_create()
        kit.validate(pid)
        kit.curate(pid)
        assert kit.stored(pid).decision == Decision.create(NEW_ID, "new topic")
        assert kit.stored(pid).curated_by == "curator/test"

    @pytest.mark.parametrize("name", ["owner", "claude-code@workstation", "null-curator"])
    def test_a_curator_name_without_the_prefix_is_refused(self, kit: Kit, name: str) -> None:
        kit.curator = FakeCurator(Decision.create(NEW_ID, "new topic"), name=name)
        pid = kit.submit_create()
        kit.validate(pid)
        with pytest.raises(InvalidValueError):
            kit.curate(pid)
        assert kit.stored(pid).curated_by is None

    def test_the_curator_sees_the_target_and_similar_notes_within_the_ceiling(self, kit: Kit) -> None:
        curator = FakeCurator()
        kit.curator = curator
        pid = kit.submit_update()
        kit.validate(pid)
        kit.curate_uc().execute(ProposalIdRequest(proposal_id=pid), owner_ctx(INTERNAL))
        request = curator.requests[0]
        assert request.target is not None
        assert request.target.id == SSH
        assert all(hit.sensitivity <= INTERNAL for hit in request.similar)

    @pytest.mark.parametrize(
        ("kind", "decision"),
        [
            ("update", Decision.merge_into(NID("core-rules"))),  # redirects to another note
            ("update", Decision.create(NID("core-rules"))),  # wrong shape
            ("create", Decision.merge_into(SSH)),  # a create cannot be merged
        ],
    )
    def test_a_decision_that_does_not_fit_is_replaced_by_needs_owner(
        self, kit: Kit, kind: str, decision: Decision
    ) -> None:
        kit.curator = FakeCurator(decision)
        pid = kit.submit_update() if kind == "update" else kit.submit_create()
        kit.validate(pid)
        stored = kit.curate(pid).decision
        assert stored is not None
        assert stored.kind is DecisionKind.NEEDS_OWNER

    def test_a_rejection_is_kept_for_the_owner_to_confirm(self, kit: Kit) -> None:
        kit.curator = FakeCurator(Decision.reject("duplicate"))
        pid = kit.submit_create()
        kit.validate(pid)
        assert kit.curate(pid).status is S.CURATED
        with pytest.raises(ProposalNotApplicable):
            kit.review(pid)  # a rejection is not approvable

    def test_status_and_existence(self, kit: Kit) -> None:
        with pytest.raises(ProposalNotFound):
            kit.curate("nope")
        with pytest.raises(InvalidTransition):
            kit.curate(kit.submit_create())  # still received


VAULT = NID("vault-layout")  # restricted in the standard world


class TestCurateProposalNeverSendsRestrictedToTheCurator:
    """ADR-0016: enforced by the use case, whatever the curator adapter."""

    def validated_with(self, kit: Kit, **changes: Any) -> str:
        """A validated proposal whose fields are then replaced behind the use cases' back (the write path would
        refuse some of these combinations, but stored data may predate a rule)."""
        pid = kit.submit_create()
        kit.validate(pid)
        kit.overwrite(replace(kit.stored(pid), **changes))
        return pid

    def validated_on(self, kit: Kit, kind: ProposalKind, target: NoteId, content: ProposedContent) -> str:
        note = kit.world.notes.get(target)
        assert note is not None
        return self.validated_with(kit, kind=kind, target=target, base_hash=content_hash(note), content=content)

    def validated_content(self, kit: Kit, **content: Any) -> str:
        stored_content = new_content(**content)
        return self.validated_with(kit, content=stored_content)

    def assert_skipped(self, kit: Kit, curator: FakeCurator, pid: str) -> None:
        outcome = kit.curate(pid)
        assert curator.requests == []
        assert outcome.status is S.NEEDS_OWNER
        assert outcome.decision is not None
        assert outcome.decision.kind is DecisionKind.NEEDS_OWNER
        assert outcome.decision.reason == "touches restricted content: the owner decides"
        stored = kit.stored(pid)
        assert stored.curated_by == "curator/policy"
        assert stored.decision == outcome.decision

    def test_a_restricted_new_note_skips_the_curator(self, kit: Kit) -> None:
        curator = FakeCurator()
        kit.curator = curator
        pid = self.validated_content(kit, sensitivity=Sensitivity.RESTRICTED)
        self.assert_skipped(kit, curator, pid)

    def test_an_update_of_a_restricted_note_skips_the_curator(self, kit: Kit) -> None:
        curator = FakeCurator()
        kit.curator = curator
        pid = self.validated_on(kit, ProposalKind.UPDATE, VAULT, ProposedContent(body="New.\n"))
        self.assert_skipped(kit, curator, pid)

    def test_an_update_that_raises_sensitivity_to_restricted_skips_the_curator(self, kit: Kit) -> None:
        curator = FakeCurator()
        kit.curator = curator
        content = ProposedContent(body="# SSH\n\nBody.\n", sensitivity=Sensitivity.RESTRICTED)
        pid = self.validated_on(kit, ProposalKind.UPDATE, SSH, content)
        self.assert_skipped(kit, curator, pid)

    @pytest.mark.parametrize(
        "content",
        [
            {"hub": VAULT},
            {"related": (NID("core-rules"), VAULT)},
            {"superseded_by": VAULT},
            {"body": "See [[vault-layout]].\n"},
            {"rationale": "see [[vault-layout]]"},
            {"title": "About [[vault-layout]]"},
            {"summary": "Mentions [[vault-layout|it]]."},
            {"body": "See [[vault-layout|the layout]] and [[vault-layout#Part|x]].\n"},
            {"body": "Code `[[vault-layout]]` here.\n"},
            {"body": "```\n[[vault-layout]]\n```\n"},
        ],
        ids=[
            "hub",
            "related",
            "superseded_by",
            "wikilink",
            "rationale",
            "title",
            "summary",
            "alias",
            "code-span",
            "code-block",
        ],
    )
    def test_a_link_to_a_restricted_note_skips_the_curator(self, kit: Kit, content: dict[str, Any]) -> None:
        curator = FakeCurator()
        kit.curator = curator
        pid = self.validated_content(kit, **content)
        self.assert_skipped(kit, curator, pid)

    def test_a_link_proposal_to_a_restricted_target_skips_the_curator(self, kit: Kit) -> None:
        curator = FakeCurator()
        kit.curator = curator
        content = ProposedContent(related=(SSH,))
        pid = self.validated_on(kit, ProposalKind.LINK, VAULT, content)
        self.assert_skipped(kit, curator, pid)

    def test_a_deprecation_superseded_by_a_restricted_note_skips_the_curator(self, kit: Kit) -> None:
        curator = FakeCurator()
        kit.curator = curator
        content = ProposedContent(superseded_by=VAULT)
        pid = self.validated_on(kit, ProposalKind.DEPRECATE, SSH, content)
        self.assert_skipped(kit, curator, pid)

    def test_unknown_link_targets_are_not_restricted(self, kit: Kit) -> None:
        curator = FakeCurator()
        kit.curator = curator
        pid = self.validated_content(kit, body="See [[does-not-exist]].\n", related=(NID("also-missing"),))
        assert kit.curate(pid).status is S.NEEDS_OWNER
        assert len(curator.requests) == 1
        assert kit.stored(pid).curated_by == "curator/fake"

    def test_a_proposal_that_touches_no_restricted_note_still_reaches_the_curator(self, kit: Kit) -> None:
        curator = FakeCurator(Decision.create(NEW_ID, "new topic"))
        kit.curator = curator
        pid = kit.submit_create()
        kit.validate(pid)
        assert kit.curate(pid).status is S.CURATED
        assert len(curator.requests) == 1
        assert curator.requests[0].proposal.id == pid

    def test_the_curator_never_gets_restricted_similar_hits_even_for_an_owner_with_a_restricted_ceiling(
        self, kit: Kit
    ) -> None:
        curator = FakeCurator()
        kit.curator = curator
        pid = self.validated_content(kit, title="Vault secrets layout", summary="Where the vault lives.")
        kit.curate_uc().execute(ProposalIdRequest(proposal_id=pid), owner_ctx(Sensitivity.RESTRICTED))
        assert len(curator.requests) == 1
        similar = curator.requests[0].similar
        assert all(hit.sensitivity <= Sensitivity.INTERNAL for hit in similar)
        assert all(hit.id != VAULT for hit in similar)
        # sanity: the owner's own ceiling does see it
        owner_hits = kit.world.search.find_similar(
            "Vault secrets layout Where the vault lives.", ceiling=owner_ctx().ceiling, limit=5
        )
        assert any(hit.id == VAULT for hit in owner_hits)

    def test_an_internal_target_is_visible_to_the_curator_with_a_restricted_ceiling(self, kit: Kit) -> None:
        curator = FakeCurator()
        kit.curator = curator
        pid = kit.submit_update()
        kit.validate(pid)
        kit.curate_uc().execute(ProposalIdRequest(proposal_id=pid), owner_ctx(Sensitivity.RESTRICTED))
        target = curator.requests[0].target
        assert target is not None
        assert target.id == SSH

    def test_the_target_references_to_restricted_notes_are_removed(self, kit: Kit) -> None:
        curator = FakeCurator()
        kit.curator = curator
        ssh = kit.world.notes.get(SSH)
        assert ssh is not None
        leaky = replace(ssh, related=(VAULT, NID("core-rules")), superseded_by=VAULT, hub=VAULT)
        kit.world.notes.apply([NoteChange(note=leaky, expected_hash=content_hash(ssh))], SEED_MESSAGE)
        pid = self.validated_on(kit, ProposalKind.UPDATE, SSH, ProposedContent(body="# SSH\n\nNew.\n"))
        kit.curate_uc().execute(ProposalIdRequest(proposal_id=pid), owner_ctx(Sensitivity.RESTRICTED))
        target = curator.requests[0].target
        assert target is not None
        assert (target.hub, target.superseded_by) == (None, None)
        assert target.related == (NID("core-rules"),)

    def test_the_target_body_given_to_the_curator_has_restricted_links_redacted(self, kit: Kit) -> None:
        curator = FakeCurator()
        kit.curator = curator
        ssh = kit.world.notes.get(SSH)
        assert ssh is not None
        leaky = replace(ssh, body="# SSH\n\nSee [[vault-layout|layout]].\n")
        kit.world.notes.apply([NoteChange(note=leaky, expected_hash=content_hash(ssh))], SEED_MESSAGE)
        pid = kit.submit_update()
        kit.validate(pid)
        kit.curate_uc().execute(ProposalIdRequest(proposal_id=pid), owner_ctx(Sensitivity.RESTRICTED))
        target = curator.requests[0].target
        assert target is not None
        assert "vault-layout" not in target.body


# -- ReviewProposal ------------------------------------------------------------------------------------------------


class TestProposalRevisions:
    def test_every_saved_transition_bumps_the_revision(self, kit: Kit) -> None:
        pid = kit.submit_create()
        assert kit.stored(pid).revision == 0
        kit.validate(pid)
        assert kit.stored(pid).revision == 1
        kit.curate(pid)
        assert kit.stored(pid).revision == 2
        kit.review(pid)
        assert kit.stored(pid).revision == 3
        kit.apply(pid)
        assert kit.stored(pid).revision == 4

    def test_a_racing_reject_beats_an_approval_that_loaded_the_stale_copy(self, kit: Kit) -> None:
        pid = kit.submit_create()
        kit.validate(pid)
        kit.curate(pid)
        kit.race_after_next_get(lambda: kit.review(pid, ReviewAction.REJECT, reason="no"))
        with pytest.raises(ProposalConflict):
            kit.review(pid)
        stored = kit.stored(pid)
        assert (stored.status, stored.approved_by) == (S.REJECTED, None)

    def test_two_racing_validations_cannot_both_win(self, kit: Kit) -> None:
        pid = kit.submit_create()
        kit.race_after_next_get(lambda: kit.validate(pid))
        with pytest.raises(ProposalConflict):
            kit.validate(pid)

    def test_two_racing_curations_cannot_both_win(self, kit: Kit) -> None:
        pid = kit.submit_create()
        kit.validate(pid)
        kit.race_after_next_get(lambda: kit.curate(pid))
        with pytest.raises(ProposalConflict):
            kit.curate(pid)


class TestReviewProposal:
    def curated(self, kit: Kit, pid: str) -> str:
        kit.validate(pid)
        kit.curate(pid)
        return pid

    def test_owner_principal_is_required(self, kit: Kit) -> None:
        pid = self.curated(kit, kit.submit_create())
        request = ReviewProposalRequest(proposal_id=pid, action=ReviewAction.APPROVE)
        with pytest.raises(PermissionDenied):
            kit.review_uc().execute(request, agent_ctx())
        assert kit.stored(pid).status is S.NEEDS_OWNER

    def test_approving_records_the_owner(self, kit: Kit) -> None:
        pid = self.curated(kit, kit.submit_update())
        outcome = kit.review(pid)
        assert outcome.status is S.APPROVED
        stored = kit.stored(pid)
        assert (stored.approved_by, stored.curated_by) == ("owner", "curator/null")

    def test_a_create_needs_the_decision_to_name_the_note(self, kit: Kit) -> None:
        pid = self.curated(kit, kit.submit_create())
        assert kit.review(pid).status is S.APPROVED  # the null curator suggested the id from the title

    def test_override_replaces_the_decision(self, kit: Kit) -> None:
        pid = self.curated(kit, kit.submit_create())
        other = NID("docker-compose-conventions")
        outcome = kit.review(pid, override=Decision.create(other))
        assert outcome.decision == Decision.create(other)
        assert kit.stored(pid).decision == Decision.create(other)

    def test_override_cannot_redirect_an_update_and_changes_nothing(self, kit: Kit) -> None:
        pid = self.curated(kit, kit.submit_update())
        before = kit.stored(pid)
        with pytest.raises(ProposalNotApplicable):
            kit.review(pid, override=Decision.merge_into(NID("core-rules")))
        assert kit.stored(pid) == before

    def test_override_with_a_rejection_cannot_be_approved(self, kit: Kit) -> None:
        pid = self.curated(kit, kit.submit_update())
        with pytest.raises(ProposalNotApplicable):
            kit.review(pid, override=Decision.reject("no"))
        assert kit.stored(pid).status is S.NEEDS_OWNER

    def test_a_create_decision_cannot_be_approved_for_an_update(self, kit: Kit) -> None:
        pid = self.curated(kit, kit.submit_update())
        with pytest.raises(ProposalNotApplicable):
            kit.review(pid, override=Decision.create(NID("anything")))

    def test_rejecting_needs_a_reason_and_stores_it(self, kit: Kit) -> None:
        pid = self.curated(kit, kit.submit_create())
        with pytest.raises(InvalidValueError, match="reason"):
            ReviewProposalRequest(proposal_id=pid, action=ReviewAction.REJECT)
        outcome = kit.review(pid, ReviewAction.REJECT, reason="not useful")
        assert outcome.status is S.REJECTED
        assert kit.stored(pid).decision == Decision.reject("not useful")

    def test_rejecting_cannot_carry_an_override(self) -> None:
        with pytest.raises(InvalidValueError, match="override"):
            ReviewProposalRequest(
                proposal_id="p", action=ReviewAction.REJECT, reason="x", override=Decision.create(NEW_ID)
            )

    def test_request_validation(self) -> None:
        with pytest.raises(InvalidValueError):
            ReviewProposalRequest(proposal_id="", action=ReviewAction.APPROVE)
        with pytest.raises(InvalidValueError):
            ReviewProposalRequest(proposal_id="p", action="approve")  # type: ignore[arg-type]
        with pytest.raises(InvalidValueError):
            ReviewProposalRequest(proposal_id="p", action=ReviewAction.APPROVE, reason=3)  # type: ignore[arg-type]
        with pytest.raises(InvalidValueError):
            ReviewProposalRequest(proposal_id="p", action=ReviewAction.APPROVE, override="x")  # type: ignore[arg-type]

    def test_wrong_status_and_unknown_proposal(self, kit: Kit) -> None:
        with pytest.raises(ProposalNotFound):
            kit.review("nope")
        with pytest.raises(InvalidTransition):
            kit.review(kit.submit_create())  # received
        pid = self.curated(kit, kit.submit_create())
        kit.review(pid)
        with pytest.raises(InvalidTransition):
            kit.review(pid)  # twice

    def test_hidden_targets_are_not_found(self, kit: Kit) -> None:
        pid = self.curated(kit, kit.submit_update())
        request = ReviewProposalRequest(proposal_id=pid, action=ReviewAction.APPROVE)
        with pytest.raises(NoteNotFound):
            kit.review_uc().execute(request, owner_ctx(PUBLIC))


# -- ApplyProposal -------------------------------------------------------------------------------------------------


def edit_note(kit: Kit, **changes: Any) -> Note:
    """The owner changes ``server-ssh-access`` behind the back of a pending proposal."""
    current = kit.world.notes.get(SSH)
    assert current is not None
    request = OwnerEditNoteRequest(note=replace(current, **changes), expected_hash=content_hash(current))
    kit.edit_uc().execute(request, owner_ctx())
    changed = kit.world.notes.get(SSH)
    assert changed is not None
    return changed


class TestApplyProposal:
    def test_owner_principal_is_required(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_update())
        with pytest.raises(PermissionDenied):
            kit.apply(pid, agent_ctx())
        assert kit.stored(pid).status is S.APPROVED
        assert kit.commits == 5

    def test_create_writes_the_note_with_the_trailers(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_create())
        result = kit.apply(pid)
        assert (result.outcome, result.status, result.note_id) == (ApplyOutcome.APPLIED, S.APPLIED, NEW_ID)
        note = kit.world.notes.get(NEW_ID)
        assert note is not None
        assert (note.status, note.created, note.updated, note.lang) == (
            Status.CURRENT,
            date(2026, 11, 25),
        ) * 1 + (
            date(2026, 11, 25),
            "en",
        )
        revision, message, ids = kit.world.notes.commits[-1]
        assert (revision, ids) == (result.revision, (NEW_ID,))
        assert message.render() == (
            "Add note: docker-conventions\n\n"
            "Proposed-by: claude-code\nCurated-by: curator/null\nApproved-by: owner\n"
            f"Proposal-Id: {pid}"
        )
        assert kit.stored(pid).status is S.APPLIED
        # the derived indexes follow
        assert [h.id for h in kit.world.search.search(_query("compose"))] == [NEW_ID]
        assert NEW_ID in {n.id for n in kit.world.graph.graph(ceiling=owner_ctx().ceiling).nodes}

    def test_update_keeps_created_and_sets_updated(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_update(body="# SSH\n\nBrand new.\n"))
        result = kit.apply(pid)
        assert result.outcome is ApplyOutcome.APPLIED
        note = kit.world.notes.get(SSH)
        assert note is not None
        assert note.body == "# SSH\n\nBrand new.\n"
        assert (note.created, note.updated) == (date(2026, 11, 24), date(2026, 11, 25))
        assert kit.world.notes.commits[-1][1].subject == "Update note: server-ssh-access"

    def test_update_changes_only_what_it_carries(self, kit: Kit) -> None:
        before = kit.world.notes.get(SSH)
        assert before is not None
        pid = kit.approved(kit.submit_update(title="SSH access, revised", related=(NID("core-rules"),)))
        kit.apply(pid)
        after = kit.world.notes.get(SSH)
        assert after is not None
        assert after.title == "SSH access, revised"
        assert after.related == (*before.related, NID("core-rules"))
        assert (after.hub, after.summary, after.sensitivity) == (before.hub, before.summary, before.sensitivity)

    def test_link_and_deprecate(self, kit: Kit) -> None:
        link = SubmitProposalRequest(
            kind=ProposalKind.LINK,
            target=SSH,
            base_hash=note_hash(kit),
            content=ProposedContent(related=(NID("core-rules"),)),
        )
        pid = kit.approved(kit.submit(link).proposal_id)
        kit.apply(pid)
        linked = kit.world.notes.get(SSH)
        assert linked is not None
        assert NID("core-rules") in linked.related
        assert kit.world.notes.commits[-1][1].subject == "Link note: server-ssh-access"

        deprecate = SubmitProposalRequest(
            kind=ProposalKind.DEPRECATE,
            target=SSH,
            base_hash=note_hash(kit),
            content=ProposedContent(superseded_by=NID("core-rules")),
        )
        pid = kit.approved(kit.submit(deprecate).proposal_id)
        kit.apply(pid)
        gone = kit.world.notes.get(SSH)
        assert gone is not None
        assert (gone.status, gone.superseded_by) == (Status.SUPERSEDED, NID("core-rules"))

    def test_deprecate_without_a_successor_archives(self, kit: Kit) -> None:
        request = SubmitProposalRequest(
            kind=ProposalKind.DEPRECATE, target=SSH, base_hash=note_hash(kit), content=ProposedContent()
        )
        kit.apply(kit.approved(kit.submit(request).proposal_id))
        note = kit.world.notes.get(SSH)
        assert note is not None
        assert note.status is Status.ARCHIVED

    def test_conflict_sends_the_proposal_back_and_writes_nothing(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_update())
        changed = edit_note(kit, body="# SSH\n\nThe owner got there first.\n")
        commits = kit.commits
        result = kit.apply(pid)
        assert (result.outcome, result.status) == (ApplyOutcome.CONFLICT, S.NEEDS_OWNER)
        stored = kit.stored(pid)
        assert (stored.status, stored.conflict, stored.approved_by) == (S.NEEDS_OWNER, True, None)
        assert kit.commits == commits
        assert kit.world.notes.get(SSH) == changed

    def test_a_conflicted_proposal_cannot_be_approved_again(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_update())
        edit_note(kit, body="# SSH\n\nChanged.\n")
        kit.apply(pid)
        with pytest.raises(InvalidTransition):
            kit.review(pid)
        assert kit.review(pid, ReviewAction.REJECT, reason="stale").status is S.REJECTED

    def test_applying_twice_is_safe(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_create())
        kit.apply(pid)
        commits = kit.commits
        again = kit.apply(pid)
        assert (again.outcome, again.status, again.note_id) == (ApplyOutcome.ALREADY_APPLIED, S.APPLIED, NEW_ID)
        assert kit.commits == commits

    @pytest.mark.parametrize("create", [True, False])
    def test_a_retry_after_a_lost_save_does_not_commit_twice(self, kit: Kit, create: bool) -> None:
        pid = kit.approved(kit.submit_create() if create else kit.submit_update())
        saves = kit.store.save

        def failing_save(proposal: Any, *, expected_revision: int) -> Any:
            if proposal.status is S.APPLIED:
                kit.store.save = saves  # type: ignore[method-assign]
                raise RuntimeError("store unavailable")
            return saves(proposal, expected_revision=expected_revision)

        kit.store.save = failing_save  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            kit.apply(pid)
        assert kit.commits == 6
        assert kit.stored(pid).status is S.APPROVED
        retry = kit.apply(pid)
        assert (retry.outcome, retry.status) == (ApplyOutcome.ALREADY_APPLIED, S.APPLIED)
        assert kit.commits == 6

    def test_two_racing_applies_commit_once_and_both_end_applied(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_create())
        commits = kit.commits
        winner: list[Any] = []
        kit.race_after_next_get(lambda: winner.append(kit.apply(pid)))
        loser = kit.apply(pid)  # loaded the approved copy, then the other apply finished
        assert winner[0].outcome is ApplyOutcome.APPLIED
        assert (loser.outcome, loser.status) == (ApplyOutcome.ALREADY_APPLIED, S.APPLIED)
        assert kit.commits == commits + 1
        assert kit.stored(pid).status is S.APPLIED

    def test_a_send_back_that_lost_the_race_to_an_applied_proposal_reports_already_applied(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_update())
        stored = kit.stored(pid)
        commits = kit.commits

        def other_apply_finished() -> None:
            current = kit.world.notes.get(SSH)
            assert current is not None
            change = NoteChange(note=replace(current, body="raced\n"), expected_hash=content_hash(current))
            kit.world.notes.apply([change], kit.world.notes.commits[0][1])
            kit.overwrite(stored.mark_applied(stored.updated_at))

        kit.race_after_next_get(other_apply_finished)
        result = kit.apply(pid)  # sees a changed target and would send the proposal back
        assert (result.outcome, result.status) == (ApplyOutcome.ALREADY_APPLIED, S.APPLIED)
        assert kit.commits == commits + 1  # only the racing writer's commit
        assert kit.stored(pid).status is S.APPLIED

    def test_a_lost_proposal_race_after_the_commit_is_not_swallowed(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_create())
        commits = kit.commits
        stored = kit.stored(pid)
        kit.race_after_next_get(lambda: kit.overwrite(stored.mark_needs_owner(stored.updated_at, conflict=True)))
        with pytest.raises(ProposalConflict):
            kit.apply(pid)
        assert kit.commits == commits + 1  # the note is committed; the owner sees the proposal as needs_owner
        assert kit.stored(pid).status is S.NEEDS_OWNER

    def test_a_concurrent_writer_between_check_and_write_is_a_conflict(self, kit: Kit) -> None:
        class Racing(InMemoryNoteRepository):
            armed = False

            def apply(self, changes: Any, message: Any) -> str:
                if self.armed:
                    self.armed = False  # another writer commits first, once
                    current = self.get(SSH)
                    assert current is not None
                    other = NoteChange(note=replace(current, body="raced\n"), expected_hash=content_hash(current))
                    super().apply([other], message)
                return super().apply(changes, message)

        racing = Racing()
        for note in kit.world.notes.list_notes():
            racing.apply([NoteChange(note=note)], kit.world.notes.commits[0][1])
        kit.world.notes = racing
        pid = kit.approved(kit.submit_update())
        racing.armed = True
        commits = len(racing.commits)
        result = kit.apply(pid)
        assert result.outcome is ApplyOutcome.CONFLICT
        assert len(racing.commits) == commits + 1  # only the racing writer's commit
        assert kit.stored(pid).conflict

    def test_create_over_an_existing_note_is_a_conflict(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_create(), override=Decision.create(NID("core-rules")))
        before = kit.world.notes.get(NID("core-rules"))
        result = kit.apply(pid)
        assert result.outcome is ApplyOutcome.CONFLICT
        assert kit.world.notes.get(NID("core-rules")) == before

    def test_create_over_a_hidden_note_is_not_found(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_create(), override=Decision.create(NID("vault-layout")))
        with pytest.raises(NoteNotFound):
            kit.apply(pid, owner_ctx(INTERNAL))
        assert kit.stored(pid).status is S.APPROVED

    def test_target_mismatch_is_refused_and_nothing_is_written(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_update())
        tampered = replace(kit.stored(pid), decision=Decision.merge_into(NID("core-rules")))
        kit.overwrite(tampered)
        before = kit.world.notes.get(NID("core-rules"))
        with pytest.raises(ProposalNotApplicable):
            kit.apply(pid)
        assert kit.world.notes.get(NID("core-rules")) == before
        assert kit.commits == 5
        assert kit.stored(pid).status is S.APPROVED

    @pytest.mark.parametrize(
        ("update", "decision"),
        [
            (True, Decision.reject("no")),
            (True, Decision.create(NID("core-rules"))),
            (False, Decision.merge_into(SSH)),
            (False, Decision.needs_owner("x")),  # names no note to create
        ],
    )
    def test_decisions_of_the_wrong_shape_are_refused(self, kit: Kit, update: bool, decision: Decision) -> None:
        pid = kit.approved(kit.submit_update() if update else kit.submit_create())
        kit.overwrite(replace(kit.stored(pid), decision=decision))
        with pytest.raises(ProposalNotApplicable):
            kit.apply(pid)
        assert kit.commits == 5

    @pytest.mark.parametrize("status", [S.RECEIVED, S.VALIDATED, S.CURATED, S.NEEDS_OWNER, S.REJECTED])
    def test_only_approved_proposals_are_applied(self, kit: Kit, status: ProposalStatus) -> None:
        pid = kit.submit_create()
        if status is not S.RECEIVED:
            kit.validate(pid)
        if status in (S.CURATED, S.NEEDS_OWNER, S.REJECTED):
            kit.curate(pid)
        if status is S.REJECTED:
            kit.review(pid, ReviewAction.REJECT, reason="no")
        with pytest.raises(InvalidTransition):
            kit.apply(pid)
        assert kit.commits == 5

    def test_content_is_validated_again_right_before_writing(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_create())
        invalid = replace(kit.stored(pid), content=new_content(summary="too short"))
        kit.overwrite(invalid)
        result = kit.apply(pid)
        assert (result.outcome, result.status) == (ApplyOutcome.INVALID, S.NEEDS_OWNER)
        assert result.validation is not None
        assert ViolationCode.SUMMARY_LENGTH in result.validation.codes
        assert kit.commits == 5
        assert kit.world.notes.get(NEW_ID) is None

    def test_a_secret_in_approved_content_is_never_written_or_echoed(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_create())
        leaked = replace(kit.stored(pid), content=new_content(body=f"password = '{SECRET}'\n"))
        kit.overwrite(leaked)
        result = kit.apply(pid)
        assert result.outcome is ApplyOutcome.INVALID
        assert result.validation is not None
        assert ViolationCode.SECRET_DETECTED in result.validation.codes
        assert SECRET not in repr(result)
        assert kit.commits == 5

    def test_content_that_cannot_be_built_is_sent_back(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_create())
        kit.overwrite(replace(kit.stored(pid), content=new_content(summary=None)))
        result = kit.apply(pid)
        assert result.outcome is ApplyOutcome.INVALID
        assert kit.commits == 5

    def test_hidden_targets_are_not_found(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_update())
        with pytest.raises(NoteNotFound):
            kit.apply(pid, owner_ctx(PUBLIC))
        assert kit.commits == 5

    def test_the_owner_ceiling_limits_what_can_be_written(self, kit: Kit) -> None:
        pid = kit.submit(create_request(sensitivity=RESTRICTED), agent_ctx(RESTRICTED)).proposal_id
        kit.approved(pid)
        with pytest.raises(PermissionDenied):
            kit.apply(pid, owner_ctx(INTERNAL))
        assert kit.commits == 5

    def test_unknown_proposal(self, kit: Kit) -> None:
        with pytest.raises(ProposalNotFound):
            kit.apply("nope")

    def test_a_failure_to_write_leaves_the_proposal_approved(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_create())

        def boom(*_: Any) -> str:
            raise OSError("disk full")

        kit.world.notes.apply = boom  # type: ignore[method-assign,assignment]
        with pytest.raises(OSError, match="disk full"):
            kit.apply(pid)
        assert kit.stored(pid).status is S.APPROVED
        assert kit.world.notes.get(NEW_ID) is None


# -- OwnerEditNote -------------------------------------------------------------------------------------------------


class TestOwnerEditNote:
    def edit(self, kit: Kit, note: Note, expected: str | None, c: Any = None) -> Any:
        return kit.edit_uc().execute(OwnerEditNoteRequest(note=note, expected_hash=expected), c or owner_ctx())

    def test_only_the_owner_edits(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        with pytest.raises(PermissionDenied):
            self.edit(kit, current, note_hash(kit), agent_ctx())
        assert kit.commits == 5

    def test_edit_commits_with_owner_trailers(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        result = self.edit(kit, replace(current, body="# SSH\n\nEdited.\n", created=date(2000, 1, 1)), note_hash(kit))
        assert result.revision is not None
        assert result.content_hash == note_hash(kit)
        note = kit.world.notes.get(SSH)
        assert note is not None
        assert (note.created, note.updated, note.body) == (date(2026, 11, 24), date(2026, 11, 25), "# SSH\n\nEdited.\n")
        message = kit.world.notes.commits[-1][1]
        assert message.render() == (
            "Update note: server-ssh-access\n\n"
            "Proposed-by: owner\nCurated-by: owner\nApproved-by: owner\n"
            "Proposal-Id: owner-edit-proposal-0001"
        )
        assert [h.id for h in kit.world.search.search(_query("Edited"))] == [SSH]

    def test_stale_hash_is_a_conflict_and_writes_nothing(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        stale = note_hash(kit)
        edit_note(kit, body="# SSH\n\nSomeone else.\n")
        commits = kit.commits
        with pytest.raises(ContentConflict):
            self.edit(kit, replace(current, body="mine\n"), stale)
        assert kit.commits == commits

    def test_missing_and_hidden_notes_are_not_found(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        ghost = replace(current, id=NID("ghost-note"))
        with pytest.raises(NoteNotFound):
            self.edit(kit, ghost, "a" * 64)
        hidden = kit.world.notes.get(NID("vault-layout"))
        assert hidden is not None
        with pytest.raises(NoteNotFound):
            self.edit(kit, hidden, content_hash(hidden), owner_ctx(PUBLIC))

    def test_create(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        fresh = replace(current, id=NEW_ID, related=(), created=date(2000, 1, 1))
        result = self.edit(kit, fresh, None)
        assert result.revision is not None
        stored = kit.world.notes.get(NEW_ID)
        assert stored is not None
        assert stored.created == date(2026, 11, 25)
        assert kit.world.notes.commits[-1][1].subject == "Add note: docker-conventions"

    def test_create_over_an_existing_note_conflicts(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        with pytest.raises(ContentConflict):
            self.edit(kit, current, None)
        hidden = kit.world.notes.get(NID("vault-layout"))
        assert hidden is not None
        with pytest.raises(NoteNotFound):
            self.edit(kit, hidden, None, owner_ctx(PUBLIC))
        assert kit.commits == 5

    @pytest.mark.parametrize("where", ["title", "summary", "body"])
    def test_secrets_in_frontmatter_and_body_are_errors(self, kit: Kit, where: str) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        value = f"api_key = '{SECRET}' " + VALID_SUMMARY if where != "body" else f"token {SECRET}\n"
        edited: Note = {
            "title": replace(current, title=value),
            "summary": replace(current, summary=value),
            "body": replace(current, body=value),
        }[where]
        result = self.edit(kit, edited, note_hash(kit))
        assert result.revision is None
        assert ViolationCode.SECRET_DETECTED in result.validation.codes
        assert SECRET not in repr(result)
        assert kit.commits == 5

    def test_broken_links_only_warn_for_the_owner(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        result = self.edit(kit, replace(current, body="See [[not-written-yet]].\n"), note_hash(kit))
        assert result.revision is not None
        assert ViolationCode.BROKEN_LINK in {v.code for v in result.validation.warnings}

    @pytest.mark.parametrize("kind", ["body", "related", "superseded_by", "hub"])
    def test_links_to_a_higher_sensitivity_are_errors_for_the_owner_too(self, kit: Kit, kind: str) -> None:
        current = kit.world.notes.get(SSH)  # internal; vault-layout is restricted
        assert current is not None
        options: dict[str, dict[str, Any]] = {
            "body": {"body": "See [[vault-layout]].\n"},
            "related": {"related": (NID("vault-layout"),)},
            "superseded_by": {
                "superseded_by": NID("vault-layout"),
                "status": Status.SUPERSEDED,
                "priority": Priority.P3,
            },
            "hub": {"hub": NID("vault-layout")},
        }
        commits = kit.commits
        result = self.edit(kit, replace(current, **options[kind]), note_hash(kit))
        assert result.revision is None
        assert ViolationCode.LINK_TO_HIGHER_SENSITIVITY in {v.code for v in result.validation.errors}
        assert ViolationCode.LINK_TO_HIGHER_SENSITIVITY not in {v.code for v in result.validation.warnings}
        assert kit.commits == commits
        messages = [
            v.message for v in result.validation.violations if v.code is ViolationCode.LINK_TO_HIGHER_SENSITIVITY
        ]
        assert [m.endswith(" may not be linked from this note") for m in messages] == [True]
        assert "vault-layout" not in messages[0]

    @pytest.mark.parametrize("body", ["`[[vault-layout]]`", "[[vault-layout|the vault]]"])
    def test_alias_and_code_span_links_are_errors_for_the_owner(self, kit: Kit, body: str) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        result = self.edit(kit, replace(current, body=body + "\n"), note_hash(kit))
        assert result.revision is None
        assert ViolationCode.LINK_TO_HIGHER_SENSITIVITY in {v.code for v in result.validation.errors}

    def test_raising_sensitivity_is_refused_while_lower_notes_link_to_the_note(self, kit: Kit) -> None:
        # the write kit's data obeys the rule, so add a public note that links to the internal one
        kit.world.add(id=NID("linker"), hub=NID("infrastructure"), body="See [[server-ssh-access]].\n")
        current = kit.world.notes.get(SSH)  # internal
        assert current is not None
        commits = kit.commits
        result = self.edit(kit, replace(current, sensitivity=RESTRICTED), note_hash(kit))
        assert result.revision is None
        assert "linker links to this note" in [v.message for v in result.validation.errors]
        assert kit.commits == commits

    def test_an_edit_that_does_not_raise_sensitivity_is_not_blocked_by_legacy_inbound_links(self, kit: Kit) -> None:
        kit.world.add(id=NID("linker"), hub=NID("infrastructure"), body="See [[server-ssh-access]].\n")
        current = kit.world.notes.get(SSH)  # internal, linked from the public "linker"
        assert current is not None
        unrelated = self.edit(kit, replace(current, body="# SSH\n\nEdited.\n"), note_hash(kit))
        assert unrelated.revision is not None
        again = kit.world.notes.get(SSH)
        assert again is not None
        # equal sensitivity and lowering are fine too
        assert self.edit(kit, replace(again, sensitivity=PUBLIC), note_hash(kit)).revision is not None

    def test_creating_a_note_that_lower_notes_link_to_is_refused_for_the_owner(self, kit: Kit) -> None:
        kit.world.add(id=NID("linker"), hub=NID("infrastructure"), body="See [[not-yet]].\n")
        current = kit.world.notes.get(SSH)
        assert current is not None
        new = replace(current, id=NID("not-yet"), related=(), sensitivity=INTERNAL)
        result = self.edit(kit, new, None)
        assert result.revision is None
        assert [v.message for v in result.validation.errors] == ["linker links to this note"]

    def test_raising_a_hubs_sensitivity_is_refused_while_members_point_at_it(self, kit: Kit) -> None:
        hub = kit.world.notes.get(NID("infrastructure"))  # public hub with public/internal members
        assert hub is not None
        result = self.edit(kit, replace(hub, sensitivity=RESTRICTED), note_hash(kit, NID("infrastructure")))
        assert result.revision is None
        assert ViolationCode.LINK_TO_HIGHER_SENSITIVITY in {v.code for v in result.validation.errors}

    @pytest.mark.parametrize("field", ["title", "summary"])
    def test_wikilinks_in_title_or_summary_are_errors_for_the_owner(self, kit: Kit, field: str) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        value = "About [[core-rules]]" if field == "title" else VALID_SUMMARY + " [[core-rules]]"
        changes: dict[str, Any] = {field: value}
        result = self.edit(kit, replace(current, **changes), note_hash(kit))
        assert result.revision is None
        assert ViolationCode.WIKILINK_IN_METADATA in {v.code for v in result.validation.errors}

    def test_the_owner_may_link_to_equal_or_lower_sensitivity(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        result = self.edit(kit, replace(current, body="See [[core-rules]] and [[infrastructure]].\n"), note_hash(kit))
        assert result.revision is not None

    def test_invalid_notes_are_not_written(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        result = self.edit(kit, replace(current, summary="short"), note_hash(kit))
        assert result.revision is None
        assert ViolationCode.SUMMARY_LENGTH in result.validation.codes
        assert kit.commits == 5

    def test_the_new_sensitivity_may_not_exceed_the_ceiling(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        with pytest.raises(PermissionDenied):
            self.edit(kit, replace(current, sensitivity=RESTRICTED), note_hash(kit), owner_ctx(INTERNAL))
        assert kit.commits == 5

    def test_the_owner_may_lower_sensitivity(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        assert self.edit(kit, replace(current, sensitivity=PUBLIC), note_hash(kit)).revision is not None


# -- support -------------------------------------------------------------------------------------------------------


def _query(text: str) -> Any:
    from cerebro.application.ports.search import SearchQuery

    return SearchQuery(ceiling=owner_ctx().ceiling, text=text)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Docker conventions", "docker-conventions"),
        ("  Café -- Déjà vu!  ", "cafe-deja-vu"),
        ("???", None),
        ("", None),
        (None, None),
        ("x" * 200, "x" * 80),
    ],
)
def test_provisional_note_id(title: str | None, expected: str | None) -> None:
    result = provisional_note_id(title)
    assert (result.value if result else None) == expected


def test_submit_request_validation() -> None:
    with pytest.raises(InvalidValueError):
        SubmitProposalRequest(kind="create", content=new_content())  # type: ignore[arg-type]
    with pytest.raises(InvalidValueError):
        SubmitProposalRequest(kind=ProposalKind.CREATE, content="x")  # type: ignore[arg-type]
    with pytest.raises(InvalidValueError):
        SubmitProposalRequest(kind=ProposalKind.CREATE, content=new_content(), target="x")  # type: ignore[arg-type]
    with pytest.raises(InvalidValueError):
        SubmitProposalRequest(kind=ProposalKind.CREATE, content=new_content(), base_hash=1)  # type: ignore[arg-type]
    with pytest.raises(InvalidValueError):
        SubmitProposalRequest(kind=ProposalKind.CREATE, content=new_content(), source_context="x" * 2001)


def test_other_request_validation() -> None:
    with pytest.raises(InvalidValueError):
        ProposalIdRequest(proposal_id="")
    with pytest.raises(InvalidValueError):
        OwnerEditNoteRequest(note="x")  # type: ignore[arg-type]
    with pytest.raises(InvalidValueError):
        OwnerEditNoteRequest(note=_a_note(), expected_hash=3)  # type: ignore[arg-type]


def _a_note() -> Note:
    from tests.unit.domain.builders import make_note

    return make_note()


def test_build_candidate_findings_never_quote_content() -> None:
    from datetime import date as _date

    from cerebro.application.proposal_support import build_candidate
    from tests.unit.domain.test_proposal import make_proposal, make_update

    today = _date(2026, 11, 25)
    note, found = build_candidate(make_update(), note_id=SSH, base=None, today=today)
    assert note is None
    assert [f.field for f in found] == ["target"]
    content = new_content(superseded_by=NID("core-rules"))
    note, found = build_candidate(make_proposal(content=content), note_id=NEW_ID, base=None, today=today)
    assert note is None
    assert [f.field for f in found] == ["superseded_by"]
    note, found = build_candidate(make_proposal(), note_id=NEW_ID, base=None, today=today)
    assert note is None
    assert {f.field for f in found} == {"type", "area", "priority", "sensitivity", "summary"}


def test_apply_result_is_a_dto() -> None:
    result = ApplyProposalResult(proposal_id="p", status=S.APPLIED, outcome=ApplyOutcome.APPLIED)
    assert result.revision is None
    assert PROPOSE


def test_a_proposal_without_a_decision_has_no_commit_target() -> None:
    from cerebro.application.proposal_support import commit_target
    from tests.unit.domain.test_proposal import make_proposal

    with pytest.raises(ProposalNotApplicable, match="no decision"):
        commit_target(make_proposal())


class TestIndexRevisionOnWrites:
    """The indexes record the revision they reflect; a failed index update after a commit is detectable."""

    @staticmethod
    def stale(kit: Kit) -> bool:
        w = kit.world
        return index_is_stale(w.notes, w.search, w.graph)

    @staticmethod
    def health(kit: Kit) -> Any:
        w = kit.world
        use_case = HealthReport(w.notes, kit.proposals, w.search, w.graph, kit.clock, VOCABULARY)
        return use_case.execute(HealthReportRequest(), owner_ctx())

    def test_the_seeded_world_is_fresh(self, kit: Kit) -> None:
        assert not self.stale(kit)

    def test_apply_advances_both_indexes_to_the_new_head(self, kit: Kit) -> None:
        result = kit.apply(kit.approved(kit.submit_create()))
        assert result.revision == kit.world.notes.head()
        assert kit.world.search.built_from() == kit.world.graph.built_from() == result.revision
        assert not self.stale(kit)

    def test_owner_edit_advances_both_indexes_to_the_new_head(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        result = kit.edit_uc().execute(
            OwnerEditNoteRequest(note=replace(current, body="# SSH\n\nEdited.\n"), expected_hash=note_hash(kit)),
            owner_ctx(),
        )
        assert result.revision == kit.world.notes.head()
        assert kit.world.search.built_from() == kit.world.graph.built_from() == result.revision

    def test_a_failed_index_update_after_apply_leaves_it_stale_until_rebuilt(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_create())

        def boom(ref: Any) -> None:
            raise RuntimeError("graph store down")

        kit.world.graph.upsert = boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            kit.apply(pid)
        assert self.stale(kit)  # committed, search updated, graph not
        assert HealthCode.INDEX_STALE in {i.code for i in self.health(kit).issues}

        del kit.world.graph.upsert  # the store is back
        RebuildIndex(kit.world.notes, kit.world.search, kit.world.graph).execute(RebuildIndexRequest(), owner_ctx())
        assert not self.stale(kit)
        stale_codes = {HealthCode.INDEX_STALE, HealthCode.SEARCH_INDEX_STALE, HealthCode.GRAPH_INDEX_STALE}
        assert not stale_codes & {i.code for i in self.health(kit).issues}

    def test_a_failed_index_update_after_owner_edit_is_flagged(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None

        def boom(note: Any) -> None:
            raise RuntimeError("search store down")

        kit.world.search.upsert = boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            kit.edit_uc().execute(
                OwnerEditNoteRequest(note=replace(current, body="# SSH\n\nEdited.\n"), expected_hash=note_hash(kit)),
                owner_ctx(),
            )
        assert self.stale(kit)

    def test_a_later_successful_write_does_not_hide_an_earlier_failure(self, kit: Kit) -> None:
        pid = kit.approved(kit.submit_create())
        kit.world.search.upsert = lambda note: (_ for _ in ()).throw(RuntimeError("down"))  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            kit.apply(pid)
        del kit.world.search.upsert
        current = kit.world.notes.get(SSH)
        assert current is not None
        kit.edit_uc().execute(
            OwnerEditNoteRequest(note=replace(current, body="# SSH\n\nEdited.\n"), expected_hash=note_hash(kit)),
            owner_ctx(),
        )
        assert kit.world.graph.built_from() != kit.world.notes.head()  # not advanced past the missed commit
        assert kit.world.search.built_from() != kit.world.notes.head()
        assert self.stale(kit)
