"""Load the office masters and (optionally) fictional demo cases.

    python manage.py seed_demo            # masters + demo users + demo cases
    python manage.py seed_demo --masters  # only AEO offices and demo users

All members, claimants and establishments below are invented.
"""

from datetime import date, datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from claims import workflow
from claims.models import (AEOOffice, Claim, Communication, Establishment, Event, Role,
                           User)

PASSWORD = "kadapa@123"
TODAY = timezone.localdate()


def ago(days):
    return TODAY - timedelta(days=days)


AEO_OFFICES = {
    "Kadapa": "Regional Office,\nKadapa.",
    "Kurnool": "District Office,\nKurnool.",
    "Tirupati": "District Office,\nTirupati.",
    "Anantapur": "District Office,\nAnantapur.",
    "Nellore": "District Office,\nNellore.",
}
USERS = [
    ("da1", "Lakshmi", "Prasanna", Role.DA, "Senior Social Security Assistant"),
    ("da2", "Ravi", "Teja", Role.DA, "Social Security Assistant"),
    ("ss1", "Padmaja", "Rao", Role.SS, "Section Supervisor"),
    ("ao1", "Srinivasa", "Murthy", Role.AO, "Accounts Officer (D/C)"),
    ("nodal", "Nodal", "Officer", Role.ADMIN, "Nodal Officer, Death Claims Cell"),
]
ESTABLISHMENTS = [
    ("GRCDP0098101000", "Seshachala Spinning Mills Pvt. Ltd.",
     "Plot 14, Industrial Estate,\nRenigunta Road,\nTirupati – 517506.", "Tirupati",
     "LIVE", False, "ACTIVE"),
    ("GRCDP0098102000", "Penna Valley Cements Ltd.",
     "Factory Road, Yerraguntla,\nKadapa Dist – 516309.", "Kadapa", "LIVE", False,
     "ACTIVE"),
    ("GRCDP0098103000", "Simhapuri Aqua Exports",
     "Survey No. 88, Kovur Mandal,\nNellore – 524137.", "Nellore", "LIVE", False,
     "ACTIVE"),
    ("GRCDP0098104000", "Nallamala Dairy Producers Co-op. Society Ltd.",
     "Station Road,\nNandyal – 518501.", "Kurnool", "LIVE", False, "ACTIVE"),
    ("GRCDP0098105000", "Rayadurg Motors",
     "12-4-7, Bellary Road,\nAnantapur – 515001.", "Anantapur", "LIVE", False,
     "NOT_REGISTERED"),
    ("GRCDP0098106000", "Tungabhadra Security Services",
     "Door No. 40/12, Bhagya Nagar,\nKurnool – 518004.", "Kurnool", "LIVE", False,
     "ACTIVE"),
    ("GRCDP0098107000", "Chandragiri Power Distribution Co.",
     "Corporate Office, Tiruchanur Road,\nTirupati – 517503.", "Tirupati", "LIVE",
     True, "ACTIVE"),
]


def iso(d):
    return d.isoformat()


def clean_answers(claim, doj, dob, in_service=True, dol=None, father="Venkata Subbaiah"):
    """Checklist answers of a claim that is fully in order."""
    dod = claim.date_of_death
    dol = dod if in_service else dol
    est = claim.establishment
    a = {
        "off_name": claim.member_name, "clm_name": claim.member_name,
        "off_father": father, "clm_father": father,
        "off_dob": iso(dob), "clm_dob": iso(dob),
        "off_gender": claim.gender, "clm_gender": claim.gender,
        "off_marital": "MARRIED", "clm_marital": "MARRIED",
        "off_doj": iso(doj), "clm_doj": iso(doj),
        "off_dol": iso(dol), "clm_dol": iso(dol),
        "off_dod": iso(dod) if in_service else "", "clm_dod": iso(dod),
        "off_reason": "DEATH" if in_service else "CESSATION",
        "clm_reason": "DEATH_IN_SERVICE" if in_service else "DEATH_AWAY",
        "estt_status": est.status, "estt_claimed_closed": "NO",
        "attested_by": "EMPLOYER",
        "edli_exempted": "YES" if est.edli_exempted else "NO",
        "edli_contrib": "NA" if est.edli_exempted else "ALL",
        "dsc_status": est.dsc_status, "sign_tally": "YES",
        "prev_services": [], "no_prev_service": "YES", "scheme_cert": "NA",
        "first_contrib_month": f"{doj.year}-{doj.month:02d}",
        "lcm": f"{dol.year}-{dol.month:02d}",
        "contrib_complete": "YES", "ncp_updated": "YES", "post_death_contrib": "NO",
        "death_month_contrib": "PAID" if in_service else "NA",
        "wage_anomaly": "NO",
        "wages_gt_15000": "NO" if doj >= date(2014, 9, 1) else "NA",
        "eps_contributed": "YES",
        "enom_status": "NOT_FILED",
        "forms_filled": "YES", "forms_signed": "YES", "encl_attested": "YES",
        "uan_available": "YES", "aadhaar_seeded": "YES",
        "death_cert": "OK", "fmc": "OK", "family_aadhaar": "OK", "child_dob": "OK",
        "photos": "OK", "bank": "OK", "minor_children": "NO", "ncp_statement": "NA",
        "interest_excess": "NO", "desc_rolls": "OK",
    }
    return a


class Command(BaseCommand):
    help = "Load AEO offices, demo users and fictional demo death cases."

    def add_arguments(self, parser):
        parser.add_argument("--masters", action="store_true",
                            help="Only AEO offices and demo users; no demo cases.")

    @transaction.atomic
    def handle(self, *args, **options):
        offices = {}
        for name, address in AEO_OFFICES.items():
            offices[name], _ = AEOOffice.objects.get_or_create(
                name=name, defaults={"address": address})
        users = {}
        for username, first, last, role, designation in USERS:
            user, created = User.objects.get_or_create(username=username, defaults={
                "first_name": first, "last_name": last, "role": role,
                "designation": designation,
                "is_staff": role == Role.ADMIN, "is_superuser": role == Role.ADMIN})
            if created:
                user.set_password(PASSWORD)
                user.save()
            users[username] = user
        self.stdout.write(f"Users: {', '.join(users)}  (password: {PASSWORD})")
        if options["masters"]:
            return
        if Claim.objects.exists():
            self.stdout.write("Claims already exist – demo cases not loaded again.")
            return

        estt = {}
        for code, name, address, district, status, exempt, dsc in ESTABLISHMENTS:
            estt[name.split()[0]] = Establishment.objects.create(
                code=code, name=name, address=address, district=district,
                status=status, edli_exempted=exempt, dsc_status=dsc,
                aeo_office=offices[district])
        self.cases(users, estt)
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {Claim.objects.count()} demo death cases."))

    # ------------------------------------------------------------------
    def new_claim(self, users, days_ago, name, pf, est, death, claimant, relation,
                  gender="M", uan="", da="da1", forms=("20", "10D", "5IF"),
                  efile="", mode="POST", ag="103"):
        claim = Claim.objects.create(
            received_on=ago(days_ago), mode=mode, member_name=name, gender=gender,
            uan=uan, pf_no=pf, establishment=est, accounts_group=ag, efile_no=efile,
            date_of_death=death, claimant_name=claimant, claimant_relation=relation,
            claimant_address=f"D.No. 3-{days_ago + 11}, Main Road,\n"
                             f"{est.district} Dist, Andhra Pradesh.",
            claimant_mobile="", form_20="20" in forms, form_10d="10D" in forms,
            form_5if="5IF" in forms, form_10c="10C" in forms,
            assigned_to=users[da], created_by=users[da])
        claim.log(users[da], "Claim received and diarised",
                  f"{claim.forms_text}; {claim.get_mode_display().lower()}",
                  to_status="REGISTERED")
        return claim

    def scrutinise(self, claim, users, answers, upto="approve", da="da1"):
        scrutiny = workflow.start_scrutiny(claim, users[da])
        workflow.save_answers(scrutiny, answers, users[da])
        if upto == "draft":
            return
        workflow.submit(claim, users[da], "Scrutinised as per checklist.")
        if upto == "submit":
            return
        workflow.validate(claim, users["ss1"], "Verified with MIS and ledger.")
        if upto == "validate":
            return
        workflow.approve(claim, users["ao1"])

    def backdate(self, claim):
        """Spread the audit trail between receipt and today for realism."""
        events = list(claim.events.order_by("id"))
        span = max((TODAY - claim.received_on).days, 0)
        for i, event in enumerate(events):
            day = claim.received_on + timedelta(
                days=round(span * i / max(len(events), 1) * 0.6))
            when = timezone.make_aware(datetime.combine(day, time(10 + i % 7, 15)))
            Event.objects.filter(pk=event.pk).update(at=min(when, timezone.now()))
        Claim.objects.filter(pk=claim.pk).update(
            stage_since=claim.events.order_by("-at").first().at)

    def issue(self, claim, users, days_ago, reply=False):
        for comm in claim.communications.filter(status=Communication.Status.APPROVED):
            Communication.objects.filter(pk=comm.pk).update(letter_date=ago(days_ago))
            workflow.issue_letter(comm, users["da1"], ago(days_ago), "Speed post")
            if reply:
                comm.refresh_from_db()
                workflow.record_reply(comm, users["da1"], ago(max(days_ago - 9, 0)),
                                      "Documents received from the employer.", True)

    def cases(self, users, estt):
        U = users

        # 1. In order -> inwarded and settled
        c = self.new_claim(U, 58, "Bandi Ramanaiah", "GR/CDP/98101/412",
                           estt["Seshachala"], ago(120), "B. Sarojamma", "Wife",
                           uan="100345678901", efile="1310442")
        self.scrutinise(c, U, clean_answers(c, date(2009, 6, 1), date(1975, 4, 12)))
        workflow.record_inward(c, U["da1"], ago(40), {
            "Form 20": "GRCDP250900001234", "Form 10D": "GRCDP250900001235",
            "Form 5(IF)": "GRCDP250900001236"})
        rows = {f.id: {"state": "SETTLED", "settled_on": ago(28),
                       "amount": amount, "reference": ref}
                for f, amount, ref in zip(c.forms.all(), (412560, None, 700000),
                                          ("", "PPO GRCDP00054321", ""))}
        workflow.record_settlement(c, U["da1"], rows)
        self.backdate(c)

        # 2. In order -> inwarded, under settlement
        c = self.new_claim(U, 21, "Shaik Mahaboob Basha", "GR/CDP/98102/1187",
                           estt["Penna"], ago(70), "Shaik Rahimunnisa", "Wife",
                           uan="100345678902", da="da2")
        self.scrutinise(c, U, clean_answers(c, date(2012, 2, 15), date(1980, 8, 3)),
                        da="da2")
        workflow.record_inward(c, U["da2"], ago(9), {
            "Form 20": "GRCDP251000002210", "Form 10D": "GRCDP251000002211",
            "Form 5(IF)": "GRCDP251000002212"})
        self.backdate(c)

        # 3. In order -> approved, ready for inward
        c = self.new_claim(U, 6, "Gunturu Venkata Lakshmi", "GR/CDP/98107/2301",
                           estt["Chandragiri"], ago(44), "G. Narasimhulu", "Husband",
                           gender="F", uan="100345678903", forms=("20", "10D"))
        self.scrutinise(c, U, clean_answers(c, date(2006, 11, 20), date(1978, 1, 30)))
        self.backdate(c)

        # 4. Exit not updated + contributions after death -> letter to employer, overdue
        c = self.new_claim(U, 47, "Yerragudi Obulesu", "GR/CDP/98104/344",
                           estt["Nallamala"], ago(170), "Y. Lakshmi Devi", "Wife",
                           uan="100345678904", efile="1318870")
        a = clean_answers(c, date(2010, 3, 1), date(1972, 5, 9))
        a.update(off_dol="", off_dod="", off_reason="NOT_UPDATED",
                 post_death_contrib="YES", post_death_detail="up to 08/2026")
        self.scrutinise(c, U, a)
        self.issue(c, U, 38)
        self.backdate(c)

        # 5. Un-exempted EDLI not received -> AEO reference, awaited
        c = self.new_claim(U, 33, "Kavali Sai Charan", "GR/CDP/98103/10336",
                           estt["Simhapuri"], ago(95), "Kavali Pradeep", "Father",
                           uan="100345678905", efile="1336309", da="da2")
        a = clean_answers(c, date(2021, 4, 5), date(1997, 10, 14),
                          father="Kavali Pradeep")
        a.update(edli_contrib="PARTLY", edli_gap_period="01.04.2021 to 30.04.2023",
                 off_marital="UNMARRIED", clm_marital="UNMARRIED", child_dob="NA")
        self.scrutinise(c, U, a, da="da2")
        self.issue(c, U, 12)
        self.backdate(c)

        # 6. DSC not registered + descriptive rolls missing -> employer complied
        c = self.new_claim(U, 40, "Boya Jayashankar", "GR/CDP/98105/130",
                           estt["Rayadurg"], ago(110), "B. Anasuya", "Wife",
                           uan="100345678906")
        a = clean_answers(c, date(2016, 7, 1), date(1988, 3, 22))
        a.update(desc_rolls="NOT_ENCLOSED", sign_tally="NA")
        self.scrutinise(c, U, a)
        self.issue(c, U, 30, reply=True)
        self.backdate(c)

        # 7. No UAN (JD attached) + past service in another RO -> U.O. note + RO letter
        c = self.new_claim(U, 16, "Mallela Siva Subbaiah", "GR/CDP/98107/10513",
                           estt["Chandragiri"], ago(60), "M. Venkata Sivamma", "Wife",
                           forms=("20", "10D"), ag="107")
        a = clean_answers(c, date(2015, 4, 1), date(1970, 6, 6))
        a.update(uan_available="NO", aadhaar_seeded="", jd_aadhaar="YES",
                 no_prev_service="", prior_eps_member="YES", prev_services=[
                     {"member_id": "AP/HYD/72623/1077", "estt": "Deccan Infra Projects",
                      "office": "Hyderabad (Barkatpura)", "from": "2011-12-01",
                      "to": "2015-02-28", "transferred": "NO"}])
        self.scrutinise(c, U, a)
        self.backdate(c)

        # 8. Death in service, death-month contribution not paid -> with AO
        c = self.new_claim(U, 9, "Kothapalli Rajendra", "GR/CDP/98106/136",
                           estt["Tungabhadra"], ago(52), "K. Sujatha", "Wife",
                           uan="100345678908", efile="1340112", da="da2")
        a = clean_answers(c, date(2018, 1, 10), date(1984, 12, 1))
        a.update(estt_claimed_closed="YES",
                 attested_by="GAZETTED", death_month_contrib="NOT_PAID",
                 contrib_complete="NO", contrib_gaps="03/2026 to 08/2026",
                 ncp_updated="NO")
        self.scrutinise(c, U, a, upto="validate", da="da2")
        self.backdate(c)

        # 9. Joined after 01.09.2014 on high wages with EPS contribution -> with SS
        c = self.new_claim(U, 5, "Pasupuleti Hari Prasad", "GR/CDP/98101/3391",
                           estt["Seshachala"], ago(36), "P. Bhagyamma", "Mother",
                           uan="100345678909")
        a = clean_answers(c, date(2019, 9, 2), date(1971, 2, 18))
        a.update(wages_gt_15000="YES", prior_eps_member="NO", eps_contributed="YES",
                 eps_wage_proof="NO")
        self.scrutinise(c, U, a, upto="submit")
        self.backdate(c)

        # 10. Name mismatch, DOJ before contributions – scrutiny in progress
        c = self.new_claim(U, 3, "Pidugu Siddaiah", "GR/CDP/98102/1347",
                           estt["Penna"], ago(30), "P. Padmavathi", "Wife",
                           uan="100345678910", da="da2")
        a = clean_answers(c, date(2002, 8, 1), date(1969, 7, 7))
        a.update(off_name="P Sidhaiah", first_contrib_month="2013-05", bank="")
        self.scrutinise(c, U, a, upto="draft", da="da2")

        # 11, 12. Just received
        self.new_claim(U, 1, "Dudekula Khasim Vali", "GR/CDP/98104/902",
                       estt["Nallamala"], ago(25), "D. Fathima Bee", "Wife",
                       uan="100345678911", mode="IN_PERSON")
        self.new_claim(U, 0, "Tirumala Sher Singh", "GR/CDP/98103/10030",
                       estt["Simhapuri"], ago(19), "T. Poornima", "Wife",
                       uan="100345678912", da="da2")
