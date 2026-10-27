"""Shared design tokens and CSS for workshop validator reports.

Neutral, modern look with light and dark mode support via prefers-color-scheme.
Works offline with no external dependencies.
"""

# System font stack for maximum compatibility
FONT_SYSTEM = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, '
    '"Helvetica Neue", Arial, sans-serif'
)

# Color palette - light mode
COLORS_LIGHT = {
    "bg": "#f8f9fa",
    "surface": "#ffffff",
    "surface_alt": "#f1f3f5",
    "border": "#e3e6e9",
    "border_accent": "#d1d6db",
    "text": "#1a1f24",
    "text_secondary": "#5a6570",
    "text_muted": "#8b95a1",
    "link": "#0969da",
    "link_hover": "#0550ae",
    # Status colors
    "pass": "#0d7a4f",
    "pass_bg": "#e6f7ef",
    "fail": "#c41f2e",
    "fail_bg": "#ffebe9",
    "not_run": "#636e7b",
    "not_run_bg": "#eaedef",
    "needs_review": "#9a6700",
    "needs_review_bg": "#fff4e0",
    # Chip colors
    "chip_already_set": "#6639ba",
    "chip_already_set_bg": "#f3f0ff",
    "chip_renamed": "#0969da",
    "chip_renamed_bg": "#e7f2ff",
    # Banner colors
    "banner_verified": "#0d7a4f",
    "banner_verified_bg": "#d7f3e5",
    "banner_failed": "#c41f2e",
    "banner_failed_bg": "#ffe8e5",
    "banner_incomplete": "#9a6700",
    "banner_incomplete_bg": "#fff4e0",
}

# Color palette - dark mode
COLORS_DARK = {
    "bg": "#0d1117",
    "surface": "#161b22",
    "surface_alt": "#1c2128",
    "border": "#30363d",
    "border_accent": "#484f58",
    "text": "#e6edf3",
    "text_secondary": "#9198a1",
    "text_muted": "#6e7681",
    "link": "#4493f8",
    "link_hover": "#79c0ff",
    # Status colors
    "pass": "#57ab5a",
    "pass_bg": "#0e3e1e",
    "fail": "#f47067",
    "fail_bg": "#4c1a19",
    "not_run": "#9198a1",
    "not_run_bg": "#21262d",
    "needs_review": "#e09b13",
    "needs_review_bg": "#3d2d00",
    # Chip colors
    "chip_already_set": "#b083f0",
    "chip_already_set_bg": "#2e1f47",
    "chip_renamed": "#4493f8",
    "chip_renamed_bg": "#0c2d6b",
    # Banner colors
    "banner_verified": "#57ab5a",
    "banner_verified_bg": "#0a3818",
    "banner_failed": "#f47067",
    "banner_failed_bg": "#3d1211",
    "banner_incomplete": "#e09b13",
    "banner_incomplete_bg": "#3d2d00",
}


def get_shared_css() -> str:
    """Return the shared CSS for all workshop validator reports."""
    return f"""
* {{ box-sizing: border-box; }}
html {{ color-scheme: light dark; }}

body {{
  margin: 0;
  color: {COLORS_LIGHT["text"]};
  background: {COLORS_LIGHT["bg"]};
  font: 16px/1.5 {FONT_SYSTEM};
}}

h1, h2, h3 {{
  font-weight: 600;
  letter-spacing: -0.015em;
  line-height: 1.25;
  margin: 0;
}}

h1 {{ font-size: 2rem; }}
h2 {{ font-size: 1.5rem; }}
h3 {{ font-size: 1.125rem; }}

a {{ color: {COLORS_LIGHT["link"]}; text-decoration: none; }}
a:hover {{ color: {COLORS_LIGHT["link_hover"]}; text-decoration: underline; }}

.wrap {{
  max-width: 960px;
  margin: 0 auto;
  padding: 0 24px;
}}

header {{
  background: {COLORS_LIGHT["surface"]};
  border-bottom: 1px solid {COLORS_LIGHT["border"]};
  padding: 32px 0;
}}

header .meta {{
  margin: 8px 0 0;
  color: {COLORS_LIGHT["text_secondary"]};
  font-size: 0.9375rem;
}}

main {{ padding: 32px 0 64px; }}

.banner {{
  margin: 0 0 24px;
  padding: 16px 20px;
  border-radius: 8px;
  border: 1px solid;
  font-weight: 500;
}}

.banner.verified {{
  background: {COLORS_LIGHT["banner_verified_bg"]};
  border-color: {COLORS_LIGHT["banner_verified"]};
  color: {COLORS_LIGHT["banner_verified"]};
}}

.banner.failed {{
  background: {COLORS_LIGHT["banner_failed_bg"]};
  border-color: {COLORS_LIGHT["banner_failed"]};
  color: {COLORS_LIGHT["banner_failed"]};
}}

.banner.incomplete {{
  background: {COLORS_LIGHT["banner_incomplete_bg"]};
  border-color: {COLORS_LIGHT["banner_incomplete"]};
  color: {COLORS_LIGHT["banner_incomplete"]};
}}

.banner .reason {{
  margin: 4px 0 0;
  font-weight: 400;
  font-size: 0.9375rem;
}}

.stats {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
  margin: 24px 0;
}}

.stat {{
  background: {COLORS_LIGHT["surface"]};
  border: 1px solid {COLORS_LIGHT["border"]};
  border-radius: 8px;
  padding: 16px;
}}

.stat-value {{
  display: block;
  font-size: 2rem;
  font-weight: 700;
  line-height: 1;
  font-variant-numeric: tabular-nums;
  margin: 0 0 4px;
}}

.stat-label {{
  display: block;
  font-size: 0.875rem;
  color: {COLORS_LIGHT["text_secondary"]};
}}

.stat.pass .stat-value {{ color: {COLORS_LIGHT["pass"]}; }}
.stat.fail .stat-value {{ color: {COLORS_LIGHT["fail"]}; }}
.stat.needs-review .stat-value {{ color: {COLORS_LIGHT["needs_review"]}; }}

.progress-bar {{
  height: 32px;
  background: {COLORS_LIGHT["surface"]};
  border: 1px solid {COLORS_LIGHT["border"]};
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  margin: 24px 0;
}}

.progress-segment {{
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  font-weight: 600;
  color: #ffffff;
  white-space: nowrap;
  padding: 0 8px;
}}

.progress-segment.pass {{ background: {COLORS_LIGHT["pass"]}; }}
.progress-segment.fail {{ background: {COLORS_LIGHT["fail"]}; }}
.progress-segment.needs-review {{ background: {COLORS_LIGHT["needs_review"]}; }}
.progress-segment.not-run {{ background: {COLORS_LIGHT["not_run"]}; }}

.card {{
  background: {COLORS_LIGHT["surface"]};
  border: 1px solid {COLORS_LIGHT["border"]};
  border-radius: 8px;
  padding: 20px;
  margin: 16px 0;
}}

.card h3 {{ margin: 0 0 12px; }}

.section {{
  margin: 32px 0;
}}

.section-header {{
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  margin: 0 0 12px;
}}

details {{ margin: 12px 0; }}

summary {{
  cursor: pointer;
  user-select: none;
  padding: 12px 16px;
  background: {COLORS_LIGHT["surface"]};
  border: 1px solid {COLORS_LIGHT["border"]};
  border-radius: 8px;
  font-weight: 500;
  display: flex;
  align-items: center;
  justify-content: space-between;
}}

summary:hover {{ background: {COLORS_LIGHT["surface_alt"]}; }}

details[open] > summary {{
  border-bottom-left-radius: 0;
  border-bottom-right-radius: 0;
}}

.details-content {{
  background: {COLORS_LIGHT["surface"]};
  border: 1px solid {COLORS_LIGHT["border"]};
  border-top: 0;
  border-bottom-left-radius: 8px;
  border-bottom-right-radius: 8px;
  padding: 16px;
}}

.step-list {{
  list-style: none;
  margin: 0;
  padding: 0;
}}

.step-item {{
  padding: 12px 0;
  border-bottom: 1px solid {COLORS_LIGHT["border"]};
  display: grid;
  grid-template-columns: 3rem 1fr auto;
  align-items: start;
  gap: 12px;
}}

.step-item:last-child {{ border-bottom: 0; }}

.step-number {{
  color: {COLORS_LIGHT["text_secondary"]};
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}}

.step-text {{ overflow-wrap: anywhere; }}

.step-reason {{
  margin: 4px 0 0;
  padding: 8px 12px;
  background: {COLORS_LIGHT["fail_bg"]};
  border-left: 3px solid {COLORS_LIGHT["fail"]};
  border-radius: 4px;
  font-size: 0.9375rem;
  color: {COLORS_LIGHT["fail"]};
  grid-column: 2 / -1;
}}

.chips {{
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 6px 0 0;
  grid-column: 2 / -1;
}}

.chip {{
  display: inline-block;
  font: 600 0.6875rem/1 {FONT_SYSTEM};
  letter-spacing: 0.02em;
  border-radius: 12px;
  padding: 4px 10px;
  white-space: nowrap;
}}

.chip.already-set {{
  background: {COLORS_LIGHT["chip_already_set_bg"]};
  color: {COLORS_LIGHT["chip_already_set"]};
}}

.chip.renamed {{
  background: {COLORS_LIGHT["chip_renamed_bg"]};
  color: {COLORS_LIGHT["chip_renamed"]};
}}

.pill {{
  display: inline-block;
  font: 700 0.6875rem/1 {FONT_SYSTEM};
  letter-spacing: 0.03em;
  text-transform: uppercase;
  border-radius: 12px;
  padding: 5px 10px;
  white-space: nowrap;
}}

.pill.pass {{
  background: {COLORS_LIGHT["pass_bg"]};
  color: {COLORS_LIGHT["pass"]};
}}

.pill.fail {{
  background: {COLORS_LIGHT["fail_bg"]};
  color: {COLORS_LIGHT["fail"]};
}}

.pill.not-run {{
  background: {COLORS_LIGHT["not_run_bg"]};
  color: {COLORS_LIGHT["not_run"]};
}}

.pill.needs-review {{
  background: {COLORS_LIGHT["needs_review_bg"]};
  color: {COLORS_LIGHT["needs_review"]};
}}

.timeline {{
  margin: 24px 0;
}}

.timeline-item {{
  padding: 12px 0 12px 32px;
  position: relative;
  border-left: 2px solid {COLORS_LIGHT["border"]};
}}

.timeline-item:last-child {{ border-left-color: transparent; }}

.timeline-item::before {{
  content: "";
  position: absolute;
  left: -5px;
  top: 16px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: {COLORS_LIGHT["link"]};
  border: 2px solid {COLORS_LIGHT["bg"]};
}}

.timeline-time {{
  font-size: 0.875rem;
  color: {COLORS_LIGHT["text_muted"]};
  margin: 0 0 2px;
}}

.timeline-action {{
  font-weight: 500;
  margin: 0;
}}

.timeline-detail {{
  font-size: 0.9375rem;
  color: {COLORS_LIGHT["text_secondary"]};
  margin: 4px 0 0;
}}

.footer {{
  margin: 32px 0 0;
  padding: 16px 0 0;
  border-top: 1px solid {COLORS_LIGHT["border"]};
  font-size: 0.875rem;
  color: {COLORS_LIGHT["text_secondary"]};
}}

table {{
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
}}

th, td {{
  padding: 12px;
  text-align: left;
  border-bottom: 1px solid {COLORS_LIGHT["border"]};
}}

th {{
  background: {COLORS_LIGHT["surface_alt"]};
  font-weight: 600;
  font-size: 0.875rem;
}}

/* Dark mode */
@media (prefers-color-scheme: dark) {{
  body {{
    color: {COLORS_DARK["text"]};
    background: {COLORS_DARK["bg"]};
  }}

  a {{ color: {COLORS_DARK["link"]}; }}
  a:hover {{ color: {COLORS_DARK["link_hover"]}; }}

  header {{
    background: {COLORS_DARK["surface"]};
    border-bottom-color: {COLORS_DARK["border"]};
  }}

  header .meta {{ color: {COLORS_DARK["text_secondary"]}; }}

  .banner.verified {{
    background: {COLORS_DARK["banner_verified_bg"]};
    border-color: {COLORS_DARK["banner_verified"]};
    color: {COLORS_DARK["banner_verified"]};
  }}

  .banner.failed {{
    background: {COLORS_DARK["banner_failed_bg"]};
    border-color: {COLORS_DARK["banner_failed"]};
    color: {COLORS_DARK["banner_failed"]};
  }}

  .banner.incomplete {{
    background: {COLORS_DARK["banner_incomplete_bg"]};
    border-color: {COLORS_DARK["banner_incomplete"]};
    color: {COLORS_DARK["banner_incomplete"]};
  }}

  .stat {{
    background: {COLORS_DARK["surface"]};
    border-color: {COLORS_DARK["border"]};
  }}

  .stat-label {{ color: {COLORS_DARK["text_secondary"]}; }}

  .stat.pass .stat-value {{ color: {COLORS_DARK["pass"]}; }}
  .stat.fail .stat-value {{ color: {COLORS_DARK["fail"]}; }}
  .stat.needs-review .stat-value {{ color: {COLORS_DARK["needs_review"]}; }}

  .progress-bar {{
    background: {COLORS_DARK["surface"]};
    border-color: {COLORS_DARK["border"]};
  }}

  .progress-segment.pass {{ background: {COLORS_DARK["pass"]}; }}
  .progress-segment.fail {{ background: {COLORS_DARK["fail"]}; }}
  .progress-segment.needs-review {{ background: {COLORS_DARK["needs_review"]}; }}
  .progress-segment.not-run {{ background: {COLORS_DARK["not_run"]}; }}

  .card {{
    background: {COLORS_DARK["surface"]};
    border-color: {COLORS_DARK["border"]};
  }}

  summary {{
    background: {COLORS_DARK["surface"]};
    border-color: {COLORS_DARK["border"]};
  }}

  summary:hover {{ background: {COLORS_DARK["surface_alt"]}; }}

  .details-content {{
    background: {COLORS_DARK["surface"]};
    border-color: {COLORS_DARK["border"]};
  }}

  .step-item {{ border-bottom-color: {COLORS_DARK["border"]}; }}

  .step-number {{ color: {COLORS_DARK["text_secondary"]}; }}

  .step-reason {{
    background: {COLORS_DARK["fail_bg"]};
    border-left-color: {COLORS_DARK["fail"]};
    color: {COLORS_DARK["fail"]};
  }}

  .chip.already-set {{
    background: {COLORS_DARK["chip_already_set_bg"]};
    color: {COLORS_DARK["chip_already_set"]};
  }}

  .chip.renamed {{
    background: {COLORS_DARK["chip_renamed_bg"]};
    color: {COLORS_DARK["chip_renamed"]};
  }}

  .pill.pass {{
    background: {COLORS_DARK["pass_bg"]};
    color: {COLORS_DARK["pass"]};
  }}

  .pill.fail {{
    background: {COLORS_DARK["fail_bg"]};
    color: {COLORS_DARK["fail"]};
  }}

  .pill.not-run {{
    background: {COLORS_DARK["not_run_bg"]};
    color: {COLORS_DARK["not_run"]};
  }}

  .pill.needs-review {{
    background: {COLORS_DARK["needs_review_bg"]};
    color: {COLORS_DARK["needs_review"]};
  }}

  .timeline-item {{ border-left-color: {COLORS_DARK["border"]}; }}

  .timeline-item::before {{
    background: {COLORS_DARK["link"]};
    border-color: {COLORS_DARK["bg"]};
  }}

  .timeline-time {{ color: {COLORS_DARK["text_muted"]}; }}
  .timeline-detail {{ color: {COLORS_DARK["text_secondary"]}; }}

  .footer {{
    border-top-color: {COLORS_DARK["border"]};
    color: {COLORS_DARK["text_secondary"]};
  }}

  th, td {{ border-bottom-color: {COLORS_DARK["border"]}; }}
  th {{ background: {COLORS_DARK["surface_alt"]}; }}
}}

/* Print styles */
@media print {{
  body {{ background: white; color: black; }}
  header {{ border-bottom: 2px solid #000; }}
  .card, details, summary {{ border-color: #999; }}
  a {{ color: #000; text-decoration: underline; }}
  details {{ page-break-inside: avoid; }}
  .step-item {{ page-break-inside: avoid; }}
}}

/* Accessibility */
:focus-visible {{
  outline: 2px solid {COLORS_LIGHT["link"]};
  outline-offset: 2px;
}}

@media (prefers-color-scheme: dark) {{
  :focus-visible {{
    outline-color: {COLORS_DARK["link"]};
  }}
}}

/* Small screens */
@media (max-width: 640px) {{
  .wrap {{ padding: 0 16px; }}
  header {{ padding: 24px 0; }}
  h1 {{ font-size: 1.5rem; }}
  .stats {{ grid-template-columns: repeat(2, 1fr); }}
  .step-item {{
    grid-template-columns: 2.5rem 1fr;
  }}
  .step-item .pill {{
    grid-column: 2;
    justify-self: start;
    margin-top: 4px;
  }}
}}
"""
