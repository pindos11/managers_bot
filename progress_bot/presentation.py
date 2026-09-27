from __future__ import annotations

from html import escape

from .domain import Summary, format_user_label
from .translations import Translator


def format_summary(
    summary: Summary,
    translator: Translator | None = None,
    card_percent_bad_threshold: float | None = None,
) -> str:
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
        headers = (
            translator.text("summary_table_person"),
            translator.text("summary_table_personal"),
            translator.text("summary_table_location"),
            translator.text("summary_table_card"),
            translator.text("summary_table_cash"),
            translator.text("summary_table_reports"),
            translator.text("summary_table_last"),
        )
        rows = []
        for report in summary.participants:
            card = "—"
            if report.card_units is not None:
                percent = 0 if report.personal_units == 0 else report.card_units / report.personal_units * 100
                is_bad = card_percent_bad_threshold is not None and percent < card_percent_bad_threshold
                card = f"{report.card_units} ({percent:.0f}%){' ⚠' if is_bad else ''}"
            rows.append((
                format_user_label(report.user_id, report.display_name),
                str(report.personal_units),
                str(report.location_units),
                card,
                "—" if report.cash_units is None else str(report.cash_units),
                str(report.report_count),
                report.received_at.strftime("%H:%M"),
            ))
        lines.append(_format_table(headers, rows))

    # Summary text is sent with Telegram HTML parsing enabled so that the
    # table can be a preformatted block. Escape every ordinary text fragment.
    return "\n".join(line if line.startswith("<pre>") else escape(line) for line in lines)


def _format_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    """Return a Telegram HTML preformatted table with aligned columns."""
    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]

    def render(row: tuple[str, ...]) -> str:
        return "  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip()

    divider = "  ".join("-" * width for width in widths)
    table = "\n".join((render(headers), divider, *(render(row) for row in rows)))
    return f"<pre>{escape(table)}</pre>"
