from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (AEOOffice, Claim, ClaimForm, Communication, Establishment, Event,
                     Finding, OfficeSettings, Scrutiny, User)


@admin.register(User)
class CellUserAdmin(UserAdmin):
    list_display = ("username", "first_name", "last_name", "role", "designation",
                    "is_active")
    list_filter = ("role", "is_active")
    fieldsets = UserAdmin.fieldsets + (("Death Claims Cell", {
        "fields": ("role", "designation")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Death Claims Cell", {
        "fields": ("first_name", "last_name", "role", "designation")}),)


@admin.register(Establishment)
class EstablishmentAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "district", "status", "edli_exempted", "dsc_status")
    list_filter = ("status", "edli_exempted", "dsc_status", "district")
    search_fields = ("code", "name")


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
    list_display = ("diary_no", "member_name", "pf_no", "establishment", "status",
                    "received_on")
    list_filter = ("status",)
    search_fields = ("diary_no", "member_name", "pf_no", "uan")
    readonly_fields = ("diary_no",)


@admin.register(Communication)
class CommunicationAdmin(admin.ModelAdmin):
    list_display = ("number", "kind", "claim", "status", "letter_date", "issued_on")
    list_filter = ("kind", "status")
    search_fields = ("number", "claim__diary_no")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("at", "claim", "user", "action")
    readonly_fields = ("claim", "user", "action", "note", "from_status", "to_status")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register([AEOOffice, Scrutiny, Finding, ClaimForm, OfficeSettings])
admin.site.site_header = "Death Claims Cell – administration"
