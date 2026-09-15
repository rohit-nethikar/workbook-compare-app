"""Tests for workbook_compare_engine.py: parsing, diffing, and impact
classification. Runs the handoff brief's own "how to verify" checklist
(identity compare = 0 changes; rename/removal, datatype, calc, parameter,
and connection mutation checks) as pytest functions.

The fixture workbook is built with xml.etree.ElementTree and re-serialized
via ET.tostring for every mutated variant - never via raw string/byte
replacement - because ElementTree decodes XML entities on parse, and a
byte-level replace would diverge from what parse_workbook() actually sees.
"""
import copy
import xml.etree.ElementTree as ET

import pytest

from workbook_compare_engine import (
    _fmt,
    _normalize_column_ref,
    _referenced_fields,
    build_plain_language_summary,
    classify_custom_view_impact,
    classify_view_impact,
    compute_diff,
    parse_workbook,
)


def _build_root():
    """A minimal but structurally realistic <workbook> covering: a
    Parameters datasource, a federated datasource with one live postgres
    connection plus a hyper/extract connection, a dimension field, a
    calculated measure field, a worksheet with a categorical filter, and a
    dashboard with one zone."""
    workbook = ET.Element("workbook")
    datasources = ET.SubElement(workbook, "datasources")

    params_ds = ET.SubElement(datasources, "datasource", {"name": "Parameters", "caption": "Parameters"})
    param_col = ET.SubElement(params_ds, "column", {"name": "[Parameter 1]", "caption": "Top N", "datatype": "integer"})
    ET.SubElement(param_col, "calculation", {"class": "tableau", "formula": "10"})

    orders_ds = ET.SubElement(datasources, "datasource", {"name": "federated.0abc123", "caption": "Orders"})
    fed_conn = ET.SubElement(orders_ds, "connection", {"class": "federated"})
    named_conns = ET.SubElement(fed_conn, "named-connections")
    named_conn = ET.SubElement(named_conns, "named-connection", {"name": "postgres.0xyz", "caption": "Orders DB"})
    ET.SubElement(named_conn, "connection", {
        "class": "postgres", "server": "db.example.com", "port": "5432",
        "dbname": "orders_db", "username": "svc_user",
    })
    ET.SubElement(orders_ds, "connection", {"class": "hyper", "dbname": "extract.hyper"})

    ET.SubElement(orders_ds, "column", {
        "name": "[Region]", "caption": "Region", "datatype": "string", "role": "dimension",
    })
    calc_col = ET.SubElement(orders_ds, "column", {
        "name": "[Calculation_1]", "caption": "Total Sales", "datatype": "real",
        "role": "measure", "aggregation": "Sum",
    })
    ET.SubElement(calc_col, "calculation", {"class": "tableau", "formula": "SUM([Sales])"})

    worksheets = ET.SubElement(workbook, "worksheets")
    ws = ET.SubElement(worksheets, "worksheet", {"name": "Sales Summary"})
    table = ET.SubElement(ws, "table")
    view = ET.SubElement(table, "view")
    filt = ET.SubElement(view, "filter", {"class": "categorical", "column": "[federated.0abc123].[Region]"})
    ET.SubElement(filt, "groupfilter", {"function": "member", "level": "[Region]", "member": "East"})

    dashboards = ET.SubElement(workbook, "dashboards")
    dash = ET.SubElement(dashboards, "dashboard", {"name": "Overview"})
    zones = ET.SubElement(dash, "zones")
    ET.SubElement(zones, "zone", {
        "id": "1", "type-v2": "worksheet", "name": "Sales Summary", "x": "0", "y": "0", "w": "800", "h": "600",
    })

    return workbook


def _root_with_group():
    """base_root plus a Group column (<groupfilter> directly under
    <column>) on the Orders datasource."""
    root = _build_root()
    orders_ds = root.find(".//datasource[@caption='Orders']")
    group_col = ET.SubElement(orders_ds, "column", {
        "name": "[Region Groups]", "caption": "Region Groups", "datatype": "string", "role": "dimension",
    })
    gf = ET.SubElement(group_col, "groupfilter", {"function": "union", "name": "[Region Groups]"})
    ET.SubElement(gf, "groupfilter", {"function": "member", "level": "[Region]", "member": "East"})
    ET.SubElement(gf, "groupfilter", {"function": "member", "level": "[Region]", "member": "West"})
    return root


def _root_with_action():
    """base_root plus one top-level filter action."""
    root = _build_root()
    actions = ET.SubElement(root, "actions")
    action = ET.SubElement(actions, "action", {"name": "Filter Action 1"})
    ET.SubElement(action, "filter")
    return root


def _root_with_windows():
    """base_root plus a <windows> section where every sheet has a visible tab."""
    root = _build_root()
    windows = ET.SubElement(root, "windows")
    ET.SubElement(windows, "window", {"class": "worksheet", "name": "Sales Summary"})
    ET.SubElement(windows, "window", {"class": "dashboard", "name": "Overview"})
    return root


def _bytes(root) -> bytes:
    return ET.tostring(root, encoding="utf-8")


def _parse(root):
    return parse_workbook(_bytes(root))


@pytest.fixture
def base_root():
    return _build_root()


def test_identity_diff_is_empty(base_root):
    published = _parse(base_root)
    candidate = _parse(copy.deepcopy(base_root))

    diff = compute_diff(published, candidate)
    assert diff.total == 0
    assert diff.groups == []

    impact = classify_custom_view_impact(published, candidate, diff)
    assert impact["classification"] == "no_change"
    assert impact["risk_level"] == "None"
    assert impact["findings"] == []


def test_federated_hash_prefix_normalizes_away(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    filt = candidate_root.find(".//worksheet/table/view/filter")
    filt.set("column", "[federated.9xyz789].[Region]")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    assert diff.total == 0
    assert not any(g.key == "filters" for g in diff.groups)


def test_hyper_extract_connection_mutation_is_ignored(base_root):
    """Negative test: changing the extract cache connection must never
    surface as a diff - it's the false-change trap the handoff called out."""
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    hyper_conn = candidate_root.find(".//datasource[@caption='Orders']/connection[@class='hyper']")
    hyper_conn.set("dbname", "extract_v2.hyper")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    assert diff.total == 0
    assert not any(g.key == "conns" for g in diff.groups)


def test_worksheet_rename_is_high_and_directly_impacted(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    ws = candidate_root.find(".//worksheet[@name='Sales Summary']")
    ws.set("name", "Sales Summary v2")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    views_group = next(g for g in diff.groups if g.key == "views")
    ops = {(i.op, i.label) for i in views_group.items}
    assert ("remove", "View: Sales Summary") in ops
    assert ("add", "View: Sales Summary v2") in ops

    impact = classify_custom_view_impact(published, candidate, diff)
    assert impact["risk_level"] == "High"
    assert impact["classification"] == "directly_impacted"
    assert any(
        f["severity"] == "High" and "removed or renamed" in f["title"]
        for f in impact["findings"]
    )


def test_field_datatype_change_is_medium_and_potentially_impacted(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    region_col = candidate_root.find(".//column[@name='[Region]']")
    region_col.set("datatype", "integer")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    fields_group = next(g for g in diff.groups if g.key == "fields")
    change_item = next(i for i in fields_group.items if i.op == "change")
    assert change_item.label == "Field: [Region]"
    datatype_part = next(p for p in change_item.parts if p["attr"] == "datatype")
    assert datatype_part == {"attr": "datatype", "before": "string", "after": "integer"}

    impact = classify_custom_view_impact(published, candidate, diff)
    assert impact["risk_level"] == "Medium"
    assert impact["classification"] == "potentially_impacted"


def test_calc_formula_edit_shows_exact_before_after(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    calc = candidate_root.find(".//column[@name='[Calculation_1]']/calculation")
    calc.set("formula", "SUM([Sales]) * 1.1")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    calcs_group = next(g for g in diff.groups if g.key == "calcs")
    change_item = next(i for i in calcs_group.items if i.op == "change")
    assert change_item.before == "SUM([Sales])"
    assert change_item.after == "SUM([Sales]) * 1.1"


def test_parameter_value_change_flags_params_group(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    param_calc = candidate_root.find(".//datasource[@name='Parameters']/column/calculation")
    param_calc.set("formula", "20")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    params_group = next(g for g in diff.groups if g.key == "params")
    change_item = next(i for i in params_group.items if i.op == "change")
    value_part = next(p for p in change_item.parts if p["attr"] == "value")
    assert value_part == {"attr": "value", "before": "10", "after": "20"}

    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(f["category"] == "Parameters" and f["severity"] == "Medium" for f in impact["findings"])


def test_live_connection_change_flags_conns_group(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    conn = candidate_root.find(".//named-connection/connection[@class='postgres']")
    conn.set("server", "db2.example.com")
    conn.set("dbname", "orders_db_v2")
    conn.set("username", "svc_user2")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    conns_group = next(g for g in diff.groups if g.key == "conns")
    change_item = next(i for i in conns_group.items if i.op == "change")
    parts_by_attr = {p["attr"]: p for p in change_item.parts}
    assert parts_by_attr["server"] == {"attr": "server", "before": "db.example.com", "after": "db2.example.com"}
    assert parts_by_attr["dbname"] == {"attr": "dbname", "before": "orders_db", "after": "orders_db_v2"}
    assert parts_by_attr["username"] == {"attr": "username", "before": "svc_user", "after": "svc_user2"}


def test_classify_view_impact_scopes_rename_to_its_own_view(base_root):
    """A rename of one view must not mark every other custom view as
    directly_impacted - only the view that was actually removed/renamed."""
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    ws = candidate_root.find(".//worksheet[@name='Sales Summary']")
    ws.set("name", "Sales Summary v2")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)
    assert impact["classification"] == "directly_impacted"

    renamed_view = classify_view_impact("Sales Summary", published, candidate, diff, impact["classification"])
    assert renamed_view == "directly_impacted"

    unrelated_view = classify_view_impact("Overview", published, candidate, diff, impact["classification"])
    assert unrelated_view == "potentially_impacted"


def test_parameters_datasource_excluded_from_fields(base_root):
    struct = _parse(base_root)
    assert "[Parameter 1]" not in struct.fields
    assert "[Parameter 1]" in struct.parameters
    assert struct.parameters["[Parameter 1]"] == {"caption": "Top N", "datatype": "integer", "value": "10"}


@pytest.mark.parametrize("ref,expected", [
    ("[federated.abc123].[Sales]", "[Sales]"),
    ("[federated.XYZ_789].[Region]", "[Region]"),
    ("[Region]", "[Region]"),
    ("", ""),
    (None, None),
])
def test_normalize_column_ref(ref, expected):
    assert _normalize_column_ref(ref) == expected


@pytest.mark.parametrize("value,expected", [
    (None, "(none)"),
    (True, "yes"),
    (False, "no"),
    ({"a": 1, "b": 2}, "a=1, b=2"),
    ([], "(none)"),
    (["x", "y"], "x, y"),
    ([{"encoding": "color", "field": "[Region]"}], "color: [Region]"),
    ([{"field": "[Region]", "direction": "ASC"}], "[Region] ASC"),
    (5, "5"),
])
def test_fmt(value, expected):
    assert _fmt(value) == expected


@pytest.mark.parametrize("formula,expected", [
    (None, set()),
    ("", set()),
    ("10", set()),
    ("SUM([Sales])", {"[Sales]"}),
    ("SUM([Sales]) + LEN([Region])", {"[Sales]", "[Region]"}),
])
def test_referenced_fields(formula, expected):
    assert _referenced_fields(formula) == expected


def test_field_role_change_flags_medium_finding(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    region_col = candidate_root.find(".//column[@name='[Region]']")
    region_col.set("role", "measure")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Fields" and f["severity"] == "Medium" and "role" in f["title"]
        for f in impact["findings"]
    )


def test_filter_card_removed_flags_finding(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    view = candidate_root.find(".//worksheet[@name='Sales Summary']/table/view")
    filt = view.find("./filter")
    view.remove(filt)
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)

    filter_findings = [f for f in impact["findings"] if f["category"] == "Filters"]
    assert len(filter_findings) == 1
    assert filter_findings[0]["severity"] == "Medium"
    assert "removed" in filter_findings[0]["title"]


def test_new_filter_added_flags_finding(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    view = candidate_root.find(".//worksheet[@name='Sales Summary']/table/view")
    filt = ET.SubElement(view, "filter", {"class": "categorical", "column": "[federated.0abc123].[Calculation_1]"})
    ET.SubElement(filt, "groupfilter", {"function": "member", "level": "[Calculation_1]", "member": "100"})
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)

    assert any(
        f["category"] == "Filters" and f["severity"] == "Medium" and "New filter added" in f["title"]
        for f in impact["findings"]
    )


def test_sort_order_change_flags_low_finding(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    ws = candidate_root.find(".//worksheet[@name='Sales Summary']")
    view = ws.find("./table/view")
    ET.SubElement(view, "sort", {"column": "[federated.0abc123].[Region]", "direction": "ASC", "class": "computed"})
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)

    assert any(
        f["category"] == "Sorts" and f["severity"] == "Low" and "Sort order changed" in f["title"]
        for f in impact["findings"]
    )


def test_encoding_change_flags_medium_finding(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    ws = candidate_root.find(".//worksheet[@name='Sales Summary']")
    view = ws.find("./table/view")
    encodings = ET.SubElement(view, "encodings")
    ET.SubElement(encodings, "color", {"column": "[federated.0abc123].[Region]"})
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)

    assert any(
        f["category"] == "Encodings" and f["severity"] == "Medium" and "Shelf/encoding pills changed" in f["title"]
        for f in impact["findings"]
    )


def test_calc_referenced_fields_changed_flags_finding(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    calc = candidate_root.find(".//column[@name='[Calculation_1]']/calculation")
    calc.set("formula", "SUM([Sales]) + LEN([Region])")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)

    calc_findings = [f for f in impact["findings"] if f["category"] == "Calculations"]
    assert len(calc_findings) == 1
    assert calc_findings[0]["severity"] == "Medium"
    assert "[Region]" in calc_findings[0]["detail"]


def test_calc_formula_edit_same_fields_no_referenced_field_finding(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    calc = candidate_root.find(".//column[@name='[Calculation_1]']/calculation")
    calc.set("formula", "SUM([Sales]) * 1.1")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    calcs_group = next(g for g in diff.groups if g.key == "calcs")
    change_item = next(i for i in calcs_group.items if i.op == "change")
    assert change_item.before == "SUM([Sales])"
    assert change_item.after == "SUM([Sales]) * 1.1"

    impact = classify_custom_view_impact(published, candidate, diff)
    assert not any(f["category"] == "Calculations" for f in impact["findings"])


# --- (a) fixes: data already parsed/diffed but never surfaced to the classifier ---

def test_connection_change_flags_high_data_source_finding(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    conn = candidate_root.find(".//named-connection/connection[@class='postgres']")
    conn.set("server", "db2.example.com")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)

    assert any(
        f["category"] == "Data Sources" and f["severity"] == "High" and "connection changed" in f["title"]
        for f in impact["findings"]
    )
    assert impact["risk_level"] == "High"


def test_datasource_filter_added_flags_high_finding(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    orders_ds = candidate_root.find(".//datasource[@caption='Orders']")
    filt = ET.SubElement(orders_ds, "filter", {"class": "categorical", "column": "[Region]"})
    ET.SubElement(filt, "groupfilter", {"function": "member", "level": "[Region]", "member": "East"})
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    ds_filters_group = next(g for g in diff.groups if g.key == "ds_filters")
    assert any(i.op == "add" for i in ds_filters_group.items)

    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Data Source Filters" and f["severity"] == "High" and "added" in f["title"].lower()
        for f in impact["findings"]
    )


def test_datasource_filter_removed_flags_high_finding(base_root):
    published_root = copy.deepcopy(base_root)
    orders_ds = published_root.find(".//datasource[@caption='Orders']")
    filt = ET.SubElement(orders_ds, "filter", {"class": "categorical", "column": "[Region]"})
    ET.SubElement(filt, "groupfilter", {"function": "member", "level": "[Region]", "member": "East"})
    published = _parse(published_root)

    candidate = _parse(base_root)  # no ds-level filter

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Data Source Filters" and f["severity"] == "High" and "removed" in f["title"].lower()
        for f in impact["findings"]
    )


def test_datasource_filter_condition_changed_flags_high_finding(base_root):
    published_root = copy.deepcopy(base_root)
    orders_ds = published_root.find(".//datasource[@caption='Orders']")
    filt = ET.SubElement(orders_ds, "filter", {"class": "categorical", "column": "[Region]"})
    ET.SubElement(filt, "groupfilter", {"function": "member", "level": "[Region]", "member": "East"})
    published = _parse(published_root)

    candidate_root = copy.deepcopy(published_root)
    member = candidate_root.find(".//datasource[@caption='Orders']/filter/groupfilter")
    member.set("member", "West")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Data Source Filters" and f["severity"] == "High" and "changed" in f["title"].lower()
        for f in impact["findings"]
    )


def test_mark_type_change_flags_high_finding(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    view = candidate_root.find(".//worksheet[@name='Sales Summary']/table/view")
    ET.SubElement(view, "mark", {"class": "Bar"})
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    visuals_group = next(g for g in diff.groups if g.key == "visuals")
    assert any(any(p["attr"] == "marks" for p in i.parts) for i in visuals_group.items)

    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Marks" and f["severity"] == "High" and "Mark type changed" in f["title"]
        for f in impact["findings"]
    )


def test_parameter_caption_change_flags_medium_finding(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    param_col = candidate_root.find(".//datasource[@name='Parameters']/column")
    param_col.set("caption", "Top N Value")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Parameters" and f["severity"] == "Medium" and "caption changed" in f["title"]
        for f in impact["findings"]
    )


def test_new_parameter_added_flags_low_finding(base_root):
    published = _parse(base_root)

    candidate_root = copy.deepcopy(base_root)
    params_ds = candidate_root.find(".//datasource[@name='Parameters']")
    new_col = ET.SubElement(params_ds, "column", {"name": "[Parameter 2]", "caption": "Bottom N", "datatype": "integer"})
    ET.SubElement(new_col, "calculation", {"class": "tableau", "formula": "5"})
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Parameters" and f["severity"] == "Low" and "New parameter" in f["title"]
        for f in impact["findings"]
    )


# --- (b) fixes: previously-unparsed constructs (Groups/Sets, Actions, hidden
# sheets, quick-filter-card visibility, parameter domain) ---

def test_group_parsed_with_members():
    struct = _parse(_root_with_group())
    assert "[Region Groups]" in struct.groups
    assert struct.groups["[Region Groups]"]["members"] == ["East", "West"]
    # A Group column is still a <column> and shows up as a field too.
    assert "[Region Groups]" in struct.fields


def test_group_membership_change_flags_medium_finding():
    base = _root_with_group()
    published = _parse(base)

    candidate_root = copy.deepcopy(base)
    member = candidate_root.find(".//column[@name='[Region Groups]']//groupfilter[@member='West']")
    member.set("member", "Central")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    groups_group = next(g for g in diff.groups if g.key == "groups")
    assert any(i.op == "change" for i in groups_group.items)

    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Groups & Sets" and f["severity"] == "Medium" and "membership changed" in f["title"]
        for f in impact["findings"]
    )


def test_group_removed_flags_high_finding():
    base = _root_with_group()
    published = _parse(base)

    candidate_root = copy.deepcopy(base)
    orders_ds = candidate_root.find(".//datasource[@caption='Orders']")
    group_col = candidate_root.find(".//column[@name='[Region Groups]']")
    orders_ds.remove(group_col)
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Groups & Sets" and f["severity"] == "High" and "removed" in f["title"].lower()
        for f in impact["findings"]
    )


def test_computed_set_parsed_and_condition_change_flags_medium_finding(base_root):
    orders_ds = base_root.find(".//datasource[@caption='Orders']")
    set_col = ET.SubElement(orders_ds, "column", {
        "name": "[Top Customers]", "caption": "Top Customers", "datatype": "boolean", "role": "dimension",
    })
    ET.SubElement(set_col, "calculation", {"class": "tableau-app:set-computed", "formula": "[Sales] > 1000"})

    published = _parse(base_root)
    assert published.computed_sets["[Top Customers]"]["formula"] == "[Sales] > 1000"

    candidate_root = copy.deepcopy(base_root)
    calc = candidate_root.find(".//column[@name='[Top Customers]']/calculation")
    calc.set("formula", "[Sales] > 5000")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    set_group = next(g for g in diff.groups if g.key == "computed_sets")
    assert any(i.op == "change" for i in set_group.items)

    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Groups & Sets" and f["severity"] == "Medium" and "condition changed" in f["title"]
        for f in impact["findings"]
    )


def test_action_type_change_flags_medium_finding():
    base = _root_with_action()
    published = _parse(base)
    assert published.actions["Filter Action 1"]["type"] == "filter"

    candidate_root = copy.deepcopy(base)
    action = candidate_root.find(".//action[@name='Filter Action 1']")
    action.remove(action.find("./filter"))
    ET.SubElement(action, "highlight")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    actions_group = next(g for g in diff.groups if g.key == "actions")
    assert any(i.before == "filter" and i.after == "highlight" for i in actions_group.items)

    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Actions" and f["severity"] == "Medium" and "type changed" in f["title"]
        for f in impact["findings"]
    )


def test_action_removed_flags_medium_finding():
    base = _root_with_action()
    published = _parse(base)

    candidate_root = copy.deepcopy(base)
    actions_el = candidate_root.find(".//actions")
    actions_el.remove(candidate_root.find(".//action[@name='Filter Action 1']"))
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Actions" and f["severity"] == "Medium" and "removed" in f["title"].lower()
        for f in impact["findings"]
    )


def test_sheet_hidden_flags_high_finding():
    base = _root_with_windows()
    published = _parse(base)
    assert published.sheet_windows_known is True
    assert published.hidden_sheets == set()

    candidate_root = copy.deepcopy(base)
    windows_el = candidate_root.find(".//windows")
    windows_el.remove(candidate_root.find(".//window[@name='Sales Summary']"))
    candidate = _parse(candidate_root)
    assert candidate.hidden_sheets == {"Sales Summary"}

    diff = compute_diff(published, candidate)
    vis_group = next(g for g in diff.groups if g.key == "sheet_visibility")
    assert any(i.before == "Visible" and i.after == "Hidden" for i in vis_group.items)

    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Sheet Visibility" and f["severity"] == "High" and "now hidden" in f["title"]
        for f in impact["findings"]
    )


def test_no_windows_section_does_not_flag_hidden_sheets(base_root):
    """base_root has no <windows> element at all - sheet_windows_known must
    stay False and hidden_sheets must stay empty rather than assuming every
    worksheet is hidden just because window state wasn't captured."""
    struct = _parse(base_root)
    assert struct.sheet_windows_known is False
    assert struct.hidden_sheets == set()


def test_quick_filter_card_hidden_flags_medium_finding(base_root):
    """The <filter> condition stays intact, but its <slices> quick-filter-card
    entry disappears - distinct from 'filter card removed' (which is about
    the <filter> element itself going away)."""
    published_root = copy.deepcopy(base_root)
    view = published_root.find(".//worksheet[@name='Sales Summary']/table/view")
    slices = ET.SubElement(view, "slices")
    col = ET.SubElement(slices, "column")
    col.text = "[federated.0abc123].[Region]"
    published = _parse(published_root)
    assert published.filter_cards["Sales Summary"] == {"[Region]": True}

    candidate_root = copy.deepcopy(published_root)
    view = candidate_root.find(".//worksheet[@name='Sales Summary']/table/view")
    view.remove(view.find("./slices"))
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    card_group = next(g for g in diff.groups if g.key == "filter_cards")
    assert any(i.op == "remove" for i in card_group.items)

    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Filters" and f["severity"] == "Medium" and "Quick filter card hidden" in f["title"]
        for f in impact["findings"]
    )


def test_parameter_range_parsed_and_change_flags_medium_finding(base_root):
    param_col = base_root.find(".//datasource[@name='Parameters']/column")
    ET.SubElement(param_col, "range", {"min": "0", "max": "100", "granularity": "1"})

    published = _parse(base_root)
    assert published.parameters["[Parameter 1]"]["range"] == {"min": "0", "max": "100", "granularity": "1"}

    candidate_root = copy.deepcopy(base_root)
    range_el = candidate_root.find(".//datasource[@name='Parameters']/column/range")
    range_el.set("max", "200")
    candidate = _parse(candidate_root)

    diff = compute_diff(published, candidate)
    params_group = next(g for g in diff.groups if g.key == "params")
    assert any(
        any(p["attr"] == "range" for p in i.parts)
        for i in params_group.items if i.op == "change"
    )

    impact = classify_custom_view_impact(published, candidate, diff)
    assert any(
        f["category"] == "Parameters" and f["severity"] == "Medium" and f["title"] == 'Parameter "[Parameter 1]" changed'
        for f in impact["findings"]
    )


def test_parameter_allowable_values_parsed(base_root):
    param_col = base_root.find(".//datasource[@name='Parameters']/column")
    members = ET.SubElement(param_col, "members")
    ET.SubElement(members, "member", {"value": "10", "alias": "Ten"})
    ET.SubElement(members, "member", {"value": "20", "alias": "Twenty"})

    struct = _parse(base_root)
    assert struct.parameters["[Parameter 1]"]["allowable_values"] == [
        {"value": "10", "alias": "Ten"},
        {"value": "20", "alias": "Twenty"},
    ]


# --- build_plain_language_summary ---------------------------------------

def _cv(severity):
    return {"name": "CV", "view_name": "V", "impact_severity": severity}


def test_plain_language_summary_no_custom_views():
    summary = build_plain_language_summary({"findings": []}, [])
    assert summary["total_views"] == 0
    assert summary["tone"] == "info"
    assert "no existing custom views" in summary["headline"]
    assert summary["view_counts"] == {
        "directly_impacted": 0, "potentially_impacted": 0, "label_change_only": 0, "no_change": 0,
    }


def test_plain_language_summary_directly_impacted():
    impact = {"findings": [{"category": "Data Sources", "severity": "High", "title": "t", "detail": "d"}]}
    custom_views = [_cv("High"), _cv("None")]
    summary = build_plain_language_summary(impact, custom_views)
    assert summary["tone"] == "danger"
    assert summary["headline"].startswith("Yes -")
    assert summary["view_counts"]["directly_impacted"] == 1
    assert summary["view_counts"]["no_change"] == 1


def test_plain_language_summary_potentially_impacted_when_no_high():
    impact = {"findings": [{"category": "Filters", "severity": "Medium", "title": "t", "detail": "d"}]}
    custom_views = [_cv("Medium"), _cv("Medium")]
    summary = build_plain_language_summary(impact, custom_views)
    assert summary["tone"] == "warning"
    assert summary["headline"].startswith("Possibly -")
    assert summary["view_counts"]["potentially_impacted"] == 2


def test_plain_language_summary_label_change_only():
    impact = {"findings": [{"category": "Parameters", "severity": "Medium", "title": "caption changed", "detail": "d"}]}
    custom_views = [_cv("Low")]
    summary = build_plain_language_summary(impact, custom_views)
    assert summary["tone"] == "info"
    assert summary["headline"].startswith("Only a cosmetic change")
    assert summary["view_counts"]["label_change_only"] == 1


def test_plain_language_summary_no_change():
    summary = build_plain_language_summary({"findings": []}, [_cv("None"), _cv("None")])
    assert summary["tone"] == "success"
    assert summary["headline"].startswith("No -")
    assert summary["view_counts"]["no_change"] == 2


def test_plain_language_summary_reasons_deduped_and_sorted_by_severity():
    impact = {
        "findings": [
            {"category": "Filters", "severity": "Low", "title": "t1", "detail": "d1"},
            {"category": "Filters", "severity": "High", "title": "t2", "detail": "d2"},
            {"category": "Marks", "severity": "Medium", "title": "t3", "detail": "d3"},
        ]
    }
    summary = build_plain_language_summary(impact, [])
    assert [r["category"] for r in summary["reasons"]] == ["Filters", "Marks"]
    assert summary["reasons"][0]["severity"] == "High"


def test_plain_language_summary_unknown_category_falls_back_to_detail():
    impact = {"findings": [{"category": "Something New", "severity": "Medium", "title": "t", "detail": "custom detail text"}]}
    summary = build_plain_language_summary(impact, [])
    assert summary["reasons"][0]["text"] == "custom detail text"


def test_plain_language_summary_no_findings_reasons_empty():
    summary = build_plain_language_summary({"findings": []}, [])
    assert summary["reasons"] == []
