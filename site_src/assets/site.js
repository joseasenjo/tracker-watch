// Menu, filters, search and sorting for the sites table. No network access, no storage.
(function () {
  var header = document.getElementById('top');
  var button = document.getElementById('menu-btn');
  if (header && button) {
    button.addEventListener('click', function () {
      var open = header.classList.toggle('open');
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }

  var table = document.getElementById('sites-table');
  if (!table) return;
  var body = table.tBodies[0];
  var rows = Array.prototype.slice.call(body.rows);
  var search = document.getElementById('search');
  var empty = document.getElementById('no-results');
  var active = { group: 'all', kind: 'all' };

  function apply() {
    var text = ((search && search.value) || '').trim().toLowerCase();
    var visible = 0;
    rows.forEach(function (row) {
      var ok = (active.group === 'all' || row.dataset.group === active.group) &&
               (active.kind === 'all' || row.dataset.kind === active.kind) &&
               (!text || row.dataset.name.indexOf(text) !== -1);
      row.hidden = !ok;
      if (ok) visible++;
    });
    if (empty) empty.hidden = visible !== 0;
  }

  document.querySelectorAll('.chip[data-filter]').forEach(function (chip) {
    chip.addEventListener('click', function () {
      var type = chip.dataset.filter;
      document.querySelectorAll('.chip[data-filter="' + type + '"]').forEach(function (c) { c.classList.remove('on'); });
      chip.classList.add('on');
      active[type] = chip.dataset.value;
      apply();
    });
  });
  if (search) search.addEventListener('input', apply);

  var direction = {};
  table.querySelectorAll('th button[data-sort]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var key = btn.dataset.sort;
      direction[key] = direction[key] === 'desc' ? 'asc' : 'desc';
      var factor = direction[key] === 'desc' ? -1 : 1;
      rows.sort(function (a, b) {
        var x = a.dataset[key], y = b.dataset[key];
        var nx = parseFloat(x), ny = parseFloat(y);
        if (!isNaN(nx) && !isNaN(ny) && nx !== ny) return (nx - ny) * factor;
        return x.localeCompare(y) * factor;
      });
      rows.forEach(function (row) { body.appendChild(row); });
    });
  });
})();
