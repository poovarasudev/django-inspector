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
