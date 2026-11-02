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

// Owner domain filter: narrows an owner-email column to Mayo.edu or non-Mayo.edu
// addresses. Blank or unparseable emails count as non-Mayo.edu.
var emailDomainFilters = {};

function isMayoEmail(email) {
  var at = email.lastIndexOf('@');
  if (at < 0) { return false; }
  return /(^|\.)mayo\.edu$/i.test(email.slice(at + 1).trim());
}

$.fn.dataTable.ext.search.push(function (settings, data) {
  var f = emailDomainFilters[settings.nTable.id];
  if (!f || f.value === 'all') { return true; }
  var isMayo = isMayoEmail(String(data[f.column] || '').trim());
  return f.value === 'mayo' ? isMayo : !isMayo;
});

// Wires a <select> (value: all | mayo | non-mayo) to filter the given table's
// email column. Call after initDataTable.
function addEmailDomainFilter(tableSelector, selectSelector, emailColumn) {
  var tableId = $(tableSelector).attr('id');
  var state = { column: emailColumn, value: 'all' };
  emailDomainFilters[tableId] = state;
  $(selectSelector).on('change', function () {
    state.value = this.value;
    $(tableSelector).DataTable().draw();
  });
}
