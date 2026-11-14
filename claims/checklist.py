"""The Death Claims Checklist (28 points) as data.

The scrutiny screen, the printable checklist and the rules engine are all
driven from POINTS, so when Head Office revises the checklist only this file
(and, for a new check, rules.py) has to change.

Answers are stored in Scrutiny.answers as {field name: value}:
  yn / ynna / choice -> the choice key, e.g. "YES"
  date               -> "YYYY-MM-DD"
  month              -> "YYYY-MM"
  text / number      -> string
"""

from dataclasses import dataclass, field
from datetime import date

YN = [("YES", "Yes"), ("NO", "No")]
YNNA = YN + [("NA", "N/A")]


@dataclass
class F:
    name: str
    label: str
    kind: str = "yn"  # yn | ynna | choice | date | month | text | number
    choices: list = field(default_factory=list)
    short: str = ""
    help: str = ""

    def __post_init__(self):
        if self.kind == "yn":
            self.choices = YN
        elif self.kind == "ynna":
            self.choices = YNNA

    @property
    def is_choice(self):
        return self.kind in ("yn", "ynna", "choice")

    def display(self, value):
        if value in (None, ""):
            return ""
        if self.is_choice:
            return dict(self.choices).get(value, value)
        if self.kind == "date":
            return fmt_date(value)
        if self.kind == "month":
            return fmt_month(value)
        return str(value)


@dataclass
class P:
    no: str
    title: str
    fields: list = field(default_factory=list)
    special: str = ""  # forms | mode | compare | previous | computed
    page: int = 1


# -- point 3: office records vs claim form ---------------------------------
GENDER = [("M", "Male"), ("F", "Female")]
MARITAL = [("MARRIED", "Married"), ("UNMARRIED", "Un-married"),
           ("WIDOWED", "Widow / widower"), ("DIVORCED", "Divorced")]
OFFICE_EXIT = [("NOT_UPDATED", "Not updated"), ("DEATH", "Death in service"),
               ("CESSATION", "Cessation / resignation"),
               ("SUPERANNUATION", "Superannuation"), ("RETIREMENT", "Retirement"),
               ("DISABLEMENT", "Permanent disablement"), ("OTHER", "Other")]
CLAIM_EXIT = [("DEATH_IN_SERVICE", "Death in service"),
              ("DEATH_AWAY", "Death away from service")]

# (key, label, kind, choices for office column, choices for claim column)
COMPARE_ROWS = [
    ("name", "MEMBER NAME", "text", None, None),
    ("father", "FATHER/SPOUSE NAME", "text", None, None),
    ("dob", "DATE OF BIRTH", "date", None, None),
    ("gender", "GENDER", "choice", GENDER, GENDER),
    ("marital", "MARITAL STATUS", "choice", MARITAL, MARITAL),
    ("doj", "DATE OF JOINING", "date", None, None),
    ("dol", "DATE OF LEAVING", "date", None, None),
    ("dod", "DATE OF DEATH", "date", None, None),
    ("reason", "REASON OF EXIT", "choice", OFFICE_EXIT, CLAIM_EXIT),
]
# Rows whose two columns must agree, with the wording used in a JD remark.
PROFILE_FIELDS = {
    "name": "Name of the member",
    "father": "Father's / spouse name",
    "dob": "Date of birth",
    "gender": "Gender",
    "marital": "Marital status",
    "doj": "Date of joining",
}

ENCL = [("OK", "Attached & in order"), ("NOT_IN_ORDER", "Attached – not in order"),
        ("NOT_ATTACHED", "Not attached")]

POINTS = [
    P("1", "Nature of the claim form 10D, Form 20 & Form 5IF", special="forms"),
    P("2", "Mode of receipt (By Post / In person)", special="mode"),
    P("3", "Details as per office records and as per claim forms & enclosures",
      special="compare"),
    P("4", "Whether the establishment is Live or Closed?", [
        F("estt_status", "Status as per office records", "choice",
          [("LIVE", "Live"), ("CLOSED", "Closed")], short="Establishment"),
        F("estt_claimed_closed", "Claimant states that the establishment is closed",
          short="Claimant states closed"),
        F("attested_by", "Claim forms & enclosures attested by", "choice",
          [("EMPLOYER", "Authorised signatory of the establishment"),
           ("GAZETTED", "Gazetted Officer / other authorised official"),
           ("NONE", "Not attested")], short="Attested by"),
    ]),
    P("5", "Whether the establishment is Exempted under EDLI or not? Check whether "
           "EDLI contributions are received till date of death of the member or not? "
           "Clearly mention the same.", [
        F("edli_exempted", "Establishment exempted under EDLI", short="EDLI exempted"),
        F("edli_contrib", "EDLI contributions (A/c No. 21) received till date of death",
          "choice", [("ALL", "Received in full"),
                     ("PARTLY", "Not received for some period"),
                     ("NONE", "Not received"), ("NA", "N/A (exempted)")],
          short="A/c 21 contributions"),
        F("edli_gap_period", "Period for which A/c 21 contributions are not received",
          "text", short="Not received for",
          help="e.g. 01.04.2021 to 30.04.2023"),
    ]),
    P("6", "Whether DSC/E-Sign of the establishment is active or not? If active, the "
           "signature in claim form is tallying with DSC/E-Sign signature or not?", [
        F("dsc_status", "DSC / e-Sign of the establishment", "choice",
          [("ACTIVE", "Active"), ("NOT_REGISTERED", "Not registered"),
           ("EXPIRED", "Expired / inactive")], short="DSC/e-Sign"),
        F("sign_tally", "Signature in claim forms tallies with DSC / e-Sign", "ynna",
          short="Signature tallying"),
    ]),
    P("7", "Previous and Present employment status of member should be checked in MIS "
           "- Whether transferred to present PF account or not should be mentioned "
           "here.", [
        F("scheme_cert", "Original Scheme Certificate for past service", "choice",
          [("NA", "N/A"), ("ATTACHED", "Attached"), ("NOT_ATTACHED", "Not attached")],
          short="Scheme certificate"),
    ], special="previous"),
    P("8", "Contributions details to be checked: (a) from date of joining to date of "
           "exit (death away from service); (b) from date of joining till date of "
           "death (death in service); (c) NCP days updated properly or not; (d) "
           "delayed payment for the month of death – explanation with documentary "
           "proof.", [
        F("first_contrib_month", "First wage month for which contribution is received",
          "month", short="Contributions from"),
        F("lcm", "Last contributed month (LCM) as per member ledger", "month",
          short="LCM"),
        F("contrib_complete", "Contributions received for all months between joining "
                              "and exit / death", short="All months received"),
        F("contrib_gaps", "Wage months for which contributions are not received",
          "text", short="Gaps", help="e.g. 12/2017, 06/2018, 10/2018 to 01/2019"),
        F("ncp_updated", "NCP days updated properly", "ynna", short="NCP days updated"),
        F("post_death_contrib", "Contributions received after the date of death",
          short="Post-death contributions"),
        F("post_death_detail", "Post-death contribution particulars", "text",
          short="Post-death detail", help="e.g. up to 11/2024"),
        F("death_month_contrib", "Contribution for the month of death (death in "
                                 "service)", "choice",
          [("PAID", "Paid in time"), ("PAID_LATE", "Paid belatedly"),
           ("NOT_PAID", "Not paid"), ("NA", "N/A (away from service)")],
          short="Death-month contribution"),
        F("death_month_paid_on", "If paid belatedly – date of remittance", "date",
          short="Remitted on"),
        F("death_month_proof", "Employer's explanation with documentary proof "
                               "(attendance / wage registers) attached", "ynna",
          short="Explanation & proof"),
        F("wage_anomaly", "Abnormal variation in wages / contributions (sudden rise, "
                          "fall or long non-contributory period)",
          short="Abnormal variation"),
        F("wage_anomaly_detail", "Details of the variation", "text", short="Variation"),
    ]),
    P("9", "In case member joined after 01.09.2014 - Check whether EPF Wages > "
           "Rs.15,000/- for Pension eligibility, and mention here clearly.", [
        F("wages_gt_15000", "EPF wages at the time of joining were above Rs.15,000/-",
          "ynna", short="Wages > 15,000 at joining"),
        F("prior_eps_member", "Member was an EPS member before 01.09.2014 (past "
                              "service)", "ynna", short="EPS member before 01.09.2014"),
        F("eps_contributed", "Pension contributions (A/c No. 10) remitted",
          short="EPS contributions remitted"),
        F("eps_wage_proof", "Wage register / salary slips at joining and employer's "
                            "confirmation of EPS eligibility on record", "ynna",
          short="Employer confirmation on record"),
    ]),
    P("10", "Check whether the E-Nomination made by the member while he was alive. In "
            "case of E-Nomination done after death of the member by somebody, the "
            "said E-Nomination is invalid. Clearly mention the same.", [
        F("enom_status", "E-Nomination", "choice",
          [("ALIVE", "Filed while the member was alive"),
           ("AFTER_DEATH", "Filed after death (invalid)"), ("NOT_FILED", "Not filed")],
          short="E-Nomination"),
        F("enom_date", "Date of e-nomination", "date", short="Dated"),
        F("enom_nominee", "In favour of", "text", short="In favour of"),
    ]),
    P("11", "Check whether all the columns in F-20, 5IF & 10D filled or not and all "
            "the enclosures are attested by the Authorised signatory. If not, "
            "specifically mention which columns were not filled/not attested.", [
        F("forms_filled", "All columns in the claim forms are filled",
          short="All columns filled"),
        F("forms_unfilled", "Columns not filled", "text", short="Not filled"),
        F("forms_signed", "Claim forms signed by all the claimants",
          short="Signed by all claimants"),
        F("encl_attested", "All claim forms and enclosures attested by the authorised "
                           "signatory", short="All enclosures attested"),
        F("encl_unattested", "Documents not attested", "text", short="Not attested"),
    ]),
    P("12", "Check whether member Aadhaar is seeded with UAN. If not, JD & enclosures "
            "as per SOP are required for linking aadhar to UAN. (Member Aadhar is "
            "mandatory)", [
        F("uan_available", "UAN is generated for the member", short="UAN generated"),
        F("aadhaar_seeded", "Member's Aadhaar is seeded with UAN", "ynna",
          short="Aadhaar seeded"),
        F("jd_aadhaar", "JD & enclosures for UAN generation / Aadhaar seeding "
                        "attached", "ynna", short="JD for Aadhaar attached"),
    ]),
    P("13", "Original Death Certificate attached or not. If attached, the same is in "
            "order or not to be specified here.", [
        F("death_cert", "Death certificate", "choice", ENCL, short="Death certificate"),
    ], page=2),
    P("14", "Whether FMC attached or not. If attached, the same is in order or not to "
            "be specified here.", [
        F("fmc", "Family Members Certificate", "choice", ENCL + [("NA", "N/A")],
          short="FMC"),
    ], page=2),
    P("15", "Aadhaar Card copies of the family members who are eligible to receive "
            "benefits. Names & DOB of beneficiaries are in order and tallied or not.", [
        F("family_aadhaar", "Aadhaar copies of the beneficiaries", "choice",
          [("OK", "Submitted & tallied"), ("NOT_TALLIED", "Names / DOB not tallied"),
           ("NOT_SUBMITTED", "Not submitted")], short="Aadhaar of beneficiaries"),
    ], page=2),
    P("16", "DOB proofs of children below 25 years (attested by employer).", [
        F("child_dob", "DOB proofs of children below 25 years", "choice",
          [("OK", "Submitted & attested"), ("NOT_SUBMITTED", "Not submitted"),
           ("NA", "N/A")], short="DOB proofs"),
    ], page=2),
    P("17", "03 Photos of Family: (i) widow along with children in case of Married. "
            "(ii) dependent parents (both parents in one photo) in case of "
            "Un-married. (attested by the authorized signatory on back side)", [
        F("photos", "3 joint photographs", "choice",
          [("OK", "Submitted & attested"), ("NOT_ATTESTED", "Submitted – not attested"),
           ("NOT_SUBMITTED", "Not submitted"), ("NA", "N/A")], short="Photographs"),
    ], page=2),
    P("18", "Bank Account copies of widow & children (in same bank & branch) duly "
            "attested by the authorized signatory. Names of Beneficiaries in the bank "
            "copies tallied or not. If bank accounts of APGB/SGB are submitted, "
            "please verify for updated/latest passbook with printed acct.no. & IFSC.", [
        F("bank", "Bank account copies", "choice",
          [("OK", "In order"), ("NOT_SUBMITTED", "Not submitted"),
           ("NOT_SAME_BRANCH", "Not in the same bank & branch"),
           ("NAMES_NOT_TALLIED", "Names not tallied"),
           ("NOT_ATTESTED", "Not attested"),
           ("PASSBOOK_OLD", "APGB/SGB passbook not updated (A/c no. & IFSC not "
                            "printed)")], short="Bank accounts"),
    ], page=2),
    P("19", "No separate Guardianship Certificate is required if individual bank "
            "accounts in the name of the minor children are submitted (As per HO "
            "circular dt.13.08.2025).", [
        F("minor_children", "Minor children among the beneficiaries",
          short="Minor children"),
        F("minor_accounts", "Arrangement for the minors' share", "choice",
          [("INDIVIDUAL", "Individual accounts of minors"),
           ("GUARDIAN_CERT", "Guardianship certificate"),
           ("NONE", "Neither submitted"), ("NA", "N/A")], short="Minors' share"),
    ], page=2),
    P("20", "In case of the Muslim community, either a Joint Bank Account with the "
            "Natural Guardian (father's father) or an Individual Minor Bank Account "
            "shall be accepted for release of the minor's share of benefits.", [
        F("muslim_minor", "Applicable?", "choice",
          [("NA", "N/A"),
           ("JOINT", "Joint account with natural guardian submitted"),
           ("INDIVIDUAL", "Individual minor account submitted"),
           ("NOT_COMPLIED", "Applicable – not submitted")], short="Muslim minor share"),
    ], page=2),
    P("21", "NCP days statement (in case of variation in wages in last 60 months).", [
        F("ncp_statement", "NCP days statement", "choice",
          [("NA", "N/A – no variation"), ("SUBMITTED", "Submitted"),
           ("NOT_SUBMITTED", "Required – not submitted")], short="NCP statement"),
    ], page=2),
    P("22", "If member died away from service (having service less than 10 years) and "
            "member died after 3 years from Date of Exit - Not eligible for Pension. "
            "Eligible only for Form-10C (W/B).", [
        F("eligible_service", "Eligible (pensionable) service in years, including "
                              "past service", "number", short="Eligible service (yrs)",
          help="Leave blank to use the service computed from the dates above."),
    ], special="computed", page=2),
    P("23", "If Un-married member died away from service without eligible service of "
            "10 years – Family not eligible for Pension. Dependent Parents eligible "
            "only for F-10C (W/B).", special="computed", page=2),
    P("24", "Verify that interest is restricted to 3 years from the Date of Death; If "
            "not, excess interest to be deducted from the Member's balance.", [
        F("interest_excess", "Interest credited beyond 3 years from the date of "
                             "death", "ynna", short="Excess interest credited"),
    ], page=2),
    P("25", "Check whether all the details in the member profile are updated in FO, "
            "if not JD is required for updating the same.", [
        F("jd_attached", "Joint Declaration attached with the claim", "ynna",
          short="JD attached"),
    ], special="computed", page=2),
    P("26", "If Joint declaration is attached for correction of member profile "
            "details, ensure that all required corrections are updated before Inward "
            "receipt entry of death claims.", [
        F("jd_updated", "JD inwarded and corrections updated", "ynna",
          short="JD corrections updated"),
    ], page=2),
    P("27", "Check whether Descriptive rolls of the eligible pensioners are enclosed "
            "or not.", [
        F("desc_rolls", "Descriptive rolls", "choice",
          [("OK", "Enclosed"), ("NOT_ENCLOSED", "Not enclosed"), ("NA", "N/A")],
          short="Descriptive rolls"),
    ], page=2),
    P("28", "If Death claims are submitted after 6 years from the member's death, the "
            "Death Case shall be referred to the AEO for verification. A written "
            "clarification for delay shall be obtained.", [
        F("delay_clarification", "Written clarification for the delay enclosed",
          "ynna", short="Delay clarification"),
    ], special="computed", page=2),
]

# Office-side actions that no checklist point covers.
EXTRA_FIELDS = [
    F("recredit", "Re-credit to the member's account required (amount received "
                  "back by DD / dues realised)", short="Re-credit required"),
    F("recredit_detail", "Particulars (DD no., date, amount, nature)", "text",
      short="Re-credit particulars"),
]

PREV_COLUMNS = [("member_id", "PF account / member ID"), ("estt", "Establishment"),
                ("office", "PF office (blank = RO Kadapa)"), ("from", "From"),
                ("to", "To"), ("transferred", "Transferred?")]

FIELDS = {f.name: f for p in POINTS for f in p.fields}
FIELDS.update({f.name: f for f in EXTRA_FIELDS})
POINT_BY_NO = {p.no: p for p in POINTS}


# -- value helpers -----------------------------------------------------------
def parse_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def parse_month(value):
    """'YYYY-MM' -> (year, month)"""
    try:
        y, m = str(value).split("-")[:2]
        return int(y), int(m)
    except (ValueError, AttributeError):
        return None


def fmt_date(value):
    d = parse_date(value)
    return d.strftime("%d.%m.%Y") if d else ""


def fmt_month(value):
    ym = value if isinstance(value, tuple) else parse_month(value)
    return f"{ym[1]:02d}/{ym[0]}" if ym else ""


def compare_display(key, side, value):
    """Human text for one cell of the point-3 comparison table."""
    for row_key, _label, kind, office_choices, claim_choices in COMPARE_ROWS:
        if row_key == key:
            if kind == "date":
                return fmt_date(value)
            if kind == "choice":
                choices = office_choices if side == "off" else claim_choices
                return dict(choices).get(value, value or "")
            return value or ""
    return value or ""


def answers_from_post(post):
    """Collect checklist answers from a submitted scrutiny form."""
    answers = {}
    for name in FIELDS:
        answers[name] = post.get(name, "").strip()
    for key, *_ in COMPARE_ROWS:
        for side in ("off", "clm"):
            answers[f"{side}_{key}"] = post.get(f"{side}_{key}", "").strip()
    for p in POINTS:
        answers[f"remark_{p.no}"] = post.get(f"remark_{p.no}", "").strip()
    ids = post.getlist("prev_member_id")
    rows = []
    for i, member_id in enumerate(ids):
        def col(name, i=i):
            values = post.getlist(f"prev_{name}")
            return values[i].strip() if i < len(values) else ""
        row = {"member_id": member_id.strip(), "estt": col("estt"),
               "office": col("office"), "from": col("from"), "to": col("to"),
               "transferred": col("transferred") or "NO"}
        if row["member_id"] or row["estt"]:
            rows.append(row)
    answers["prev_services"] = rows
    answers["no_prev_service"] = "YES" if post.get("no_prev_service") else ""
    return answers


def initial_answers(claim):
    """Pre-fill a first-round scrutiny from the diary entry and estt master."""
    est = claim.establishment
    return {
        "clm_name": claim.member_name,
        "clm_gender": claim.gender,
        "clm_dod": claim.date_of_death.isoformat() if claim.date_of_death else "",
        "estt_status": est.status,
        "edli_exempted": "YES" if est.edli_exempted else "NO",
        "dsc_status": est.dsc_status,
        "uan_available": "YES" if claim.uan else "",
        "prev_services": [],
    }


def point_remarks(claim, answers, facts=None):
    """Text for the 'Remarks' column of the printed checklist, point by point."""
    out = {}
    for p in POINTS:
        parts = []
        if p.special == "forms":
            parts.append(claim.forms_text)
        elif p.special == "mode":
            parts.append(f"{claim.get_mode_display()} on {fmt_date(claim.received_on)}")
        elif p.special == "previous":
            rows = answers.get("prev_services") or []
            if rows:
                for r in rows:
                    period = " to ".join(x for x in (fmt_date(r.get("from")),
                                                     fmt_date(r.get("to"))) if x)
                    state = ("transferred" if r.get("transferred") == "YES"
                             else "NOT transferred")
                    office = f", {r['office']}" if r.get("office") else ""
                    parts.append(f"{r.get('member_id')} ({r.get('estt')}{office}"
                                 f"{'; ' + period if period else ''}) – {state}")
            elif answers.get("no_prev_service"):
                parts.append("No previous service found in MIS")
        for f in p.fields:
            shown = f.display(answers.get(f.name))
            if shown:
                parts.append(f"{f.short or f.label}: {shown}")
        if facts and p.no in facts:
            parts.append(facts[p.no])
        remark = answers.get(f"remark_{p.no}")
        if remark:
            parts.append(remark)
        out[p.no] = parts
    return out
