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

  // "Is your site in the list?": looks the typed host up in the list embedded in the page.
  // Nothing typed is sent anywhere.
  var form = document.getElementById('check-form');
  var lookup = document.getElementById('site-lookup');
  if (form && lookup) {
    var sites = JSON.parse(lookup.textContent);
    var input = document.getElementById('check-url');
    var result = document.getElementById('check-result');
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var raw = input.value.trim();
      var host = '';
      try { host = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : 'https://' + raw).hostname.toLowerCase().replace(/^www\./, ''); } catch (e) { host = ''; }
      result.hidden = false;
      if (!host || host.indexOf('.') === -1) {
        result.textContent = 'Please type a site address, for example example-news.com.';
        return;
      }
      var found = sites.filter(function (s) {
        return s.host === host || host.endsWith('.' + s.host) || s.host.endsWith('.' + host);
      })[0];
      if (found) {
        window.location.href = form.dataset.root + 'sites/' + found.stem + '.html';
        return;
      }
      result.textContent = host + ' is not in our list. We measure a fixed list of sites once a week; live checks of any address are not available yet. ';
      if (form.dataset.repo) {
        var link = document.createElement('a');
        link.href = form.dataset.repo + '/issues/new?template=add-site.yml&title=' + encodeURIComponent('[Add site] ' + host);
        link.rel = 'noopener';
        link.textContent = 'Suggest adding it.';
        result.appendChild(link);
      }
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
