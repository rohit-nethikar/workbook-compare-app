// Shared DataTables init used by every table across the app: adds a CSV export
// button, a column-visibility toggle, and a sticky header on top of whatever
// page-specific options (order, scrollX, etc.) the caller passes in.
function initDataTable(selector, options) {
  options = options || {};
  var navbar = document.querySelector('.navbar-gradient');

  var defaults = {
    dom: "<'row mb-2 align-items-center'<'col-sm-6'B><'col-sm-6 d-flex justify-content-end'f>>" +
         "rt" +
         "<'row mt-2 align-items-center'<'col-sm-6'i><'col-sm-6'p>>",
    buttons: [
      { extend: 'csvHtml5', text: '<i class="bi bi-download"></i> CSV', className: 'btn btn-sm btn-outline-secondary me-1' },
      { extend: 'colvis', text: '<i class="bi bi-layout-three-columns"></i> Columns', className: 'btn btn-sm btn-outline-secondary' }
    ],
    pageLength: 25
  };

  // FixedHeader doesn't play well with horizontally-scrolling tables, so only
  // enable it when the caller isn't using scrollX.
  if (!options.scrollX) {
    defaults.fixedHeader = { header: true, headerOffset: navbar ? navbar.offsetHeight : 0 };
  }

  var merged = $.extend(true, {}, defaults, options);
  return $(selector).DataTable(merged);
}
