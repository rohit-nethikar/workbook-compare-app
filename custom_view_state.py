"""Capture and normalize Tableau filter/parameter state from live embedded views.

This module provides dataclasses and functions to:
1. Capture effective filter and parameter state from a base view and a Custom View
2. Normalize them for comparison (accounting for field ordering, worksheet/filter name variations, null handling)
3. Compute a delta between base and custom-view states
4. Apply the delta as evidence to strengthen Custom View impact analysis

All functions are pure (deterministic, no side effects) and testable without live Tableau server.
This state is inferred/observed effective state, not Tableau's explicit saved-override metadata.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FilterState:
    """One applied filter value from a worksheet."""
    field_name: str
    worksheet_name: str
    applied_values: list[str]  # Can be empty if filter is cleared/inactive
    filter_type: str = "categorical"  # Only categorical filters are captured for now


@dataclass
class ParameterState:
    """One parameter's current value."""
    name: str
    current_value: Optional[str]


@dataclass
class DerivedCustomViewState:
    """Complete captured state of a view: all filters and parameters."""
    filters: list[FilterState] = field(default_factory=list)
    parameters: list[ParameterState] = field(default_factory=list)


@dataclass
class StateFieldDelta:
    """A single difference between base and custom-view state."""
    field_type: str  # "filter" or "parameter"
    field_name: str
    worksheet_name: Optional[str]  # Only for filters; None for parameters
    change_type: str  # "added", "removed", "value_changed", "values_changed"
    base_value: Optional[list[str] | str]  # For filters: list of values; for parameters: single value
    custom_view_value: Optional[list[str] | str]


@dataclass
class DerivedCustomViewStateDelta:
    """Complete diff between base-view and custom-view states."""
    deltas: list[StateFieldDelta] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True if base and custom view states are identical."""
        return len(self.deltas) == 0


def normalize_filters(filters: list[FilterState]) -> dict:
    """Normalize a list of filters into a canonical form for comparison.

    Returns a dict keyed by (worksheet_name, field_name), each mapping to a sorted
    list of applied values. Normalizes:
    - Field/worksheet name case (lowercase for comparison)
    - Applied-values ordering (sorted for consistent diffs)
    - Empty applied_values (treated as "no filter" / cleared state)
    """
    result = {}
    for f in filters:
        key = (f.worksheet_name.lower(), f.field_name.lower())
        # Sort applied values for deterministic comparison
        normalized_values = sorted(f.applied_values) if f.applied_values else []
        result[key] = normalized_values
    return result


def normalize_parameters(parameters: list[ParameterState]) -> dict:
    """Normalize a list of parameters into a canonical form for comparison.

    Returns a dict keyed by parameter name (lowercase), each mapping to the current value.
    """
    result = {}
    for p in parameters:
        key = p.name.lower()
        result[key] = p.current_value
    return result


def compute_state_delta(
    base_state: DerivedCustomViewState,
    custom_view_state: DerivedCustomViewState,
) -> DerivedCustomViewStateDelta:
    """Compute the delta between base-view and custom-view states.

    Returns a DerivedCustomViewStateDelta listing:
    - Filters added/removed/changed in the custom view
    - Parameters added/removed/changed in the custom view

    Any difference (even a single value in a multi-value categorical filter) counts as "changed".
    """
    delta = DerivedCustomViewStateDelta()

    base_filters = normalize_filters(base_state.filters)
    cv_filters = normalize_filters(custom_view_state.filters)
    base_params = normalize_parameters(base_state.parameters)
    cv_params = normalize_parameters(custom_view_state.parameters)

    # Diff filters: check for removed, changed, or added
    for (worksheet, field), base_values in base_filters.items():
        cv_key = (worksheet, field)
        if cv_key not in cv_filters:
            # Filter was removed in custom view
            delta.deltas.append(
                StateFieldDelta(
                    field_type="filter",
                    field_name=field,
                    worksheet_name=worksheet,
                    change_type="removed",
                    base_value=base_values,
                    custom_view_value=None,
                )
            )
        elif cv_filters[cv_key] != base_values:
            # Filter values changed
            delta.deltas.append(
                StateFieldDelta(
                    field_type="filter",
                    field_name=field,
                    worksheet_name=worksheet,
                    change_type="values_changed",
                    base_value=base_values,
                    custom_view_value=cv_filters[cv_key],
                )
            )

    # Check for added filters
    for (worksheet, field), cv_values in cv_filters.items():
        if (worksheet, field) not in base_filters:
            delta.deltas.append(
                StateFieldDelta(
                    field_type="filter",
                    field_name=field,
                    worksheet_name=worksheet,
                    change_type="added",
                    base_value=None,
                    custom_view_value=cv_values,
                )
            )

    # Diff parameters: check for removed, changed, or added
    for param_name, base_value in base_params.items():
        if param_name not in cv_params:
            # Parameter was removed in custom view
            delta.deltas.append(
                StateFieldDelta(
                    field_type="parameter",
                    field_name=param_name,
                    worksheet_name=None,
                    change_type="removed",
                    base_value=base_value,
                    custom_view_value=None,
                )
            )
        elif cv_params[param_name] != base_value:
            # Parameter value changed
            delta.deltas.append(
                StateFieldDelta(
                    field_type="parameter",
                    field_name=param_name,
                    worksheet_name=None,
                    change_type="value_changed",
                    base_value=base_value,
                    custom_view_value=cv_params[param_name],
                )
            )

    # Check for added parameters
    for param_name, cv_value in cv_params.items():
        if param_name not in base_params:
            delta.deltas.append(
                StateFieldDelta(
                    field_type="parameter",
                    field_name=param_name,
                    worksheet_name=None,
                    change_type="added",
                    base_value=None,
                    custom_view_value=cv_value,
                )
            )

    return delta
