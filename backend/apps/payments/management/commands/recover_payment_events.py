"""运行一次 Mock 支付 Inbox 恢复，可先预览候选事件。"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.payments.recovery import recover_payment_events, republish_payment_events


class Command(BaseCommand):
    help = "恢复首次处理和到期可重试的 Inbox；遵守累计预算，只运行一轮。"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100, help="本轮最多选取条数。")
        parser.add_argument("--event-id", type=int, help="指定 Inbox 内部数据库 ID。")
        parser.add_argument("--dry-run", action="store_true", help="只列出候选，不处理。")
        parser.add_argument("--publish", action="store_true", help="只向 MQ 补投原事件 ID，不在命令中处理业务。")

    def handle(self, *args, **options):
        if not settings.IS_LOCAL_ENVIRONMENT:
            raise CommandError("本期 Mock 支付恢复命令仅允许 development/test 环境。")
        if options["publish"] and not settings.PAYMENT_EVENTS_ASYNC:
            raise CommandError("MQ 补投要求 PAYMENT_EVENTS_ASYNC=true；请显式选择异步环境。")
        recover = republish_payment_events if options["publish"] else recover_payment_events
        try:
            result = recover(
                limit=options["limit"],
                event_id=options["event_id"],
                dry_run=options["dry_run"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if options["dry_run"]:
            self.stdout.write(
                f"dry_run=true selected={len(result.selected_ids)} "
                f"inbox_ids={','.join(map(str, result.selected_ids)) or '-'}"
            )
            return
        if options["publish"]:
            self.stdout.write(
                f"selected={len(result.selected_ids)} published={len(result.published_ids)} "
                f"errors={len(result.failed_ids)} skipped={len(result.skipped_ids)} "
                f"unattempted={len(result.unattempted_ids)}"
            )
            if result.failed_ids:
                self.stderr.write(f"failed_inbox_ids={','.join(map(str, result.failed_ids))}")
                raise CommandError("MQ 发布失败，本轮已停止补投；原事件保留，请恢复 Broker 后重跑。")
            return
        self.stdout.write(
            f"selected={len(result.selected_ids)} processed={result.processed} "
            f"rejected={result.rejected} errors={len(result.failed_ids)} skipped={result.skipped}"
        )
        if result.failed_ids:
            self.stderr.write(f"failed_inbox_ids={','.join(map(str, result.failed_ids))}")
            raise CommandError("部分事件处理出现异常，已继续处理本批其他事件；请检查 Inbox。")
