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
    return Promise.resolve();
  }

  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.type = 'module';
    script.src = serverUrl + '/javascripts/api/tableau.embedding.3.latest.js';
    script.onload = () => {
      window.__tableauEmbeddingApiLoaded = true;
      resolve();
    };
    script.onerror = (err) => {
      reject(new Error('Failed to load Tableau Embedding API: ' + err));
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
    const container = document.createElement('div');
    container.id = '__derived_state_viz_container';
    container.style.display = 'none';
    document.body.appendChild(container);

    const viz = document.createElement('tableau-viz');
    viz.id = '__derived_state_viz';
    viz.src = viewUrl;
    container.appendChild(viz);

    const timeoutId = setTimeout(() => {
      reject(new Error('Tableau viz did not become interactive within 30 seconds'));
      cleanup();
    }, 30000);

    // Wait for FirstInteractive event to ensure the viz is ready
    viz.addEventListener('firstinteractive', () => {
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
      reject(new Error('Tableau viz error: ' + err.message));
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

  for (const worksheet of worksheets) {
    try {
      const filtersInSheet = await worksheet.getFiltersAsync();
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
        }
      }
    } catch (err) {
      // If a worksheet's filters cannot be fetched, skip it (not all worksheets may be visible)
      console.warn('Could not fetch filters from worksheet ' + worksheet.name + ': ' + err.message);
    }
  }

  return filters;
}

/**
 * Capture parameter state from the workbook.
 * Returns an array of {name, current_value}.
 */
async function captureParameterState(viz) {
  const parameters = [];

  try {
    const paramsInWorkbook = await viz.workbook.getParametersAsync();
    for (const param of paramsInWorkbook) {
      parameters.push({
        name: param.name,
        current_value: param.currentValue,
      });
    }
  } catch (err) {
    console.warn('Could not fetch parameters: ' + err.message);
  }

  return parameters;
}

/**
 * Load a Custom View by ID into the existing viz.
 * Returns a promise that resolves once the custom view is fully interactive.
 */
async function loadCustomView(viz, customViewId) {
  return new Promise((resolve, reject) => {
    const timeoutId = setTimeout(() => {
      reject(new Error('Custom view did not become interactive within 30 seconds'));
    }, 30000);

    // Listen for the FirstInteractive event after showCustomViewAsync
    const handleInteractive = () => {
      clearTimeout(timeoutId);
      viz.removeEventListener('firstinteractive', handleInteractive);
      resolve();
    };

    viz.addEventListener('firstinteractive', handleInteractive);

    // Trigger the custom view load
    viz.workbook
      .showCustomViewAsync(customViewId)
      .then(() => {
        // showCustomViewAsync resolved, but we still need to wait for FirstInteractive
      })
      .catch((err) => {
        clearTimeout(timeoutId);
        viz.removeEventListener('firstinteractive', handleInteractive);
        reject(new Error('Failed to show custom view: ' + err.message));
      });
  });
}

/**
 * Cleanup the embedded viz and container.
 */
function cleanupViz() {
  const container = document.getElementById('__derived_state_viz_container');
  if (container && container.parentNode) {
    container.parentNode.removeChild(container);
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
    console.warn('No toast-container found for status updates');
  }

  try {
    showToast(`Capturing base-view state for "${customViewName}"...`, 'info');

    // Load the Embedding API
    const serverUrl = window.location.origin; // Assumes app is on same domain as Tableau Server
    await loadTableauEmbeddingApi(serverUrl);

    // Create and embed the base view
    const viz = await createAndEmbedViz(baseViewUrl);

    // Capture base-view state
    const baseFilters = await captureFilterState(viz);
    const baseParameters = await captureParameterState(viz);
    const baseState = {
      filters: baseFilters,
      parameters: baseParameters,
    };

    showToast(`Loading Custom View "${customViewName}"...`, 'info');

    // Load the custom view
    await loadCustomView(viz, customViewId);

    // Capture custom-view state
    const cvFilters = await captureFilterState(viz);
    const cvParameters = await captureParameterState(viz);
    const customViewState = {
      filters: cvFilters,
      parameters: cvParameters,
    };

    // Clean up the viz
    cleanupViz();

    // POST the captured states to the backend
    showToast('Analyzing state differences...', 'info');
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
      throw new Error(
        errorBody.error || `Server returned ${response.status}`
      );
    }

    const evidence = await response.json();

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

    // Return evidence object in case caller wants to do something with it
    return evidence;

  } catch (err) {
    cleanupViz();
    console.error('Error capturing derived state:', err);
    showToast(`Error capturing state for "${customViewName}": ${err.message}`, 'error');
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

  if (!baseViewUrl || !customViewId) {
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

  captureAndCompareViewState(baseViewUrl, customViewId, customViewName)
    .finally(() => {
      btn.disabled = false;
      btn.textContent = originalText;
    });
});
