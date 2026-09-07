from __future__ import annotations

from .domain import Summary


def format_summary(summary: Summary) -> str:
    stamp = summary.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"Progress summary — business day {summary.business_day}", f"Generated: {stamp}", f"Participants reporting today: {len(summary.participants)}"]
    if summary.location_units is None:
        lines.append("Location: no accepted reports yet.")
    elif summary.target is None:
        lines.append(f"Location: {summary.location_units}; today’s target is not set.")
    else:
        percent = 0 if summary.target == 0 else summary.location_units / summary.target * 100
        lines.append(f"Location: {summary.location_units}/{summary.target} ({percent:.1f}%)")
        lines.append(f"Expected linear pace: {summary.pace_expected:.1f}; variance: {summary.pace_variance:+.1f}")
    if summary.conflicting_location_values:
        conflicts = ", ".join(f"{user_id}={value}" for user_id, value in summary.conflicting_location_values.items())
        lines.append(f"⚠ Conflicting current location totals: {conflicts}. Latest value is used.")
    if summary.participants:
        lines.append("Users:")
        for report in summary.participants:
            when = report.received_at.strftime("%H:%M UTC")
            lines.append(f"• {report.user_id}: personal {report.personal_units}, location {report.location_units}; {report.report_count} reports; last {when}")
    return "\n".join(lines)
