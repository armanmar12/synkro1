from django.db import migrations


def seed_globalfruit(apps, schema_editor):
    Tenant = apps.get_model("core", "Tenant")
    Tenant.objects.get_or_create(slug="globalfruit", defaults={"name": "GlobalFruit"})


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_reportmessage"),
    ]

    operations = [
        migrations.RunPython(seed_globalfruit, migrations.RunPython.noop),
    ]
