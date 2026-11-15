"""Scrutiny rules engine.

evaluate(answers, ctx) reads the checklist answers and returns
  * findings – each a letter-ready remark, routed to whoever must act on it
  * facts    – computed observations shown against checklist points
  * missing  – checklist items the Dealing Assistant has not answered yet

The engine is pure Python (no database access) so each rule can be unit
tested.  sync_findings() then stores the result against a Scrutiny, keeping
any wording or routing the Dealing Assistant has changed by hand.

Rule codes are stable identifiers: they are how an edited finding is matched
when the checklist is re-evaluated, and how reports group discrepancies.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from .checklist import (PROFILE_FIELDS, compare_display, fmt_date, fmt_month,
                        parse_date, parse_month)

EPS_CUTOFF = date(2014, 9, 1)
NON_AADHAAR = ("In cases where Aadhaar seeding is not possible, necessary approval of the Competent Authority may be obtained through E-file notings for processing the death claim as a \"Non-Aadhaar\" case.")

EMPLOYER, CLAIMANT, AEO = "EMPLOYER", "CLAIMANT", "AEO"
ACCOUNTS, OTHER_RO, INTERNAL, NOTE = "ACCOUNTS", "OTHER_RO", "INTERNAL", "NOTE"


@dataclass
class Ctx:
    """What the engine needs to know about the claim itself."""

    pf_no: str = ""
    forms: set = field(default_factory=set)  # {"20", "10D", "5IF", "10C"}
    received_on: date = None
    date_of_death: date = None
    older_age: int = 45
    belated_years: int = 6
    own_offices: tuple = ("", "kadapa")

    @classmethod
    def from_claim(cls, claim):
        forms = set()
        for flag, name in (("form_20", "20"), ("form_10d", "10D"),
                           ("form_5if", "5IF"), ("form_10c", "10C")):
            if getattr(claim, flag):
                forms.add(name)
        from .models import office as office_config  # the engine itself stays DB-free

        office = office_config()
        return cls(pf_no=claim.pf_no, forms=forms, received_on=claim.received_on,
                   date_of_death=claim.date_of_death,
                   older_age=office["older_age_at_joining"],
                   belated_years=office["belated_claim_years"],
                   own_offices=tuple(office["own_office_names"]))


@dataclass
class Draft:
    code: str
    point: str
    category: str
    route: str
    title: str
    text: str
    other_office: str = ""


@dataclass
class Result:
    findings: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)

    def add(self, code, point, category, route, title, text, other_office=""):
        self.findings.append(Draft(code, point, category, route, title,
                                   " ".join(text.split()), other_office))

    def fact(self, point, text):
        self.facts[point] = (self.facts[point] + " " + text) if point in self.facts \
            else text

    @property
    def routes(self):
        return {f.route for f in self.findings}

    @property
    def action(self):
        return recommend(self.routes)


def recommend(routes):
    """The proposed disposal of the claim, given who still has to act."""
    routes = set(routes) - {NOTE}
    if AEO in routes:
        return "REFER_AEO"
    if routes & {EMPLOYER, CLAIMANT}:
        return "RETURN"
    if routes:
        return "OFFICE_ACTION"
    return "INWARD"


# -- small helpers -----------------------------------------------------------
def _norm(text):
    return re.sub(r"[^A-Z0-9]", "", str(text).upper())


def _same(key, office_value, claim_value):
    if key in ("name", "father"):
        if _norm(office_value) == _norm(claim_value):
            return True
        # Same words in a different order (surname first / last) is not a
        # discrepancy worth a Joint Declaration.
        tokens = lambda s: sorted(re.findall(r"[A-Z0-9]+", str(s).upper()))  # noqa: E731
        return tokens(office_value) == tokens(claim_value)
    return office_value == claim_value


def _years(start, end):
    return (end - start).days / 365.25 if start and end else None


def _months_apart(a, b):
    """Whole months from (y, m) a to (y, m) b."""
    return (b[0] - a[0]) * 12 + (b[1] - a[1])


def _shift(ym, months):
    index = ym[0] * 12 + (ym[1] - 1) + months
    return index // 12, index % 12 + 1


def _yes(a, key):
    return a.get(key) == "YES"


def _no(a, key):
    return a.get(key) == "NO"


def _detail(a, key, prefix=" (", suffix=")"):
    value = (a.get(key) or "").strip()
    return f"{prefix}{value}{suffix}" if value else ""


def _join(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def is_own_office(name, own_offices):
    cleaned = (name or "").strip().lower()
    return cleaned in own_offices or any(o and o in cleaned for o in own_offices)


# -- the engine --------------------------------------------------------------
def evaluate(a, ctx):  # noqa: C901 - one long, linear walk down the checklist
    r = Result()
    need = r.missing.append

    forms = ctx.forms
    pension_claimed = "10D" in forms
    pf = ctx.pf_no

    doj = parse_date(a.get("off_doj")) or parse_date(a.get("clm_doj"))
    dob = parse_date(a.get("clm_dob")) or parse_date(a.get("off_dob"))
    dod = (parse_date(a.get("clm_dod")) or parse_date(a.get("off_dod"))
           or ctx.date_of_death)
    off_dol = parse_date(a.get("off_dol"))
    clm_dol = parse_date(a.get("clm_dol"))
    in_service = a.get("clm_reason") == "DEATH_IN_SERVICE"
    away = a.get("clm_reason") == "DEATH_AWAY"
    dol = dod if in_service else (off_dol or clm_dol)
    estt_live = a.get("estt_status") != "CLOSED"
    prev = a.get("prev_services") or []

    if not a.get("clm_reason"):
        need("3 – Reason of exit as per claim form (death in / away from service)")
    if not a.get("off_reason"):
        need("3 – Reason of exit as per office records")
    if not dod:
        need("3 – Date of death")
    if not doj:
        need("3 – Date of joining")

    # ---- Point 3 / 25 / 26: profile and exit particulars -------------------
    diffs = []
    for key, label in PROFILE_FIELDS.items():
        office_value, claim_value = a.get(f"off_{key}"), a.get(f"clm_{key}")
        if office_value and claim_value and not _same(key, office_value, claim_value):
            diffs.append((label, compare_display(key, "off", office_value),
                          compare_display(key, "clm", claim_value)))

    exit_issues = []
    off_reason = a.get("off_reason")
    if off_reason:
        if not off_dol and off_reason == "NOT_UPDATED":
            exit_issues.append("the Date of Exit (DOE) and the Reason for Leaving "
                               "have not been updated")
        elif not off_dol:
            exit_issues.append("the Date of Exit (DOE) has not been updated")
        elif off_reason == "NOT_UPDATED":
            exit_issues.append("the Reason for Leaving has not been updated")
        if off_reason == "NOT_UPDATED":
            pass
        elif in_service and off_reason != "DEATH":
            exit_issues.append(
                "the Reason for Leaving is updated as "
                f"\"{compare_display('reason', 'off', off_reason)}\" whereas the member "
                "is stated to have died while in service")
        elif away and off_reason == "DEATH":
            exit_issues.append(
                "the Reason for Leaving is updated as \"Death in service\" whereas the "
                "claim forms state that the member died away from service")
        if in_service and off_dol and dod and off_dol != dod:
            exit_issues.append(
                f"the Date of Exit is updated as {fmt_date(off_dol)} whereas the date "
                f"of death is {fmt_date(dod)}")
        if away and off_dol and dod and off_dol > dod:
            exit_issues.append(
                f"the Date of Exit ({fmt_date(off_dol)}) is later than the date of "
                f"death ({fmt_date(dod)})")

    jd_attached, jd_updated = _yes(a, "jd_attached"), _yes(a, "jd_updated")
    diff_text = "; ".join(
        f"({chr(97 + i)}) {label} – \"{o}\" as per office records, \"{c}\" as per claim "
        f"forms/enclosures" for i, (label, o, c) in enumerate(diffs))

    if diffs or exit_issues:
        wanted = [label.lower() for label, _, _ in diffs]
        if exit_issues:
            wanted.append("date / reason of exit")
        r.fact("25", "JD / update required for: " + _join(wanted) + ".")

    if (diffs or exit_issues) and jd_attached:
        if not jd_updated:
            r.add("JD_INWARD", "26", "PROFILE", INTERNAL,
                  "Inward the Joint Declaration and update the member profile",
                  f"Joint Declaration enclosed with the claim is to be inwarded and the "
                  f"corrections ({_join(wanted)}) updated in r/o PF A/c No. {pf} "
                  f"before inward receipt entry of the death claims.")
    else:
        if diffs:
            r.add("PROFILE_MISMATCH", "3", "PROFILE", EMPLOYER,
                  "Member details not tallying with office records",
                  f"The following details of the member are not tallying with the "
                  f"office records: {diff_text}. A Joint Declaration for correction of "
                  f"the same, signed by the claimant and the employer, may be submitted "
                  f"online through the Employer Portal along with the supporting "
                  f"documents prescribed under the SOP; if any error persists while "
                  f"submitting it online, a physical Joint Declaration duly attested "
                  f"by the authorised signatory may be furnished along with a "
                  f"screenshot of the error.")
        if exit_issues:
            issues = _join(exit_issues)
            r.add("EXIT_NOT_UPDATED", "3", "EXIT", EMPLOYER,
                  "Date / reason of exit not updated",
                  f"In r/o PF A/c No. {pf}, {issues}. The employer is advised to "
                  f"update the Date of Exit and Reason for Leaving through the online "
                  f"Employer Portal. If any error persists while updating the details "
                  f"online, a physical Joint Declaration (JD) for updating the same may "
                  f"be furnished along with the supporting documents prescribed under "
                  f"the latest SOP and a screenshot of the error, duly attested by the "
                  f"authorised signatory.")

    # ---- Point 4: establishment status and attestation --------------------
    attested = a.get("attested_by")
    if not a.get("estt_status"):
        need("4 – Establishment live or closed")
    if not attested:
        need("4 – Who attested the claim forms")
    if estt_live and _yes(a, "estt_claimed_closed"):
        by = ("a Gazetted Officer / authorised official" if attested == "GAZETTED"
              else "the employer" if attested == "EMPLOYER" else "no authority")
        r.add("ESTT_STATUS_CONFLICT", "4", "ESTT", AEO,
              "Establishment live in records but claimed closed",
              f"As per this office records the establishment is LIVE, whereas the "
              f"claimant has stated that the establishment is closed and the claim "
              f"forms are attested by {by}. The present working status of the "
              f"establishment and the authenticity of the attestation on the claim "
              f"forms and enclosures may be verified and confirmed.")
    elif estt_live and attested in ("GAZETTED", "NONE"):
        r.add("ATTESTATION_BY_EMPLOYER", "4", "ESTT", EMPLOYER,
              "Claim forms not attested by the employer",
              "The establishment is live, but the claim forms and enclosures are not "
              "attested by the authorised signatory of the establishment. All the "
              "claim forms, enclosures and photographs are to be attested by the "
              "authorised signatory of the establishment.")
    elif not estt_live and attested == "NONE":
        r.add("ATTESTATION_CLOSED_ESTT", "4", "ESTT", CLAIMANT,
              "Closed establishment – forms not attested",
              "As the establishment is closed, the claim forms, enclosures and "
              "photographs are to be attested by a Gazetted Officer or any other "
              "official authorised for the purpose in the instructions to the claim "
              "form, with name, designation and seal.")
    elif not estt_live and attested == "GAZETTED":
        r.fact("4", "Establishment closed; forms attested by a Gazetted Officer / "
                    "authorised official – refer to AEO if the attestation is in "
                    "doubt.")

    # ---- Point 5: EDLI ------------------------------------------------------
    if not a.get("edli_exempted"):
        need("5 – EDLI exemption status")
    elif _no(a, "edli_exempted"):
        if not a.get("edli_contrib"):
            need("5 – EDLI contributions received or not")
        elif a.get("edli_contrib") in ("PARTLY", "NONE"):
            period = _detail(a, "edli_gap_period", " for the period ", "")
            r.add("EDLI_NOT_RECEIVED", "5", "EDLI", AEO,
                  "EDLI contributions (A/c 21) not received",
                  f"The establishment is reported to be un-exempted under the EDLI "
                  f"Scheme. However, EDLI contributions under A/c No. 21 have not been "
                  f"received{period or ' up to the date of death of the member'}. The "
                  f"exemption status of the establishment under the EDLI Scheme may be "
                  f"verified and confirmed, and clarification obtained from the "
                  f"employer regarding the non-remittance of EDLI contributions; if "
                  f"the establishment is not exempted, the employer may be advised to "
                  f"remit the EDLI contributions due.")
    elif "5IF" in forms:
        r.fact("5", "Establishment is exempted under EDLI – Form 5(IF) is not payable "
                    "by this office; EDLI benefit lies with the establishment's "
                    "exempted scheme.")

    # ---- Point 6: DSC / e-Sign ---------------------------------------------
    dsc = a.get("dsc_status")
    if not dsc:
        need("6 – DSC / e-Sign status")
    elif estt_live and dsc != "ACTIVE":
        r.add("DSC_NOT_ACTIVE", "6", "DSC", EMPLOYER,
              "DSC / e-Sign not registered or inactive",
              "DSC/e-Sign of the authorised signatory of the establishment is not "
              "available/active in this office records. The employer is advised to "
              "register and get approved the DSC/e-Sign of the authorised signatory "
              "through the Employer Portal at the earliest, as the attestation on the "
              "claim forms can be accepted only on verification with the same.")
    elif estt_live and dsc == "ACTIVE" and attested == "EMPLOYER":
        if not a.get("sign_tally"):
            need("6 – Signature tallying with DSC / e-Sign")
        elif _no(a, "sign_tally"):
            r.add("SIGNATURE_NOT_TALLYING", "6", "DSC", EMPLOYER,
                  "Attestation not tallying with DSC / e-Sign",
                  "The signature of the attesting authority on the claim forms and "
                  "enclosures is not tallying with the DSC/e-Sign of the authorised "
                  "signatory registered with this office. The claim forms and all the "
                  "enclosures are to be attested by the authorised signatory whose "
                  "DSC/e-Sign is registered, or the DSC/e-Sign of the present "
                  "signatory is to be registered through the Employer Portal.")

    # ---- Point 7: past service --------------------------------------------
    if not prev and not a.get("no_prev_service"):
        need("7 – Previous service (add the past member IDs or tick 'none found')")
    past_days = 0
    external = {}
    for row in prev:
        start, end = parse_date(row.get("from")), parse_date(row.get("to"))
        if start and end and end > start:
            past_days += (end - start).days
        if row.get("transferred") == "YES":
            continue
        member_id = row.get("member_id") or "—"
        period = ""
        if start or end:
            period = f" for the period {fmt_date(start) or '—'} to {fmt_date(end) or '—'}"
        described = f"PF A/c No. {member_id}"
        if row.get("estt"):
            described += f" (M/s {row['estt']})"
        if is_own_office(row.get("office"), ctx.own_offices):
            r.add(f"FORM13_INTERNAL:{member_id}", "7", "SERVICE", INTERNAL,
                  f"Transfer-in of past service {member_id}",
                  f"Previous service of the member under {described}{period} is not "
                  f"transferred to the present PF A/c No. {pf}. Form-13 (transfer-in) "
                  f"is to be inwarded and settled before inward of the death claims.")
        else:
            external.setdefault(row["office"].strip(), []).append(described + period)
    for office, items in external.items():
        r.add(f"FORM13_EXTERNAL:{office}", "7", "SERVICE", OTHER_RO,
              f"Past service to be transferred from {office}",
              "; ".join(items), other_office=office)
    if a.get("scheme_cert") == "NOT_ATTACHED":
        r.add("SCHEME_CERT", "7", "SERVICE", CLAIMANT,
              "Original Scheme Certificate not enclosed",
              "The member had previous pensionable service for which a Scheme "
              "Certificate was issued, but the original Scheme Certificate is not "
              "enclosed. The same may be submitted along with the claim forms.")

    present_years = _years(doj, dol) or 0
    computed_service = present_years + past_days / 365.25
    try:
        service = float(a.get("eligible_service") or "")
    except ValueError:
        service = computed_service if (doj and dol) else None
    age_at_joining = _years(dob, doj)
    if (age_at_joining is not None and age_at_joining >= ctx.older_age and not prev
            and service is not None and service < 10):
        r.add("PAST_SERVICE_QUERY", "7", "SERVICE", EMPLOYER,
              "Older member with short service – past employment to be confirmed",
              f"The member was aged about {int(age_at_joining)} years at the time of "
              f"joining the establishment on {fmt_date(doj)} and has only about "
              f"{service:.1f} years of service under PF A/c No. {pf}, and no previous "
              f"service is found linked to the account. The previous employment / "
              f"provident fund membership particulars of the member, if any (PF "
              f"account number or UAN, name of the establishment and period of "
              f"service), may be furnished with supporting records, so that the past "
              f"service is not left out while determining the death benefits.")

    # ---- Point 8: contributions -------------------------------------------
    first = parse_month(a.get("first_contrib_month"))
    lcm = parse_month(a.get("lcm"))
    death_month = (dod.year, dod.month) if dod else None
    if not a.get("contrib_complete"):
        need("8 – Contributions received for all months or not")
    if not a.get("post_death_contrib"):
        need("8 – Contributions received after death or not")
    if not a.get("ncp_updated"):
        need("8 – NCP days updated or not")

    if doj and first and _months_apart((doj.year, doj.month), first) > 1:
        r.add("DOJ_VS_FIRST_CONTRIBUTION", "8", "CONTRIBUTION", EMPLOYER,
              "Contributions start later than the date of joining",
              f"The date of joining in r/o PF A/c No. {pf} is furnished in the system "
              f"as {fmt_date(doj)}, but contributions have been received only from "
              f"{fmt_month(first)} onwards. The employer is advised to clarify the "
              f"same and to furnish the service and contribution particulars of the "
              f"member for the intervening period, along with supporting documents; "
              f"if the date of joining is incorrect, a Joint Declaration for "
              f"correction of the date of joining may be submitted duly attested.")
    if _no(a, "contrib_complete"):
        gaps = _detail(a, "contrib_gaps", " for the wage month(s) ", "")
        ncp = " and NCP days are also not updated" if not _yes(a, "ncp_updated") else ""
        r.add("CONTRIBUTION_GAPS", "8", "CONTRIBUTION", EMPLOYER,
              "Contributions not received for some months",
              f"Contributions have not been received{gaps or ' for certain wage months'}"
              f"{ncp}. An NCP days statement from the date of joining to the date of "
              f"leaving, month-wise and year-wise, may be submitted duly attested by "
              f"the authorised signatory, along with clarification for the "
              f"non-remittance.")
    elif _no(a, "ncp_updated"):
        r.add("NCP_NOT_UPDATED", "8", "CONTRIBUTION", EMPLOYER,
              "NCP days not updated",
              "Non-Contributory Period (NCP) days have not been updated properly"
              f"{_detail(a, 'remark_8')}. An NCP days statement from the date of "
              "joining to the date of leaving, duly attested by the authorised "
              "signatory, may be submitted.")
    post_death = _yes(a, "post_death_contrib")
    if post_death:
        detail = _detail(a, "post_death_detail", ", i.e. ", "")
        r.add("POST_DEATH_CONTRIBUTION", "8", "CONTRIBUTION", EMPLOYER,
              "Contributions received after the date of death",
              f"The member expired on {fmt_date(dod)}; however, contributions have "
              f"been received even after the date of death{detail}. The employer is "
              f"advised to furnish a clarification regarding the remittance of "
              f"contributions after the date of death, along with the relevant "
              f"supporting documents (wage and attendance registers for the period).")

    death_month_flagged = False
    if in_service:
        paid = a.get("death_month_contrib")
        last3 = (f"{fmt_month(_shift(death_month, -2))} to {fmt_month(death_month)}"
                 if death_month else "the last three months")
        if not paid:
            need("8(d) – Contribution for the month of death")
        elif paid == "NOT_PAID":
            death_month_flagged = True
            r.add("DEATH_MONTH_NOT_PAID", "8", "CONTRIBUTION", AEO,
                  "Death in service – contribution for the death month not received",
                  f"The member is stated to have died while in service on "
                  f"{fmt_date(dod)}, but the contribution for the month of death "
                  f"({fmt_month(death_month)}) has not been received. The employer's "
                  f"explanation for the non-remittance, along with copies of the "
                  f"muster rolls / attendance registers and wage registers or pay "
                  f"slips for {last3} (and gratuity / ESIC death-benefit particulars, "
                  f"if any) duly attested by the authorised official, is required to "
                  f"establish that the member was on the rolls of the establishment at "
                  f"the time of death, for deciding eligibility under the EDLI Scheme.")
        elif paid == "PAID_LATE" and not _yes(a, "death_month_proof"):
            death_month_flagged = True
            paid_on = fmt_date(a.get("death_month_paid_on"))
            paid_on = f" on {paid_on}" if paid_on else ""
            r.add("DEATH_MONTH_DELAYED", "8", "CONTRIBUTION", EMPLOYER,
                  "Contribution for the death month remitted belatedly",
                  f"The member's date of death is {fmt_date(dod)}, but the "
                  f"contribution for the wage month {fmt_month(death_month)} was "
                  f"remitted belatedly{paid_on}. The reasons for the delay in "
                  f"remittance may be clarified with documentary evidence (attendance "
                  f"and wage registers and proof of payment of wages and other "
                  f"benefits till the date of death) to show that the member was on "
                  f"the rolls of the establishment.")
    expected = (dol.year, dol.month) if dol else None
    if (lcm and expected and _yes(a, "contrib_complete") and not post_death
            and not death_month_flagged and lcm != expected):
        when = "death" if in_service else "exit"
        r.add("LCM_MISMATCH", "8", "CONTRIBUTION", EMPLOYER,
              "Last contributed month not tallying with the date of exit",
              f"The last contribution in r/o PF A/c No. {pf} is received for the wage "
              f"month {fmt_month(lcm)}, whereas the date of {when} is "
              f"{fmt_date(dol)}. The employer is advised to clarify the variation "
              f"with supporting documents and to update the correct date of exit / "
              f"remit the contributions due, as the case may be.")
    if _yes(a, "wage_anomaly"):
        r.add("WAGE_VARIATION", "8", "CONTRIBUTION", EMPLOYER,
              "Abnormal variation in wages / contributions",
              f"Abnormal variation is observed in the wages / contributions of the "
              f"member{_detail(a, 'wage_anomaly_detail')}. The employer is advised to "
              f"clarify the same and to submit copies of the wage registers / pay "
              f"slips for the relevant period and an NCP days statement for the last "
              f"60 months, duly attested by the authorised signatory.")

    # ---- Point 9: pension membership --------------------------------------
    joined_after = bool(doj and doj >= EPS_CUTOFF)
    age_at_death = _years(dob, dod)
    if doj:
        r.fact("9", f"Joined on {fmt_date(doj)} – "
                    f"{'after' if joined_after else 'before'} 01.09.2014.")
    if not a.get("eps_contributed"):
        need("9 – Whether pension contributions were remitted")
    if joined_after:
        if not a.get("wages_gt_15000"):
            need("9 – Whether EPF wages at joining exceeded Rs.15,000/-")
        if not prev and not a.get("prior_eps_member"):
            a = {**a, "prior_eps_member": "NO"}
    eps_eligible = None
    if not joined_after and doj:
        eps_eligible = True
    elif joined_after and a.get("wages_gt_15000") in ("YES", "NO"):
        eps_eligible = _no(a, "wages_gt_15000") or _yes(a, "prior_eps_member")
    if eps_eligible is False and _yes(a, "eps_contributed"):
        if _yes(a, "eps_wage_proof"):
            r.add("EPS_DIVERT_TO_EPF", "9", "EPS", ACCOUNTS,
                  "Divert pension contributions of ineligible member to A/c 1",
                  f"The member joined on {fmt_date(doj)} (after 01.09.2014) with EPF "
                  f"wages above Rs.15,000/- and is not eligible for membership of the "
                  f"Employees' Pension Scheme, 1995, as confirmed by the employer with "
                  f"wage records. The pension contributions wrongly remitted to A/c "
                  f"No. 10 are to be diverted to the employer share in A/c No. 1 of PF "
                  f"A/c No. {pf}, with interest as admissible.")
        else:
            r.add("EPS_INELIGIBLE_CONTRIBUTION", "9", "EPS", EMPLOYER,
                  "Pension contributions remitted for an apparently ineligible member",
                  f"The member joined on {fmt_date(doj)}, i.e. after 01.09.2014, with "
                  f"EPF wages above Rs.15,000/- and no pension membership prior to "
                  f"01.09.2014 is on record; such a member is not eligible for "
                  f"membership of the Employees' Pension Scheme, 1995. However, "
                  f"pension contributions (A/c No. 10) have been remitted. The "
                  f"employer is requested to furnish the wage register / salary slips "
                  f"showing the wages of the member at the time of joining, to "
                  f"reconfirm the pension membership eligibility of the member, and to "
                  f"clarify the remittance of pension contributions in respect of an "
                  f"ineligible member. Previous employment and PF membership records "
                  f"of the member, if any, may also be furnished.")
    elif (eps_eligible and _no(a, "eps_contributed")
          and (age_at_death is None or age_at_death < 58)):
        r.add("EPS_DIVERT_TO_EPS", "9", "EPS", ACCOUNTS,
              "Divert 8.33% from employer share to the Pension Fund",
              f"The member is eligible for membership of the Employees' Pension "
              f"Scheme, 1995, but the pension contribution (8.33%) has not been "
              f"remitted to A/c No. 10 and the entire employer share is credited to "
              f"A/c No. 1. Diversion of the pension contribution from A/c No. 1 to "
              f"A/c No. 10 in r/o PF A/c No. {pf} is to be effected before "
              f"settlement of the death claims.")
    if eps_eligible is False:
        r.fact("9", "EPF wages above Rs.15,000/- – NOT eligible for pension "
                    "membership.")
    elif joined_after and eps_eligible:
        r.fact("9", "Eligible for pension membership.")

    # ---- Point 10: e-nomination -------------------------------------------
    enom = a.get("enom_status")
    enom_date = parse_date(a.get("enom_date"))
    if not enom:
        need("10 – E-nomination status")
    enom_invalid = enom == "AFTER_DEATH" or bool(
        enom == "ALIVE" and enom_date and dod and enom_date > dod)
    enom_valid = enom == "ALIVE" and not enom_invalid
    if enom_invalid:
        dated = f" dated {fmt_date(enom_date)}" if enom_date else ""
        r.fact("10", f"E-nomination{dated} was filed after the death of the member "
                     f"({fmt_date(dod)}) and is INVALID.")
    elif enom_valid:
        r.add("ENOMINATION_ONLINE", "10", "NOMINATION", NOTE,
              "Valid e-nomination – claim can be filed online",
              f"A valid e-nomination has been filed by the deceased member"
              f"{_detail(a, 'enom_nominee', ' in favour of ', '')}. The claimant is "
              f"advised to submit the death claim forms online through the Member "
              f"Unified Portal (Death claim filing by beneficiary).")

    # ---- Point 11: forms and attestation ----------------------------------
    for key, label in (("forms_filled", "All columns filled or not"),
                       ("forms_signed", "Signed by all claimants or not"),
                       ("encl_attested", "Enclosures attested or not")):
        if not a.get(key):
            need(f"11 – {label}")
    if _no(a, "forms_filled"):
        r.add("FORMS_INCOMPLETE", "11", "FORMS", EMPLOYER,
              "Columns in the claim forms not filled",
              f"All the columns in the claim forms are not filled"
              f"{_detail(a, 'forms_unfilled', ' – ', '')}. The claim forms may be "
              f"resubmitted duly filled in all respects.")
    if _no(a, "forms_signed"):
        r.add("FORMS_NOT_SIGNED", "11", "FORMS", EMPLOYER,
              "Claim forms not signed by all claimants",
              "The claim forms are not signed by all the claimants / beneficiaries. "
              "The forms may be resubmitted with the signature or left-hand thumb "
              "impression of each claimant.")
    if _no(a, "encl_attested") and attested != "NONE":
        r.add("ENCLOSURES_NOT_ATTESTED", "11", "FORMS", EMPLOYER,
              "Enclosures not attested",
              f"The following documents are not attested by the authorised signatory"
              f"{_detail(a, 'encl_unattested', ': ', '') or ': some of the enclosures'}"
              f". All the claim forms, enclosures and photographs are to be attested "
              f"by the authorised signatory.")

    # ---- Point 12: UAN and Aadhaar ----------------------------------------
    jd_kyc = _yes(a, "jd_aadhaar")
    if not a.get("uan_available"):
        need("12 – UAN generated or not")
    elif _no(a, "uan_available"):
        if jd_kyc:
            r.add("UAN_GENERATION", "12", "KYC", ACCOUNTS,
                  "Generate UAN and seed Aadhaar",
                  f"UAN is not generated in r/o the deceased member under PF A/c No. "
                  f"{pf}. UAN may be generated and the member's Aadhaar seeded to it "
                  f"after due verification and updation of member details through JD "
                  f"initiated from PRO. {NON_AADHAAR}")
        else:
            r.add("UAN_JD_REQUIRED", "12", "KYC", EMPLOYER,
                  "UAN not generated – JD and Aadhaar required",
                  "UAN is not generated for the deceased member. A Joint Declaration "
                  "signed by the claimant and the employer, along with a copy of the "
                  "member's Aadhaar and the other enclosures prescribed under the SOP, "
                  "is required for generation of UAN and seeding of Aadhaar (member's "
                  "Aadhaar is mandatory).")
    elif not a.get("aadhaar_seeded"):
        need("12 – Aadhaar seeded with UAN or not")
    elif _no(a, "aadhaar_seeded"):
        if jd_kyc:
            r.add("AADHAAR_SEEDING", "12", "KYC", ACCOUNTS,
                  "Seed Aadhaar with UAN and update member details",
                  f"The member's Aadhaar is not seeded to UAN. Member's Aadhaar may be "
                  f"seeded to the UAN after due verification and updation of member "
                  f"details through JD initiated from PRO. {NON_AADHAAR}")
        else:
            r.add("AADHAAR_JD_REQUIRED", "12", "KYC", EMPLOYER,
                  "Aadhaar not seeded with UAN",
                  "Member's Aadhaar is not seeded with UAN. A Joint Declaration along "
                  "with the enclosures prescribed under the SOP is required for "
                  "linking Aadhaar to UAN (member's Aadhaar is mandatory); the same "
                  "may be submitted through the Employer Portal.")

    # ---- Points 13-21, 27: enclosures -------------------------------------
    def enclosure(key, point, code, title, problems, required=True):
        value = a.get(key)
        if not value:
            if required:
                need(f"{point} – {title}")
        elif value in problems:
            remark = _detail(a, f"remark_{point}")
            r.add(code, point, "ENCLOSURE", EMPLOYER, title,
                  problems[value].rstrip(".") + remark + ".")

    enclosure("death_cert", "13", "DEATH_CERTIFICATE", "Death certificate", {
        "NOT_ATTACHED": "The Death Certificate of the member is not enclosed. The "
                        "original / attested copy of the Death Certificate may be "
                        "submitted",
        "NOT_IN_ORDER": "The Death Certificate enclosed is not in order. A correct "
                        "Death Certificate duly attested may be submitted"})
    enclosure("fmc", "14", "FAMILY_MEMBERS_CERTIFICATE", "Family Members Certificate", {
        "NOT_ATTACHED": "The original Family Members Certificate issued by the "
                        "competent Revenue authority is not submitted. The latest "
                        "original certificate may be submitted",
        "NOT_IN_ORDER": "The Family Members Certificate enclosed is not in order. The "
                        "latest original certificate issued by the competent Revenue "
                        "authority may be submitted"}, required=not enom_valid)
    enclosure("family_aadhaar", "15", "BENEFICIARY_AADHAAR",
              "Aadhaar copies of beneficiaries", {
        "NOT_SUBMITTED": "Aadhaar copies of the family members eligible to receive "
                         "the benefits are not submitted. Attested copies may be "
                         "furnished",
        "NOT_TALLIED": "The names / dates of birth of the beneficiaries in the Aadhaar "
                       "copies are not tallying with the claim forms. The same may be "
                       "got corrected and attested copies furnished"})
    enclosure("child_dob", "16", "CHILDREN_DOB_PROOF", "DOB proofs of children", {
        "NOT_SUBMITTED": "Date of birth proofs (Birth Certificate / School Leaving "
                         "Certificate) of the children below 25 years, attested by the "
                         "employer, are not enclosed"}, required=pension_claimed)
    enclosure("photos", "17", "FAMILY_PHOTOS", "Joint photographs", {
        "NOT_SUBMITTED": "Three copies of the joint photograph of the claimants "
                         "(widow along with children / both dependent parents) are not "
                         "submitted",
        "NOT_ATTESTED": "The joint photographs submitted are not attested by the "
                        "authorised signatory on the back side"},
              required=pension_claimed)
    enclosure("bank", "18", "BANK_ACCOUNTS", "Bank account copies", {
        "NOT_SUBMITTED": "Bank account copies (cancelled cheque / first page of the "
                         "passbook) of the claimants, duly attested by the authorised "
                         "signatory, are not submitted",
        "NOT_SAME_BRANCH": "The bank accounts of the widow and children are not in "
                           "the same bank and branch. Accounts of all the claimants "
                           "in the same bank and branch may be furnished",
        "NAMES_NOT_TALLIED": "The names of the beneficiaries in the bank account "
                             "copies are not tallying with the claim forms",
        "NOT_ATTESTED": "The bank account copies are not attested by the authorised "
                        "signatory",
        "PASSBOOK_OLD": "The APGB/SGB passbook copy submitted is not the updated one. "
                        "The latest passbook copy with printed account number and "
                        "IFSC may be submitted duly attested"})
    if not a.get("minor_children"):
        need("19 – Minor children among beneficiaries or not")
    elif _yes(a, "minor_children"):
        muslim = a.get("muslim_minor")
        if muslim == "NOT_COMPLIED":
            r.add("MINOR_ACCOUNT_NATURAL_GUARDIAN", "20", "ENCLOSURE", EMPLOYER,
                  "Minor's account (natural guardian) not submitted",
                  "For release of the minor's share of benefits, either a joint bank "
                  "account with the natural guardian or an individual bank account in "
                  "the name of the minor is required. The same may be submitted duly "
                  "attested.")
        elif muslim in ("JOINT", "INDIVIDUAL"):
            pass
        elif not a.get("minor_accounts"):
            need("19 – Arrangement for the minors' share")
        elif a.get("minor_accounts") == "NONE":
            r.add("MINOR_ACCOUNTS", "19", "ENCLOSURE", EMPLOYER,
                  "Minor children – bank accounts / guardianship not submitted",
                  "Individual bank accounts in the name of the minor children are not "
                  "submitted. Individual bank account copies of the minor children "
                  "(in which case no separate Guardianship Certificate is required) "
                  "may be furnished duly attested.")
    if a.get("ncp_statement") == "NOT_SUBMITTED":
        r.add("NCP_STATEMENT", "21", "ENCLOSURE", EMPLOYER, "NCP days statement",
              "As there is variation in the wages of the member during the last 60 "
              "months, an NCP days statement for the said period, duly attested by "
              "the authorised signatory, may be submitted.")
    enclosure("desc_rolls", "27", "DESCRIPTIVE_ROLLS", "Descriptive rolls", {
        "NOT_ENCLOSED": "Descriptive rolls of the widow and children / eligible "
                        "pensioners are not submitted. Descriptive rolls of all the "
                        "eligible family members may be submitted duly attested"},
              required=pension_claimed)

    # ---- Points 22 / 23: pension eligibility ------------------------------
    marital = a.get("clm_marital") or a.get("off_marital")
    gap = _years(off_dol or clm_dol, dod) if away else None
    if service is not None:
        r.fact("22", f"Eligible service about {service:.1f} years.")
    if gap is not None:
        r.fact("22", f"Death occurred {gap:.1f} years after the date of exit.")
    reasons = []
    if away and service is not None and service < 10:
        if gap is not None and gap > 3:
            reasons.append(("22", "the member died away from service with eligible "
                                  "service of less than 10 years, and the death "
                                  "occurred more than 3 years after the date of exit"))
        if marital == "UNMARRIED":
            reasons.append(("23", "the member was un-married and died away from "
                                  "service without eligible service of 10 years"))
    if reasons:
        point = reasons[0][0]
        why = _join(text for _, text in reasons)
        who = "dependent parents" if marital == "UNMARRIED" else "family"
        r.fact(point, "NOT eligible for pension – eligible only for Form 10C (W/B).")
        if pension_claimed:
            r.add("PENSION_NOT_ADMISSIBLE", point, "ELIGIBILITY", CLAIMANT,
                  "Form 10D not admissible – Form 10C to be submitted",
                  f"As {why}, the {who} are not eligible for monthly pension and the "
                  f"claim in Form 10D is not admissible. The {who} are eligible only "
                  f"for Withdrawal Benefit, for which a claim in Form 10C duly filled, "
                  f"signed and attested may be submitted.")
        else:
            r.add("PENSION_NOT_ADMISSIBLE", point, "ELIGIBILITY", NOTE,
                  "Not eligible for pension – only Form 10C (W/B)",
                  f"As {why}, the {who} are eligible only for Withdrawal Benefit "
                  f"(Form 10C) and not for monthly pension.")

    # ---- Point 24: interest ------------------------------------------------
    if _yes(a, "interest_excess"):
        limit = ""
        if dod:
            try:
                limit = f" (i.e. up to {fmt_date(dod.replace(year=dod.year + 3))})"
            except ValueError:
                limit = ""
        r.add("EXCESS_INTEREST", "24", "ACCOUNTS", ACCOUNTS,
              "Deduct interest credited beyond 3 years from death",
              f"Interest in r/o PF A/c No. {pf} is to be restricted to 3 years from "
              f"the date of death of the member{limit}. Interest credited beyond the "
              f"said period is to be deducted from the member's balance before "
              f"settlement of the death claims.")

    # ---- Office-side re-credit --------------------------------------------
    if _yes(a, "recredit"):
        r.add("RECREDIT", "", "ACCOUNTS", ACCOUNTS,
              "Re-credit amount to the member's account",
              f"The following amount is to be re-credited to PF A/c No. {pf} of the "
              f"deceased member before settlement of the death claims"
              f"{_detail(a, 'recredit_detail', ': ', '') or ' (particulars enclosed)'}"
              f".")

    # ---- Point 28: belated claims -----------------------------------------
    delay = _years(dod, ctx.received_on)
    if delay is not None and delay > ctx.belated_years:
        clarification = ("A written clarification for the delay is enclosed."
                         if _yes(a, "delay_clarification") else
                         "A clarification letter may be obtained from the employer and "
                         "the claimant explaining the cause of the delay.")
        r.fact("28", f"Claim received {delay:.1f} years after death – to be referred "
                     f"to AEO.")
        r.add("BELATED_CLAIM", "28", "DELAY", AEO,
              f"Claim submitted more than {ctx.belated_years} years after death",
              f"The member expired on {fmt_date(dod)} and the death claims have been "
              f"submitted on {fmt_date(ctx.received_on)}, i.e. after more than "
              f"{ctx.belated_years} years from the date of death. The genuineness of "
              f"the claim and the identity of the claimants may be verified. "
              f"{clarification}")
    elif delay is not None:
        r.fact("28", f"Claim received {delay:.1f} years after death – within "
                     f"{ctx.belated_years} years.")

    r.findings.sort(key=lambda f: (int(f.point) if f.point.isdigit() else 99))
    return r


# -- persistence -------------------------------------------------------------
def sync_findings(scrutiny, result):
    """Store the engine's findings, preserving the DA's own edits."""
    from .models import Finding

    existing = {f.code: f for f in scrutiny.findings.all()}
    seen = set()
    for order, draft in enumerate(result.findings, start=1):
        seen.add(draft.code)
        finding = existing.get(draft.code)
        if finding is None:
            Finding.objects.create(
                scrutiny=scrutiny, claim=scrutiny.claim, code=draft.code,
                point=draft.point, category=draft.category, route=draft.route,
                title=draft.title, text=draft.text, other_office=draft.other_office,
                order=order)
            continue
        finding.point, finding.category = draft.point, draft.category
        finding.title, finding.order = draft.title, order
        finding.other_office = draft.other_office
        if not finding.edited:
            finding.text, finding.route = draft.text, draft.route
        finding.save()
    for code, finding in existing.items():
        if finding.auto and code not in seen:
            finding.delete()


def proposed_action(scrutiny):
    routes = scrutiny.blocking_findings().values_list("route", flat=True)
    return recommend(routes)
