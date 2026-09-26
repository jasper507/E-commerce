"""运行一次未付款订单到期扫描；生产调度需另行配置。"""

from django.core.management.base import BaseCommand

from apps.ordering.lifecycle_services import expire_due_orders


class Command(BaseCommand):
    help = "关闭已到期且支付结果已明确的待付款订单，并释放预占库存。"

    def handle(self, *args, **options):
        expired, unresolved = expire_due_orders()
        self.stdout.write(f"expired={expired} payment_unresolved={unresolved}")
