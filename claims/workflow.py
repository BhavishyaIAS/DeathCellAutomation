"""Movement of a death claim through the cell.

    REGISTERED -> UNDER_SCRUTINY -> WITH_SS -> WITH_AO -+-> READY_FOR_INWARD -> INWARDED -> SETTLED
                       ^              |          |      |
                       +--- returned -+----------+      +-> PENDING_COMPLIANCE
                       ^                                        |
                       +---------- re-scrutiny on compliance ---+

Dealing Assistant scrutinises, Section Supervisor validates, Accounts Officer
approves.  Every function here checks the role, performs the transition and
writes the audit trail; views only call these.
"""

from django.db import transaction
from django.utils import timezone

from . import letters, rules
from .checklist import initial_answers
from .models import (Claim, ClaimForm, ClaimStatus, Communication, Finding, Role,
                     Route, Scrutiny)

S = ClaimStatus


class WorkflowError(Exception):
    """Raised when a user attempts a step the claim or their role does not allow."""


def _require(condition, message):
    if not condition:
        raise WorkflowError(message)


def holder_role(claim):
    """The role that has to act on the claim next (None when it is closed)."""
    return {
        S.REGISTERED: Role.DA, S.UNDER_SCRUTINY: Role.DA, S.WITH_SS: Role.SS,
        S.WITH_AO: Role.AO, S.PENDING_COMPLIANCE: Role.DA,
        S.READY_FOR_INWARD: Role.DA, S.INWARDED: Role.DA,
    }.get(claim.status)


def can_edit_scrutiny(user, claim):
    """Checklist, findings and drafts may be changed by whoever holds the file."""
    if claim.status not in (S.UNDER_SCRUTINY, S.WITH_SS, S.WITH_AO):
        return False
    return user.has_role(holder_role(claim))


def permissions(user, claim):
    """What this user may do with this claim right now (drives the buttons)."""
    scrutiny = claim.current_scrutiny
    open_blocking = claim.open_findings().exists() if scrutiny else False
    pending = claim.status == S.PENDING_COMPLIANCE
    return {
        "start": claim.status == S.REGISTERED and user.has_role(Role.DA),
        "edit": can_edit_scrutiny(user, claim),
        "submit": claim.status == S.UNDER_SCRUTINY and user.has_role(Role.DA),
        "validate": claim.status == S.WITH_SS and user.has_role(Role.SS),
        "approve": claim.status == S.WITH_AO and user.has_role(Role.AO),
        "send_back": claim.status in (S.WITH_SS, S.WITH_AO)
        and user.has_role(holder_role(claim)),
        "resolve": pending and user.has_role(Role.DA, Role.SS),
        "rescrutiny": pending and user.has_role(Role.DA),
        "proceed_inward": pending and not open_blocking and scrutiny is not None
        and scrutiny.action == Scrutiny.Action.OFFICE_ACTION
        and user.has_role(Role.DA),
        "inward": claim.status == S.READY_FOR_INWARD and user.has_role(Role.DA),
        "settle": claim.status == S.INWARDED and user.has_role(Role.DA, Role.SS),
        "close": claim.is_open and user.has_role(Role.AO),
        "edit_claim": claim.is_open and user.has_role(Role.DA, Role.SS),
    }


# -- scrutiny ----------------------------------------------------------------
def evaluate(scrutiny):
    return rules.evaluate(scrutiny.answers, rules.Ctx.from_claim(scrutiny.claim))


def refresh(scrutiny, user=None):
    """Re-run the rules, then bring the proposed action and drafts in line."""
    result = evaluate(scrutiny)
    rules.sync_findings(scrutiny, result)
    sync_outputs(scrutiny, user)
    return result


def sync_outputs(scrutiny, user=None):
    scrutiny.action = rules.proposed_action(scrutiny)
    scrutiny.save(update_fields=["action", "updated_at"])
    letters.build_drafts(scrutiny, user)


@transaction.atomic
def start_scrutiny(claim, user):
    _require(claim.status == S.REGISTERED, "Scrutiny has already been started.")
    _require(user.has_role(Role.DA), "Only a Dealing Assistant can start scrutiny.")
    scrutiny = Scrutiny.objects.create(claim=claim, round_no=1, prepared_by=user,
                                       answers=initial_answers(claim))
    if claim.assigned_to_id is None:
        claim.assigned_to = user
        claim.save(update_fields=["assigned_to"])
    claim.move_to(S.UNDER_SCRUTINY, user, "Scrutiny started")
    return scrutiny


@transaction.atomic
def save_answers(scrutiny, answers, user):
    _require(can_edit_scrutiny(user, scrutiny.claim),
             "The checklist cannot be changed at this stage.")
    scrutiny.answers = answers
    scrutiny.save(update_fields=["answers", "updated_at"])
    return refresh(scrutiny, user)


@transaction.atomic
def submit(claim, user, note=""):
    _require(claim.status == S.UNDER_SCRUTINY, "The claim is not under scrutiny.")
    _require(user.has_role(Role.DA), "Only a Dealing Assistant can submit scrutiny.")
    scrutiny = claim.current_scrutiny
    result = refresh(scrutiny, user)
    _require(not result.missing,
             f"{len(result.missing)} checklist item(s) are still unanswered: "
             + "; ".join(result.missing[:6])
             + (" …" if len(result.missing) > 6 else ""))
    scrutiny.status = Scrutiny.Status.WITH_SS
    scrutiny.prepared_by = user
    scrutiny.submitted_at = timezone.now()
    scrutiny.da_note = note
    scrutiny.save()
    claim.move_to(S.WITH_SS, user,
                  f"Scrutiny submitted – {scrutiny.get_action_display()}", note)


@transaction.atomic
def validate(claim, user, note=""):
    _require(claim.status == S.WITH_SS, "The claim is not with the Section Supervisor.")
    _require(user.has_role(Role.SS), "Only the Section Supervisor can validate.")
    scrutiny = claim.current_scrutiny
    scrutiny.status = Scrutiny.Status.WITH_AO
    scrutiny.validated_by = user
    scrutiny.validated_at = timezone.now()
    scrutiny.ss_note = note
    scrutiny.save()
    claim.move_to(S.WITH_AO, user, "Scrutiny validated – put up for approval", note)


@transaction.atomic
def send_back(claim, user, note):
    _require(claim.status in (S.WITH_SS, S.WITH_AO), "The claim cannot be returned now.")
    _require(user.has_role(holder_role(claim)), "The file is not with you.")
    _require(note.strip(), "Please record the reason for returning the file.")
    scrutiny = claim.current_scrutiny
    scrutiny.status = Scrutiny.Status.DRAFT
    if claim.status == S.WITH_SS:
        scrutiny.ss_note = note
    else:
        scrutiny.ao_note = note
    scrutiny.save()
    claim.move_to(S.UNDER_SCRUTINY, user, "Returned to Dealing Assistant", note)


@transaction.atomic
def approve(claim, user, note=""):
    _require(claim.status == S.WITH_AO, "The claim is not pending for approval.")
    _require(user.has_role(Role.AO), "Only the Accounts Officer can approve.")
    scrutiny = claim.current_scrutiny
    sync_outputs(scrutiny, user)
    scrutiny.status = Scrutiny.Status.APPROVED
    scrutiny.approved_by = user
    scrutiny.approved_at = timezone.now()
    scrutiny.ao_note = note
    scrutiny.save()
    if not scrutiny.blocking_findings().exists():
        claim.move_to(S.READY_FOR_INWARD, user, "Approved for inward", note)
        return []
    issued = []
    today = timezone.localdate()
    for comm in scrutiny.communications.filter(
            status=Communication.Status.DRAFT).order_by("id"):
        comm.assign_number(today)
        comm.status = Communication.Status.APPROVED
        comm.save(update_fields=["status"])
        issued.append(comm)
    claim.move_to(S.PENDING_COMPLIANCE, user,
                  f"Approved – {scrutiny.get_action_display()}", note)
    return issued


@transaction.atomic
def approve_letter(comm, user):
    """Approve a stand-alone draft (a reminder) outside a scrutiny round."""
    _require(user.has_role(Role.AO), "Only the Accounts Officer can approve letters.")
    _require(comm.status == Communication.Status.DRAFT, "The letter is not a draft.")
    _require(comm.claim.status == S.PENDING_COMPLIANCE or comm.reminder_of_id,
             "This draft is approved along with the scrutiny.")
    comm.assign_number()
    comm.status = Communication.Status.APPROVED
    comm.save(update_fields=["status"])
    comm.claim.log(user, f"{comm.title} approved", comm.number)


@transaction.atomic
def issue_letter(comm, user, when, mode="", due_on=None):
    _require(user.has_role(Role.DA, Role.SS), "Only the cell staff can record dispatch.")
    _require(comm.status == Communication.Status.APPROVED,
             "Only an approved letter can be issued.")
    _require(due_on is None or due_on >= when,
             "The reply-due date cannot be before the date of issue.")
    comm.mark_issued(when, mode, due_on)
    comm.claim.log(user, f"{comm.title} issued", f"No. {comm.number}; {mode}".strip("; "))


@transaction.atomic
def record_reply(comm, user, when, note="", resolve_all=False):
    _require(user.has_role(Role.DA, Role.SS), "Only the cell staff can record replies.")
    _require(comm.status == Communication.Status.ISSUED, "The letter is not awaiting a reply.")
    comm.status = Communication.Status.REPLIED
    comm.reply_on = when
    comm.reply_note = note
    comm.save(update_fields=["status", "reply_on", "reply_note"])
    comm.reminders.filter(status=Communication.Status.ISSUED).update(
        status=Communication.Status.REPLIED, reply_on=when)
    comm.claim.log(user, f"Reply received – {comm.title}", note)
    if resolve_all and comm.scrutiny_id:
        open_points = comm.scrutiny.findings.filter(
            status=Finding.Status.OPEN, text__in=comm.points)
        for finding in open_points:
            resolve_finding(finding, user, when, "Complied – " + (note or "reply received"),
                            log=False)


def resolve_finding(finding, user, when, resolution="", log=True):
    _require(user.has_role(Role.DA, Role.SS), "Only the cell staff can close a finding.")
    finding.status = Finding.Status.RESOLVED
    finding.resolved_on = when
    finding.resolution = resolution[:250]
    finding.save(update_fields=["status", "resolved_on", "resolution"])
    if log:
        finding.claim.log(user, f"Resolved – {finding.title}", resolution)


@transaction.atomic
def start_rescrutiny(claim, user):
    """Open the next scrutiny round once compliance / reports have come in."""
    _require(claim.status == S.PENDING_COMPLIANCE, "The claim is not pending compliance.")
    _require(user.has_role(Role.DA), "Only a Dealing Assistant can re-scrutinise.")
    previous = claim.current_scrutiny
    scrutiny = Scrutiny.objects.create(
        claim=claim, round_no=previous.round_no + 1, prepared_by=user,
        answers=dict(previous.answers))
    carried = previous.findings.filter(status=Finding.Status.OPEN, auto=False)
    for finding in carried:
        Finding.objects.create(
            scrutiny=scrutiny, claim=claim, code=finding.code, point=finding.point,
            category=finding.category, route=finding.route, title=finding.title,
            text=finding.text, other_office=finding.other_office, auto=False,
            edited=True, order=finding.order)
    previous.findings.filter(status=Finding.Status.OPEN).update(
        status=Finding.Status.SUPERSEDED)
    previous.communications.filter(status=Communication.Status.DRAFT).delete()
    claim.move_to(S.UNDER_SCRUTINY, user, f"Re-scrutiny started (round {scrutiny.round_no})")
    refresh(scrutiny, user)
    return scrutiny


@transaction.atomic
def proceed_to_inward(claim, user):
    """Office-side actions done, nothing was called for from outside."""
    _require(permissions(user, claim)["proceed_inward"],
             "Open items remain, or the approved action needs re-scrutiny.")
    claim.move_to(S.READY_FOR_INWARD, user, "Office-side actions completed – ready for inward")


# -- inward and settlement ---------------------------------------------------
FORM_FLAGS = (("form_20", "Form 20"), ("form_10d", "Form 10D"),
              ("form_5if", "Form 5(IF)"), ("form_10c", "Form 10C"))


@transaction.atomic
def record_inward(claim, user, when, claim_ids):
    _require(claim.status == S.READY_FOR_INWARD, "The claim is not ready for inward.")
    _require(user.has_role(Role.DA), "Only a Dealing Assistant can record inward.")
    for flag, name in FORM_FLAGS:
        if getattr(claim, flag):
            ClaimForm.objects.update_or_create(
                claim=claim, form_type=name,
                defaults={"inward_on": when, "app_claim_id": claim_ids.get(name, "")})
    claim.move_to(S.INWARDED, user, "Death claim forms inwarded",
                  ", ".join(f"{k}: {v}" for k, v in claim_ids.items() if v))


@transaction.atomic
def record_settlement(claim, user, rows):
    """rows: {form id: {state, settled_on, amount, reference}}"""
    _require(claim.status == S.INWARDED, "The claim has not been inwarded.")
    _require(user.has_role(Role.DA, Role.SS), "Only the cell staff can record settlement.")
    changed = []
    for form in claim.forms.all():
        data = rows.get(form.id)
        if not data:
            continue
        before = form.state
        for name, value in data.items():
            setattr(form, name, value)
        form.save()
        if form.state != before:
            changed.append(f"{form.form_type}: {form.get_state_display()}")
    if changed:
        claim.log(user, "Settlement updated", "; ".join(changed))
    if not claim.forms.filter(state=ClaimForm.State.PENDING).exists():
        claim.move_to(S.SETTLED, user, "All claim forms disposed")


@transaction.atomic
def close(claim, user, reason):
    _require(claim.is_open, "The claim is already closed.")
    _require(user.has_role(Role.AO), "Only the Accounts Officer can close a claim.")
    _require(reason.strip(), "Please record the reason for closing the claim.")
    claim.closed_reason = reason
    claim.save(update_fields=["closed_reason"])
    claim.move_to(S.CLOSED, user, "Claim closed / returned", reason)


# -- work queues -------------------------------------------------------------
def queue_for(user):
    """Claims waiting on this user's level."""
    qs = Claim.objects.select_related("establishment", "assigned_to")
    if user.is_superuser or user.role == Role.ADMIN:
        return qs.filter(status__in=[S.REGISTERED, S.UNDER_SCRUTINY, S.WITH_SS,
                                     S.WITH_AO, S.READY_FOR_INWARD])
    if user.role == Role.SS:
        return qs.filter(status=S.WITH_SS)
    if user.role == Role.AO:
        return qs.filter(status=S.WITH_AO)
    return qs.filter(status__in=[S.REGISTERED, S.UNDER_SCRUTINY, S.READY_FOR_INWARD])


def pending_with_counts():
    """Claims pending compliance, by who they are waiting on."""
    rows = (Finding.objects.filter(status=Finding.Status.OPEN, included=True,
                                   claim__status=S.PENDING_COMPLIANCE)
            .exclude(route=Route.NOTE).values_list("route", "claim_id").distinct())
    counts = {}
    for route, _claim in rows:
        counts[route] = counts.get(route, 0) + 1
    return [(Route(r).label, r, counts[r]) for r in Route.values if r in counts]
