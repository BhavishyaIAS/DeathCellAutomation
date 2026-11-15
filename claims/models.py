"""Data model for the Death Claims Cell.

Claim         one death case (diary entry) – a set of forms for one deceased member
Scrutiny      one round of checklist scrutiny of that claim (DA -> SS -> AO)
Finding       one discrepancy / office action thrown up by a scrutiny round
Communication one letter, AEO reference or U.O. note arising from the findings
Event         audit trail – every status change, with who and when
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models, transaction
from django.utils import timezone


class OfficeSettings(models.Model):
    """Working rules of the cell that the office may change from the screen.

    There is one row at most.  Until it is saved for the first time the
    defaults in settings.OFFICE apply.
    """

    reply_days = models.PositiveIntegerField(
        "days allowed for a reply", default=15,
        help_text="A letter, AEO reference or U.O. note shows as overdue once this "
                  "many days have passed since it was issued. Applies to "
                  "communications issued from now on.")
    older_age_at_joining = models.PositiveIntegerField(
        "age at joining that calls for a past-employment query", default=45,
        help_text="If a member was this age or older on joining, has under 10 years' "
                  "service and no past service is linked, the employer is asked for "
                  "previous employment details.")
    belated_claim_years = models.PositiveIntegerField(
        "years after death beyond which a claim goes to the AEO", default=6)
    letter_serial_start = models.PositiveIntegerField(
        "next letter number to start from", default=1,
        help_text="Set this to continue from the despatch register, e.g. 2301. "
                  "Numbers already allotted are never reused.")
    uo_serial_start = models.PositiveIntegerField(
        "next U.O. note number to start from", default=1)
    signatory = models.CharField("letters signed by", max_length=120,
                                 default="Accounts Officer (D/C)")
    signatory_office = models.CharField("signatory's office", max_length=120,
                                        default="Regional Office, Kadapa.")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    EDITABLE = ["reply_days", "older_age_at_joining", "belated_claim_years",
                "letter_serial_start", "uo_serial_start", "signatory",
                "signatory_office"]

    class Meta:
        verbose_name_plural = "office settings"

    def __str__(self):
        return "Office settings"

    def save(self, *args, **kwargs):
        self.pk = 1  # single row
        super().save(*args, **kwargs)

    @classmethod
    def current(cls):
        """The saved row, or an unsaved one carrying the configured defaults."""
        row = cls.objects.filter(pk=1).first()
        if row is None:
            row = cls(**{name: settings.OFFICE[name] for name in cls.EDITABLE})
        return row


def office():
    """Office configuration: settings.OFFICE with the on-screen settings on top."""
    config = dict(settings.OFFICE)
    row = OfficeSettings.objects.filter(pk=1).first()
    if row is not None:
        config.update({name: getattr(row, name) for name in OfficeSettings.EDITABLE})
    return config


class Role(models.TextChoices):
    DA = "DA", "Dealing Assistant"
    SS = "SS", "Section Supervisor"
    AO = "AO", "Accounts Officer"
    ADMIN = "ADMIN", "Nodal Officer / Admin"


class User(AbstractUser):
    role = models.CharField(max_length=8, choices=Role.choices, default=Role.DA)
    designation = models.CharField(max_length=80, blank=True)

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    def has_role(self, *roles):
        """Admins (and Django superusers) may act at every level."""
        return self.is_superuser or self.role == Role.ADMIN or self.role in roles


class AEOOffice(models.Model):
    """District office whose Area Enforcement Officer verifies referred cases."""

    name = models.CharField(max_length=80, unique=True, help_text="e.g. Nellore")
    address = models.TextField(
        help_text="Address block exactly as it should appear on a letter",
    )

    class Meta:
        verbose_name = "AEO / district office"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Establishment(models.Model):
    class Status(models.TextChoices):
        LIVE = "LIVE", "Live"
        CLOSED = "CLOSED", "Closed"

    class Dsc(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        NOT_REGISTERED = "NOT_REGISTERED", "Not registered"
        EXPIRED = "EXPIRED", "Expired / inactive"

    code = models.CharField("establishment code", max_length=30, unique=True)
    name = models.CharField(max_length=200)
    address = models.TextField(blank=True)
    district = models.CharField(max_length=60, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    addressee = models.CharField(
        "letters addressed to",
        max_length=120,
        default="The Authorised Signatory",
    )
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.LIVE)
    edli_exempted = models.BooleanField("exempted under EDLI", default=False)
    dsc_status = models.CharField(
        "DSC / e-Sign", max_length=16, choices=Dsc.choices, default=Dsc.ACTIVE
    )
    aeo_office = models.ForeignKey(
        AEOOffice, null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name="AEO / district office",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"

    @property
    def address_block(self):
        lines = [self.addressee + ",", "M/s " + self.name + ","]
        lines += [ln.strip() for ln in self.address.splitlines() if ln.strip()]
        return "\n".join(lines)


class ClaimStatus(models.TextChoices):
    REGISTERED = "REGISTERED", "Received – scrutiny not started"
    UNDER_SCRUTINY = "UNDER_SCRUTINY", "Under scrutiny (DA)"
    WITH_SS = "WITH_SS", "With Section Supervisor"
    WITH_AO = "WITH_AO", "With Accounts Officer"
    PENDING_COMPLIANCE = "PENDING_COMPLIANCE", "Pending compliance"
    READY_FOR_INWARD = "READY_FOR_INWARD", "Approved – ready for inward"
    INWARDED = "INWARDED", "Inwarded – under settlement"
    SETTLED = "SETTLED", "Settled"
    CLOSED = "CLOSED", "Closed / returned"


OPEN_STATUSES = [
    ClaimStatus.REGISTERED, ClaimStatus.UNDER_SCRUTINY, ClaimStatus.WITH_SS,
    ClaimStatus.WITH_AO, ClaimStatus.PENDING_COMPLIANCE, ClaimStatus.READY_FOR_INWARD,
    ClaimStatus.INWARDED,
]


class Claim(models.Model):
    class Mode(models.TextChoices):
        POST = "POST", "By post"
        IN_PERSON = "IN_PERSON", "In person"

    class Gender(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"

    diary_no = models.CharField(max_length=20, unique=True, editable=False)
    received_on = models.DateField("date of receipt", default=timezone.localdate)
    mode = models.CharField("mode of receipt", max_length=10, choices=Mode.choices,
                            default=Mode.POST)
    form_20 = models.BooleanField("Form 20 (PF)", default=True)
    form_10d = models.BooleanField("Form 10D (Pension)", default=True)
    form_5if = models.BooleanField("Form 5(IF) (EDLI)", default=True)
    form_10c = models.BooleanField("Form 10C (W/B)", default=False)

    member_name = models.CharField(max_length=120)
    gender = models.CharField(max_length=1, choices=Gender.choices, default=Gender.MALE)
    uan = models.CharField("UAN", max_length=12, blank=True)
    pf_no = models.CharField("PF account number (member ID)", max_length=40)
    establishment = models.ForeignKey(Establishment, on_delete=models.PROTECT,
                                      related_name="claims")
    accounts_group = models.CharField("accounts group (AG)", max_length=20, blank=True)
    efile_no = models.CharField("e-Office file no.", max_length=30, blank=True)
    date_of_death = models.DateField(null=True, blank=True)

    claimant_name = models.CharField(max_length=120)
    claimant_relation = models.CharField("relationship with member", max_length=40,
                                         blank=True)
    claimant_address = models.TextField(blank=True)
    claimant_mobile = models.CharField(max_length=15, blank=True)

    status = models.CharField(max_length=20, choices=ClaimStatus.choices,
                              default=ClaimStatus.REGISTERED, db_index=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name="assigned_claims",
                                    verbose_name="dealing assistant")
    remarks = models.TextField(blank=True)
    closed_reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    stage_since = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-received_on", "-id"]

    def __str__(self):
        return f"{self.diary_no} – {self.member_name}"

    def save(self, *args, **kwargs):
        if not self.diary_no:
            with transaction.atomic():
                year = self.received_on.year
                last = (Claim.objects.select_for_update()
                        .filter(diary_no__startswith=f"DC/{year}/")
                        .order_by("-diary_no").first())
                serial = int(last.diary_no.rsplit("/", 1)[1]) + 1 if last else 1
                self.diary_no = f"DC/{year}/{serial:04d}"
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    # -- convenience ------------------------------------------------------
    @property
    def late_name(self):
        title = "Smt." if self.gender == self.Gender.FEMALE else "Sri"
        return f"Late {title} {self.member_name}"

    @property
    def pf_tail(self):
        """Establishment and member number of the PF account, without the
        office code: GR/CDP/50173/123 -> 50173/123."""
        parts = [p.strip() for p in self.pf_no.split("/") if p.strip()]
        while len(parts) > 1 and parts[0].isalpha():
            parts.pop(0)
        return "/".join(parts)

    @property
    def forms_claimed(self):
        out = []
        if self.form_20:
            out.append("Form 20")
        if self.form_10d:
            out.append("Form 10D")
        if self.form_5if:
            out.append("Form 5(IF)")
        if self.form_10c:
            out.append("Form 10C")
        return out

    @property
    def forms_text(self):
        f = self.forms_claimed
        if not f:
            return "—"
        return f[0] if len(f) == 1 else ", ".join(f[:-1]) + " & " + f[-1]

    @property
    def current_scrutiny(self):
        return self.scrutinies.order_by("-round_no").first()

    @property
    def age_days(self):
        return (timezone.localdate() - self.received_on).days

    @property
    def days_in_stage(self):
        return (timezone.now() - self.stage_since).days

    @property
    def is_open(self):
        return self.status in OPEN_STATUSES

    def open_findings(self):
        return self.findings.filter(status=Finding.Status.OPEN, included=True).exclude(
            route=Route.NOTE)

    @property
    def pending_with(self):
        """Who the claim is waiting on, for the 'pending compliance' stage."""
        if self.status != ClaimStatus.PENDING_COMPLIANCE:
            return []
        routes = set(self.open_findings().values_list("route", flat=True))
        return [Route(r).label for r in Route.values if r in routes]

    def log(self, user, action, note="", from_status="", to_status=""):
        return Event.objects.create(claim=self, user=user, action=action, note=note,
                                    from_status=from_status, to_status=to_status)

    def move_to(self, status, user, action, note=""):
        old = self.status
        self.status = status
        self.stage_since = timezone.now()
        self.save(update_fields=["status", "stage_since", "updated_at"])
        self.log(user, action, note, old, status)


class ClaimForm(models.Model):
    """Inward and settlement particulars of each form of an approved claim."""

    class State(models.TextChoices):
        PENDING = "PENDING", "Under process"
        SETTLED = "SETTLED", "Settled"
        REJECTED = "REJECTED", "Rejected / returned"

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="forms")
    form_type = models.CharField(max_length=12)
    app_claim_id = models.CharField("claim ID (application software)", max_length=40,
                                blank=True)
    inward_on = models.DateField(null=True, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    settled_on = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    reference = models.CharField("PPO no. / remarks", max_length=120, blank=True)

    class Meta:
        unique_together = [("claim", "form_type")]
        ordering = ["id"]

    def __str__(self):
        return f"{self.claim.diary_no} {self.form_type}"


class Route(models.TextChoices):
    """Who has to act on a finding – this decides which letter it lands in."""

    EMPLOYER = "EMPLOYER", "Employer"
    CLAIMANT = "CLAIMANT", "Claimant"
    AEO = "AEO", "Area Enforcement Officer"
    ACCOUNTS = "ACCOUNTS", "Accounts Group (U.O. note)"
    OTHER_RO = "OTHER_RO", "Other Regional Office"
    INTERNAL = "INTERNAL", "Death Cell (JD / Form-13 inward)"
    NOTE = "NOTE", "Note only – no action"


class Scrutiny(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft with DA"
        WITH_SS = "WITH_SS", "Submitted to Section Supervisor"
        WITH_AO = "WITH_AO", "Validated – with Accounts Officer"
        APPROVED = "APPROVED", "Approved"

    class Action(models.TextChoices):
        INWARD = "INWARD", "Death claim forms may be inwarded"
        RETURN = "RETURN", "Return for rectification / call for documents"
        REFER_AEO = "REFER_AEO", "Refer to Area Enforcement Officer"
        OFFICE_ACTION = "OFFICE_ACTION", "Hold – office-side action to be completed"

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="scrutinies")
    round_no = models.PositiveIntegerField(default=1)
    answers = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    action = models.CharField("proposed action", max_length=16, choices=Action.choices,
                              blank=True)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                    on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    validated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")
    validated_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    da_note = models.TextField(blank=True)
    ss_note = models.TextField(blank=True)
    ao_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("claim", "round_no")]
        ordering = ["claim", "round_no"]
        verbose_name_plural = "scrutinies"

    def __str__(self):
        return f"{self.claim.diary_no} round {self.round_no}"

    def active_findings(self):
        return self.findings.filter(included=True)

    def blocking_findings(self):
        return self.active_findings().exclude(route=Route.NOTE)


class Finding(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        RESOLVED = "RESOLVED", "Resolved"
        SUPERSEDED = "SUPERSEDED", "Carried to next round"

    class Category(models.TextChoices):
        PROFILE = "PROFILE", "Member profile / JD"
        EXIT = "EXIT", "Date & reason of exit"
        ESTT = "ESTT", "Establishment status / attestation"
        DSC = "DSC", "DSC / e-Sign"
        SERVICE = "SERVICE", "Past service / transfer"
        CONTRIBUTION = "CONTRIBUTION", "Contributions / NCP"
        EDLI = "EDLI", "EDLI"
        EPS = "EPS", "Pension membership"
        NOMINATION = "NOMINATION", "Nomination"
        FORMS = "FORMS", "Claim form completion"
        KYC = "KYC", "UAN / Aadhaar"
        ENCLOSURE = "ENCLOSURE", "Enclosures"
        ELIGIBILITY = "ELIGIBILITY", "Benefit eligibility"
        ACCOUNTS = "ACCOUNTS", "Accounts adjustment"
        DELAY = "DELAY", "Belated claim"
        OTHER = "OTHER", "Other"

    scrutiny = models.ForeignKey(Scrutiny, on_delete=models.CASCADE,
                                 related_name="findings")
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="findings")
    code = models.CharField(max_length=80)
    point = models.CharField("checklist point", max_length=6, blank=True)
    category = models.CharField(max_length=14, choices=Category.choices,
                                default=Category.OTHER)
    route = models.CharField(max_length=10, choices=Route.choices,
                             default=Route.EMPLOYER)
    title = models.CharField(max_length=120)
    text = models.TextField()
    other_office = models.CharField(max_length=80, blank=True)
    auto = models.BooleanField(default=True)
    edited = models.BooleanField(default=False)
    included = models.BooleanField(default=True)
    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.OPEN)
    resolved_on = models.DateField(null=True, blank=True)
    resolution = models.CharField(max_length=250, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        unique_together = [("scrutiny", "code")]

    def __str__(self):
        return f"{self.claim.diary_no}: {self.title}"


class Communication(models.Model):
    class Kind(models.TextChoices):
        EMPLOYER = "EMPLOYER", "Letter to employer"
        CLAIMANT = "CLAIMANT", "Letter to claimant"
        AEO = "AEO", "Reference to AEO"
        UO_NOTE = "UO_NOTE", "U.O. note to Accounts Group"
        OTHER_RO = "OTHER_RO", "Letter to other Regional Office"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        APPROVED = "APPROVED", "Approved – to be issued"
        ISSUED = "ISSUED", "Issued – reply awaited"
        REPLIED = "REPLIED", "Reply received"
        CANCELLED = "CANCELLED", "Cancelled"

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE,
                              related_name="communications")
    scrutiny = models.ForeignKey(Scrutiny, null=True, blank=True,
                                 on_delete=models.SET_NULL,
                                 related_name="communications")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    other_office = models.CharField(max_length=80, blank=True)
    reminder_of = models.ForeignKey("self", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="reminders")
    reminder_no = models.PositiveIntegerField(default=0)

    to_block = models.TextField("addressed to")
    subject = models.TextField()
    reference = models.TextField(blank=True)
    salutation = models.CharField(max_length=40, default="Sir,")
    intro = models.TextField()
    points = models.JSONField(default=list, blank=True)
    closing = models.TextField(blank=True)
    copy_to = models.TextField(blank=True)
    signatory = models.CharField(max_length=120, blank=True)
    edited = models.BooleanField(default=False)

    year = models.PositiveIntegerField(null=True, blank=True)
    serial = models.PositiveIntegerField(null=True, blank=True)
    number = models.CharField("letter no.", max_length=80, blank=True)
    letter_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.DRAFT)
    issued_on = models.DateField(null=True, blank=True)
    dispatch_mode = models.CharField(max_length=40, blank=True)
    due_on = models.DateField(null=True, blank=True)
    reply_on = models.DateField(null=True, blank=True)
    reply_note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.get_kind_display()} – {self.claim.diary_no}"

    @property
    def is_uo(self):
        return self.kind == self.Kind.UO_NOTE

    @property
    def is_overdue(self):
        return (self.status == self.Status.ISSUED and self.due_on is not None
                and self.due_on < timezone.localdate())

    @property
    def days_overdue(self):
        return (timezone.localdate() - self.due_on).days if self.is_overdue else 0

    @property
    def title(self):
        base = self.get_kind_display()
        if self.other_office:
            base += f" ({self.other_office})"
        if self.reminder_no:
            base = f"Reminder {self.reminder_no} – {base}"
        return base

    def number_stem(self, year):
        """The letter number without its running serial."""
        if self.is_uo:
            return f"{settings.OFFICE['uo_prefix']}/{self.claim.pf_tail}/{year}/"
        return f"{settings.OFFICE['letter_prefix']} /{year}/"

    def assign_number(self, when=None):
        """Give the letter the next running number of the year (idempotent)."""
        if self.number:
            return
        when = when or timezone.localdate()
        start = office()["uo_serial_start" if self.is_uo else "letter_serial_start"]
        with transaction.atomic():
            qs = Communication.objects.select_for_update().filter(
                year=when.year, serial__isnull=False)
            qs = qs.filter(kind=self.Kind.UO_NOTE) if self.is_uo else qs.exclude(
                kind=self.Kind.UO_NOTE)
            last = qs.order_by("-serial").first()
            self.year = when.year
            self.serial = max(last.serial + 1 if last else start, start)
            self.number = f"{self.number_stem(when.year)}{self.serial}"
            self.letter_date = when
            self.save(update_fields=["year", "serial", "number", "letter_date"])

    def mark_issued(self, when, mode="", due_on=None):
        self.status = self.Status.ISSUED
        self.issued_on = when
        self.dispatch_mode = mode
        self.due_on = due_on or when + timedelta(days=office()["reply_days"])
        self.save(update_fields=["status", "issued_on", "dispatch_mode", "due_on"])


class Event(models.Model):
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="events")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                             on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=120)
    note = models.TextField(blank=True)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]

    def __str__(self):
        return f"{self.claim.diary_no}: {self.action}"
