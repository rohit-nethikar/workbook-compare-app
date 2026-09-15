"""Deterministic unit tests for custom_view_state module.

Tests normalize/diff functions with no live Tableau server required.
Covers edge cases: unchanged state, single/multi-value filters, parameters,
filter/param added/removed/changed, same filter on multiple worksheets,
null/empty values, field-name case normalization.
"""

import pytest

from custom_view_state import (
    DerivedCustomViewState,
    FilterState,
    ParameterState,
    compute_state_delta,
    normalize_filters,
    normalize_parameters,
)


class TestNormalizeFilters:
    """Test filter normalization."""

    def test_empty_filters(self):
        """Empty filter list normalizes to empty dict."""
        assert normalize_filters([]) == {}

    def test_single_categorical_filter(self):
        """Single filter normalizes to canonical form."""
        filters = [
            FilterState(
                field_name="Region",
                worksheet_name="Sales",
                applied_values=["East", "West"],
                filter_type="categorical",
            )
        ]
        result = normalize_filters(filters)
        assert result == {("sales", "region"): ["East", "West"]}

    def test_filter_values_sorted(self):
        """Applied values are sorted for deterministic comparison."""
        filters = [
            FilterState(
                field_name="Product",
                worksheet_name="Inventory",
                applied_values=["Widgets", "Gadgets", "Accessories"],
            )
        ]
        result = normalize_filters(filters)
        assert result == {("inventory", "product"): ["Accessories", "Gadgets", "Widgets"]}

    def test_empty_applied_values_treated_as_cleared(self):
        """Filter with no applied values is treated as cleared."""
        filters = [
            FilterState(
                field_name="Status",
                worksheet_name="Reports",
                applied_values=[],
            )
        ]
        result = normalize_filters(filters)
        assert result == {("reports", "status"): []}

    def test_case_insensitive_keys(self):
        """Field and worksheet names are lowercased for comparison."""
        filters = [
            FilterState(
                field_name="REGION",
                worksheet_name="SALES",
                applied_values=["East"],
            )
        ]
        result = normalize_filters(filters)
        assert ("sales", "region") in result

    def test_multiple_filters_same_worksheet(self):
        """Multiple filters on same worksheet are keyed separately."""
        filters = [
            FilterState("Region", "Sales", ["East", "West"]),
            FilterState("Product", "Sales", ["Widgets"]),
        ]
        result = normalize_filters(filters)
        assert len(result) == 2
        assert result[("sales", "region")] == ["East", "West"]
        assert result[("sales", "product")] == ["Widgets"]

    def test_same_filter_on_different_worksheets(self):
        """Same filter name on different worksheets are distinct."""
        filters = [
            FilterState("Date", "Dashboard1", ["2024-01-01"]),
            FilterState("Date", "Dashboard2", ["2024-02-01"]),
        ]
        result = normalize_filters(filters)
        assert len(result) == 2
        assert result[("dashboard1", "date")] == ["2024-01-01"]
        assert result[("dashboard2", "date")] == ["2024-02-01"]


class TestNormalizeParameters:
    """Test parameter normalization."""

    def test_empty_parameters(self):
        """Empty parameter list normalizes to empty dict."""
        assert normalize_parameters([]) == {}

    def test_single_parameter(self):
        """Single parameter normalizes to canonical form."""
        params = [ParameterState(name="Year", current_value="2024")]
        result = normalize_parameters(params)
        assert result == {"year": "2024"}

    def test_parameter_with_none_value(self):
        """Parameter with None value is preserved."""
        params = [ParameterState(name="Filter", current_value=None)]
        result = normalize_parameters(params)
        assert result == {"filter": None}

    def test_case_insensitive_names(self):
        """Parameter names are lowercased."""
        params = [ParameterState(name="REVENUE_THRESHOLD", current_value="100000")]
        result = normalize_parameters(params)
        assert ("revenue_threshold") in result

    def test_multiple_parameters(self):
        """Multiple parameters are all captured."""
        params = [
            ParameterState("Year", "2024"),
            ParameterState("Region", "North"),
            ParameterState("Status", None),
        ]
        result = normalize_parameters(params)
        assert len(result) == 3
        assert result["year"] == "2024"
        assert result["region"] == "North"
        assert result["status"] is None


class TestComputeStateDelta:
    """Test delta computation between base and custom-view states."""

    def test_identical_states_no_delta(self):
        """Identical base and custom-view states produce no delta."""
        base = DerivedCustomViewState(
            filters=[FilterState("Region", "Sales", ["East", "West"])],
            parameters=[ParameterState("Year", "2024")],
        )
        custom_view = DerivedCustomViewState(
            filters=[FilterState("Region", "Sales", ["East", "West"])],
            parameters=[ParameterState("Year", "2024")],
        )
        delta = compute_state_delta(base, custom_view)
        assert delta.is_empty()

    def test_filter_removed(self):
        """Filter removed in custom view is detected."""
        base = DerivedCustomViewState(
            filters=[FilterState("Region", "Sales", ["East", "West"])]
        )
        custom_view = DerivedCustomViewState(filters=[])
        delta = compute_state_delta(base, custom_view)
        assert len(delta.deltas) == 1
        assert delta.deltas[0].change_type == "removed"
        assert delta.deltas[0].field_name == "region"
        assert delta.deltas[0].base_value == ["East", "West"]
        assert delta.deltas[0].custom_view_value is None

    def test_filter_added(self):
        """Filter added in custom view is detected."""
        base = DerivedCustomViewState(filters=[])
        custom_view = DerivedCustomViewState(
            filters=[FilterState("Status", "Reports", ["Active"])]
        )
        delta = compute_state_delta(base, custom_view)
        assert len(delta.deltas) == 1
        assert delta.deltas[0].change_type == "added"
        assert delta.deltas[0].field_name == "status"
        assert delta.deltas[0].base_value is None
        assert delta.deltas[0].custom_view_value == ["Active"]

    def test_filter_values_changed(self):
        """Change in filter values is detected."""
        base = DerivedCustomViewState(
            filters=[FilterState("Region", "Sales", ["East", "West"])]
        )
        custom_view = DerivedCustomViewState(
            filters=[FilterState("Region", "Sales", ["North", "South"])]
        )
        delta = compute_state_delta(base, custom_view)
        assert len(delta.deltas) == 1
        assert delta.deltas[0].change_type == "values_changed"
        assert delta.deltas[0].base_value == ["East", "West"]
        assert delta.deltas[0].custom_view_value == ["North", "South"]

    def test_filter_values_order_ignored(self):
        """Filter value order does not cause a delta (values are sorted)."""
        base = DerivedCustomViewState(
            filters=[FilterState("Region", "Sales", ["East", "West"])]
        )
        custom_view = DerivedCustomViewState(
            filters=[FilterState("Region", "Sales", ["West", "East"])]
        )
        delta = compute_state_delta(base, custom_view)
        assert delta.is_empty()

    def test_parameter_removed(self):
        """Parameter removed in custom view is detected."""
        base = DerivedCustomViewState(parameters=[ParameterState("Year", "2024")])
        custom_view = DerivedCustomViewState(parameters=[])
        delta = compute_state_delta(base, custom_view)
        assert len(delta.deltas) == 1
        assert delta.deltas[0].change_type == "removed"
        assert delta.deltas[0].field_type == "parameter"
        assert delta.deltas[0].field_name == "year"
        assert delta.deltas[0].base_value == "2024"
        assert delta.deltas[0].custom_view_value is None

    def test_parameter_added(self):
        """Parameter added in custom view is detected."""
        base = DerivedCustomViewState(parameters=[])
        custom_view = DerivedCustomViewState(
            parameters=[ParameterState("Threshold", "50000")]
        )
        delta = compute_state_delta(base, custom_view)
        assert len(delta.deltas) == 1
        assert delta.deltas[0].change_type == "added"
        assert delta.deltas[0].field_type == "parameter"
        assert delta.deltas[0].field_name == "threshold"

    def test_parameter_value_changed(self):
        """Change in parameter value is detected."""
        base = DerivedCustomViewState(parameters=[ParameterState("Year", "2024")])
        custom_view = DerivedCustomViewState(
            parameters=[ParameterState("Year", "2025")]
        )
        delta = compute_state_delta(base, custom_view)
        assert len(delta.deltas) == 1
        assert delta.deltas[0].change_type == "value_changed"
        assert delta.deltas[0].field_type == "parameter"
        assert delta.deltas[0].base_value == "2024"
        assert delta.deltas[0].custom_view_value == "2025"

    def test_multiple_changes(self):
        """Multiple changes are all detected."""
        base = DerivedCustomViewState(
            filters=[
                FilterState("Region", "Sales", ["East"]),
                FilterState("Product", "Sales", ["Widgets"]),
            ],
            parameters=[ParameterState("Year", "2024")],
        )
        custom_view = DerivedCustomViewState(
            filters=[
                FilterState("Region", "Sales", ["West"]),  # Changed
                # Product filter removed
                FilterState("Status", "Sales", ["Active"]),  # Added
            ],
            parameters=[ParameterState("Year", "2025")],  # Changed
        )
        delta = compute_state_delta(base, custom_view)
        assert len(delta.deltas) == 4
        changes_by_type = {d.change_type for d in delta.deltas}
        assert "values_changed" in changes_by_type
        assert "removed" in changes_by_type
        assert "added" in changes_by_type
        assert "value_changed" in changes_by_type

    def test_case_normalization_in_delta(self):
        """Field names are normalized (lowercased) in delta keys."""
        base = DerivedCustomViewState(
            filters=[FilterState("REGION", "SALES", ["East"])]
        )
        custom_view = DerivedCustomViewState(
            filters=[FilterState("Region", "Sales", ["West"])]
        )
        delta = compute_state_delta(base, custom_view)
        assert delta.deltas[0].field_name == "region"
        assert delta.deltas[0].worksheet_name == "sales"

    def test_filter_cleared_to_empty(self):
        """Filter with values cleared ([] applied_values) is detected as change."""
        base = DerivedCustomViewState(
            filters=[FilterState("Region", "Sales", ["East", "West"])]
        )
        custom_view = DerivedCustomViewState(
            filters=[FilterState("Region", "Sales", [])]
        )
        delta = compute_state_delta(base, custom_view)
        assert len(delta.deltas) == 1
        assert delta.deltas[0].change_type == "values_changed"
        assert delta.deltas[0].base_value == ["East", "West"]
        assert delta.deltas[0].custom_view_value == []

    def test_parameter_cleared_to_none(self):
        """Parameter value cleared (set to None) is detected as change."""
        base = DerivedCustomViewState(
            parameters=[ParameterState("Year", "2024")]
        )
        custom_view = DerivedCustomViewState(
            parameters=[ParameterState("Year", None)]
        )
        delta = compute_state_delta(base, custom_view)
        assert len(delta.deltas) == 1
        assert delta.deltas[0].change_type == "value_changed"
        assert delta.deltas[0].base_value == "2024"
        assert delta.deltas[0].custom_view_value is None

    def test_complex_scenario_mixed_changes(self):
        """Complex scenario: multiple filters/params with mixed changes."""
        base = DerivedCustomViewState(
            filters=[
                FilterState("Date", "Dashboard", ["2024-01-01", "2024-01-02"]),
                FilterState("Region", "Dashboard", ["East", "West", "North"]),
                FilterState("Product", "Inventory", ["Gadgets"]),
            ],
            parameters=[
                ParameterState("MinRevenue", "100000"),
                ParameterState("MaxRevenue", "500000"),
                ParameterState("SortBy", "Name"),
            ],
        )
        custom_view = DerivedCustomViewState(
            filters=[
                FilterState("Date", "Dashboard", ["2024-01-15"]),  # Values changed
                FilterState("Region", "Dashboard", ["East", "North", "South"]),  # Values changed
                # Product filter removed
                FilterState("Status", "Dashboard", ["Active", "Pending"]),  # Added
            ],
            parameters=[
                ParameterState("MinRevenue", "150000"),  # Value changed
                ParameterState("MaxRevenue", "500000"),  # Unchanged
                # SortBy removed
                ParameterState("GroupBy", "Category"),  # Added
            ],
        )
        delta = compute_state_delta(base, custom_view)

        # Expect: 2 filter changes + 1 filter removed + 1 filter added + 1 param changed + 1 param removed + 1 param added = 7 deltas
        # (MaxRevenue is unchanged, so not in deltas)
        assert len(delta.deltas) == 7

        # Check a few key deltas
        date_delta = next(d for d in delta.deltas if d.field_name == "date")
        assert date_delta.change_type == "values_changed"

        product_delta = next(d for d in delta.deltas if d.field_name == "product")
        assert product_delta.change_type == "removed"

        min_revenue_delta = next(
            d for d in delta.deltas if d.field_name == "minrevenue"
        )
        assert min_revenue_delta.change_type == "value_changed"

        # MaxRevenue is unchanged, so it should NOT be in deltas
        assert not any(d.field_name == "maxrevenue" for d in delta.deltas)
