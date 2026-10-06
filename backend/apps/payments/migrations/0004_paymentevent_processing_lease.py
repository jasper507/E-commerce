from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0003_paymentevent_retry_policy")]

    operations = [
        migrations.AddField(
            model_name="paymentevent",
            name="processing_started_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="paymentevent",
            name="processing_lease_until",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
