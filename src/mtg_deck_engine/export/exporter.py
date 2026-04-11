"""Export deck analysis reports to JSON, Markdown, and HTML.

All export formats include the full analysis data: static analysis,
format info, archetype detection, advanced heuristics, and optionally
probability and goldfish results.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from mtg_deck_engine.models import AnalysisResult


def export_json(
    result: AnalysisResult,
    advanced: dict | None = None,
    archetype: str | None = None,
    path: Path | str | None = None,
) -> str:
    """Export analysis to JSON. Returns the JSON string and optionally writes to file."""
    data = {
        "generated_at": datetime.now().isoformat(),
        "deck_name": result.deck_name,
        "format": result.format,
        "total_cards": result.total_cards,
        "mana_curve": result.mana_curve,
        "average_cmc": result.average_cmc,
        "color_distribution": result.color_distribution,
        "color_sources": result.color_sources,
        "type_distribution": result.type_distribution,
        "tag_distribution": result.tag_distribution,
        "land_count": result.land_count,
        "nonland_count": result.nonland_count,
        "ramp_count": result.ramp_count,
        "interaction_count": result.interaction_count,
        "draw_engine_count": result.draw_engine_count,
        "threat_count": result.threat_count,
        "scores": result.scores,
        "issues": [{"severity": i.severity, "message": i.message, "card": i.card_name} for i in result.issues],
        "recommendations": result.recommendations,
    }

    if archetype:
        data["detected_archetype"] = archetype
    if advanced:
        data["advanced"] = advanced

    output = json.dumps(data, indent=2)
    if path:
        Path(path).write_text(output, encoding="utf-8")
    return output


def export_markdown(
    result: AnalysisResult,
    advanced: dict | None = None,
    archetype: str | None = None,
    path: Path | str | None = None,
) -> str:
    """Export analysis to Markdown."""
    lines: list[str] = []
    lines.append(f"# {result.deck_name} — Deck Analysis Report")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")

    if result.format:
        lines.append(f"**Format:** {result.format}  ")
    lines.append(f"**Total Cards:** {result.total_cards}  ")
    if archetype:
        lines.append(f"**Detected Archetype:** {archetype}  ")
    lines.append("")

    # Mana Curve
    lines.append("## Mana Curve")
    lines.append(f"**Average Mana Value:** {result.average_cmc}\n")
    lines.append("| MV | Count |")
    lines.append("|----|-------|")
    for mv in range(8):
        count = result.mana_curve.get(mv, 0)
        label = str(mv) if mv < 7 else "7+"
        lines.append(f"| {label} | {count} |")
    lines.append("")

    # Type Distribution
    lines.append("## Card Types")
    lines.append("| Type | Count |")
    lines.append("|------|-------|")
    for t, count in sorted(result.type_distribution.items(), key=lambda x: -x[1]):
        lines.append(f"| {t} | {count} |")
    lines.append("")

    # Key Counts
    lines.append("## Key Counts")
    lines.append(f"- **Lands:** {result.land_count}")
    lines.append(f"- **Ramp:** {result.ramp_count}")
    lines.append(f"- **Card Draw:** {result.draw_engine_count}")
    lines.append(f"- **Interaction:** {result.interaction_count}")
    lines.append(f"- **Threats:** {result.threat_count}")
    lines.append("")

    # Scores
    if result.scores:
        lines.append("## Category Scores")
        lines.append("| Category | Score |")
        lines.append("|----------|-------|")
        for key, score in result.scores.items():
            name = key.replace("_", " ").title()
            lines.append(f"| {name} | {score:.0f} |")
        lines.append("")

    # Advanced
    if advanced:
        if advanced.get("mana_base_grade"):
            lines.append(f"## Mana Base Grade: {advanced['mana_base_grade']}")
            for note in advanced.get("mana_base_notes", []):
                lines.append(f"- {note}")
            lines.append("")

        if advanced.get("synergies"):
            lines.append("## Detected Synergies")
            for syn in advanced["synergies"][:10]:
                lines.append(f"- **{syn['card_a']}** + **{syn['card_b']}** — {syn['reason']}")
            lines.append("")

    # Issues
    if result.issues:
        lines.append("## Issues")
        for issue in result.issues:
            icon = "❌" if issue.severity == "error" else "⚠️" if issue.severity == "warning" else "ℹ️"
            lines.append(f"- {icon} {issue.message}")
        lines.append("")

    # Recommendations
    if result.recommendations:
        lines.append("## Recommendations")
        for rec in result.recommendations:
            lines.append(f"- {rec}")
        lines.append("")

    if advanced and advanced.get("advanced_recommendations"):
        lines.append("## Advanced Recommendations")
        for rec in advanced["advanced_recommendations"]:
            lines.append(f"- {rec}")
        lines.append("")

    lines.append("---")
    lines.append("*Card data provided by Scryfall. Not affiliated with Wizards of the Coast.*")

    output = "\n".join(lines)
    if path:
        Path(path).write_text(output, encoding="utf-8")
    return output


def export_html(
    result: AnalysisResult,
    advanced: dict | None = None,
    archetype: str | None = None,
    path: Path | str | None = None,
) -> str:
    """Export analysis to a self-contained HTML page."""
    # Generate markdown first, then wrap in HTML with styling
    md_content = export_markdown(result, advanced, archetype)

    # Convert basic markdown to HTML (simple conversion, no external deps)
    body_html = _md_to_html(md_content)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{result.deck_name} — Deck Analysis</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 800px; margin: 40px auto; padding: 0 20px; color: #1a1a1a;
         background: #fafafa; line-height: 1.6; }}
  h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
  h2 {{ color: #34495e; margin-top: 30px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 10px 0 20px; }}
  th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
  th {{ background: #3498db; color: white; }}
  tr:nth-child(even) {{ background: #f2f2f2; }}
  ul {{ padding-left: 20px; }}
  li {{ margin: 4px 0; }}
  em {{ color: #666; }}
  strong {{ color: #2c3e50; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 30px 0; }}
  .footer {{ color: #999; font-size: 0.85em; margin-top: 40px; }}
</style>
</head>
<body>
{body_html}
</body>
</html>"""

    if path:
        Path(path).write_text(html, encoding="utf-8")
    return html


def _md_to_html(md: str) -> str:
    """Simple Markdown to HTML converter (no external deps)."""
    lines = md.split("\n")
    html_lines: list[str] = []
    in_table = False
    in_list = False
    is_header_row = True

    for line in lines:
        stripped = line.strip()

        # Headers
        if stripped.startswith("# "):
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped.lstrip("# ").strip()
            text = _inline_md(text)
            html_lines.append(f"<h{level}>{text}</h{level}>")
            continue

        # HR
        if stripped == "---":
            html_lines.append("<hr>")
            continue

        # Table
        if "|" in stripped and not stripped.startswith("-"):
            if stripped.replace("|", "").replace("-", "").strip() == "":
                continue  # Skip separator row
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                html_lines.append("<table>")
                in_table = True
                is_header_row = True
            tag = "th" if is_header_row else "td"
            row = "".join(f"<{tag}>{_inline_md(c)}</{tag}>" for c in cells)
            html_lines.append(f"<tr>{row}</tr>")
            is_header_row = False
            continue
        elif in_table:
            html_lines.append("</table>")
            in_table = False

        # List
        if stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_inline_md(stripped[2:])}</li>")
            continue
        elif in_list and stripped:
            html_lines.append("</ul>")
            in_list = False

        # Empty line
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue

        # Paragraph
        html_lines.append(f"<p>{_inline_md(stripped)}</p>")

    if in_table:
        html_lines.append("</table>")
    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def _inline_md(text: str) -> str:
    """Convert inline markdown (bold, italic, code) to HTML."""
    import re
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text
