import re

from django import forms

from django.utils import timezone

from .models import Claim, Communication, Establishment, Finding, Role, Route, User


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class ClaimForm(forms.ModelForm):
    class Meta:
        model = Claim
        fields = ["received_on", "mode", "form_20", "form_10d", "form_5if", "form_10c",
                  "member_name", "gender", "uan", "pf_no", "establishment",
                  "accounts_group", "efile_no", "date_of_death", "claimant_name",
                  "claimant_relation", "claimant_address", "claimant_mobile",
                  "assigned_to", "remarks"]
        widgets = {
            "received_on": DateInput(),
            "date_of_death": DateInput(),
            "claimant_address": forms.Textarea(attrs={"rows": 3}),
            "remarks": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = User.objects.filter(
            is_active=True, role__in=[Role.DA, Role.ADMIN])
        self.fields["assigned_to"].required = False
        self.fields["claimant_relation"].widget.attrs["placeholder"] = "Wife / Son / Mother …"
        self.fields["pf_no"].widget.attrs["placeholder"] = "GR/CDP/43246/10513"

    def clean(self):
        data = super().clean()
        if not any(data.get(f) for f in ("form_20", "form_10d", "form_5if", "form_10c")):
            raise forms.ValidationError("Select at least one claim form.")
        death, received = data.get("date_of_death"), data.get("received_on")
        if death and received and death > received:
            self.add_error("date_of_death", "Date of death is after the date of receipt.")
        return data


class EstablishmentForm(forms.ModelForm):
    class Meta:
        model = Establishment
        fields = ["code", "name", "addressee", "address", "district", "email", "phone",
                  "status", "edli_exempted", "dsc_status", "aeo_office"]
        widgets = {"address": forms.Textarea(attrs={"rows": 4})}


class CommunicationForm(forms.ModelForm):
    points_text = forms.CharField(
        label="Numbered points", required=False,
        widget=forms.Textarea(attrs={"rows": 12}),
        help_text="One point per paragraph – leave a blank line between points.")

    class Meta:
        model = Communication
        fields = ["number", "letter_date", "to_block", "subject", "reference",
                  "salutation", "intro", "points_text", "closing", "copy_to",
                  "signatory"]
        widgets = {
            "letter_date": DateInput(),
            "to_block": forms.Textarea(attrs={"rows": 5}),
            "subject": forms.Textarea(attrs={"rows": 2}),
            "reference": forms.Textarea(attrs={"rows": 2}),
            "intro": forms.Textarea(attrs={"rows": 4}),
            "closing": forms.Textarea(attrs={"rows": 5}),
            "copy_to": forms.Textarea(attrs={"rows": 8}),
        }
        help_texts = {
            "copy_to": "Leave a blank line between two addressees.",
            "closing": "Leave a blank line between paragraphs.",
            "number": "Assigned automatically on approval; may be corrected here.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["points_text"].initial = "\n\n".join(self.instance.points or [])
        for name in ("number", "letter_date", "salutation", "signatory"):
            self.fields[name].required = False

    def save(self, commit=True):
        self.instance.points = [p.strip() for p in
                                self.cleaned_data["points_text"].split("\n\n") if p.strip()]
        self.instance.edited = True
        serial = re.search(r"(\d+)\s*$", self.cleaned_data.get("number") or "")
        if serial and "number" in self.changed_data:
            # Keep the running series in step with a number corrected by hand.
            self.instance.serial = int(serial.group(1))
            if self.instance.year is None:
                self.instance.year = (self.cleaned_data.get("letter_date")
                                      or timezone.localdate()).year
        return super().save(commit)


class FindingForm(forms.ModelForm):
    class Meta:
        model = Finding
        fields = ["title", "text", "route", "other_office"]
        widgets = {"text": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["route"].choices = Route.choices
        self.fields["other_office"].required = False
