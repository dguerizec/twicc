from django.db import migrations, models


def migrate_single_url_forward(apps, schema_editor):
    """default_browser_url (single string) → browser_urls ([{url, default: true}])."""
    Project = apps.get_model("core", "Project")
    for project in Project.objects.exclude(default_browser_url=None).exclude(default_browser_url=""):
        project.browser_urls = [{"url": project.default_browser_url, "default": True}]
        project.save(update_fields=["browser_urls"])


def migrate_single_url_backward(apps, schema_editor):
    """browser_urls → default_browser_url (the default entry, else the first)."""
    Project = apps.get_model("core", "Project")
    for project in Project.objects.exclude(browser_urls=[]):
        entries = [e for e in project.browser_urls if isinstance(e, dict) and e.get("url")]
        if not entries:
            continue
        default = next((e for e in entries if e.get("default")), entries[0])
        project.default_browser_url = default["url"]
        project.save(update_fields=["default_browser_url"])


class Migration(migrations.Migration):
    dependencies = [("core", "0126_modelbenchmark")]

    operations = [
        migrations.AddField(
            model_name="project",
            name="browser_urls",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(migrate_single_url_forward, migrate_single_url_backward),
        migrations.RemoveField(
            model_name="project",
            name="default_browser_url",
        ),
    ]
