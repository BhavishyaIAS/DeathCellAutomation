import csv
from collections import Counter, OrderedDict
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import checklist as ck
from . import letters, rules, workflow
from .docx_export import letter_docx
from .forms import (ClaimForm, CommunicationForm, EstablishmentForm, FindingForm,
                    OfficeSettingsForm)
from .models import (OPEN_STATUSES, Claim, ClaimStatus, Communication, Establishment,
                     Event, Finding, OfficeSettings, Role, Route, Scrutiny, office)
from .models import ClaimForm as FormRow
from .workflow import WorkflowError

S = ClaimStatus
AGE_BUCKETS = [("0–7 d", 0, 7), ("8–15 d", 8, 15), ("16–30 d", 16, 30),
               ("31–60 d", 31, 60), ("60+ d", 61, 10 ** 6)]


# -- helpers -----------------------------------------------------------------
def _date(value, default=None):
    return ck.parse_date(value) or default


def _csv(filename, header, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")  # so Excel opens it as UTF-8
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


def _guard(request, func, *args, success=None, **kwargs):
    """Run a workflow step, turning a refusal into an on-screen message."""
    try:
        result = func(*args, **kwargs)
    except WorkflowError as exc:
        messages.error(request, str(exc))
        return None
    if success:
        messages.success(request, success)
    return result if result is not None else True


def _status_counts():
    counts = dict(Claim.objects.values_list("status").annotate(n=Count("id")))
    return {status: counts.get(status, 0) for status in S.values}


def _ageing(claims):
    """Stage x age matrix of pending claims (age counted from date of receipt)."""
    stages = [s for s in OPEN_STATUSES]
    matrix = OrderedDict((s, [0] * len(AGE_BUCKETS)) for s in stages)
    for claim in claims:
        for i, (_label, low, high) in enumerate(AGE_BUCKETS):
            if low <= claim.age_days <= high:
                matrix[claim.status][i] += 1
    rows = []
    for status, cells in matrix.items():
        rows.append({"status": status, "label": S(status).label, "cells": cells,
                     "total": sum(cells)})
    totals = [sum(r["cells"][i] for r in rows) for i in range(len(AGE_BUCKETS))]
    return rows, totals


def _summary(user):
    counts = _status_counts()
    today = timezone.localdate()
    month_start = today.replace(day=1)
    return {
        "counts": counts,
        "in_scrutiny": counts[S.REGISTERED] + counts[S.UNDER_SCRUTINY],
        "received_month": Claim.objects.filter(received_on__gte=month_start).count(),
        "settled_month": Event.objects.filter(to_status=S.SETTLED,
                                              at__date__gte=month_start).count(),
        "queue": workflow.queue_for(user).count(),
        "overdue": Communication.objects.filter(
            status=Communication.Status.ISSUED, due_on__lt=today).count(),
        "to_issue": Communication.objects.filter(
            status=Communication.Status.APPROVED).count(),
    }


# -- dashboard ---------------------------------------------------------------
@login_required
def dashboard(request):
    today = timezone.localdate()
    open_claims = list(Claim.objects.filter(status__in=OPEN_STATUSES))
    ageing_rows, ageing_totals = _ageing(open_claims)
    pending_with = workflow.pending_with_counts()
    context = {
        "summary": _summary(request.user),
        "queue": workflow.queue_for(request.user).order_by("received_on")[:12],
        "pending_with": pending_with,
        "pending_with_max": max([n for _, _, n in pending_with] or [1]),
        "ageing_rows": ageing_rows,
        "ageing_totals": ageing_totals,
        "ageing_total": sum(ageing_totals),
        "buckets": [b[0] for b in AGE_BUCKETS],
        "overdue_letters": Communication.objects.filter(
            status=Communication.Status.ISSUED, due_on__lt=today)
        .select_related("claim").order_by("due_on")[:10],
        "to_issue": Communication.objects.filter(
            status=Communication.Status.APPROVED).select_related("claim")[:10],
        "events": Event.objects.select_related("claim", "user")[:12],
    }
    return render(request, "claims/dashboard.html", context)


@login_required
def api_summary(request):
    data = _summary(request.user)
    data["counts"] = {k: v for k, v in data["counts"].items()}
    data["at"] = timezone.localtime().strftime("%H:%M:%S")
    return JsonResponse(data)


# -- claims ------------------------------------------------------------------
def _filter_claims(request):
    qs = Claim.objects.select_related("establishment", "assigned_to")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(member_name__icontains=q) | Q(diary_no__icontains=q)
                       | Q(uan__icontains=q) | Q(pf_no__icontains=q)
                       | Q(claimant_name__icontains=q)
                       | Q(establishment__name__icontains=q)
                       | Q(efile_no__icontains=q))
    status = request.GET.get("status", "")
    if status == "OPEN":
        qs = qs.filter(status__in=OPEN_STATUSES)
    elif status in S.values:
        qs = qs.filter(status=status)
    route = request.GET.get("pending_with", "")
    if route in Route.values:
        qs = qs.filter(status=S.PENDING_COMPLIANCE, findings__status=Finding.Status.OPEN,
                       findings__included=True, findings__route=route).distinct()
    district = request.GET.get("district", "")
    if district:
        qs = qs.filter(establishment__district=district)
    if request.GET.get("mine"):
        qs = qs.filter(assigned_to=request.user)
    start, end = _date(request.GET.get("from")), _date(request.GET.get("to"))
    if start:
        qs = qs.filter(received_on__gte=start)
    if end:
        qs = qs.filter(received_on__lte=end)
    return qs


@login_required
def claim_list(request):
    qs = _filter_claims(request)
    if request.GET.get("export") == "csv":
        rows = [[c.diary_no, ck.fmt_date(c.received_on), c.get_mode_display(),
                 c.member_name, c.uan, c.pf_no, c.establishment.name,
                 c.establishment.district, c.forms_text, ck.fmt_date(c.date_of_death),
                 c.claimant_name, c.get_status_display(), "; ".join(c.pending_with),
                 c.assigned_to.display_name if c.assigned_to else "", c.age_days,
                 c.efile_no] for c in qs]
        return _csv("death-claims-register.csv",
                    ["Diary No", "Received on", "Mode", "Member", "UAN", "PF A/c No",
                     "Establishment", "District", "Forms", "Date of death", "Claimant",
                     "Status", "Pending with", "Dealing Assistant", "Age (days)",
                     "e-File No"], rows)
    districts = (Establishment.objects.exclude(district="")
                 .values_list("district", flat=True).distinct().order_by("district"))
    return render(request, "claims/claim_list.html", {
        "claims": qs[:500], "total": qs.count(), "statuses": S.choices,
        "routes": [r for r in Route.choices if r[0] != Route.NOTE],
        "districts": districts, "params": request.GET,
    })


@login_required
def claim_new(request):
    if not request.user.has_role(Role.DA, Role.SS):
        messages.error(request, "Only the cell staff can register a claim.")
        return redirect("dashboard")
    form = ClaimForm(request.POST or None, initial={"assigned_to": request.user.pk})
    if request.method == "POST" and form.is_valid():
        claim = form.save(commit=False)
        claim.created_by = request.user
        claim.save()
        claim.log(request.user, "Claim received and diarised",
                  f"{claim.forms_text}; {claim.get_mode_display().lower()}",
                  to_status=S.REGISTERED)
        messages.success(request, f"Registered as {claim.diary_no}.")
        if request.POST.get("then") == "scrutiny":
            if _guard(request, workflow.start_scrutiny, claim, request.user):
                return redirect("scrutiny", claim.pk)
        return redirect("claim_detail", claim.pk)
    return render(request, "claims/claim_form.html", {"form": form, "claim": None})


@login_required
def claim_edit(request, pk):
    claim = get_object_or_404(Claim, pk=pk)
    if not workflow.permissions(request.user, claim)["edit_claim"]:
        messages.error(request, "The diary entry cannot be changed now.")
        return redirect("claim_detail", pk)
    form = ClaimForm(request.POST or None, instance=claim)
    if request.method == "POST" and form.is_valid():
        form.save()
        claim.log(request.user, "Diary entry corrected")
        scrutiny = claim.current_scrutiny
        if scrutiny and scrutiny.status != Scrutiny.Status.APPROVED:
            workflow.refresh(scrutiny, request.user)
        messages.success(request, "Diary entry updated.")
        return redirect("claim_detail", pk)
    return render(request, "claims/claim_form.html", {"form": form, "claim": claim})


@login_required
def claim_detail(request, pk):
    claim = get_object_or_404(
        Claim.objects.select_related("establishment", "assigned_to"), pk=pk)
    scrutiny = claim.current_scrutiny
    perms = workflow.permissions(request.user, claim)
    findings, result = [], None
    if scrutiny:
        findings = list(scrutiny.findings.all())
        if scrutiny.status != Scrutiny.Status.APPROVED:
            result = workflow.evaluate(scrutiny)
    open_count = sum(1 for f in findings if f.status == Finding.Status.OPEN
                     and f.included and f.route != Route.NOTE)
    resolved_count = sum(1 for f in findings if f.status == Finding.Status.RESOLVED)
    return render(request, "claims/claim_detail.html", {
        "claim": claim, "scrutiny": scrutiny, "perms": perms, "findings": findings,
        "result": result, "open_count": open_count, "resolved_count": resolved_count,
        "communications": claim.communications.select_related("scrutiny"),
        "forms": claim.forms.all(), "events": claim.events.select_related("user"),
        "earlier_rounds": claim.scrutinies.exclude(pk=scrutiny.pk) if scrutiny else [],
        "routes": Route.choices, "today": timezone.localdate(),
        "form_names": claim.forms_claimed,
        "form_states": FormRow.State.choices,
    })


@login_required
@require_POST
def claim_action(request, pk, action):
    claim = get_object_or_404(Claim, pk=pk)
    user, note = request.user, request.POST.get("note", "").strip()
    today = timezone.localdate()
    if action == "start":
        if _guard(request, workflow.start_scrutiny, claim, user):
            return redirect("scrutiny", pk)
    elif action == "submit":
        _guard(request, workflow.submit, claim, user, note,
               success="Scrutiny submitted to the Section Supervisor.")
    elif action == "validate":
        _guard(request, workflow.validate, claim, user, note,
               success="Validated and put up to the Accounts Officer.")
    elif action == "send_back":
        _guard(request, workflow.send_back, claim, user, note,
               success="Returned to the Dealing Assistant.")
    elif action == "approve":
        issued = _guard(request, workflow.approve, claim, user, note)
        if issued is not None:
            if issued is True or not issued:
                messages.success(request, "Approved. The claim is ready for inward.")
            else:
                messages.success(
                    request, f"Approved. {len(issued)} communication(s) numbered and "
                             f"ready for issue.")
    elif action == "rescrutiny":
        if _guard(request, workflow.start_rescrutiny, claim, user):
            return redirect("scrutiny", pk)
    elif action == "proceed_inward":
        _guard(request, workflow.proceed_to_inward, claim, user,
               success="Marked ready for inward.")
    elif action == "inward":
        ids = {name: request.POST.get(f"claim_id_{name}", "").strip()
               for name in claim.forms_claimed}
        _guard(request, workflow.record_inward, claim, user,
               _date(request.POST.get("inward_on"), today), ids,
               success="Inward particulars recorded.")
    elif action == "settle":
        rows = {}
        for form in claim.forms.all():
            amount = request.POST.get(f"amount_{form.id}", "").strip()
            try:
                amount = float(amount) if amount else None
            except ValueError:
                amount = None
            rows[form.id] = {
                "state": request.POST.get(f"state_{form.id}", form.state),
                "settled_on": _date(request.POST.get(f"settled_on_{form.id}")),
                "amount": amount,
                "reference": request.POST.get(f"reference_{form.id}", "").strip(),
            }
        _guard(request, workflow.record_settlement, claim, user, rows,
               success="Settlement particulars saved.")
    elif action == "close":
        _guard(request, workflow.close, claim, user, note, success="Claim closed.")
    else:
        messages.error(request, "Unknown action.")
    return redirect("claim_detail", pk)


# -- scrutiny ----------------------------------------------------------------
def _scrutiny_context(claim, scrutiny, answers, result):
    points = []
    for p in ck.POINTS:
        points.append({
            "p": p,
            "fields": [{"f": f, "value": answers.get(f.name, "")} for f in p.fields],
            "remark": answers.get(f"remark_{p.no}", ""),
            "fact": result.facts.get(p.no, ""),
        })
    compare = []
    for key, label, kind, office_choices, claim_choices in ck.COMPARE_ROWS:
        compare.append({"key": key, "label": label, "kind": kind,
                        "off_choices": office_choices, "clm_choices": claim_choices,
                        "off": answers.get(f"off_{key}", ""),
                        "clm": answers.get(f"clm_{key}", "")})
    return {
        "claim": claim, "scrutiny": scrutiny, "points": points, "compare": compare,
        "prev_services": answers.get("prev_services") or [],
        "no_prev_service": answers.get("no_prev_service"),
        "extra": [{"f": f, "value": answers.get(f.name, "")} for f in ck.EXTRA_FIELDS],
        "result": result,
        "action_label": dict(Scrutiny.Action.choices)[result.action],
    }


@login_required
def scrutiny(request, pk):
    claim = get_object_or_404(Claim.objects.select_related("establishment"), pk=pk)
    current = claim.current_scrutiny
    if current is None:
        messages.info(request, "Start the scrutiny first.")
        return redirect("claim_detail", pk)
    editable = workflow.can_edit_scrutiny(request.user, claim)
    if request.method == "POST":
        answers = ck.answers_from_post(request.POST)
        result = _guard(request, workflow.save_answers, current, answers, request.user)
        if result:
            if request.POST.get("then") == "review":
                messages.success(request, "Checklist saved. Review the findings below "
                                          "and submit.")
                return redirect("claim_detail", pk)
            messages.success(request, "Checklist saved.")
        return redirect("scrutiny", pk)
    result = workflow.evaluate(current)
    context = _scrutiny_context(claim, current, current.answers, result)
    context["editable"] = editable
    return render(request, "claims/scrutiny_form.html", context)


@login_required
@require_POST
def scrutiny_preview(request, pk):
    """Live evaluation of the checklist as it is being filled (nothing is saved)."""
    claim = get_object_or_404(Claim, pk=pk)
    result = rules.evaluate(ck.answers_from_post(request.POST),
                            rules.Ctx.from_claim(claim))
    return render(request, "claims/_findings_preview.html", {
        "result": result, "action_label": dict(Scrutiny.Action.choices)[result.action],
        "route_labels": dict(Route.choices)})


@login_required
def checklist_print(request, pk, round_no=None):
    claim = get_object_or_404(Claim.objects.select_related("establishment"), pk=pk)
    scrutiny = (claim.scrutinies.filter(round_no=round_no).first() if round_no
                else claim.current_scrutiny)
    if scrutiny is None:
        return redirect("claim_detail", pk)
    answers = scrutiny.answers
    result = workflow.evaluate(scrutiny)
    remarks = ck.point_remarks(claim, answers, result.facts)
    compare = [{"label": label,
                "off": ck.compare_display(key, "off", answers.get(f"off_{key}")),
                "clm": ck.compare_display(key, "clm", answers.get(f"clm_{key}"))}
               for key, label, *_ in ck.COMPARE_ROWS]
    rows = [{"p": p, "remarks": remarks[p.no]} for p in ck.POINTS]
    return render(request, "claims/checklist_print.html", {
        "claim": claim, "scrutiny": scrutiny, "compare": compare,
        "page1": [r for r in rows if r["p"].page == 1],
        "page2": [r for r in rows if r["p"].page == 2],
        "lcm": ck.fmt_month(answers.get("lcm")),
        "findings": scrutiny.findings.filter(included=True),
        "inward": scrutiny.action == Scrutiny.Action.INWARD,
    })


# -- findings ----------------------------------------------------------------
def _editable_finding(request, pk):
    finding = get_object_or_404(Finding.objects.select_related("claim", "scrutiny"),
                                pk=pk)
    if not workflow.can_edit_scrutiny(request.user, finding.claim):
        messages.error(request, "Findings cannot be changed at this stage.")
        return finding, False
    return finding, True


@login_required
@require_POST
def finding_save(request, pk):
    finding, ok = _editable_finding(request, pk)
    if ok:
        op = request.POST.get("op", "save")
        if op == "delete" and not finding.auto:
            finding.delete()
        elif op == "toggle":
            finding.included = not finding.included
            finding.save(update_fields=["included"])
        elif op == "reset" and finding.auto:
            finding.edited = False
            finding.included = True
            finding.save(update_fields=["edited", "included"])
            workflow.refresh(finding.scrutiny, request.user)
        else:
            form = FindingForm(request.POST, instance=finding)
            if form.is_valid():
                form.instance.edited = True
                form.save()
            else:
                messages.error(request, "The remark could not be saved – text is required.")
        workflow.sync_outputs(finding.scrutiny, request.user)
    return redirect("claim_detail", finding.claim_id)


@login_required
@require_POST
def finding_add(request, pk):
    claim = get_object_or_404(Claim, pk=pk)
    scrutiny = claim.current_scrutiny
    if scrutiny is None or not workflow.can_edit_scrutiny(request.user, claim):
        messages.error(request, "Remarks cannot be added at this stage.")
        return redirect("claim_detail", pk)
    form = FindingForm(request.POST)
    if form.is_valid():
        finding = form.save(commit=False)
        finding.scrutiny, finding.claim = scrutiny, claim
        finding.auto, finding.edited = False, True
        finding.category = Finding.Category.OTHER
        finding.code = f"MANUAL:{timezone.now().timestamp():.3f}"
        finding.order = 900 + scrutiny.findings.filter(auto=False).count()
        finding.save()
        workflow.sync_outputs(scrutiny, request.user)
        messages.success(request, "Remark added.")
    else:
        messages.error(request, "Give the remark a short title and its text.")
    return redirect("claim_detail", pk)


@login_required
@require_POST
def finding_resolve(request, pk):
    finding = get_object_or_404(Finding.objects.select_related("claim"), pk=pk)
    if finding.claim.status != S.PENDING_COMPLIANCE:
        messages.error(request, "This claim is not pending compliance.")
    else:
        _guard(request, workflow.resolve_finding, finding, request.user,
               _date(request.POST.get("resolved_on"), timezone.localdate()),
               request.POST.get("resolution", "").strip(),
               success="Marked as complied.")
    return redirect("claim_detail", finding.claim_id)


# -- communications ----------------------------------------------------------
@login_required
def letter_list(request):
    qs = Communication.objects.select_related("claim", "claim__establishment")
    kind, status = request.GET.get("kind", ""), request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    if kind in Communication.Kind.values:
        qs = qs.filter(kind=kind)
    if status == "OVERDUE":
        qs = qs.filter(status=Communication.Status.ISSUED,
                       due_on__lt=timezone.localdate())
    elif status in Communication.Status.values:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(claim__member_name__icontains=q)
                       | Q(claim__diary_no__icontains=q) | Q(to_block__icontains=q))
    if request.GET.get("export") == "csv":
        rows = [[c.number, ck.fmt_date(c.letter_date), c.title, c.claim.diary_no,
                 c.claim.member_name, c.claim.pf_no,
                 " ".join(c.to_block.split()), c.get_status_display(),
                 ck.fmt_date(c.issued_on), c.dispatch_mode, ck.fmt_date(c.due_on),
                 ck.fmt_date(c.reply_on), c.days_overdue or ""] for c in qs]
        return _csv("death-cell-dispatch-register.csv",
                    ["Letter No", "Date", "Type", "Diary No", "Member", "PF A/c No",
                     "Addressed to", "Status", "Issued on", "Mode", "Reply due",
                     "Reply received", "Days overdue"], rows)
    return render(request, "claims/letter_list.html", {
        "letters": qs[:500], "total": qs.count(), "kinds": Communication.Kind.choices,
        "statuses": Communication.Status.choices, "params": request.GET,
    })


def _can_edit_letter(user, comm):
    if comm.status == Communication.Status.DRAFT:
        return (workflow.can_edit_scrutiny(user, comm.claim)
                or (comm.reminder_of_id and user.has_role(Role.DA, Role.SS, Role.AO)))
    if comm.status == Communication.Status.APPROVED:
        return user.has_role(Role.AO)
    return False


@login_required
def letter_view(request, pk):
    comm = get_object_or_404(Communication.objects.select_related(
        "claim", "claim__establishment"), pk=pk)
    context = letters.parts(comm)
    context.update({
        "claim": comm.claim, "can_edit": _can_edit_letter(request.user, comm),
        "today": timezone.localdate(),
        "can_approve": comm.status == Communication.Status.DRAFT
        and bool(comm.reminder_of_id) and request.user.has_role(Role.AO),
        "is_staff_user": request.user.has_role(Role.DA, Role.SS),
        "print_mode": request.GET.get("print") == "1",
        "default_due": timezone.localdate() + timedelta(days=office()["reply_days"]),
    })
    return render(request, "claims/letter_view.html", context)


@login_required
def letter_edit(request, pk):
    comm = get_object_or_404(Communication, pk=pk)
    if not _can_edit_letter(request.user, comm):
        messages.error(request, "This communication cannot be edited now.")
        return redirect("letter_view", pk)
    form = CommunicationForm(request.POST or None, instance=comm)
    if request.method == "POST" and form.is_valid():
        form.save()
        comm.claim.log(request.user, f"{comm.title} edited")
        messages.success(request, "Draft saved.")
        return redirect("letter_view", pk)
    return render(request, "claims/letter_form.html", {"form": form, "comm": comm,
                                                       "claim": comm.claim})


@login_required
def letter_download(request, pk):
    comm = get_object_or_404(Communication.objects.select_related("claim"), pk=pk)
    name = f"{comm.claim.diary_no.replace('/', '-')}-{comm.kind.lower()}-{comm.pk}.docx"
    response = HttpResponse(
        letter_docx(comm),
        content_type="application/vnd.openxmlformats-officedocument."
                     "wordprocessingml.document")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@login_required
@require_POST
def letter_action(request, pk, action):
    comm = get_object_or_404(Communication.objects.select_related("claim"), pk=pk)
    user, today = request.user, timezone.localdate()
    if action == "issue":
        _guard(request, workflow.issue_letter, comm, user,
               _date(request.POST.get("issued_on"), today),
               request.POST.get("dispatch_mode", "").strip(),
               _date(request.POST.get("due_on")), success="Recorded as issued.")
    elif action == "reply":
        _guard(request, workflow.record_reply, comm, user,
               _date(request.POST.get("reply_on"), today),
               request.POST.get("reply_note", "").strip(),
               bool(request.POST.get("resolve_all")), success="Reply recorded.")
    elif action == "approve":
        _guard(request, workflow.approve_letter, comm, user,
               success="Approved and numbered.")
    elif action == "remind":
        if comm.status != Communication.Status.ISSUED:
            messages.error(request, "A reminder can be issued only for a letter "
                                    "awaiting reply.")
        else:
            reminder = letters.build_reminder(comm, user)
            comm.claim.log(user, f"{reminder.title} drafted")
            messages.success(request, "Reminder drafted – it needs the Accounts "
                                      "Officer's approval before issue.")
            return redirect("letter_view", reminder.pk)
    elif action == "regenerate":
        if comm.scrutiny and workflow.can_edit_scrutiny(user, comm.claim):
            letters.build_drafts(comm.scrutiny, user, force=True)
            messages.success(request, "Drafts regenerated from the findings.")
            return redirect("claim_detail", comm.claim_id)
        messages.error(request, "Drafts cannot be regenerated now.")
    elif action == "cancel":
        if comm.status in (Communication.Status.DRAFT, Communication.Status.APPROVED) \
                and user.has_role(Role.AO):
            comm.status = Communication.Status.CANCELLED
            comm.save(update_fields=["status"])
            comm.claim.log(user, f"{comm.title} cancelled")
            messages.success(request, "Cancelled.")
        else:
            messages.error(request, "Only the Accounts Officer can cancel an unissued "
                                    "communication.")
    return redirect("letter_view", pk)


# -- reports -----------------------------------------------------------------
def _month_starts(count):
    first = timezone.localdate().replace(day=1)
    months = []
    for _ in range(count):
        months.append(first)
        first = (first - timedelta(days=1)).replace(day=1)
    return list(reversed(months))


@login_required
def reports(request):
    export = request.GET.get("export")
    if export == "findings":
        qs = Finding.objects.select_related("claim", "claim__establishment",
                                            "scrutiny").exclude(route=Route.NOTE)
        rows = [[f.claim.diary_no, f.claim.member_name, f.claim.establishment.name,
                 f.scrutiny.round_no, f.point, f.get_category_display(), f.title,
                 f.get_route_display(), f.get_status_display(),
                 ck.fmt_date(f.resolved_on), f.text] for f in qs if f.included]
        return _csv("death-cell-discrepancies.csv",
                    ["Diary No", "Member", "Establishment", "Round", "Checklist point",
                     "Category", "Discrepancy", "Action lies with", "Status",
                     "Complied on", "Remark"], rows)

    months = _month_starts(12)
    start = months[0]

    def by_month(dates):
        tally = Counter((d.year, d.month) for d in dates if d)
        return [tally.get((m.year, m.month), 0) for m in months]

    received = by_month(Claim.objects.filter(received_on__gte=start)
                        .values_list("received_on", flat=True))
    events = Event.objects.filter(at__date__gte=start)
    inwarded = by_month(timezone.localtime(at).date() for at in events.filter(
        to_status=S.INWARDED).values_list("at", flat=True))
    settled = by_month(timezone.localtime(at).date() for at in events.filter(
        to_status=S.SETTLED).values_list("at", flat=True))
    letters_issued = by_month(Communication.objects.filter(
        issued_on__gte=start).values_list("issued_on", flat=True))
    monthly = [{"label": m.strftime("%b %Y"), "received": received[i],
                "inwarded": inwarded[i], "settled": settled[i],
                "letters": letters_issued[i]} for i, m in enumerate(months)]

    open_claims = list(Claim.objects.filter(status__in=OPEN_STATUSES)
                       .select_related("establishment"))
    ageing_rows, ageing_totals = _ageing(open_claims)

    district = Counter(c.establishment.district or "—" for c in open_claims)
    district_rows = sorted(district.items(), key=lambda kv: -kv[1])

    active = Finding.objects.filter(included=True).exclude(route=Route.NOTE)
    categories = (active.values("category").annotate(
        claims=Count("claim", distinct=True)).order_by("-claims"))
    category_rows = [(Finding.Category(c["category"]).label, c["claims"])
                     for c in categories]
    top_titles = (active.filter(auto=True).values("title").annotate(
        claims=Count("claim", distinct=True)).order_by("-claims")[:12])
    estt_rows = (active.values("claim__establishment__name").annotate(
        claims=Count("claim", distinct=True), remarks=Count("id"))
        .order_by("-claims", "-remarks")[:12])

    total_claims = Claim.objects.count()
    with_findings = active.values("claim").distinct().count()
    return render(request, "claims/reports.html", {
        "monthly": monthly,
        "monthly_max": max([m["received"] for m in monthly] + [1]),
        "ageing_rows": ageing_rows, "ageing_totals": ageing_totals,
        "ageing_total": sum(ageing_totals), "buckets": [b[0] for b in AGE_BUCKETS],
        "district_rows": district_rows,
        "district_max": max([n for _, n in district_rows] or [1]),
        "category_rows": category_rows,
        "category_max": max([n for _, n in category_rows] or [1]),
        "top_titles": top_titles, "estt_rows": estt_rows,
        "total_claims": total_claims, "with_findings": with_findings,
        "clean_first_time": Scrutiny.objects.filter(
            round_no=1, status=Scrutiny.Status.APPROVED,
            action=Scrutiny.Action.INWARD).count(),
        "aeo_refs": Communication.objects.filter(kind=Communication.Kind.AEO)
        .exclude(status__in=[Communication.Status.DRAFT,
                             Communication.Status.CANCELLED]).count(),
        "uo_notes": Communication.objects.filter(kind=Communication.Kind.UO_NOTE)
        .exclude(status__in=[Communication.Status.DRAFT,
                             Communication.Status.CANCELLED]).count(),
    })


# -- office settings ---------------------------------------------------------
@login_required
def office_settings(request):
    """Reply period, thresholds, numbering and signatory – Accounts Officer only."""
    if not request.user.has_role(Role.AO):
        messages.error(request, "Only the Accounts Officer can change office settings.")
        return redirect("dashboard")
    form = OfficeSettingsForm(request.POST or None, instance=OfficeSettings.current())
    if request.method == "POST" and form.is_valid():
        row = form.save(commit=False)
        row.updated_by = request.user
        row.save()
        messages.success(request, "Office settings saved.")
        return redirect("office_settings")
    saved = OfficeSettings.objects.filter(pk=1).select_related("updated_by").first()
    return render(request, "claims/office_settings.html", {"form": form, "saved": saved})


# -- establishments ----------------------------------------------------------
@login_required
def establishment_list(request):
    q = request.GET.get("q", "").strip()
    qs = Establishment.objects.select_related("aeo_office").annotate(
        n_claims=Count("claims"))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
    return render(request, "claims/establishment_list.html",
                  {"establishments": qs[:500], "q": q})


@login_required
def establishment_edit(request, pk=None):
    if not request.user.has_role(Role.DA, Role.SS, Role.AO):
        messages.error(request, "You cannot change the establishment master.")
        return redirect("establishment_list")
    instance = get_object_or_404(Establishment, pk=pk) if pk else None
    form = EstablishmentForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Establishment saved.")
        nxt = request.GET.get("next")
        return redirect(nxt if nxt in ("claim_new",) else "establishment_list")
    return render(request, "claims/establishment_form.html",
                  {"form": form, "instance": instance})
