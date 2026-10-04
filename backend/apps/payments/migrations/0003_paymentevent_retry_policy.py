from django.db import migrations, models


def block_unclassified_errors(apps, schema_editor):
    # 旧 ERROR 没有异常类型证据，不能猜成可自动重试；不伪造旧处理次数。
    event = apps.get_model("payments", "PaymentEvent")
    event.objects.using(schema_editor.connection.alias).filter(
        processing_status="ERROR"
    ).update(failure_kind="BLOCKED")


class Migration(migrations.Migration):
    dependencies = [("payments", "0002_refundsimulation")]

    operations = [
        migrations.AddField(
            model_name="paymentevent",
            name="processing_attempt_count",
            field=models.PositiveSmallIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="paymentevent",
            name="failure_kind",
            field=models.CharField(
                choices=[("NONE", "None"), ("TRANSIENT", "Transient"),
                         ("BLOCKED", "Needs investigation"), ("EXHAUSTED", "Attempts exhausted")],
                default="NONE", editable=False, max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="paymentevent",
            name="next_retry_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AlterField(
            model_name="paymentevent",
            name="processing_status",
            field=models.CharField(
                choices=[("RECEIVED", "Received"), ("PROCESSING", "Processing"),
                         ("PROCESSED", "Processed"), ("REJECTED", "Rejected"), ("ERROR", "Error")],
                default="RECEIVED", max_length=16,
            ),
        ),
        migrations.RunPython(block_unclassified_errors, migrations.RunPython.noop),
    ]
