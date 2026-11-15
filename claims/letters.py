"""Drafting of letters, AEO references and U.O. notes from scrutiny findings.

Which findings go into which communication:

  any finding routed to the AEO  -> one reference to the AEO carrying every
                                    external discrepancy, copy to employer and
                                    claimant (this is how the cell writes them)
  otherwise employer / claimant  -> one letter to the employer, copy to the
                                    claimant and the AEO; addressed to the
                                    claimant instead if the establishment is closed
  Accounts Group findings        -> one U.O. note to the Section Supervisor of
                                    the Accounts Group, copy to the Inward section
  past service in another office -> one letter per Regional Office

Wording follows the letters issued by the Death Claims Cell, RO Kadapa.
"""

from django.conf import settings
from django.utils import timezone

from .checklist import fmt_date
from .models import Communication, Finding, Route

Kind = Communication.Kind

RELATION_PREFIX = {
    "wife": "W/o", "widow": "W/o", "spouse": "W/o", "husband": "H/o",
    "widower": "H/o", "son": "S/o", "daughter": "D/o", "father": "F/o",
    "mother": "M/o",
}


def claimant_block(claim):
    relation = RELATION_PREFIX.get(claim.claimant_relation.strip().lower())
    title = {"W/o": "Smt.", "D/o": "Kum.", "M/o": "Smt."}.get(relation, "Sri")
    lines = [f"{title} {claim.claimant_name},"]
    if relation:
        lines.append(f"{relation} {claim.late_name},")
    lines += [ln.strip() for ln in claim.claimant_address.splitlines() if ln.strip()]
    return "\n".join(lines)


def aeo_block(claim):
    office = claim.establishment.aeo_office
    if office:
        return "The Area Enforcement Officer,\n" + office.address.strip()
    district = claim.establishment.district or "__________"
    return f"The Area Enforcement Officer,\nDistrict Office,\n{district}."


def aeo_copy(claim, purpose):
    """One-line 'copy to' entry for the AEO, as the cell writes it."""
    lines = [ln.strip().rstrip(",.") for ln in aeo_block(claim).splitlines()]
    return ", ".join(lines) + f".\n… {purpose}"


def _receipt_reference(claim):
    how = "by post" if claim.mode == claim.Mode.POST else "in person"
    return f"Death claim forms received {how} on {fmt_date(claim.received_on)}."


def _whose(claim):
    uan = f" (UAN {claim.uan})" if claim.uan else ""
    return f"{claim.late_name}, PF A/c No. {claim.pf_no}{uan}"


def _copies(*blocks):
    return "\n\n".join(b for b in blocks if b)


def _employer_letter(claim, points):
    return dict(
        kind=Kind.EMPLOYER,
        to_block=claim.establishment.address_block,
        subject=("Clarification and submission of required documents – death claim "
                 f"forms in r/o {claim.late_name}, PF A/c No. {claim.pf_no} – Reg."),
        reference=_receipt_reference(claim),
        salutation="Sir,",
        intro=("With reference to the subject cited, this office is in receipt of the "
               f"death claim forms ({claim.forms_text}) in respect of {_whose(claim)}. "
               "On scrutiny of the claim forms and the documents enclosed therewith, "
               "the following discrepancies have been observed:"),
        points=points,
        closing=("Therefore, in view of the above, you are requested to rectify the "
                 "above discrepancies and to submit the required documents / "
                 "clarification, duly attested by the authorised signatory of the "
                 "establishment, to enable this office to process the death claim "
                 "forms.\n\nThis matter may be treated as MOST URGENT."),
        copy_to=_copies(claimant_block(claim) + "\n… for information.",
                        aeo_copy(claim, "for information and follow-up action.")),
    )


def _claimant_letter(claim, points):
    return dict(
        kind=Kind.CLAIMANT,
        to_block=claimant_block(claim),
        subject=("Clarification and submission of required documents – death claim "
                 f"forms in r/o {claim.late_name}, PF A/c No. {claim.pf_no} – Reg."),
        reference=_receipt_reference(claim),
        salutation="Madam / Sir,",
        intro=("With reference to the subject cited, this office is in receipt of the "
               f"death claim forms ({claim.forms_text}) in respect of {_whose(claim)}. "
               "On scrutiny of the claim forms and the documents enclosed therewith, "
               "the following discrepancies have been observed:"),
        points=points,
        closing=("Therefore, in view of the above, you are requested to rectify the "
                 "above discrepancies and to submit the required documents, duly "
                 "attested, to enable this office to process the death claim forms."),
        copy_to=_copies(aeo_copy(claim, "for information.")),
    )


def _aeo_reference(claim, points, in_service):
    forwarded = (f"the e-File bearing No. {claim.efile_no} is forwarded"
                 if claim.efile_no else "the case is referred")
    service = (", and to ascertain whether the member's death occurred while in "
               "service" if in_service else "")
    return dict(
        kind=Kind.AEO,
        to_block=aeo_block(claim),
        subject=("Verification and confirmation of genuineness of death claim forms "
                 f"in r/o {claim.late_name}, PF A/c No. {claim.pf_no} – Reg."),
        reference=_receipt_reference(claim),
        salutation="Sir,",
        intro=("With reference to the subject cited, this office is in receipt of "
               f"death claim forms ({claim.forms_text}) in respect of {_whose(claim)}, "
               f"member of M/s {claim.establishment.name}. Upon scrutiny of the "
               "submitted death claim forms and enclosures, the following "
               "discrepancies have been observed:"),
        points=points,
        closing=(f"In view of the above, {forwarded} to the AEO for verification and "
                 "confirmation of the genuineness of the claim, the claimant's "
                 f"particulars, the member's profile and service details{service}. "
                 "The AEO is also requested to obtain the required documents, such as "
                 "wage registers, attendance registers and any other records relevant "
                 "to the processing of the death claims, and to submit a comprehensive "
                 "report along with the supporting documents.\n\n"
                 "This may be treated as MOST URGENT."),
        copy_to=_copies(
            claim.establishment.address_block + "\n… for taking necessary action.",
            claimant_block(claim) + "\n… for information."),
    )


# What a U.O. note is about, by the rule that raised the point:
# (wording for the subject line, wording for "forwarded ... for <purpose>")
UO_TOPICS = {
    "UAN_GENERATION": ("Generation of UAN, Aadhaar seeding & updation of member details",
                       "generation of UAN, Aadhaar seeding and updation of member "
                       "details"),
    "AADHAAR_SEEDING": ("Aadhaar seeding & updation of member details",
                        "Aadhaar seeding and updation of member details"),
    "EPS_DIVERT_TO_EPF": ("Diversion of pension contributions",
                          "diversion of pension contributions"),
    "EPS_DIVERT_TO_EPS": ("Diversion of pension contributions",
                          "diversion of pension contributions"),
    "EXCESS_INTEREST": ("Deduction of excess interest", "deduction of excess interest"),
    "RECREDIT": ("Re-credit of amount to the member's account",
                 "re-credit of the amount to the member's account"),
}
UO_DEFAULT_TOPIC = ("Action required at the Accounts Group", "necessary action")
KYC_CODES = {"UAN_GENERATION", "AADHAAR_SEEDING"}


def _and(items):
    items = list(dict.fromkeys(items))
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _uo_note(claim, findings):
    """U.O. note to the Accounts Group, in the format used by the cell."""
    group = claim.accounts_group or "_____"
    topics = [UO_TOPICS.get(f.code, UO_DEFAULT_TOPIC) for f in findings]
    efile = f" vide E-Office No. {claim.efile_no}" if claim.efile_no else ""
    whose = f"{claim.late_name}, PF A/c No. {claim.pf_no}"
    inward = "for information & necessary action"
    if any(f.code in KYC_CODES for f in findings):
        inward += " to initiate JD from PRO"
    return dict(
        kind=Kind.UO_NOTE,
        to_block=f"The Section Supervisor,\nAG-{group},\nRO, Kadapa.",
        subject=f"{_and(t[0] for t in topics)} in r/o {whose} – reg.",
        reference=f"Death claim forms received in r/o {whose}.",
        salutation="",
        intro=("With reference to the above, it is to inform that death claim forms and "
               f"enclosures in r/o {claim.late_name}, PF Account No. {claim.pf_no} were "
               "received for the settlement of death benefits.\n\n"
               f"On verification, the following {'is' if len(findings) == 1 else 'are'} "
               "noticed, and in this regard the said claim forms and enclosures are "
               f"forwarded to the concerned Accounts Section (SS, AG-{group}){efile}, "
               f"for {_and(t[1] for t in topics)}:"),
        points=[f.text for f in findings],
        closing=("Therefore, in view of the above, the action indicated above may be "
                 "taken after due verification. The action taken, along with the "
                 "approved E-File note orders, may be forwarded to the Inward section "
                 "for making receipt entry of the Death claims.\n\n"
                 "This may be treated as Most Urgent.\n\nEncl.: As above."),
        copy_to=f"The Section Supervisor,\nInward section\n… {inward}.",
    )


def _other_ro_letter(claim, office, points):
    return dict(
        kind=Kind.OTHER_RO,
        other_office=office,
        to_block=("The Regional P.F. Commissioner,\nEmployees' Provident Fund "
                  f"Organisation,\nRegional Office, {office}."),
        subject=(f"Transfer of past service of {claim.late_name} to PF A/c No. "
                 f"{claim.pf_no} for settlement of death claims – Reg."),
        reference=_receipt_reference(claim),
        salutation="Sir / Madam,",
        intro=("This office is in receipt of death claim forms in respect of "
               f"{_whose(claim)}, member of M/s {claim.establishment.name}. On "
               "verification, it is observed that the deceased member had previous "
               "service under the jurisdiction of your office, as detailed below, "
               "which has not been transferred to the present account:"),
        points=points,
        closing=("As the death benefits due to the bereaved family can be determined "
                 "and settled only after the past service and accumulations are "
                 "transferred, it is requested to arrange for transfer of the above "
                 f"account(s) to PF A/c No. {claim.pf_no} of this office (Form-13 / "
                 "Annexure-K) on top priority, and to intimate the transfer "
                 "particulars to this office.\n\n"
                 "This matter may be treated as MOST URGENT."),
        copy_to="",
    )


def build_drafts(scrutiny, user=None, force=False):
    """Create or refresh the draft communications for a scrutiny round.

    A draft the user has edited by hand is left alone unless force is set.
    Drafts no longer warranted by the findings are removed.  Communications
    that have already been approved or issued are never touched.
    """
    if scrutiny.status == scrutiny.Status.APPROVED:
        return []
    claim = scrutiny.claim
    findings = list(scrutiny.active_findings().filter(status=Finding.Status.OPEN))
    by_route = {}
    for f in findings:
        by_route.setdefault(f.route, []).append(f)

    external = [f for f in findings if f.route in (Route.AEO, Route.EMPLOYER,
                                                   Route.CLAIMANT)]
    wanted = []
    if by_route.get(Route.AEO):
        in_service = scrutiny.answers.get("clm_reason") == "DEATH_IN_SERVICE"
        wanted.append(_aeo_reference(claim, [f.text for f in external], in_service))
    elif external:
        points = [f.text for f in external]
        closed = claim.establishment.status == "CLOSED" or (
            scrutiny.answers.get("estt_status") == "CLOSED")
        wanted.append(_claimant_letter(claim, points) if closed
                      else _employer_letter(claim, points))
    if by_route.get(Route.ACCOUNTS):
        wanted.append(_uo_note(claim, by_route[Route.ACCOUNTS]))
    offices = {}
    for f in by_route.get(Route.OTHER_RO, []):
        offices.setdefault(f.other_office or "__________", []).append(f.text)
    for office, points in offices.items():
        wanted.append(_other_ro_letter(claim, office, points))

    drafts = {(c.kind, c.other_office): c for c in scrutiny.communications.filter(
        status=Communication.Status.DRAFT, reminder_of__isnull=True)}
    out = []
    for spec in wanted:
        key = (spec["kind"], spec.get("other_office", ""))
        comm = drafts.pop(key, None)
        if comm is None:
            comm = Communication(claim=claim, scrutiny=scrutiny, created_by=user)
        elif comm.edited and not force:
            out.append(comm)
            continue
        for name, value in spec.items():
            setattr(comm, name, value)
        comm.signatory = settings.OFFICE["signatory"]
        comm.edited = False
        comm.save()
        out.append(comm)
    for stale in drafts.values():
        stale.delete()
    return out


def build_reminder(original, user=None):
    """Draft a reminder for a letter that has gone unanswered."""
    root = original.reminder_of or original
    number = root.reminders.count() + 1
    references = [root.reference.strip()] if root.reference.strip() else []
    references.append(f"This office letter No. {root.number} dated "
                      f"{fmt_date(root.letter_date)}.")
    for earlier in root.reminders.exclude(number="").order_by("id"):
        references.append(f"This office reminder No. {earlier.number} dated "
                          f"{fmt_date(earlier.letter_date)}.")
    open_texts = None
    if root.scrutiny_id:
        still_open = set(root.scrutiny.findings.filter(
            status=Finding.Status.OPEN, included=True).values_list("text", flat=True))
        open_texts = [p for p in root.points if p in still_open]
    return Communication.objects.create(
        claim=root.claim, scrutiny=root.scrutiny, kind=root.kind,
        other_office=root.other_office, reminder_of=root, reminder_no=number,
        to_block=root.to_block,
        subject=f"REMINDER-{number}: {root.subject}",
        reference="\n".join(references),
        salutation=root.salutation,
        intro=("Kind attention is invited to this office letter cited at reference, "
               f"regarding the death claim forms in respect of {_whose(root.claim)}. "
               "No reply / compliance has been received so far on the following "
               "points communicated therein:"),
        points=open_texts or list(root.points),
        closing=("It is once again requested to furnish the required documents / "
                 "clarification / report immediately, as the settlement of the death "
                 "benefits due to the bereaved family is held up for want of the "
                 "same.\n\nThis matter may be treated as MOST URGENT."),
        copy_to=root.copy_to, signatory=root.signatory, created_by=user)


# -- rendering helpers shared by the HTML view and the Word export -----------
def paragraphs(text):
    return [p.strip() for p in (text or "").split("\n\n") if p.strip()]


def copy_blocks(text):
    """Address blocks of the 'copy to' list; a trailing '… purpose' line is
    run on to the last address line, as in the office's letters."""
    blocks = []
    for block in paragraphs(text):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) > 1 and lines[-1].startswith(("…", "...")):
            purpose = lines.pop().lstrip("….").strip()
            lines[-1] = f"{lines[-1]} …… {purpose}"
        blocks.append(lines)
    return blocks


def parts(comm):
    """Everything the HTML view and the Word export need to lay a letter out."""
    office = settings.OFFICE
    year = (comm.letter_date or comm.created_at or timezone.now()).year
    closing = [{"text": text, "urgent": "most urgent" in text.lower(),
                "flush": text.lower().startswith("encl")}
               for text in paragraphs(comm.closing)]
    return {
        "comm": comm,
        "number": comm.number or comm.number_stem(year),
        "date": fmt_date(comm.letter_date) if comm.letter_date else "",
        "to_lines": [ln for ln in comm.to_block.splitlines() if ln.strip()],
        "references": [ln for ln in comm.reference.splitlines() if ln.strip()],
        "intro": paragraphs(comm.intro),
        # A U.O. note about a single matter reads as a paragraph, not a list.
        "numbered": not (comm.is_uo and len(comm.points) == 1),
        "closing": closing,
        "copies": copy_blocks(comm.copy_to),
        "signatory": comm.signatory or office["signatory"],
        "signatory_office": office["signatory_office"],
        "footer_lines": [] if comm.is_uo else office["footer_lines"],
    }
