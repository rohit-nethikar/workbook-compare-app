#!/usr/bin/env python
"""
Quick helper script for Phase 1 mock testing (LIVE_VERIFICATION_GUIDE.md).

Runs the mock scenario tests without needing a live Tableau Server.
Use this to verify the core normalization, diffing, and evidence logic
before attempting live Tableau Server verification.

Usage:
  python test_mock_evidence.py

Expected output: All scenarios pass with "✓" marks.
"""

from custom_view_state import (
    DerivedCustomViewState,
    FilterState,
    ParameterState,
    compute_state_delta,
)
from workbook_compare_engine import apply_derived_state_evidence


def test_scenario_1():
    """Scenario 1: Custom view with filter overrides."""
    print("\n[Scenario 1] Custom View with Filter Overrides")
    print("-" * 50)

    base = DerivedCustomViewState(
        filters=[FilterState("Region", "Sales", ["East", "West"])],
        parameters=[ParameterState("Year", "2024")]
    )
    custom_view = DerivedCustomViewState(
        filters=[FilterState("Region", "Sales", ["North"])],
        parameters=[ParameterState("Year", "2024")]
    )

    delta = compute_state_delta(base, custom_view)
    evidence = apply_derived_state_evidence(delta)

    assert delta.deltas, "Delta should be non-empty"
    assert len(delta.deltas) == 1, f"Expected 1 delta, got {len(delta.deltas)}"
    assert evidence["confidence"] == "confirmed", f"Expected 'confirmed', got {evidence['confidence']}"
    print("[PASS] Confidence: {}".format(evidence['confidence']))
    print("[PASS] Delta count: {}".format(len(delta.deltas)))
    print("[PASS] Note: {}".format(evidence['note']))


def test_scenario_2():
    """Scenario 2: Custom view with no overrides (same state as base)."""
    print("\n[Scenario 2] Custom View with No Overrides")
    print("-" * 50)

    base = DerivedCustomViewState(
        filters=[FilterState("Region", "Sales", ["East", "West"])],
        parameters=[ParameterState("Year", "2024")]
    )
    custom_view = DerivedCustomViewState(
        filters=[FilterState("Region", "Sales", ["East", "West"])],
        parameters=[ParameterState("Year", "2024")]
    )

    delta = compute_state_delta(base, custom_view)
    evidence = apply_derived_state_evidence(delta)

    assert delta.is_empty(), "Delta should be empty"
    assert evidence["confidence"] == "unconfirmed", f"Expected 'unconfirmed', got {evidence['confidence']}"
    print("[PASS] Confidence: {}".format(evidence['confidence']))
    print("[PASS] Delta count: {}".format(len(delta.deltas)))
    print("[PASS] Note: {}".format(evidence['note']))


def test_scenario_3():
    """Scenario 3: Parameter changes + filter added."""
    print("\n[Scenario 3] Parameter Change + Filter Added")
    print("-" * 50)

    base = DerivedCustomViewState(
        filters=[FilterState("Region", "Sales", ["East"])],
        parameters=[ParameterState("Year", "2024")]
    )
    custom_view = DerivedCustomViewState(
        filters=[
            FilterState("Region", "Sales", ["East"]),
            FilterState("Status", "Sales", ["Active", "Pending"]),
        ],
        parameters=[ParameterState("Year", "2025")]
    )

    delta = compute_state_delta(base, custom_view)
    evidence = apply_derived_state_evidence(delta)

    assert delta.deltas, "Delta should be non-empty"
    assert len(delta.deltas) == 2, f"Expected 2 deltas, got {len(delta.deltas)}"
    assert evidence["confidence"] == "confirmed", f"Expected 'confirmed', got {evidence['confidence']}"
    print("[PASS] Confidence: {}".format(evidence['confidence']))
    print("[PASS] Delta count: {}".format(len(delta.deltas)))
    print("[PASS] Note: {}".format(evidence['note']))


def test_scenario_4():
    """Scenario 4: Filter order normalization (should be ignored)."""
    print("\n[Scenario 4] Filter Value Order Normalization")
    print("-" * 50)

    base = DerivedCustomViewState(
        filters=[FilterState("Region", "Sales", ["East", "West", "North"])]
    )
    custom_view = DerivedCustomViewState(
        filters=[FilterState("Region", "Sales", ["North", "East", "West"])]
    )

    delta = compute_state_delta(base, custom_view)
    evidence = apply_derived_state_evidence(delta)

    assert delta.is_empty(), "Delta should be empty (order irrelevant after normalization)"
    assert evidence["confidence"] == "unconfirmed"
    print("[PASS] Order-independent normalization works")
    print("[PASS] Confidence: {}".format(evidence['confidence']))


def test_scenario_5():
    """Scenario 5: Filter cleared (empty applied_values)."""
    print("\n[Scenario 5] Filter Cleared (Empty Applied Values)")
    print("-" * 50)

    base = DerivedCustomViewState(
        filters=[FilterState("Region", "Sales", ["East", "West"])]
    )
    custom_view = DerivedCustomViewState(
        filters=[FilterState("Region", "Sales", [])]
    )

    delta = compute_state_delta(base, custom_view)
    evidence = apply_derived_state_evidence(delta)

    assert delta.deltas, "Delta should detect cleared filter"
    assert evidence["confidence"] == "confirmed"
    print("[PASS] Cleared filter detected as 'values_changed'")
    print("[PASS] Confidence: {}".format(evidence['confidence']))


def test_scenario_6():
    """Scenario 6: Case-insensitive field names."""
    print("\n[Scenario 6] Case-Insensitive Field Name Normalization")
    print("-" * 50)

    base = DerivedCustomViewState(
        filters=[FilterState("REGION", "SALES", ["East"])]
    )
    custom_view = DerivedCustomViewState(
        filters=[FilterState("Region", "Sales", ["East"])]
    )

    delta = compute_state_delta(base, custom_view)
    evidence = apply_derived_state_evidence(delta)

    assert delta.is_empty(), "Delta should be empty (case-insensitive matching)"
    assert evidence["confidence"] == "unconfirmed"
    print("[PASS] Case-insensitive normalization works")
    print("[PASS] Confidence: {}".format(evidence['confidence']))


def test_scenario_7():
    """Scenario 7: No evidence (not checked)."""
    print("\n[Scenario 7] Evidence Not Checked (None delta)")
    print("-" * 50)

    evidence = apply_derived_state_evidence(None)

    assert evidence["confidence"] == "not_checked"
    assert "not performed" in evidence["note"]
    print("[PASS] Confidence: {}".format(evidence['confidence']))
    print("[PASS] Note: {}".format(evidence['note']))


def main():
    print("=" * 50)
    print("PHASE 1: MOCK EVIDENCE TESTING")
    print("=" * 50)

    try:
        test_scenario_1()
        test_scenario_2()
        test_scenario_3()
        test_scenario_4()
        test_scenario_5()
        test_scenario_6()
        test_scenario_7()

        print("\n" + "=" * 50)
        print("[SUCCESS] ALL MOCK SCENARIOS PASSED")
        print("=" * 50)
        print("\nNext steps:")
        print("1. Run: python -m pytest tests/test_custom_view_state.py -v")
        print("2. Start Flask: python app.py")
        print("3. Follow Phase 2+ in LIVE_VERIFICATION_GUIDE.md")
        return 0

    except AssertionError as e:
        print("\n[FAILED] ASSERTION FAILED: {}".format(e))
        return 1
    except Exception as e:
        print("\n[ERROR] {}".format(e))
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
