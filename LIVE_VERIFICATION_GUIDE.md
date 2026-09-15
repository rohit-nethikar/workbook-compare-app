# Live Verification Guide: Derived Custom View State Capture

This guide walks through verifying the filter/parameter capture feature against a real Tableau Server.

## Prerequisites

- Tableau Server accessible from your browser (admin can reach it)
- Flask app running: `python app.py` (listens on `http://localhost:8000` by default)
- Admin already authenticated to Tableau Server (same browser session)
- Test workbook on Tableau Server with at least one saved Custom View

## Phase 1: Pre-Deployment Local Testing (Mock Fixtures)

### 1.1 Run the Mock Test Suite

Test the normalization/diffing logic against fixture data (no live Tableau required):

```bash
cd workbook-compare-app
python -m pytest tests/test_custom_view_state.py -v
```

**Expected**: All 25 tests pass (0.8 seconds).

```
================================ 25 passed in 0.80s ================================
```

This confirms:
- Filter normalization (values sorted, keys lowercased, empty cleared)
- Parameter normalization (null values preserved)
- Delta computation (added/removed/changed states detected)

### 1.2 Test the Evidence Function

Add a quick Python script to verify the evidence logic:

```python
# test_evidence_logic.py
from custom_view_state import DerivedCustomViewState, FilterState, ParameterState, compute_state_delta
from workbook_compare_engine import apply_derived_state_evidence

# Scenario: Custom View has different filter values
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

print(f"Delta count: {evidence['delta_count']}")
print(f"Confidence: {evidence['confidence']}")
print(f"Note: {evidence['note']}")
```

Run it:
```bash
python test_evidence_logic.py
```

**Expected output**:
```
Delta count: 1
Confidence: confirmed
Note: Custom view overrides detected: 1 filter(s) changed or overridden. Impact risk is elevated.
```

### 1.3 Test Flask Endpoint with Mock Data

Start the Flask app:
```bash
python app.py
```

In another terminal, POST mock captured states:
```bash
curl -X POST http://localhost:8000/workbook-compare/derived-state \
  -H "Content-Type: application/json" \
  -d '{
    "base_state": {
      "filters": [
        {"field_name": "Region", "worksheet_name": "Sales", "applied_values": ["East", "West"]}
      ],
      "parameters": [
        {"name": "Year", "current_value": "2024"}
      ]
    },
    "custom_view_state": {
      "filters": [
        {"field_name": "Region", "worksheet_name": "Sales", "applied_values": ["North"]}
      ],
      "parameters": [
        {"name": "Year", "current_value": "2025"}
      ]
    }
  }'
```

**Expected response** (HTTP 200):
```json
{
  "confidence": "confirmed",
  "note": "Custom view overrides detected: 1 filter(s) changed or overridden, 1 parameter(s) changed or overridden. Impact risk is elevated.",
  "delta_count": 2
}
```

---

## Phase 2: Live Tableau Server Verification

### 2.1 Verify Embed URL Construction

**Goal**: Confirm `view_content_url` is correctly populated and embeddable.

1. Go to Workbook Compare: `http://localhost:8000/workbook-compare`
2. Complete a compare (select a published workbook, upload a candidate .twbx)
3. Scroll to the custom-views results table
4. **Check the "Live State" column**:
   - If "Check" button appears → `view_content_url` was populated ✓
   - If "—" appears → `view_content_url` is None (workbook has no views, or `populate_views()` failed)

**Troubleshoot if "—" appears**:
- Check browser console (F12 → Console tab) for any errors during page load
- Check Flask logs for exceptions during `populate_views()` call
- Verify the workbook actually has views (not dashboard-only)

### 2.2 Verify Embedding API Script Loading

**Goal**: Confirm Tableau Embedding API v3 script loads from server.

1. Open browser DevTools (F12)
2. Click "Check Live State" button on any custom-view row
3. **Watch Network tab**:
   - You should see a `GET /javascripts/api/tableau.embedding.3.latest.js` request to your Tableau Server
   - Status should be `200 OK`
   - If `404` or `CORS error` → embedding API script not accessible from your browser

**Troubleshoot CORS errors**:
- Confirm Tableau Server allows cross-origin requests (check Tableau Server settings)
- If Tableau Server is on different domain, may need CORS proxy or domain adjustment

### 2.3 Verify View Embedding & State Capture

**Goal**: Confirm `<tableau-viz>` embeds and filter/parameter state is captured.

1. Open browser DevTools (F12 → Console tab)
2. Click "Check Live State" button
3. **Watch for these events**:
   - `console.log`: "Capturing base-view state for 'CustomViewName'..." (should appear immediately)
   - `console.log`: "Loading Custom View 'CustomViewName'..." (after ~5 seconds)
   - `console.log`: "Analyzing state differences..." (after another ~5 seconds)
   - **Toast notification** at bottom-right: "Evidence: Custom view overrides detected: X filter(s)..." or "Evidence: Custom view applies the same filters..."

**If you see a timeout error** ("Tableau viz did not become interactive within 30 seconds"):
- The embed URL may be invalid or the view may not be accessible to your user
- Check Flask logs to verify the `view_content_url` matches the actual published view name/workbook

### 2.4 Verify Delta Detection & Evidence

**Goal**: Confirm captured deltas are correct and evidence is accurate.

**Test Case 1: Custom View with filter overrides**
1. Find a custom view you know has different filter values than the base view
2. Click "Check Live State"
3. **Expected toast**: "Custom view overrides detected: X filter(s)... (Z state differences)"
4. **Confidence**: "confirmed"

**Test Case 2: Custom View with no overrides**
1. Find a custom view that applies the exact same filters as the base view
2. Click "Check Live State"
3. **Expected toast**: "Custom view applies the same filters and parameters as the base view—no live overrides detected."
4. **Confidence**: "unconfirmed"

**Test Case 3: Parameter changes**
1. Find a custom view with a saved parameter value different from the workbook default
2. Click "Check Live State"
3. **Expected toast**: Should mention both filters and parameters if both changed

### 2.5 Verify Browser Console for Warnings

**Goal**: Ensure no unexpected errors or silent failures.

1. Open browser DevTools (F12 → Console tab)
2. Click "Check Live State" multiple times on different custom views
3. **Watch for**:
   - `console.warn("Could not fetch filters from worksheet X: ...")`: OK (hidden/inaccessible worksheet)
   - `console.warn("Could not fetch parameters: ...")`: Rare, but OK if no parameters
   - `console.error("Error capturing derived state: ...")`: **NOT OK** → investigate message

**Common warnings to ignore**:
- "Could not fetch filters from worksheet Dashboard: ..." → dashboard worksheets may be inaccessible; not an error

**Errors to investigate**:
- "Failed to load Tableau Embedding API" → script URL wrong or CORS blocked
- "Custom view did not become interactive within 30 seconds" → embed URL invalid or server unreachable
- "Failed to show custom view" → custom view ID (LUID) invalid or deleted

---

## Phase 3: Integration Testing

### 3.1 Verify Evidence Doesn't Break Impact Analysis

**Goal**: Confirm live evidence is additive and doesn't mutate existing impact fields.

1. Complete a workbook compare
2. Note the existing Impact severity for a custom view (e.g., "Potentially impacted / Medium")
3. Click "Check Live State" on that custom view
4. **Verify**:
   - The Impact badge **still shows the original classification** (e.g., "Potentially impacted / Medium")
   - The toast shows evidence separately (not replacing the badge)
   - The custom-view table row is unchanged except for the new evidence toast

**If Impact badge changes**: This is a bug—evidence should be additive only.

### 3.2 Verify Per-Row Evidence Isolation

**Goal**: Confirm checking one custom view doesn't affect others.

1. Click "Check Live State" on custom view A
2. Wait for toast to appear
3. Click "Check Live State" on custom view B
4. **Verify**:
   - Each row gets its own toast
   - Evidence for A is independent from evidence for B
   - No cross-contamination (evidence from A doesn't show on B)

---

## Phase 4: Edge Cases & Error Handling

### 4.1 Unreachable Tableau Server

**Scenario**: Tableau Server is down or unreachable.

1. Stop your Tableau Server or block network access to it
2. Click "Check Live State"
3. **Expected**: Toast error after ~30 seconds: "Error capturing state for 'X': Tableau viz did not become interactive within 30 seconds"
4. **Button state**: Button re-enables (not stuck in "Checking..." state)

### 4.2 Missing Custom View

**Scenario**: Custom view was deleted on Tableau Server.

1. Click "Check Live State" on a custom view
2. If the custom view LUID is invalid or deleted:
3. **Expected**: Toast error: "Error capturing state for 'X': Failed to show custom view: ..."
4. **Button state**: Button re-enables

### 4.3 User Has No Access to View

**Scenario**: Admin is logged in but doesn't have permission to read the base view.

1. Find a custom view whose base view is in a restricted project
2. Click "Check Live State"
3. **Expected**: Toast error: "Error capturing state for 'X': Tableau viz did not become interactive..." (permission error treated as timeout)
4. **Workaround**: Only admins should use this feature; restrict to users with workbook-level read access

### 4.4 Custom View with No Filters/Parameters

**Scenario**: Custom view inherits base view's state exactly (no overrides).

1. Find or create a custom view with no saved filter/parameter differences
2. Click "Check Live State"
3. **Expected toast**: "Evidence: Custom view applies the same filters and parameters as the base view—no live overrides detected. (0 state differences)"
4. **Confidence**: "unconfirmed" (live state matches structural expectations, so uncertainty remains)

---

## Phase 5: Performance & Load Testing

### 5.1 Single View Capture Time

**Goal**: Measure capture latency per view.

1. Open browser DevTools (F12 → Performance tab)
2. Click "Check Live State"
3. **Expected**: Entire flow (load API, embed base, embed custom view, capture states, POST result) takes **10-20 seconds** on a fast network
   - Embedding API load: ~2 sec
   - Base view FirstInteractive: ~3-5 sec
   - Custom view switch + FirstInteractive: ~3-5 sec
   - POST + response: ~1 sec

**If capture takes >30 seconds**: Tableau Server may be slow; increase timeout in JS if needed.

### 5.2 Multiple Sequential Captures

**Goal**: Verify no memory leaks or state accumulation.

1. Click "Check Live State" on 10 different custom views in sequence
2. Watch browser memory (DevTools → Memory tab)
3. **Expected**: Memory stays roughly constant (old `<tableau-viz>` elements cleaned up after each capture)

**If memory grows unbounded**: Hidden `<tableau-viz>` elements may not be cleaned up properly; check `cleanupViz()` logic.

---

## Troubleshooting Quick Reference

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| "Check" button doesn't appear in Live State column | `populate_views()` failed or workbook has no views | Verify workbook has at least one published view; check Flask logs |
| "Tableau viz did not become interactive within 30 seconds" | Embed URL invalid or server unreachable | Verify `view_content_url` format (should be `/t/site/views/wb/view` or `/views/wb/view`); confirm Tableau Server is reachable from browser |
| Toast says "Failed to load Tableau Embedding API" | Script URL wrong or CORS blocked | Check Tableau Server hostname is correct; verify CORS config on Tableau Server |
| No toast appears, button stuck in "Checking..." | JS error or fetch hanging | Open browser console (F12), look for errors; check Network tab for stuck requests |
| Evidence shows "confirmed" but Impact badge is unchanged | This is correct behavior | Live evidence is additive; Impact severity/classification come from structural analysis only |
| Browser console shows "Could not fetch filters from worksheet X" | Worksheet not visible in this session | This is a warning, not an error; hidden or inaccessible worksheets are skipped |

---

## Verification Checklist

- [ ] Phase 1: Mock tests pass (25/25)
- [ ] Phase 1: Evidence function produces correct output
- [ ] Phase 1: Flask endpoint accepts POST and returns JSON
- [ ] Phase 2: Embed URLs are populated (button appears, not "—")
- [ ] Phase 2: Embedding API script loads (Network tab shows /javascripts/api/...)
- [ ] Phase 2: Base view embeds and FirstInteractive fires
- [ ] Phase 2: Custom view loads via showCustomViewAsync()
- [ ] Phase 2: Toast appears with evidence
- [ ] Phase 3: Evidence is additive (Impact badge unchanged)
- [ ] Phase 3: Per-row evidence is isolated (no cross-contamination)
- [ ] Phase 4: Unreachable server handled gracefully (error toast, button re-enables)
- [ ] Phase 4: Missing custom view handled gracefully
- [ ] Phase 4: Custom view with no overrides shows "unconfirmed" evidence
- [ ] Phase 5: Single capture completes in <30 seconds
- [ ] Phase 5: Multiple captures don't accumulate memory

---

## Next Steps After Successful Verification

1. **Deploy to production**: If all checks pass, deploy to your actual Tableau Server environment
2. **Monitor usage**: Watch Flask logs and browser console errors for issues in production
3. **Collect feedback**: Ask admins if the evidence is useful and if they'd want batch operations
4. **Add optional enhancements**:
   - Batch "Check Live State" for multiple rows
   - Store captured deltas in audit log
   - Add trend tracking (has this custom view's state changed over time?)
