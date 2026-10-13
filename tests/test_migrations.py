"""The model and migrations must stay in sync (SCHEMA-01)."""

import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_makemigrations_check_finds_no_changes():
    """
    --check --dry-run exits non-zero (raises SystemExit) if the model has
    changes no migration captures yet.
    """
    call_command("makemigrations", "django_inspector", "--check", "--dry-run")
