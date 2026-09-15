/**
 * Live filter/parameter state capture via Tableau Embedding API v3.
 *
 * Dynamically loads the Embedding API from the Tableau Server, embeds a hidden
 * <tableau-viz> with the base view, captures its effective filter/parameter state,
 * loads a Custom View with showCustomViewAsync(), captures its state, computes
 * the delta, and sends it to the Flask endpoint for evidence analysis.
 *
 * Usage:
 *   - Call captureAndCompareViewState(baseViewUrl, customViewId, customViewName)
 *     from a per-row button click handler (e.g., .derived-state-btn with data attributes)
 *   - The function loads the Embedding API once (cached in window.__tableauEmbeddingApiLoaded),
 *     embeds the views, captures state, and POSTs the delta to /workbook-compare/derived-state
 *   - Results are displayed as a toast or badge inline on the results page
 */

// Cache flag to avoid reloading the Embedding API multiple times
window.__tableauEmbeddingApiLoaded = false;
window.__tableauVizInstance = null;

/**
 * Load Tableau Embedding API v3 script from the Tableau Server.
 * Returns a promise that resolves once the API is available (tableau.embedding global).
 */
async function loadTableauEmbeddingApi(serverUrl) {
  if (window.__tableauEmbeddingApiLoaded) {
    console.log('[Derived State] Embedding API already loaded (cached)');
    return Promise.resolve();
  }

  return new Promise((resolve, reject) => {
    const apiUrl = serverUrl + '/javascripts/api/tableau.embedding.3.latest.js';
    console.log('[Derived State] Loading Embedding API from:', apiUrl);

    const script = document.createElement('script');
    script.type = 'module';
    script.src = apiUrl;

    const timeoutId = setTimeout(() => {
      reject(new Error('Tableau Embedding API script load timeout (30 seconds)'));
    }, 30000);

    script.onload = () => {
      clearTimeout(timeoutId);
      window.__tableauEmbeddingApiLoaded = true;
      console.log('[Derived State] Embedding API loaded successfully');
      resolve();
    };

    script.onerror = (err) => {
      clearTimeout(timeoutId);
      console.error('[Derived State] Embedding API load failed:', err);
      console.error('[Derived State] Attempted URL:', apiUrl);
      console.error('[Derived State] Troubleshooting:');
      console.error('  1. Check Network tab (F12) for exact error (404, CORS, timeout, etc.)');
      console.error('  2. Verify Tableau Server is reachable at:', serverUrl);
      console.error('  3. If Tableau Server is on a different domain, CORS must be enabled');
      console.error('  4. Try accessing this URL in browser:', apiUrl);
      reject(new Error(
        'Failed to load Tableau Embedding API from ' + apiUrl + '. ' +
        'Check browser console (F12) Network tab for the exact error (404, CORS, timeout). ' +
        'Verify Tableau Server URL is correct and Embedding API is available at /javascripts/api/tableau.embedding.3.latest.js'
      ));
    };

    document.head.appendChild(script);
  });
}

/**
 * Create a hidden <tableau-viz> element and embed it.
 * Returns a promise that resolves once the viz is interactive (FirstInteractive event).
 */
async function createAndEmbedViz(viewUrl) {
  return new Promise((resolve, reject) => {
    console.log('[Derived State] Creating hidden <tableau-viz> container');

    const container = document.createElement('div');
    container.id = '__derived_state_viz_container';
    container.style.display = 'none';
    document.body.appendChild(container);

    const viz = document.createElement('tableau-viz');
    viz.id = '__derived_state_viz';
    viz.src = viewUrl;
    container.appendChild(viz);

    console.log('[Derived State] <tableau-viz> src set to:', viewUrl);

    const timeoutId = setTimeout(() => {
      console.error('[Derived State] Timeout: viz did not fire FirstInteractive within 30 seconds');
      reject(new Error('Tableau viz did not become interactive within 30 seconds. The view URL may be invalid or the Tableau Server may be unreachable.'));
      cleanup();
    }, 30000);

    // Wait for FirstInteractive event to ensure the viz is ready
    viz.addEventListener('firstinteractive', () => {
      console.log('[Derived State] FirstInteractive event fired');
      clearTimeout(timeoutId);
      window.__tableauVizInstance = viz;
      resolve(viz);
    });

    const cleanup = () => {
      if (container.parentNode) {
        container.parentNode.removeChild(container);
      }
      window.__tableauVizInstance = null;
    };

    // Clean up on error or dismissal
    viz.addEventListener('error', (err) => {
      clearTimeout(timeoutId);
      console.error('[Derived State] Tableau viz error event:', err);
      reject(new Error('Tableau viz error: ' + (err.message || 'Unknown error')));
      cleanup();
    });
  });
}

/**
 * Capture effective filter state from all worksheets in the viz.
 * Returns an array of {field_name, worksheet_name, applied_values, filter_type}.
 */
async function captureFilterState(viz) {
  const filters = [];
  const worksheets = viz.workbook.worksheets;

  console.log('[Derived State] Capturing filters from', worksheets.length, 'worksheet(s)');

  for (const worksheet of worksheets) {
    try {
      console.log('[Derived State] Calling getFiltersAsync() on worksheet:', worksheet.name);
      const filtersInSheet = await worksheet.getFiltersAsync();
      console.log('[Derived State] Worksheet', worksheet.name, 'has', filtersInSheet.length, 'filter(s)');

      for (const filter of filtersInSheet) {
        // Only capture categorical filters for now (skip range/relative-date)
        if (
          filter.fieldName &&
          filter.appliedValues !== undefined &&
          filter.appliedValues !== null
        ) {
          filters.push({
            field_name: filter.fieldName,
            worksheet_name: worksheet.name,
            applied_values: Array.isArray(filter.appliedValues) ? filter.appliedValues : [filter.appliedValues],
            filter_type: 'categorical',
          });
          console.log('[Derived State] Captured filter:', filter.fieldName, 'with values:', filter.appliedValues);
        }
      }
    } catch (err) {
      // If a worksheet's filters cannot be fetched, skip it (not all worksheets may be visible)
      console.warn('[Derived State] Could not fetch filters from worksheet', worksheet.name + ':', err.message);
    }
  }

  console.log('[Derived State] Total filters captured:', filters.length);
  return filters;
}

/**
 * Capture parameter state from the workbook.
 * Returns an array of {name, current_value}.
 */
async function captureParameterState(viz) {
  const parameters = [];

  try {
    console.log('[Derived State] Calling getParametersAsync() on workbook');
    const paramsInWorkbook = await viz.workbook.getParametersAsync();
    console.log('[Derived State] Workbook has', paramsInWorkbook.length, 'parameter(s)');

    for (const param of paramsInWorkbook) {
      parameters.push({
        name: param.name,
        current_value: param.currentValue,
      });
      console.log('[Derived State] Captured parameter:', param.name, '=', param.currentValue);
    }
  } catch (err) {
    console.warn('[Derived State] Could not fetch parameters:', err.message);
  }

  console.log('[Derived State] Total parameters captured:', parameters.length);
  return parameters;
}

/**
 * Load a Custom View by ID into the existing viz.
 * Returns a promise that resolves once the custom view is fully interactive.
 */
async function loadCustomView(viz, customViewId) {
  return new Promise((resolve, reject) => {
    console.log('[Derived State] Loading custom view with ID:', customViewId);

    const timeoutId = setTimeout(() => {
      console.error('[Derived State] Timeout: custom view did not fire FirstInteractive within 30 seconds');
      reject(new Error('Custom view did not become interactive within 30 seconds. The custom view ID may be invalid or the custom view may have been deleted.'));
    }, 30000);

    // Listen for the FirstInteractive event after showCustomViewAsync
    const handleInteractive = () => {
      console.log('[Derived State] FirstInteractive event fired for custom view');
      clearTimeout(timeoutId);
      viz.removeEventListener('firstinteractive', handleInteractive);
      resolve();
    };

    viz.addEventListener('firstinteractive', handleInteractive);

    // Trigger the custom view load
    console.log('[Derived State] Calling showCustomViewAsync()');
    viz.workbook
      .showCustomViewAsync(customViewId)
      .then(() => {
        console.log('[Derived State] showCustomViewAsync() returned successfully, waiting for FirstInteractive');
        // showCustomViewAsync resolved, but we still need to wait for FirstInteractive
      })
      .catch((err) => {
        clearTimeout(timeoutId);
        viz.removeEventListener('firstinteractive', handleInteractive);
        console.error('[Derived State] showCustomViewAsync() failed:', err);
        reject(new Error('Failed to show custom view: ' + (err.message || 'Unknown error')));
      });
  });
}

/**
 * Cleanup the embedded viz and container.
 */
function cleanupViz() {
  console.log('[Derived State] Cleaning up hidden <tableau-viz> container');
  const container = document.getElementById('__derived_state_viz_container');
  if (container && container.parentNode) {
    container.parentNode.removeChild(container);
    console.log('[Derived State] Container removed from DOM');
  }
  window.__tableauVizInstance = null;
}

/**
 * Main entry point: capture base view state, load custom view, capture its state,
 * compute delta, and POST to the backend for evidence analysis.
 *
 * @param {string} baseViewUrl - The Embedding API URL for the base view (e.g., https://server/views/wb/view)
 * @param {string} customViewId - The Custom View LUID (ID)
 * @param {string} customViewName - The Custom View name (for display in UI)
 */
async function captureAndCompareViewState(baseViewUrl, customViewId, customViewName) {
  const toastContainer = document.getElementById('toast-container');
  if (!toastContainer) {
    console.warn('[Derived State] No toast-container found for status updates');
  }

  console.log('[Derived State] Starting state capture for custom view:', customViewName);
  console.log('[Derived State] Base view URL:', baseViewUrl);
  console.log('[Derived State] Custom view ID:', customViewId);

  try {
    showToast(`Capturing base-view state for "${customViewName}"...`, 'info');

    // Load the Embedding API
    // Use the server URL passed from the template, or fall back to current location origin
    const serverUrl = window.__tableauServerUrl || window.location.origin;
    console.log('[Derived State] Tableau Server URL:', serverUrl);

    await loadTableauEmbeddingApi(serverUrl);

    // Create and embed the base view
    console.log('[Derived State] Creating and embedding base view...');
    const viz = await createAndEmbedViz(baseViewUrl);
    console.log('[Derived State] Base view embedded successfully');

    // Capture base-view state
    console.log('[Derived State] Capturing base-view filters and parameters...');
    const baseFilters = await captureFilterState(viz);
    const baseParameters = await captureParameterState(viz);
    console.log('[Derived State] Base state captured:', {
      filterCount: baseFilters.length,
      parameterCount: baseParameters.length,
      filters: baseFilters.map(f => f.field_name),
      parameters: baseParameters.map(p => p.name),
    });

    const baseState = {
      filters: baseFilters,
      parameters: baseParameters,
    };

    showToast(`Loading Custom View "${customViewName}"...`, 'info');

    // Load the custom view
    console.log('[Derived State] Switching to custom view with ID:', customViewId);
    await loadCustomView(viz, customViewId);
    console.log('[Derived State] Custom view loaded successfully');

    // Capture custom-view state
    console.log('[Derived State] Capturing custom-view filters and parameters...');
    const cvFilters = await captureFilterState(viz);
    const cvParameters = await captureParameterState(viz);
    console.log('[Derived State] Custom view state captured:', {
      filterCount: cvFilters.length,
      parameterCount: cvParameters.length,
      filters: cvFilters.map(f => f.field_name),
      parameters: cvParameters.map(p => p.name),
    });

    const customViewState = {
      filters: cvFilters,
      parameters: cvParameters,
    };

    // Clean up the viz
    console.log('[Derived State] Cleaning up embedded viz...');
    cleanupViz();

    // POST the captured states to the backend
    showToast('Analyzing state differences...', 'info');
    console.log('[Derived State] POSTing captured states to /workbook-compare/derived-state...');

    const response = await fetch('/workbook-compare/derived-state', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        base_state: baseState,
        custom_view_state: customViewState,
      }),
    });

    if (!response.ok) {
      const errorBody = await response.json().catch(() => ({}));
      console.error('[Derived State] Server error response:', response.status, errorBody);
      throw new Error(
        errorBody.error || `Server returned ${response.status}`
      );
    }

    const evidence = await response.json();
    console.log('[Derived State] Evidence received:', evidence);

    // Display result
    const confidenceColor = {
      confirmed: 'success',
      unconfirmed: 'warning',
      not_checked: 'info',
    }[evidence.confidence] || 'info';

    showToast(
      `Evidence: ${evidence.note} (${evidence.delta_count} state difference${evidence.delta_count !== 1 ? 's' : ''})`,
      confidenceColor
    );

    console.log('[Derived State] State capture completed successfully');

    // Return evidence object in case caller wants to do something with it
    return evidence;

  } catch (err) {
    cleanupViz();
    console.error('[Derived State] Error capturing derived state:', err);
    console.error('[Derived State] Error details:', {
      message: err.message,
      stack: err.stack,
    });

    let userMessage = err.message;
    if (err.message.includes('FirstInteractive')) {
      userMessage = err.message + ' Verify: (1) View URL is correct, (2) Tableau Server is reachable, (3) You have permission to view it.';
    }

    showToast(`Error capturing state for "${customViewName}": ${userMessage}`, 'error');
    throw err;
  }
}

/**
 * Global click delegation for .derived-state-btn buttons.
 * Expected attributes: data-view-url, data-custom-view-id, data-custom-view-name
 */
document.addEventListener('click', function (e) {
  const btn = e.target.closest('.derived-state-btn');
  if (!btn) return;

  const baseViewUrl = btn.getAttribute('data-view-url');
  const customViewId = btn.getAttribute('data-custom-view-id');
  const customViewName = btn.getAttribute('data-custom-view-name') || 'Custom View';

  console.log('[Derived State] Button clicked:', customViewName);

  if (!baseViewUrl || !customViewId) {
    console.error('[Derived State] Missing button attributes:', { baseViewUrl, customViewId });
    showToast(
      'Missing data attributes on derived-state-btn (needs data-view-url, data-custom-view-id)',
      'error'
    );
    return;
  }

  // Disable the button during capture to prevent double-clicks
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = 'Checking...';

  console.log('[Derived State] Starting capture flow...');
  captureAndCompareViewState(baseViewUrl, customViewId, customViewName)
    .finally(() => {
      console.log('[Derived State] Capture flow completed, re-enabling button');
      btn.disabled = false;
      btn.textContent = originalText;
    });
});
