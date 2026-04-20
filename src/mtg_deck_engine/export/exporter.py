"""Export deck analysis reports to JSON, Markdown, and HTML.

All export formats include the full analysis data: static analysis,
format info, archetype detection, advanced heuristics, and optionally
probability and goldfish results.
"""

from __future__ import annotations

import json
from datetime import datetime
from html import escape as _html_escape
from pathlib import Path

from mtg_deck_engine.models import AnalysisResult


def export_json(
    result: AnalysisResult,
    advanced: dict | None = None,
    archetype: str | None = None,
    path: Path | str | None = None,
    power: object | None = None,
    castability: object | None = None,
    staples: object | None = None,
    goldfish: object | None = None,
    gauntlet: object | None = None,
) -> str:
    """Export analysis to JSON. Returns the JSON string and optionally writes to file.

    The dataclass-typed kwargs (power/castability/staples/goldfish/gauntlet) are
    converted to plain dicts here so callers don't have to. Typed as `object` to
    avoid pulling import cycles at module load.
    """
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
    if power is not None:
        data["power_level"] = _power_to_dict(power)
    if castability is not None:
        data["castability"] = _castability_to_dict(castability)
    if staples is not None:
        data["staples"] = _staples_to_dict(staples)
    if goldfish is not None:
        data["goldfish"] = _goldfish_to_dict(goldfish)
    if gauntlet is not None:
        data["gauntlet"] = _gauntlet_to_dict(gauntlet)

    output = json.dumps(data, indent=2)
    if path:
        Path(path).write_text(output, encoding="utf-8")
    return output


def _power_to_dict(p) -> dict:
    return {
        "overall": getattr(p, "overall", 0.0),
        "tier": getattr(p, "tier", ""),
        "breakdown": {
            "speed": getattr(p, "speed", 0.0),
            "interaction": getattr(p, "interaction", 0.0),
            "combo_potential": getattr(p, "combo_potential", 0.0),
            "mana_efficiency": getattr(p, "mana_efficiency", 0.0),
            "win_condition_quality": getattr(p, "win_condition_quality", 0.0),
            "card_quality": getattr(p, "card_quality", 0.0),
        },
        "reasons_up": list(getattr(p, "reasons_up", [])),
        "reasons_down": list(getattr(p, "reasons_down", [])),
    }


def _castability_to_dict(c) -> dict:
    def _card(cc):
        return {
            "name": cc.name,
            "mana_cost": cc.mana_cost,
            "cmc": cc.cmc,
            "pip_requirements": dict(cc.pip_requirements),
            "on_curve_probability": cc.on_curve_probability,
            "castable_by_turn": dict(cc.castable_by_turn),
            "bottleneck_color": cc.bottleneck_color,
            "reliable": cc.reliable,
        }
    return {
        "cards": [_card(cc) for cc in getattr(c, "cards", [])],
        "unreliable_cards": [_card(cc) for cc in getattr(c, "unreliable_cards", [])],
        "color_bottlenecks": dict(getattr(c, "color_bottlenecks", {})),
    }


def _staples_to_dict(s) -> dict:
    return {
        "format": getattr(s, "format", ""),
        "color_identity": list(getattr(s, "color_identity", [])),
        "staple_coverage": getattr(s, "staple_coverage", 0.0),
        "present_staples": list(getattr(s, "present_staples", [])),
        "missing": [
            {"name": m.name, "reason": m.reason, "priority": m.priority}
            for m in getattr(s, "missing", [])
        ],
    }


def _goldfish_to_dict(g) -> dict:
    return {
        "simulations": getattr(g, "simulations", 0),
        "max_turns": getattr(g, "max_turns", 0),
        "average_mulligans": getattr(g, "average_mulligans", 0.0),
        "mulligan_distribution": dict(getattr(g, "mulligan_distribution", {})),
        "average_kill_turn": getattr(g, "average_kill_turn", 0.0),
        "kill_rate": getattr(g, "kill_rate", 0.0),
        "kill_turn_distribution": dict(getattr(g, "kill_turn_distribution", {})),
        "commander_cast_rate": getattr(g, "commander_cast_rate", 0.0),
        "average_commander_turn": getattr(g, "average_commander_turn", 0.0),
        "average_spells_cast": getattr(g, "average_spells_cast", 0.0),
        "objective_pass_rates": dict(getattr(g, "objective_pass_rates", {})),
    }


def _gauntlet_to_dict(gt) -> dict:
    return {
        "simulations_per_matchup": getattr(gt, "simulations_per_matchup", 0),
        "total_games": getattr(gt, "total_games", 0),
        "overall_win_rate": getattr(gt, "overall_win_rate", 0.0),
        "weighted_win_rate": getattr(gt, "weighted_win_rate", 0.0),
        "best_matchup": getattr(gt, "best_matchup", ""),
        "best_win_rate": getattr(gt, "best_win_rate", 0.0),
        "worst_matchup": getattr(gt, "worst_matchup", ""),
        "worst_win_rate": getattr(gt, "worst_win_rate", 0.0),
        "speed_score": getattr(gt, "speed_score", 0.0),
        "resilience_score": getattr(gt, "resilience_score", 0.0),
        "interaction_score": getattr(gt, "interaction_score", 0.0),
        "consistency_score": getattr(gt, "consistency_score", 0.0),
        "matchups": [
            {
                "archetype": m.archetype_name,
                "wins": m.wins,
                "losses": m.losses,
                "simulations": m.simulations,
                "win_rate": m.win_rate,
                "avg_turns": m.avg_turns,
            }
            for m in getattr(gt, "matchups", [])
        ],
    }


def export_markdown(
    result: AnalysisResult,
    advanced: dict | None = None,
    archetype: str | None = None,
    path: Path | str | None = None,
    power: object | None = None,
    castability: object | None = None,
    staples: object | None = None,
    goldfish: object | None = None,
    gauntlet: object | None = None,
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
            lines.append(f"| {_md_cell(name)} | {score:.0f} |")
        lines.append("")

    # Power level breakdown
    if power is not None:
        lines.append("## Power Level")
        lines.append(
            f"**Overall:** {getattr(power, 'overall', 0):.1f} / 10  "
            f"({getattr(power, 'tier', '')})\n"
        )
        lines.append("| Axis | Score |")
        lines.append("|------|-------|")
        for axis_key, axis_label in [
            ("speed", "Speed"),
            ("interaction", "Interaction"),
            ("combo_potential", "Combo Potential"),
            ("mana_efficiency", "Mana Efficiency"),
            ("win_condition_quality", "Win Conditions"),
            ("card_quality", "Card Quality"),
        ]:
            lines.append(f"| {axis_label} | {getattr(power, axis_key, 0):.1f} |")
        lines.append("")
        for reason in getattr(power, "reasons_up", [])[:3]:
            lines.append(f"- ✅ {reason}")
        for reason in getattr(power, "reasons_down", [])[:3]:
            lines.append(f"- ⚠️ {reason}")
        if getattr(power, "reasons_up", None) or getattr(power, "reasons_down", None):
            lines.append("")

    # Castability (unreliable cards)
    if castability is not None:
        unreliable = getattr(castability, "unreliable_cards", [])
        if unreliable:
            lines.append("## Castability Warnings")
            lines.append("| Card | Cost | On-curve % | Bottleneck |")
            lines.append("|------|------|-----------:|------------|")
            for cc in unreliable[:15]:
                pct = f"{cc.on_curve_probability * 100:.0f}%"
                lines.append(
                    f"| {_md_cell(cc.name)} | {_md_cell(cc.mana_cost)} | "
                    f"{pct} | {_md_cell(cc.bottleneck_color)} |"
                )
            lines.append("")
        bottlenecks = getattr(castability, "color_bottlenecks", {})
        if bottlenecks:
            lines.append("**Color bottlenecks:** " +
                         ", ".join(f"{c} ({n})" for c, n in bottlenecks.items()))
            lines.append("")

    # Staples
    if staples is not None:
        missing = getattr(staples, "missing", [])
        if missing:
            lines.append("## Missing Staples")
            coverage = getattr(staples, "staple_coverage", 0.0)
            lines.append(f"*Coverage: {coverage * 100:.0f}%*\n")
            lines.append("| Card | Priority | Reason |")
            lines.append("|------|----------|--------|")
            for s in missing:
                lines.append(
                    f"| {_md_cell(s.name)} | {_md_cell(s.priority)} | {_md_cell(s.reason)} |"
                )
            lines.append("")

    # Goldfish
    if goldfish is not None:
        lines.append("## Goldfish Simulation")
        sims = getattr(goldfish, "simulations", 0)
        lines.append(f"*{sims} games simulated*\n")
        lines.append(f"- **Average kill turn:** {getattr(goldfish, 'average_kill_turn', 0):.2f}")
        lines.append(f"- **Kill rate:** {getattr(goldfish, 'kill_rate', 0) * 100:.1f}%")
        lines.append(f"- **Commander cast rate:** {getattr(goldfish, 'commander_cast_rate', 0) * 100:.1f}%")
        lines.append(f"- **Average mulligans:** {getattr(goldfish, 'average_mulligans', 0):.2f}")
        lines.append(f"- **Average spells cast:** {getattr(goldfish, 'average_spells_cast', 0):.2f}")
        rates = getattr(goldfish, "objective_pass_rates", {})
        if rates:
            lines.append("")
            lines.append("### Objective Pass Rates")
            lines.append("| Objective | Pass Rate |")
            lines.append("|-----------|----------:|")
            for name, rate in rates.items():
                lines.append(f"| {_md_cell(name)} | {rate * 100:.0f}% |")
        lines.append("")

    # Gauntlet
    if gauntlet is not None:
        lines.append("## Matchup Gauntlet")
        lines.append(
            f"*{getattr(gauntlet, 'total_games', 0)} games across "
            f"{len(getattr(gauntlet, 'matchups', []))} archetypes*\n"
        )
        lines.append(f"- **Overall win rate:** {getattr(gauntlet, 'overall_win_rate', 0) * 100:.1f}%")
        lines.append(f"- **Weighted win rate (meta share):** {getattr(gauntlet, 'weighted_win_rate', 0) * 100:.1f}%")
        if getattr(gauntlet, "best_matchup", ""):
            lines.append(f"- **Best matchup:** {gauntlet.best_matchup} ({gauntlet.best_win_rate * 100:.0f}%)")
        if getattr(gauntlet, "worst_matchup", ""):
            lines.append(f"- **Worst matchup:** {gauntlet.worst_matchup} ({gauntlet.worst_win_rate * 100:.0f}%)")
        lines.append("")
        matchups = getattr(gauntlet, "matchups", [])
        if matchups:
            lines.append("| Archetype | Wins | Sims | Win Rate | Avg Turns |")
            lines.append("|-----------|-----:|-----:|---------:|----------:|")
            for m in matchups:
                lines.append(
                    f"| {_md_cell(m.archetype_name)} | {m.wins} | {m.simulations} | "
                    f"{m.win_rate * 100:.0f}% | {m.avg_turns:.1f} |"
                )
            lines.append("")

    # Analyst (LLM layer) — optional summary + cut suggestions
    if advanced and advanced.get("analyst_summary"):
        lines.append("## Analyst Summary")
        lines.append(advanced["analyst_summary"])
        lines.append("")
    if advanced and advanced.get("analyst_cuts"):
        lines.append("## Analyst Cut Suggestions")
        lines.append("| Card | Reason |")
        lines.append("|------|--------|")
        for cut in advanced["analyst_cuts"]:
            lines.append(f"| {_md_cell(cut['card'])} | {_md_cell(cut['reason'])} |")
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
    power: object | None = None,
    castability: object | None = None,
    staples: object | None = None,
    goldfish: object | None = None,
    gauntlet: object | None = None,
) -> str:
    """Export analysis to a self-contained HTML page."""
    # Generate markdown first, then wrap in HTML with styling
    md_content = export_markdown(
        result, advanced, archetype,
        power=power, castability=castability, staples=staples,
        goldfish=goldfish, gauntlet=gauntlet,
    )

    # Convert basic markdown to HTML (simple conversion, no external deps)
    body_html = _md_to_html(md_content)

    # deck_name is user-controlled (pulled from decklist input) and must be escaped
    # before it lands in the <title> tag — without this, a deck saved as
    # `</title><script>...` would inject into the exported report.
    safe_title = _html_escape(result.deck_name)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title} — Deck Analysis</title>
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
            # Split on unescaped pipes only — _md_cell escapes embedded `|` as `\|`
            # so card names like "Fire // Ice" or user-entered notes don't blow up a row.
            import re as _re
            raw_cells = _re.split(r"(?<!\\)\|", stripped.strip("|"))
            cells = [c.strip().replace("\\|", "|").replace("\\\\", "\\") for c in raw_cells]
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
    from html import escape
    text = escape(text)  # Escape HTML entities first to prevent injection
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text


def _md_cell(value: object) -> str:
    """Escape a value for use inside a Markdown table cell.

    Pipes break table row parsing and newlines break the whole table; both can
    appear once user-controlled data (card names, synergy reasons, etc.) lands
    in a table row. Current exports don't route user strings through tables,
    but downstream work that adds goldfish / matchup / castability rows will.
    """
    s = str(value)
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")
