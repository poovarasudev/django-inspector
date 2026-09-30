import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SECRET_KEY = "django-inspector-test-secret-key"
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django_inspector",
]
MIDDLEWARE = [
    "django_inspector.middleware.InspectorMiddleware",
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
# INSPECTOR_TEST_DB=postgres runs the suite on PostgreSQL (JSONField lookups
# behave differently per backend). CI provides the server; see ci.yml.
if os.environ.get("INSPECTOR_TEST_DB") == "postgres":
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("PGDATABASE", "inspector_test"),
        "USER": os.environ.get("PGUSER", "postgres"),
        "PASSWORD": os.environ.get("PGPASSWORD", ""),
        "HOST": os.environ.get("PGHOST", "localhost"),
        "PORT": os.environ.get("PGPORT", "5432"),
    }
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DJANGO_INSPECTOR = {
    "INSPECTOR_ENABLED": True,
}
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "templates")],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]
ROOT_URLCONF = "tests.urls"
