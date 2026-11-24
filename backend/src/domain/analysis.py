import json
from datetime import date
from math import isfinite
from pathlib import Path
from collections import Counter, defaultdict
from math import comb

_SPEC = json.loads(Path(__file__).with_name("spec.json").read_text(encoding="utf-8"))


def _validate_fields(values, fields, *, defaults=False):
    result = {}
    for field in fields:
        key = field["key"]
        fallback = field.get("default")
        value = values.get(key, fallback)
        if value is None or (isinstance(value, str) and not value.strip()):
            if field.get("required", True) is False and fallback is not None:
                value = fallback
            elif field.get("required", True) is False:
                continue
        kind = field["type"]
        if kind == "number" or kind == "integer":
            valid = type(value) in (int, float) and isfinite(value) and field["min"] <= value <= field["max"]
            valid = valid and (kind != "integer" or int(value) == value)
            if valid:
                value = int(value) if kind == "integer" else float(value)
        elif kind == "choice":
            valid = value in field["choices"]
        else:
            valid = isinstance(value, str) and 0 < len(value.strip()) <= 300
            if valid and kind == "date":
                try:
                    valid = date.fromisoformat(value).isoformat() == value
                except ValueError:
                    valid = False
            if valid:
                value = value.strip()
        if not valid:
            raise ValueError(f"「{field['label']}」格式或范围无效")
        result[key] = value
    return result


def validate(row):
    _validate_fields(row, _SPEC["fields"])


def _inputs(rows, parameters, identity="ticket"):
    clean = [_validate_fields(row, _SPEC["fields"]) for row in rows]
    if len({row[identity] for row in clean}) != len(clean):
        raise ValueError(f"存在重复的{next(field['label'] for field in _SPEC['fields'] if field['key'] == identity)}，请检查单据")
    params = _validate_fields(parameters, _SPEC["parameters"], defaults=True)
    return sorted(clean, key=lambda row: (row.get("day", ""), row[identity])), params


def _shown(value, places=2):
    return "—" if value is None else round(float(value), places)


def _output(headline, summary, columns, rows, metrics, chart_name, chart_unit, chart_data):
    return {
        "headline": headline,
        "summary": summary,
        "columns": [{"key": key, "label": label} for key, label in columns],
        "rows": rows,
        "metrics": [{"label": label, "value": value, "unit": unit} for label, value, unit in metrics],
        "chart": {
            "label": chart_name, "unit": chart_unit,
            "points": [{"label": str(label), "value": value} for label, value in chart_data],
        },
    }


FIBER = ("无", "轻", "重")
REDO = ("无需", "仅一次", "两次及以上")
COLUMNS = (("scanner", "扫描台"), ("n", "交接样本"), ("gamma", "飞白与返扫 γ"),
           ("concordant", "同向单对"), ("discordant", "反向单对"), ("tied", "并列单对"),
           ("tied_pct", "等级并列（%）"), ("decision", "复核说明"))
HEADLINE = "纸纹飞白与返扫负担等级关联"
SUMMARY = "同一台的同向对和反向对才参与比较。等级并列比例提示覆盖范围，关联强度用于安排原画复核。"


def _agreement(group):
    cells = Counter((FIBER.index(row["fiber"]), REDO.index(row["redo"])) for row in group)
    together = opposite = 0
    for (left, y), n in cells.items():
        for (right, other), m in cells.items():
            if left < right and y < other:
                together += n * m
            if left < right and y > other:
                opposite += n * m
    comparable = together + opposite
    pairs = comb(len(group), 2)
    tied = pairs - comparable
    gamma = (together - opposite) / comparable if comparable else None
    return together, opposite, tied, pairs, gamma


def scanner_metrics(scanner, group, minimum):
    """Summarise one scanner sample as comparable (concordant/discordant) and tied pairs."""
    n = len(group)
    if n < minimum:
        return {"scanner": scanner, "n": n, "short": True, "concordant": None, "discordant": None,
                "tied": None, "pairs": None, "gamma": None, "tied_pct": None, "decision": "补齐交接样本"}
    together, opposite, tied, pairs, gamma = _agreement(group)
    decision = "等级无可比较对" if gamma is None else (
        "抽查飞白重与返扫多的原画" if gamma > 0.5 else "与曝光工位共同核对")
    return {"scanner": scanner, "n": n, "short": False, "concordant": together, "discordant": opposite,
            "tied": tied, "pairs": pairs, "gamma": gamma,
            "tied_pct": 100 * tied / pairs if pairs else 0.0, "decision": decision}


def _row(metric):
    return {"scanner": metric["scanner"], "n": metric["n"],
            "gamma": _shown(metric["gamma"], 4), "concordant": metric["concordant"],
            "discordant": metric["discordant"], "tied": metric["tied"],
            "tied_pct": _shown(metric["tied_pct"]), "decision": metric["decision"]}


def render_result(scanners):
    """Render ordered per-scanner metrics into the result payload shared by runs and the workbench."""
    ordered = sorted(scanners, key=lambda item: item["scanner"])
    detail = [_row(item) for item in ordered]
    eligible = [item for item in ordered if not item["short"] and item["gamma"] is not None]
    same = sum(item["concordant"] for item in eligible)
    opposite = sum(item["discordant"] for item in eligible)
    return _output(HEADLINE, SUMMARY, COLUMNS, detail,
                   [("可估计扫描台", len(eligible), "台"), ("同向单对", same, "对"), ("反向单对", opposite, "对")],
                   "各台飞白与返扫 γ", "", [(item["scanner"], _shown(item["gamma"], 4)) for item in eligible])


def group_by_scanner(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["scanner"]].append(row)
    return groups


def analyze(rows, parameters):
    rows, options = _inputs(rows, parameters)
    groups = group_by_scanner(rows)
    minimum = options["minimum"]
    if not any(len(group) >= minimum for group in groups.values()):
        raise ValueError("无扫描台达到最少记录数")
    return render_result([scanner_metrics(scanner, group, minimum) for scanner, group in sorted(groups.items())])


def seed():
    tables = (
        ("东列 A", ((12, 3, 0), (3, 9, 2), (0, 3, 8))),
        ("西列 B", ((4, 3, 5), (3, 6, 5), (2, 5, 5))),
        ("转拍 C", ((0, 0, 0), (1, 6, 2), (0, 0, 0))),
        ("夜线 D", ((2, 0, 1), (0, 1, 0), (0, 0, 0))),
    )
    rows = []
    for scanner, counts in tables:
        for fiber, line in enumerate(counts):
            for redo, count in enumerate(line):
                for _ in range(count):
                    rows.append({"ticket": f"MQ-{len(rows)+1:03}", "scanner": scanner,
                                 "fiber": FIBER[fiber], "redo": REDO[redo]})
    return rows
