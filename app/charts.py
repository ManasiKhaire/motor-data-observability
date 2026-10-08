"""Altair charts in one dark visual system (Altair ships with Streamlit, so no extra install).

Colour rules:
  - each source keeps the same hue everywhere (validated categorical palette, fixed order)
  - status colours (good / warning / serious / critical) only ever mean health
  - magnitude uses one hue, dark -> bright
"""
import altair as alt
import pandas as pd

SURFACE = "#161b22"
INK, INK_2, MUTED = "#e6edf3", "#c3c2b7", "#8b949e"
GRID, AXIS = "#21262d", "#30363d"
SOURCES = ["telematics", "policy", "claims", "garage_bills", "customer_kyc"]
SOURCE_COLOURS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
SEVERITY_COLOURS = [STATUS["critical"], STATUS["serious"], STATUS["warning"], "#6e7681"]
HEAT = ["#1f2a3a", "#184f95", "#2a78d6", "#5598e7", "#9ec5f4"]  # one hue, dark -> bright

SOURCE_SCALE = alt.Scale(domain=SOURCES, range=SOURCE_COLOURS)


def _theme():
    return {"config": {
        "background": "transparent",
        "view": {"stroke": None},
        "font": "system-ui, -apple-system, Segoe UI, sans-serif",
        "axis": {"labelColor": MUTED, "titleColor": MUTED, "gridColor": GRID, "domainColor": AXIS,
                 "tickColor": AXIS, "labelFontSize": 11, "titleFontSize": 11, "titleFontWeight": 400,
                 "gridWidth": 0.6},
        "legend": {"labelColor": INK_2, "titleColor": MUTED, "labelFontSize": 11, "titleFontSize": 11,
                   "orient": "top", "symbolType": "circle", "titleFontWeight": 400},
        "title": {"color": INK, "fontSize": 13, "fontWeight": 600, "anchor": "start"},
    }}


alt.themes.register("obs_dark", _theme)
alt.themes.enable("obs_dark")


def _time_x(title=None):
    return alt.X("time:T", title=title, axis=alt.Axis(format="%H:%M:%S", labelAngle=0, tickCount=6))


def throughput(data: pd.DataFrame):
    """Rows per minute per source: lines with a hover crosshair."""
    hover = alt.selection_point(fields=["time"], nearest=True, on="pointerover", empty=False)
    base = alt.Chart(data).encode(_time_x(), color=alt.Color("source:N", scale=SOURCE_SCALE, title=None))
    lines = base.mark_line(strokeWidth=2, interpolate="monotone").encode(
        y=alt.Y("rows_per_min:Q", title="rows / min"))
    points = base.mark_circle(size=60, stroke=SURFACE, strokeWidth=2).encode(
        y="rows_per_min:Q", opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[alt.Tooltip("time:T", format="%H:%M:%S"), "source:N",
                 alt.Tooltip("rows_per_min:Q", title="rows/min", format=".0f")]).add_params(hover)
    rule = alt.Chart(data).mark_rule(color=AXIS).encode(x="time:T").transform_filter(hover)
    return (lines + rule + points).properties(height=220)


def quality(data: pd.DataFrame):
    """Share of rows passing all checks; dashed line at the 95% target."""
    area = alt.Chart(data).mark_area(
        line={"color": "#3987e5", "strokeWidth": 2}, interpolate="monotone",
        color=alt.Gradient(gradient="linear", x1=1, x2=1, y1=1, y2=0,
                           stops=[alt.GradientStop(color="rgba(57,135,229,0.02)", offset=0),
                                  alt.GradientStop(color="rgba(57,135,229,0.35)", offset=1)])
    ).encode(_time_x(), y=alt.Y("pass_rate:Q", title="% rows passing", scale=alt.Scale(domain=[0, 100])),
             tooltip=[alt.Tooltip("time:T", format="%H:%M:%S"), alt.Tooltip("pass_rate:Q", title="% passing"),
                      alt.Tooltip("rows:Q", title="rows"), alt.Tooltip("good:Q", title="passed")])
    target = alt.Chart(pd.DataFrame({"y": [95]})).mark_rule(color=STATUS["warning"], strokeDash=[4, 4]).encode(y="y:Q")
    return (area + target).properties(height=220)


def alerts_by_severity(data: pd.DataFrame):
    return alt.Chart(data).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3, stroke=SURFACE, strokeWidth=1,
                                    size=14).encode(
        _time_x(), y=alt.Y("alerts:Q", title="new alerts", axis=alt.Axis(tickMinStep=1)),
        color=alt.Color("severity:N", scale=alt.Scale(domain=SEVERITY_ORDER, range=SEVERITY_COLOURS), title=None),
        order=alt.Order("severity:N"),
        tooltip=[alt.Tooltip("time:T", format="%H:%M"), "severity:N", "alerts:Q"]).properties(height=200)


def failure_heatmap(data: pd.DataFrame):
    """Sources x checks; brighter = more failures. Every cell is labelled."""
    checks = ["schema", "nulls", "range", "format", "allowed_values", "rule", "duplicates",
              "referential", "reconciliation", "freshness", "pii"]
    grid = pd.MultiIndex.from_product([SOURCES, checks], names=["source", "check_name"]).to_frame(index=False)
    full = grid.merge(data.groupby(["source", "check_name"], as_index=False)["rows"].sum(),
                      how="left", on=["source", "check_name"]).fillna({"rows": 0})
    base = alt.Chart(full).encode(
        x=alt.X("check_name:N", sort=checks, title=None, axis=alt.Axis(labelAngle=-30, domain=False, ticks=False)),
        y=alt.Y("source:N", sort=SOURCES, title=None, axis=alt.Axis(domain=False, ticks=False)))
    cells = base.mark_rect(cornerRadius=3, stroke=SURFACE, strokeWidth=2).encode(
        color=alt.condition("datum.rows == 0", alt.value("#1c2129"),
                            alt.Color("rows:Q", scale=alt.Scale(range=HEAT[1:], type="sqrt"), legend=None)),
        tooltip=["source:N", alt.Tooltip("check_name:N", title="check"), alt.Tooltip("rows:Q", title="failures")])
    text = base.mark_text(fontSize=11).encode(
        text=alt.condition("datum.rows > 0", alt.Text("rows:Q", format=".0f"), alt.value("·")),
        color=alt.condition("datum.rows > 0", alt.value(INK), alt.value("#30363d")))
    return (cells + text).properties(height=210)


def freshness(data: pd.DataFrame):
    """How long each source has been silent, as % of its limit. 100% = alert."""
    colour = alt.Scale(domain=["Fresh", "Late", "Stale"], range=[STATUS["good"], STATUS["warning"], STATUS["critical"]])
    bars = alt.Chart(data).mark_bar(cornerRadiusEnd=4, height=14).encode(
        x=alt.X("bar_pct:Q", title="silence as % of alert limit", scale=alt.Scale(domain=[0, 200])),
        y=alt.Y("source:N", sort=SOURCES, title=None),
        color=alt.Color("state:N", scale=colour, title=None),
        tooltip=["source:N", alt.Tooltip("silent_s:Q", title="silent (s)", format=".0f"),
                 alt.Tooltip("limit_s:Q", title="limit (s)"), "state:N"])
    labels = alt.Chart(data).mark_text(align="left", dx=6, color=INK_2, fontSize=11).encode(
        x="bar_pct:Q", y=alt.Y("source:N", sort=SOURCES), text="label:N")
    limit = alt.Chart(pd.DataFrame({"x": [100]})).mark_rule(color=STATUS["critical"], strokeDash=[3, 3]).encode(x="x:Q")
    return (bars + labels + limit).properties(height=190)


def incident_timeline(data: pd.DataFrame):
    colour = alt.Scale(domain=["Open", "Awaiting approval", "Resolved"],
                       range=[STATUS["critical"], STATUS["warning"], STATUS["good"]])
    bars = alt.Chart(data).mark_bar(cornerRadius=4, height=16).encode(
        x=alt.X("start:T", title=None, axis=alt.Axis(format="%H:%M:%S", labelAngle=0, tickCount=6)), x2="end:T",
        y=alt.Y("incident_id:N", title=None, sort=alt.EncodingSortField("start", order="descending")),
        color=alt.Color("state:N", scale=colour, title=None),
        tooltip=["incident_id:N", "source:N", "severity:N", "category:N", "status:N",
                 alt.Tooltip("minutes:Q", title="duration (min)"), "title:N"])
    text = alt.Chart(data).mark_text(align="left", dx=6, color=INK_2, fontSize=11).encode(
        x="end:T", y=alt.Y("incident_id:N", sort=alt.EncodingSortField("start", order="descending")),
        text="source:N")
    return (bars + text).properties(height=max(120, 34 * len(data)))


def sla(data: pd.DataFrame):
    colour = alt.Scale(domain=["OK", "At risk", "Blocked", "Breached"],
                       range=[STATUS["good"], STATUS["warning"], STATUS["serious"], STATUS["critical"]])
    bars = alt.Chart(data).mark_bar(cornerRadiusEnd=4, height=16).encode(
        x=alt.X("pct_left:Q", title="SLA time remaining (%)", scale=alt.Scale(domain=[0, 100])),
        y=alt.Y("product:N", title=None, sort="x"),
        color=alt.Color("status:N", scale=colour, title=None),
        tooltip=["product:N", "owner:N", "status:N", alt.Tooltip("minutes_left:Q", title="minutes left")])
    labels = alt.Chart(data).mark_text(align="left", dx=6, color=INK_2, fontSize=11).encode(
        x="pct_left:Q", y=alt.Y("product:N", sort="x"), text="label:N")
    return (bars + labels).properties(height=200)


def quarantine_reasons(data: pd.DataFrame):
    bars = alt.Chart(data).mark_bar(cornerRadiusEnd=4, height=14).encode(
        x=alt.X("rows:Q", title="rows stopped"), y=alt.Y("reason:N", sort="-x", title=None),
        color=alt.Color("source:N", scale=SOURCE_SCALE, title=None),
        tooltip=["source:N", "check_name:N", "rows:Q"])
    labels = alt.Chart(data).mark_text(align="left", dx=6, color=INK_2, fontSize=11).encode(
        x="rows:Q", y=alt.Y("reason:N", sort="-x"), text="rows:Q")
    return (bars + labels).properties(height=max(140, 26 * len(data)))


def sparkline(values, colour="#3987e5"):
    d = pd.DataFrame({"i": range(len(values)), "v": values})
    return alt.Chart(d).mark_area(
        line={"color": colour, "strokeWidth": 1.5}, interpolate="monotone",
        color=alt.Gradient(gradient="linear", x1=1, x2=1, y1=1, y2=0,
                           stops=[alt.GradientStop(color="rgba(57,135,229,0)", offset=0),
                                  alt.GradientStop(color="rgba(57,135,229,0.25)", offset=1)])
    ).encode(x=alt.X("i:Q", axis=None), y=alt.Y("v:Q", axis=None, scale=alt.Scale(zero=False))
             ).properties(height=46)
