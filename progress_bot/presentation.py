from __future__ import annotations

from .domain import Summary, format_user_label
from .translations import Translator


def format_summary(summary: Summary, translator: Translator | None = None) -> str:
    translator = translator or Translator()
    stamp = summary.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        translator.text("summary_title", day=summary.business_day),
        translator.text("summary_generated", stamp=stamp),
        translator.text("summary_participants", count=len(summary.participants)),
    ]
    if summary.location_units is None:
        lines.append(translator.text("summary_location_empty"))
    elif summary.target is None:
        lines.append(translator.text("summary_location_no_target", location=summary.location_units))
    else:
        percent = 0 if summary.target == 0 else summary.location_units / summary.target * 100
        lines.append(translator.text("summary_location_target", location=summary.location_units, target=summary.target, percent=percent))
        lines.append(translator.text("summary_pace", expected=summary.pace_expected, variance=summary.pace_variance))
    if summary.conflicting_location_values:
        names = {report.user_id: report.display_name for report in summary.participants}
        conflicts = ", ".join(
            f"{format_user_label(user_id, names.get(user_id))}={value}"
            for user_id, value in summary.conflicting_location_values.items()
        )
        lines.append(translator.text("summary_conflict", conflicts=conflicts))
    if summary.participants:
        lines.append(translator.text("summary_users"))
        for report in summary.participants:
            payment_values = []
            if report.card_units is not None:
                payment_values.append(translator.text("summary_card", card=report.card_units))
            if report.cash_units is not None:
                payment_values.append(translator.text("summary_cash", cash=report.cash_units))
            payments = f", {', '.join(payment_values)}" if payment_values else ""
            lines.append(translator.text(
                "summary_user",
                label=format_user_label(report.user_id, report.display_name),
                personal=report.personal_units,
                location=report.location_units,
                count=report.report_count,
                when=report.received_at.strftime("%H:%M UTC"),
            ) + payments)
    return "\n".join(lines)
