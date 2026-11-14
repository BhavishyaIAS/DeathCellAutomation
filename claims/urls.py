from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("api/summary/", views.api_summary, name="api_summary"),
    path("claims/", views.claim_list, name="claim_list"),
    path("claims/new/", views.claim_new, name="claim_new"),
    path("claims/<int:pk>/", views.claim_detail, name="claim_detail"),
    path("claims/<int:pk>/edit/", views.claim_edit, name="claim_edit"),
    path("claims/<int:pk>/do/<slug:action>/", views.claim_action, name="claim_action"),
    path("claims/<int:pk>/scrutiny/", views.scrutiny, name="scrutiny"),
    path("claims/<int:pk>/scrutiny/preview/", views.scrutiny_preview,
         name="scrutiny_preview"),
    path("claims/<int:pk>/checklist/", views.checklist_print, name="checklist_print"),
    path("claims/<int:pk>/checklist/<int:round_no>/", views.checklist_print,
         name="checklist_print_round"),
    path("claims/<int:pk>/findings/add/", views.finding_add, name="finding_add"),
    path("findings/<int:pk>/save/", views.finding_save, name="finding_save"),
    path("findings/<int:pk>/resolve/", views.finding_resolve, name="finding_resolve"),
    path("letters/", views.letter_list, name="letter_list"),
    path("letters/<int:pk>/", views.letter_view, name="letter_view"),
    path("letters/<int:pk>/edit/", views.letter_edit, name="letter_edit"),
    path("letters/<int:pk>/word/", views.letter_download, name="letter_download"),
    path("letters/<int:pk>/do/<slug:action>/", views.letter_action,
         name="letter_action"),
    path("reports/", views.reports, name="reports"),
    path("establishments/", views.establishment_list, name="establishment_list"),
    path("establishments/new/", views.establishment_edit, name="establishment_new"),
    path("establishments/<int:pk>/", views.establishment_edit,
         name="establishment_edit"),
]
