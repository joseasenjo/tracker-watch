// "Show the result here": reads the reply of the analysis bot from the public GitHub issue and shows it on this page,
// with download as JSON or CSV. It only runs when the visitor presses the button, makes one or two requests to GitHub's
// public API from the visitor's browser (GitHub sees the visitor's IP address), stores nothing and sets no cookies.
// Only comments written by github-actions[bot] are trusted: anyone can comment on an issue.
(function () {
  var form = document.getElementById('result-form');
  if (!form || !form.dataset.repo) return;
  var out = document.getElementById('result-out');
  var note = form.querySelector('.note');
  var api = form.dataset.repo.replace('https://github.com/', 'https://api.github.com/repos/');
  var MARK = /<!-- tw-result:([A-Za-z0-9+\/=]+) -->/;

  function el(tag, text, cls) {
    var n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  }
  function say(text) { note.hidden = false; note.textContent = text; }
  function hostOf(raw) {
    try {
      var u = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : 'https://' + raw);
      return u.hostname.indexOf('.') === -1 ? '' : u.hostname.toLowerCase();
    } catch (e) { return ''; }
  }
  function get(path) {
    return fetch(api + path, { headers: { Accept: 'application/vnd.github+json' } }).then(function (r) {
      if (r.status === 403 || r.status === 429) throw new Error('limit');
      if (!r.ok) throw new Error('http ' + r.status);
      return r.json();
    });
  }
  function decode(b64) {
    var bin = atob(b64), bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return JSON.parse(new TextDecoder('utf-8').decode(bytes));
  }
  function csvCell(v) {
    var s = String(v == null ? '' : v);
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;  // a spreadsheet must never read a cell as a formula
    return '"' + s.replace(/"/g, '""') + '"';
  }
  function download(name, type, text) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: type }));
    a.download = name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }
  function counter(value, label) {
    var d = el('div', undefined, 'counter');
    d.appendChild(el('b', value));
    d.appendChild(el('span', label));
    return d;
  }

  function show(result, issue) {
    out.textContent = '';
    var box = el('div', undefined, 'card');
    box.appendChild(el('h3', 'Analysis of ' + result.host));
    box.appendChild(el('p', 'Measured ' + result.measured_at + ' from ' + result.vantage + ', ' + result.passes +
      ' passes of ' + result.observe_seconds + ' s, no clicks and no consent. Requested by @' + issue.user.login + '.', 'small'));
    if (result.status !== 'ok') {
      box.appendChild(el('p', 'No figures: the site did not allow a measurement (status: ' + result.status + '). We do not try to bypass blocks.', 'note'));
    } else {
      var m = result.metrics, row = el('div', undefined, 'counters');
      row.appendChild(counter(m.tracking_services + (result.band ? ' (' + result.band + ')' : ''), 'tracking services contacted'));
      row.appendChild(counter(m.third_party_domains, 'third-party domains'));
      row.appendChild(counter(m.third_party_requests, 'third-party requests'));
      row.appendChild(counter(m.third_party_cookies + ' of ' + m.cookies_total, 'cookies from third parties'));
      box.appendChild(row);
      box.appendChild(el('p', 'Confidence: ' + (result.confidence || 'n/a') + ' (' + result.passes_ok + ' of ' + result.passes_total + ' passes measured).', 'small'));
      if (result.services.length) {
        var table = el('table'), head = el('tr');
        ['Service', 'Company', 'Category'].forEach(function (h) { head.appendChild(el('th', h)); });
        table.appendChild(head);
        result.services.forEach(function (s) {
          var tr = el('tr');
          [s.service, s.company, s.category.replace(/_/g, ' ')].forEach(function (c) { tr.appendChild(el('td', c)); });
          table.appendChild(tr);
        });
        var wrap = el('div', undefined, 'table-wrap');
        wrap.appendChild(table);
        box.appendChild(wrap);
      }
    }
    box.appendChild(el('p', 'Counts of what the page contacted before any interaction, not legal conclusions or statements of intent. ' +
      'One measurement from servers in the US; the list of services is limited and may be incomplete. Not part of the weekly ranking.', 'small'));
    var actions = el('p');
    var json = el('button', 'Download JSON', 'btn');
    json.type = 'button';
    json.addEventListener('click', function () {
      download('tracker-watch-' + result.host + '.json', 'application/json', JSON.stringify(Object.assign({ source: issue.html_url }, result), null, 2));
    });
    var csv = el('button', 'Download CSV', 'btn sec');
    csv.type = 'button';
    csv.addEventListener('click', function () {
      var lines = [['service', 'company', 'category'].map(csvCell).join(',')];
      (result.services || []).forEach(function (s) { lines.push([s.service, s.company, s.category].map(csvCell).join(',')); });
      download('tracker-watch-' + result.host + '.csv', 'text/csv', lines.join('\r\n') + '\r\n');
    });
    actions.appendChild(json);
    actions.appendChild(document.createTextNode(' '));
    actions.appendChild(csv);
    actions.appendChild(document.createTextNode(' '));
    var link = el('a', 'Open the GitHub issue');
    link.href = issue.html_url;
    link.target = '_blank';
    link.rel = 'noopener';
    actions.appendChild(link);
    box.appendChild(actions);
    out.appendChild(box);
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    var host = hostOf(form.querySelector('input').value.trim());
    out.textContent = '';
    if (!host) { say('Please type the public web address you analysed, for example https://www.example-news.com.'); return; }
    say('Looking for the latest analysis of ' + host + '…');
    get('/issues?labels=scan-request&state=all&per_page=50').then(function (issues) {
      var issue = issues.filter(function (i) { return !i.pull_request && i.title.toLowerCase().indexOf(host) !== -1; })[0];
      if (!issue) { say('No analysis of ' + host + ' found yet. Send the request above, wait about a minute and press the button again.'); return null; }
      if (issue.state !== 'closed') { say('The analysis of ' + host + ' is still running. Wait a little and press the button again.'); return null; }
      return get('/issues/' + issue.number + '/comments?per_page=20').then(function (comments) {
        var bot = comments.filter(function (c) { return c.user && c.user.login === 'github-actions[bot]'; });
        for (var i = bot.length - 1; i >= 0; i--) {
          var found = MARK.exec(bot[i].body || '');
          if (found) {
            try { show(decode(found[1]), issue); note.hidden = true; return; } catch (e) { /* damaged block: fall through */ }
          }
        }
        if (bot.length) {  // a refusal (limit reached, address refused…): show the bot's own words as text
          out.textContent = '';
          var box = el('div', undefined, 'card');
          box.appendChild(el('p', (bot[bot.length - 1].body || '').replace(/<!--[\s\S]*?-->/g, '').trim().slice(0, 700)));
          out.appendChild(box);
          note.hidden = true;
        } else {
          say('The analysis has no reply yet. Wait a little and press the button again.');
        }
      });
    }).catch(function (e) {
      say(e && e.message === 'limit'
        ? 'GitHub limits how often its public API can be asked from one connection. Try again in a few minutes, or open the issue on GitHub.'
        : 'Could not read the result from GitHub. Try again, or open the issue on GitHub.');
    });
  });
})();
