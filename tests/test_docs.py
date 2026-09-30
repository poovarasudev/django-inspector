"""Keep the README's settings reference in step with conf.DEFAULTS."""

import pathlib

from django_inspector import __version__
from django_inspector.conf import DEFAULTS

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_readme_documents_every_setting_and_watcher():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = [key for key in DEFAULTS if "`%s`" % key not in readme]
    missing += ['"%s"' % name for name in DEFAULTS["WATCHERS"] if '`"%s"`' % name not in readme]
    assert missing == []


def test_changelog_has_an_entry_for_the_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [%s]" % __version__ in changelog
