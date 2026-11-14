"""Each test mirrors a situation the Death Claims Cell actually writes letters about."""

from datetime import date

from django.test import SimpleTestCase

from claims.rules import (ACCOUNTS, AEO, CLAIMANT, EMPLOYER, INTERNAL, NOTE, OTHER_RO,
                          Ctx, evaluate)


def ctx(**over):
    base = dict(pf_no="GR/CDP/1/1", forms={"20", "10D", "5IF"},
                received_on=date(2026, 6, 3), date_of_death=date(2026, 3, 10),
                own_offices=("", "kadapa"))
    base.update(over)
    return Ctx(**base)


def clean(**over):
    """A death-in-service claim that is fully in order."""
    a = {
        "off_name": "K NAGESWARA RAO", "clm_name": "K. Nageswara Rao",
        "off_father": "K RAMAIAH", "clm_father": "K Ramaiah",
        "off_dob": "1980-05-01", "clm_dob": "1980-05-01",
        "off_marital": "MARRIED", "clm_marital": "MARRIED",
        "off_doj": "2010-04-01", "clm_doj": "2010-04-01",
        "off_dol": "2026-03-10", "clm_dol": "2026-03-10", "clm_dod": "2026-03-10",
        "off_reason": "DEATH", "clm_reason": "DEATH_IN_SERVICE",
        "estt_status": "LIVE", "estt_claimed_closed": "NO", "attested_by": "EMPLOYER",
        "edli_exempted": "NO", "edli_contrib": "ALL",
        "dsc_status": "ACTIVE", "sign_tally": "YES",
        "prev_services": [], "no_prev_service": "YES",
        "first_contrib_month": "2010-04", "lcm": "2026-03",
        "contrib_complete": "YES", "ncp_updated": "YES", "post_death_contrib": "NO",
        "death_month_contrib": "PAID", "wage_anomaly": "NO",
        "eps_contributed": "YES", "enom_status": "NOT_FILED",
        "forms_filled": "YES", "forms_signed": "YES", "encl_attested": "YES",
        "uan_available": "YES", "aadhaar_seeded": "YES",
        "death_cert": "OK", "fmc": "OK", "family_aadhaar": "OK", "child_dob": "OK",
        "photos": "OK", "bank": "OK", "minor_children": "NO", "desc_rolls": "OK",
    }
    a.update(over)
    return a


def codes(result, route=None):
    return {f.code for f in result.findings if route is None or f.route == route}


class CleanClaim(SimpleTestCase):
    def test_claim_in_order_is_inwarded(self):
        result = evaluate(clean(), ctx())
        self.assertEqual(result.findings, [])
        self.assertEqual(result.missing, [])
        self.assertEqual(result.action, "INWARD")

    def test_blank_checklist_is_incomplete_not_clean(self):
        result = evaluate({}, ctx(date_of_death=None))
        self.assertTrue(result.missing)

    def test_initials_and_punctuation_are_not_a_name_mismatch(self):
        result = evaluate(clean(off_name="RAO NAGESWARA K", clm_name="K. Nageswara Rao"),
                          ctx())
        self.assertNotIn("PROFILE_MISMATCH", codes(result))


class ProfileAndExit(SimpleTestCase):
    def test_exit_not_updated_goes_to_employer(self):
        result = evaluate(clean(off_dol="", off_reason="NOT_UPDATED"), ctx())
        self.assertEqual(codes(result), {"EXIT_NOT_UPDATED"})
        self.assertEqual(result.action, "RETURN")
        self.assertIn("Joint Declaration", result.findings[0].text)

    def test_name_and_dob_mismatch_calls_for_joint_declaration(self):
        result = evaluate(clean(off_name="K NAGESH", off_dob="1981-05-01"), ctx())
        finding = next(f for f in result.findings if f.code == "PROFILE_MISMATCH")
        self.assertEqual(finding.route, EMPLOYER)
        self.assertIn("K NAGESH", finding.text)
        self.assertIn("01.05.1981", finding.text)

    def test_attached_jd_becomes_an_office_task_instead_of_a_letter(self):
        result = evaluate(clean(off_name="K NAGESH", jd_attached="YES"), ctx())
        self.assertEqual(codes(result), {"JD_INWARD"})
        self.assertEqual(result.findings[0].route, INTERNAL)
        self.assertEqual(result.action, "OFFICE_ACTION")

    def test_jd_already_updated_raises_nothing(self):
        result = evaluate(clean(off_name="K NAGESH", jd_attached="YES",
                                jd_updated="YES"), ctx())
        self.assertEqual(codes(result), set())


class Establishment(SimpleTestCase):
    def test_dsc_not_registered(self):
        result = evaluate(clean(dsc_status="NOT_REGISTERED", sign_tally="NA"), ctx())
        self.assertEqual(codes(result, EMPLOYER), {"DSC_NOT_ACTIVE"})

    def test_signature_not_tallying(self):
        result = evaluate(clean(sign_tally="NO"), ctx())
        self.assertEqual(codes(result), {"SIGNATURE_NOT_TALLYING"})

    def test_live_in_records_but_claimed_closed_goes_to_aeo(self):
        result = evaluate(clean(estt_claimed_closed="YES", attested_by="GAZETTED"),
                          ctx())
        self.assertIn("ESTT_STATUS_CONFLICT", codes(result, AEO))
        self.assertEqual(result.action, "REFER_AEO")

    def test_closed_establishment_attested_by_gazetted_officer_is_accepted(self):
        result = evaluate(clean(estt_status="CLOSED", attested_by="GAZETTED",
                                dsc_status="EXPIRED", sign_tally=""), ctx())
        self.assertEqual(codes(result), set())

    def test_unexempted_establishment_without_edli_contributions_goes_to_aeo(self):
        result = evaluate(clean(edli_contrib="PARTLY",
                                edli_gap_period="01.04.2021 to 30.04.2023"), ctx())
        finding = result.findings[0]
        self.assertEqual((finding.code, finding.route), ("EDLI_NOT_RECEIVED", AEO))
        self.assertIn("01.04.2021 to 30.04.2023", finding.text)


class Contributions(SimpleTestCase):
    def test_death_in_service_without_death_month_contribution_goes_to_aeo(self):
        result = evaluate(clean(death_month_contrib="NOT_PAID", lcm="2026-02"), ctx())
        finding = next(f for f in result.findings if f.code == "DEATH_MONTH_NOT_PAID")
        self.assertEqual(finding.route, AEO)
        self.assertIn("01/2026 to 03/2026", finding.text)
        self.assertNotIn("LCM_MISMATCH", codes(result))

    def test_delayed_death_month_contribution_asks_employer_to_explain(self):
        result = evaluate(clean(death_month_contrib="PAID_LATE",
                                death_month_paid_on="2027-05-01"), ctx())
        finding = result.findings[0]
        self.assertEqual((finding.code, finding.route), ("DEATH_MONTH_DELAYED", EMPLOYER))
        self.assertIn("01.05.2027", finding.text)

    def test_delay_explained_with_proof_is_accepted(self):
        result = evaluate(clean(death_month_contrib="PAID_LATE",
                                death_month_proof="YES"), ctx())
        self.assertEqual(codes(result), set())

    def test_contributions_after_death(self):
        result = evaluate(clean(post_death_contrib="YES",
                                post_death_detail="up to 11/2026", lcm="2026-11"), ctx())
        self.assertEqual(codes(result), {"POST_DEATH_CONTRIBUTION"})
        self.assertIn("up to 11/2026", result.findings[0].text)

    def test_contributions_starting_long_after_joining(self):
        result = evaluate(clean(off_doj="2002-08-01", clm_doj="2002-08-01",
                                first_contrib_month="2013-05"), ctx())
        self.assertIn("DOJ_VS_FIRST_CONTRIBUTION", codes(result))

    def test_missing_months_ask_for_ncp_statement(self):
        result = evaluate(clean(contrib_complete="NO", ncp_updated="NO",
                                contrib_gaps="12/2017, 06/2018"), ctx())
        self.assertEqual(codes(result), {"CONTRIBUTION_GAPS"})
        self.assertIn("NCP days statement", result.findings[0].text)

    def test_last_contributed_month_short_of_exit(self):
        result = evaluate(clean(lcm="2025-12"), ctx())
        self.assertEqual(codes(result), {"LCM_MISMATCH"})


class Pension(SimpleTestCase):
    after_2014 = dict(off_doj="2019-09-02", clm_doj="2019-09-02",
                      first_contrib_month="2019-09", off_dob="1990-01-01",
                      clm_dob="1990-01-01")

    def test_high_wage_post_2014_joiner_with_eps_contribution(self):
        result = evaluate(clean(**self.after_2014, wages_gt_15000="YES",
                                prior_eps_member="NO"), ctx())
        self.assertEqual(codes(result, EMPLOYER), {"EPS_INELIGIBLE_CONTRIBUTION"})

    def test_once_employer_confirms_accounts_group_diverts(self):
        result = evaluate(clean(**self.after_2014, wages_gt_15000="YES",
                                prior_eps_member="NO", eps_wage_proof="YES"), ctx())
        self.assertEqual(codes(result, ACCOUNTS), {"EPS_DIVERT_TO_EPF"})
        self.assertEqual(result.action, "OFFICE_ACTION")

    def test_post_2014_joiner_within_wage_ceiling_is_eligible(self):
        result = evaluate(clean(**self.after_2014, wages_gt_15000="NO"), ctx())
        self.assertEqual(codes(result), set())

    def test_eligible_member_without_pension_contribution(self):
        result = evaluate(clean(eps_contributed="NO"), ctx())
        self.assertEqual(codes(result, ACCOUNTS), {"EPS_DIVERT_TO_EPS"})

    def test_older_first_time_member_with_short_service(self):
        result = evaluate(clean(off_doj="2019-09-02", clm_doj="2019-09-02",
                                first_contrib_month="2019-09", off_dob="1968-01-01",
                                clm_dob="1968-01-01", wages_gt_15000="NO"), ctx())
        self.assertEqual(codes(result), {"PAST_SERVICE_QUERY"})

    def away(self, **over):
        base = dict(clm_reason="DEATH_AWAY", off_reason="CESSATION",
                    off_doj="2012-01-01", clm_doj="2012-01-01",
                    first_contrib_month="2012-01", off_dol="2018-01-31",
                    clm_dol="2018-01-31", lcm="2018-01", death_month_contrib="NA")
        base.update(over)
        return clean(**base)

    def test_death_three_years_after_exit_with_short_service(self):
        result = evaluate(self.away(), ctx())
        finding = next(f for f in result.findings if f.code == "PENSION_NOT_ADMISSIBLE")
        self.assertEqual(finding.route, CLAIMANT)
        self.assertIn("Form 10C", finding.text)

    def test_only_a_note_when_pension_is_not_claimed(self):
        result = evaluate(self.away(), ctx(forms={"20"}))
        finding = next(f for f in result.findings if f.code == "PENSION_NOT_ADMISSIBLE")
        self.assertEqual(finding.route, NOTE)
        self.assertEqual(result.action, "INWARD")

    def test_ten_years_service_keeps_pension(self):
        result = evaluate(self.away(off_doj="2005-01-01", clm_doj="2005-01-01",
                                    first_contrib_month="2005-01"), ctx())
        self.assertNotIn("PENSION_NOT_ADMISSIBLE", codes(result))


class OfficeSide(SimpleTestCase):
    def test_no_uan_with_jd_is_a_uo_note(self):
        result = evaluate(clean(uan_available="NO", aadhaar_seeded="",
                                jd_aadhaar="YES"), ctx())
        self.assertEqual(codes(result, ACCOUNTS), {"UAN_GENERATION"})

    def test_no_uan_without_jd_asks_employer(self):
        result = evaluate(clean(uan_available="NO", aadhaar_seeded=""), ctx())
        self.assertEqual(codes(result, EMPLOYER), {"UAN_JD_REQUIRED"})

    def test_past_service_internal_and_external(self):
        rows = [
            {"member_id": "GR/CDP/55/9", "estt": "A", "office": "",
             "from": "2005-01-01", "to": "2008-01-01", "transferred": "NO"},
            {"member_id": "AP/HYD/72623/1077", "estt": "B", "office": "Hyderabad",
             "from": "2008-02-01", "to": "2010-03-01", "transferred": "NO"},
            {"member_id": "GR/CDP/77/3", "estt": "C", "office": "Kadapa",
             "from": "2001-01-01", "to": "2004-01-01", "transferred": "YES"},
        ]
        result = evaluate(clean(prev_services=rows, no_prev_service=""), ctx())
        self.assertEqual(codes(result, INTERNAL), {"FORM13_INTERNAL:GR/CDP/55/9"})
        external = [f for f in result.findings if f.route == OTHER_RO]
        self.assertEqual([f.other_office for f in external], ["Hyderabad"])
        self.assertEqual(result.action, "OFFICE_ACTION")

    def test_excess_interest_and_recredit(self):
        result = evaluate(clean(interest_excess="YES", recredit="YES",
                                recredit_detail="DD 123456 dt 01.02.2026 Rs.45,000"),
                          ctx())
        self.assertEqual(codes(result, ACCOUNTS), {"EXCESS_INTEREST", "RECREDIT"})

    def test_claim_after_six_years_goes_to_aeo(self):
        result = evaluate(clean(), ctx(received_on=date(2033, 1, 1)))
        self.assertIn("BELATED_CLAIM", codes(result, AEO))

    def test_missing_enclosures(self):
        result = evaluate(clean(death_cert="NOT_ATTACHED", bank="NOT_SAME_BRANCH",
                                desc_rolls="NOT_ENCLOSED", photos="NOT_ATTESTED",
                                minor_children="YES", minor_accounts="NONE"), ctx())
        self.assertEqual(codes(result), {"DEATH_CERTIFICATE", "BANK_ACCOUNTS",
                                         "DESCRIPTIVE_ROLLS", "FAMILY_PHOTOS",
                                         "MINOR_ACCOUNTS"})

    def test_enomination_after_death_is_flagged_invalid(self):
        result = evaluate(clean(enom_status="ALIVE", enom_date="2026-04-01"), ctx())
        self.assertIn("INVALID", result.facts["10"])
        self.assertNotIn("ENOMINATION_ONLINE", codes(result))
