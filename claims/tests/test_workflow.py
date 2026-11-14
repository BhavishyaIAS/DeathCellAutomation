from datetime import date, timedelta
from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from docx import Document

from claims import workflow
from claims.docx_export import letter_docx
from claims.letters import build_reminder
from claims.models import (AEOOffice, Claim, ClaimStatus, Communication, Establishment,
                           Finding, Role, Route, User)
from claims.workflow import WorkflowError

from .test_rules import clean

S = ClaimStatus


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.da = User.objects.create_user("da", password="x", role=Role.DA)
        cls.ss = User.objects.create_user("ss", password="x", role=Role.SS)
        cls.ao = User.objects.create_user("ao", password="x", role=Role.AO)
        office = AEOOffice.objects.create(name="Nellore",
                                          address="District Office,\nNellore.")
        cls.estt = Establishment.objects.create(
            code="GRCDP1", name="Test Mills", address="Road 1,\nNellore.",
            district="Nellore", aeo_office=office)

    def claim(self, **over):
        data = dict(received_on=date(2026, 6, 3), member_name="K Nageswara Rao",
                    pf_no="GR/CDP/1/1", uan="100000000001", establishment=self.estt,
                    date_of_death=date(2026, 3, 10), claimant_name="K Lakshmi Devi",
                    claimant_relation="Wife", claimant_address="1-13A, Gospadu",
                    accounts_group="103", efile_no="1336309", created_by=self.da)
        data.update(over)
        return Claim.objects.create(**data)

    def to_ao(self, claim, **answers):
        scrutiny = workflow.start_scrutiny(claim, self.da)
        workflow.save_answers(scrutiny, clean(**answers), self.da)
        workflow.submit(claim, self.da)
        workflow.validate(claim, self.ss)
        return scrutiny


class Lifecycle(Base):
    def test_diary_numbers_run_serially_within_the_year(self):
        first, second = self.claim(), self.claim()
        self.assertEqual(first.diary_no, "DC/2026/0001")
        self.assertEqual(second.diary_no, "DC/2026/0002")

    def test_clean_claim_goes_from_receipt_to_settlement(self):
        claim = self.claim()
        self.to_ao(claim)
        workflow.approve(claim, self.ao)
        self.assertEqual(claim.status, S.READY_FOR_INWARD)
        self.assertFalse(claim.communications.exists())
        workflow.record_inward(claim, self.da, date(2026, 6, 10), {"Form 20": "C1"})
        self.assertEqual(claim.forms.count(), 3)
        rows = {f.id: {"state": "SETTLED", "settled_on": date(2026, 6, 20),
                       "amount": 1000, "reference": ""} for f in claim.forms.all()}
        workflow.record_settlement(claim, self.da, rows)
        self.assertEqual(claim.status, S.SETTLED)
        actions = list(claim.events.values_list("to_status", flat=True))
        self.assertIn(S.WITH_SS, actions)
        self.assertIn(S.SETTLED, actions)

    def test_incomplete_checklist_cannot_be_submitted(self):
        claim = self.claim()
        workflow.start_scrutiny(claim, self.da)
        with self.assertRaisesMessage(WorkflowError, "unanswered"):
            workflow.submit(claim, self.da)

    def test_each_level_can_only_do_its_own_step(self):
        claim = self.claim()
        with self.assertRaises(WorkflowError):
            workflow.start_scrutiny(claim, self.ao)
        scrutiny = workflow.start_scrutiny(claim, self.da)
        workflow.save_answers(scrutiny, clean(), self.da)
        workflow.submit(claim, self.da)
        with self.assertRaises(WorkflowError):
            workflow.validate(claim, self.da)
        with self.assertRaises(WorkflowError):
            workflow.save_answers(scrutiny, clean(), self.da)  # file has moved on
        workflow.validate(claim, self.ss)
        with self.assertRaises(WorkflowError):
            workflow.approve(claim, self.ss)

    def test_return_needs_a_reason_and_sends_the_file_back(self):
        claim = self.claim()
        self.to_ao(claim)
        with self.assertRaises(WorkflowError):
            workflow.send_back(claim, self.ao, " ")
        workflow.send_back(claim, self.ao, "Verify LCM again")
        self.assertEqual(claim.status, S.UNDER_SCRUTINY)
        self.assertEqual(claim.current_scrutiny.ao_note, "Verify LCM again")


class Letters(Base):
    def test_employer_letter_is_drafted_numbered_issued_and_complied(self):
        claim = self.claim()
        self.to_ao(claim, off_dol="", off_reason="NOT_UPDATED",
                   desc_rolls="NOT_ENCLOSED")
        draft = claim.communications.get()
        self.assertEqual(draft.kind, Communication.Kind.EMPLOYER)
        self.assertEqual(len(draft.points), 2)
        self.assertIn("M/s Test Mills", draft.to_block)
        self.assertIn("K Lakshmi Devi", draft.copy_to)
        self.assertIn("Area Enforcement Officer", draft.copy_to)

        workflow.approve(claim, self.ao)
        letter = claim.communications.get()
        self.assertEqual(claim.status, S.PENDING_COMPLIANCE)
        self.assertEqual(letter.status, Communication.Status.APPROVED)
        self.assertTrue(letter.number.endswith(f"/{timezone.localdate().year}/1"))
        self.assertEqual(claim.pending_with, ["Employer"])

        workflow.issue_letter(letter, self.da, timezone.localdate(), "Speed post")
        letter.refresh_from_db()
        self.assertEqual(letter.due_on, timezone.localdate() + timedelta(days=15))
        workflow.record_reply(letter, self.da, timezone.localdate(), "received", True)
        self.assertFalse(claim.open_findings().exists())

        scrutiny = workflow.start_rescrutiny(claim, self.da)
        self.assertEqual(scrutiny.round_no, 2)
        self.assertEqual(scrutiny.findings.count(), 2)  # answers not yet corrected
        workflow.save_answers(scrutiny, clean(), self.da)
        self.assertEqual(scrutiny.findings.count(), 0)
        workflow.submit(claim, self.da)
        workflow.validate(claim, self.ss)
        workflow.approve(claim, self.ao)
        self.assertEqual(claim.status, S.READY_FOR_INWARD)

    def test_aeo_reference_carries_all_points_and_copies_employer(self):
        claim = self.claim()
        self.to_ao(claim, edli_contrib="NONE", death_cert="NOT_ATTACHED")
        ref = claim.communications.get()
        self.assertEqual(ref.kind, Communication.Kind.AEO)
        self.assertIn("District Office,\nNellore.", ref.to_block)
        self.assertEqual(len(ref.points), 2)
        self.assertIn("e-File bearing No. 1336309", ref.closing)
        self.assertIn("M/s Test Mills", ref.copy_to)
        self.assertEqual(claim.current_scrutiny.action, "REFER_AEO")

    def test_uo_note_and_other_ro_letter_have_their_own_series(self):
        claim = self.claim(uan="")
        rows = [{"member_id": "AP/HYD/1/2", "estt": "Old Co", "office": "Hyderabad",
                 "from": "2005-01-01", "to": "2009-12-31", "transferred": "NO"}]
        self.to_ao(claim, uan_available="NO", aadhaar_seeded="", jd_aadhaar="YES",
                   prev_services=rows, no_prev_service="")
        workflow.approve(claim, self.ao)
        kinds = {c.kind: c for c in claim.communications.all()}
        self.assertEqual(set(kinds), {Communication.Kind.UO_NOTE,
                                      Communication.Kind.OTHER_RO})
        self.assertIn("U.O.", kinds[Communication.Kind.UO_NOTE].number)
        self.assertIn("Accounts Group – 103", kinds[Communication.Kind.UO_NOTE].to_block)
        self.assertIn("Regional Office, Hyderabad",
                      kinds[Communication.Kind.OTHER_RO].to_block)
        # nothing was asked of the employer, so no fresh scrutiny round is needed
        for finding in claim.open_findings():
            workflow.resolve_finding(finding, self.da, timezone.localdate(), "done")
        workflow.proceed_to_inward(claim, self.da)
        self.assertEqual(claim.status, S.READY_FOR_INWARD)

    def test_da_edits_survive_re_evaluation(self):
        claim = self.claim()
        scrutiny = workflow.start_scrutiny(claim, self.da)
        workflow.save_answers(scrutiny, clean(sign_tally="NO"), self.da)
        finding = scrutiny.findings.get()
        finding.text, finding.route, finding.edited = "My wording", Route.AEO, True
        finding.save()
        workflow.save_answers(scrutiny, clean(sign_tally="NO", bank="NOT_SUBMITTED"),
                              self.da)
        finding.refresh_from_db()
        self.assertEqual((finding.text, finding.route), ("My wording", Route.AEO))
        self.assertEqual(scrutiny.findings.count(), 2)
        self.assertEqual(claim.communications.get().kind, Communication.Kind.AEO)

    def test_dropped_finding_leaves_the_letter(self):
        claim = self.claim()
        scrutiny = workflow.start_scrutiny(claim, self.da)
        workflow.save_answers(scrutiny, clean(sign_tally="NO"), self.da)
        Finding.objects.filter(scrutiny=scrutiny).update(included=False)
        workflow.sync_outputs(scrutiny, self.da)
        self.assertFalse(claim.communications.exists())
        self.assertEqual(scrutiny.action, "INWARD")

    def test_reminder_and_word_export(self):
        claim = self.claim()
        self.to_ao(claim, sign_tally="NO")
        workflow.approve(claim, self.ao)
        letter = claim.communications.get()
        workflow.issue_letter(letter, self.da, timezone.localdate() - timedelta(days=30))
        letter.refresh_from_db()
        self.assertTrue(letter.is_overdue)
        reminder = build_reminder(letter, self.da)
        self.assertTrue(reminder.subject.startswith("REMINDER-1:"))
        self.assertIn(letter.number, reminder.reference)
        workflow.approve_letter(reminder, self.ao)
        self.assertTrue(reminder.number.endswith("/2"))

        document = Document(BytesIO(letter_docx(letter)))
        text = "\n".join(p.text for p in document.paragraphs)
        self.assertIn("K Nageswara Rao", text)
        self.assertIn("Yours faithfully,", text)
        self.assertEqual(len(document.inline_shapes), 1)  # the letterhead
        self.assertIn("EPFO never demands", document.sections[0].footer.paragraphs[0].text)


class Screens(Base):
    def test_every_screen_renders_for_every_role(self):
        claim = self.claim()
        self.to_ao(claim, sign_tally="NO", edli_contrib="NONE")
        workflow.approve(claim, self.ao)
        letter = claim.communications.first()
        urls = [reverse("dashboard"), reverse("claim_list"), reverse("claim_new"),
                reverse("claim_detail", args=[claim.pk]),
                reverse("scrutiny", args=[claim.pk]),
                reverse("checklist_print", args=[claim.pk]), reverse("letter_list"),
                reverse("letter_view", args=[letter.pk]),
                reverse("letter_download", args=[letter.pk]), reverse("reports"),
                reverse("establishment_list"), reverse("api_summary"),
                reverse("claim_list") + "?export=csv",
                reverse("letter_list") + "?export=csv",
                reverse("reports") + "?export=findings"]
        for user in (self.da, self.ss, self.ao):
            self.client.force_login(user)
            for url in urls:
                response = self.client.get(url, follow=True)
                self.assertEqual(response.status_code, 200, f"{user} {url}")

    def test_login_is_required(self):
        response = self.client.get(reverse("claim_list"))
        self.assertRedirects(response, "/login/?next=/claims/")

    def test_register_scrutinise_and_submit_through_the_browser(self):
        self.client.force_login(self.da)
        response = self.client.post(reverse("claim_new"), {
            "received_on": "2026-06-03", "mode": "POST", "form_20": "on",
            "form_10d": "on", "form_5if": "on", "member_name": "V Sai Krishna",
            "gender": "M", "pf_no": "GR/CDP/1/2", "establishment": self.estt.pk,
            "date_of_death": "2026-03-10", "claimant_name": "V Pradeep Kumar",
            "then": "scrutiny"})
        claim = Claim.objects.get(member_name="V Sai Krishna")
        self.assertRedirects(response, reverse("scrutiny", args=[claim.pk]))

        answers = {k: v for k, v in clean(sign_tally="NO").items()
                   if k != "prev_services"}
        answers["no_prev_service"] = "1"
        preview = self.client.post(reverse("scrutiny_preview", args=[claim.pk]), answers)
        self.assertContains(preview, "Attestation not tallying")
        self.client.post(reverse("scrutiny", args=[claim.pk]), answers)
        response = self.client.post(reverse("claim_action", args=[claim.pk, "submit"]),
                                    follow=True)
        self.assertContains(response, "With Section Supervisor")

        # The Dealing Assistant cannot approve their own scrutiny.
        self.client.post(reverse("claim_action", args=[claim.pk, "approve"]))
        claim.refresh_from_db()
        self.assertEqual(claim.status, S.WITH_SS)


class Editing(Base):
    def setUp(self):
        self.claim_ = self.claim()
        self.scrutiny = workflow.start_scrutiny(self.claim_, self.da)
        workflow.save_answers(self.scrutiny, clean(sign_tally="NO"), self.da)
        self.client.force_login(self.da)

    def test_hand_edited_letter_is_not_overwritten_by_later_findings(self):
        letter = self.claim_.communications.get()
        response = self.client.post(reverse("letter_edit", args=[letter.pk]), {
            "to_block": letter.to_block, "subject": "My subject", "reference": "",
            "salutation": "Sir,", "intro": letter.intro,
            "points_text": "First point\n\nSecond point", "closing": letter.closing,
            "copy_to": "", "signatory": "Accounts Officer (D/C)"})
        self.assertRedirects(response, reverse("letter_view", args=[letter.pk]))
        workflow.save_answers(self.scrutiny, clean(sign_tally="NO", bank="NOT_ATTESTED"),
                              self.da)
        letter.refresh_from_db()
        self.assertEqual(letter.points, ["First point", "Second point"])
        self.assertEqual(letter.subject, "My subject")

    def test_manual_remark_reroute_and_drop(self):
        self.client.post(reverse("finding_add", args=[self.claim_.pk]), {
            "title": "Photo unclear", "text": "The photograph is not clear.",
            "route": Route.CLAIMANT, "other_office": ""})
        letter = self.claim_.communications.get()
        self.assertIn("The photograph is not clear.", letter.points)
        auto = self.scrutiny.findings.get(auto=True)
        self.client.post(reverse("finding_save", args=[auto.pk]), {
            "op": "save", "title": auto.title, "text": auto.text, "route": Route.AEO,
            "other_office": ""})
        self.assertEqual(self.claim_.communications.get().kind, Communication.Kind.AEO)
        self.client.post(reverse("finding_save", args=[auto.pk]), {"op": "toggle"})
        self.assertEqual(self.claim_.communications.get().kind,
                         Communication.Kind.EMPLOYER)

    def test_approved_scrutiny_is_frozen(self):
        workflow.submit(self.claim_, self.da)
        workflow.validate(self.claim_, self.ss)
        workflow.approve(self.claim_, self.ao)
        finding = self.scrutiny.findings.get()
        self.client.post(reverse("finding_save", args=[finding.pk]), {"op": "toggle"})
        finding.refresh_from_db()
        self.assertTrue(finding.included)
        self.assertEqual(self.claim_.communications.count(), 1)
