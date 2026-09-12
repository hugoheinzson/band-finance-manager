/* Band Manager – Frontend (ES-Module, kein Build). Alle Daten aus /api (siehe API.md). */

// ---------------------------------------------------------------------------
// Hilfen
// ---------------------------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const eurFmt = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
const eur = (n) => eurFmt.format(n || 0);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const initials = (n) => n ? n.split(/[\s/]+/).filter(Boolean).slice(0, 2).map((s) => s[0]).join('').toUpperCase() : '?';
const fmtDate = (d) => d ? new Date(d + 'T00:00:00').toLocaleDateString('de-DE', { weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric' }) : 'ohne Datum';
const shortDate = (d) => {
  if (!d) return { day: '—', mon: '' };
  const x = new Date(d + 'T00:00:00');
  return { day: String(x.getDate()).padStart(2, '0'), mon: x.toLocaleDateString('de-DE', { month: 'short' }).replace('.', '') };
};
const isPhone = () => window.matchMedia('(max-width:900px)').matches;
const NEXT_FLAG = { open: 'done', done: 'na', na: 'open' };
const STATUS = {
  vorlage: { label: 'Vorlage', cls: '' },
  angebot: { label: 'Angebot', cls: 'accent' },
  bestaetigt: { label: 'Bestätigt', cls: 'warn' },
  abgerechnet: { label: 'Abgerechnet', cls: 'good' },
  abgesagt: { label: 'Abgesagt', cls: 'bad' },
};
const PLAYED = new Set(['bestaetigt', 'abgerechnet']);

let toastTimer;
function toast(msg, isError = false) {
  const t = $('#toast');
  t.textContent = msg; t.classList.toggle('error', isError); t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, isError ? 4200 : 2200);
}

async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error('Server nicht erreichbar');
  }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* leer */ }
  if (!res.ok) throw new Error((data && data.detail) || `${res.status} ${res.statusText}`);
  return data;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
  tab: 'gigs',          // gigs | musicians | stats
  gigId: null,
  filter: 'all',
  gigs: [],             // GigListItem[]
  musicians: [],        // Musician[] (aktive)
  gig: null,            // Gig-Detail
  stats: null,
};
const LS_KEY = 'band-manager:lastGig';

function activeVariant(g = state.gig) {
  if (!g) return null;
  return g.variants.find((v) => v.id === g.active_variant_id) || g.variants[0];
}

// ---------------------------------------------------------------------------
// Laden
// ---------------------------------------------------------------------------
async function loadGigs() { state.gigs = await api('GET', '/api/gigs'); }
async function loadMusicians() { state.musicians = await api('GET', '/api/musicians'); }
async function loadStats() { state.stats = await api('GET', '/api/stats'); }
async function loadGig(id) {
  state.gig = await api('GET', `/api/gigs/${id}`);
  state.gigId = state.gig.id;
  try { localStorage.setItem(LS_KEY, String(id)); } catch { /* egal */ }
}

// ---------------------------------------------------------------------------
// Routing (#/gigs, #/gigs/12, #/musicians, #/stats)
// ---------------------------------------------------------------------------
function parseHash() {
  const h = location.hash.replace(/^#\/?/, '');
  const [tab, id] = h.split('/');
  if (tab === 'musicians' || tab === 'stats') return { tab, gigId: null };
  return { tab: 'gigs', gigId: id ? Number(id) : null };
}
function go(hash) { if (location.hash !== hash) location.hash = hash; else route(); }

function pickDefaultGig() {
  let last = null;
  try { last = Number(localStorage.getItem(LS_KEY)); } catch { /* egal */ }
  if (last && state.gigs.some((g) => g.id === last)) return last;
  const open = state.gigs.find((g) => PLAYED.has(g.status) && g.totals.paid_done < g.totals.items);
  return (open || state.gigs.find((g) => g.status !== 'vorlage') || state.gigs[0])?.id ?? null;
}

async function route() {
  const r = parseHash();
  state.tab = r.tab;
  const main = $('#main');
  main.classList.add('loading');
  try {
    if (r.tab === 'gigs') {
      if (r.gigId == null) {
        if (!isPhone()) {
          const id = pickDefaultGig();
          if (id != null) { main.classList.remove('loading'); return go(`#/gigs/${id}`); }
        }
        state.gig = null; state.gigId = null;
        document.body.dataset.view = 'list';
        renderSidebar(); renderDetail();
      } else {
        document.body.dataset.view = 'detail';
        if (!state.gig || state.gig.id !== r.gigId) {
          renderSidebar();
          await loadGig(r.gigId);
        }
        renderSidebar(); renderDetail();
      }
    } else if (r.tab === 'musicians') {
      document.body.dataset.view = 'musicians';
      renderSidebar();
      await loadMusicians();
      renderMusicians();
    } else {
      document.body.dataset.view = 'stats';
      renderSidebar();
      await loadStats();
      renderStats();
    }
  } catch (e) {
    if (r.tab === 'gigs' && r.gigId != null) {
      toast(e.message === 'Server nicht erreichbar' ? e.message : 'Gig nicht gefunden', true);
      state.gig = null; state.gigId = null;
      return go('#/gigs');
    }
    toast(e.message, true);
    main.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  } finally {
    main.classList.remove('loading');
  }
  $$('[data-nav]').forEach((a) => a.setAttribute('aria-selected', a.dataset.nav === state.tab));
  window.scrollTo(0, 0);
}

// ---------------------------------------------------------------------------
// Sidebar / Gig-Liste
// ---------------------------------------------------------------------------
function gigDot(g) {
  if (g.status === 'vorlage' || g.status === 'abgesagt') return '';
  if (g.status === 'angebot') return 'accent';
  const t = g.totals;
  if (t.rest < 0) return 'bad';
  return t.paid_done >= t.items ? 'good' : 'warn';
}
function gigMeta(g) {
  const t = g.totals;
  if (g.status === 'vorlage') return `Vorlage · ${eur(t.fee)}`;
  if (g.status === 'angebot') return `Angebot · ${eur(t.fee)}`;
  if (g.status === 'abgesagt') return 'Abgesagt';
  if (t.items === 0) return `${eur(t.fee)} · keine Posten`;
  return t.paid_done >= t.items ? `${eur(t.fee)} · alles bezahlt` : `${eur(t.fee)} · ${t.items - t.paid_done} offen`;
}
function filteredGigs() {
  const f = state.filter;
  return state.gigs.filter((g) => {
    if (f === 'all') return true;
    if (f === 'angebot') return g.status === 'angebot';
    if (f === 'vorlage') return g.status === 'vorlage';
    if (f === 'open') return PLAYED.has(g.status) && g.totals.paid_done < g.totals.items;
    return true;
  });
}

function renderSidebar() {
  const el = $('#gigList');
  $$('#filters button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.filter === state.filter));
  const gigs = filteredGigs();
  if (!state.gigs.length) {
    el.innerHTML = '<div class="empty">Noch kein Gig – lege den ersten an.</div>';
    return;
  }
  if (!gigs.length) {
    el.innerHTML = '<div class="empty">Nichts in diesem Filter.</div>';
    return;
  }
  const years = [...new Set(gigs.map((g) => g.year))].sort((a, b) => b - a);
  let html = '';
  for (const y of years) {
    const gs = gigs.filter((g) => g.year === y);
    const sum = state.gigs.filter((g) => g.year === y && PLAYED.has(g.status)).reduce((s, g) => s + g.totals.fee, 0);
    html += `<div class="year"><span>${y}</span><span class="num">${sum ? eur(sum) : ''}</span></div>`;
    for (const g of gs) {
      const sd = shortDate(g.date);
      html += `<a class="gig-item" href="#/gigs/${g.id}" data-gig="${g.id}" aria-current="${g.id === state.gigId}">
        <span class="d">${esc(sd.mon)}<b>${esc(sd.day)}</b></span>
        <span class="t"><span class="n">${esc(g.title)}</span><span class="m">${esc(gigMeta(g))}</span></span>
        <span class="dot ${gigDot(g)}"></span></a>`;
    }
  }
  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Gig-Detail
// ---------------------------------------------------------------------------
function totalsOf(v) {
  // lokal nachrechnen (für optimistische Updates), gleiche Regeln wie Backend
  const items = v.items || [];
  const musicians = items.filter((i) => i.kind === 'musician').reduce((s, i) => s + (+i.amount || 0), 0);
  const costs = items.filter((i) => i.kind === 'cost').reduce((s, i) => s + (+i.amount || 0), 0);
  const cnt = items.filter((i) => (+i.amount || 0) > 0 && i.paid !== 'na');
  return {
    fee: v.fee, musicians, costs, rest: v.fee - musicians - costs, items: cnt.length,
    info_done: cnt.filter((i) => i.info === 'done').length,
    invoice_done: cnt.filter((i) => i.invoice === 'done').length,
    paid_done: cnt.filter((i) => i.paid === 'done').length,
  };
}
function syncListItem() {
  // Sidebar-Eintrag aus dem Detail aktualisieren, ohne neu zu laden
  const g = state.gig; if (!g) return;
  const v = activeVariant(g);
  const li = state.gigs.find((x) => x.id === g.id);
  if (li) Object.assign(li, { title: g.title, date: g.date, venue: g.venue, status: g.status, active_variant_id: v.id, variant_count: g.variants.length, totals: totalsOf(v), year: Number((g.date || g.created_at).slice(0, 4)) });
  renderSidebar();
}

function musicianSelect(item) {
  const opts = [`<option value="" ${item.musician_id ? '' : 'selected'}>unbesetzt</option>`];
  const known = new Set(state.musicians.map((m) => m.id));
  for (const m of state.musicians) opts.push(`<option value="${m.id}" ${m.id === item.musician_id ? 'selected' : ''}>${esc(m.name)}</option>`);
  if (item.musician_id && !known.has(item.musician_id)) opts.push(`<option value="${item.musician_id}" selected>${esc(item.musician_name || '?')}</option>`);
  opts.push('<option value="__new">Neue Person …</option>');
  return `<select class="sel ${item.musician_id ? '' : 'unset'}" data-mus="${item.id}" aria-label="Musiker">${opts.join('')}</select>`;
}

function rowHTML(it, g) {
  const na = it.info === 'na' && it.invoice === 'na' && it.paid === 'na';
  const due = PLAYED.has(g.status) && (+it.amount || 0) > 0;
  // Eigene Zeile (Bandleitung): keine Häkchen – der Server hält sie ohnehin auf „entfällt"
  const self = it.musician_id != null && state.musicians.some((m) => m.id === it.musician_id && m.is_self);
  const tg = (k, label) => self
    ? `<td class="c"><span class="tg-self" aria-label="${label}: entfällt (eigene Zeile)">–</span></td>`
    : `<td class="c"><button class="tg ${it[k] === 'open' && due ? 'due' : ''}" data-tg="${it.id}:${k}" data-s="${it[k]}" aria-label="${label}: ${it[k] === 'done' ? 'erledigt' : it[k] === 'na' ? 'entfällt' : 'offen'}">${it[k] === 'done' ? '✓' : it[k] === 'na' ? '–' : ''}</button></td>`;
  const who = it.kind === 'musician'
    ? `${it.musician_name ? `<span class="avatar">${esc(initials(it.musician_name))}</span>` : ''}${musicianSelect(it)}`
    : `<span class="inline ${it.label ? '' : 'placeholder'}" data-edit="item:${it.id}:label">${esc(it.label || 'wer / woher')}</span>`;
  return `<tr class="${it.paid === 'done' ? 'done' : ''} ${na ? 'na' : ''}" data-item="${it.id}">
    <td class="role"><span class="inline ${it.role ? '' : 'placeholder'}" data-edit="item:${it.id}:role">${esc(it.role || (it.kind === 'cost' ? 'Posten' : 'Rolle'))}</span></td>
    <td class="person">${who}</td>
    <td class="r"><span class="amt num"><input type="number" inputmode="numeric" min="0" step="1" data-amt="${it.id}" value="${it.amount}" aria-label="Betrag"><span>€</span></span></td>
    ${tg('info', 'Über Gage informiert')}${tg('invoice', 'Rechnung liegt vor')}${tg('paid', 'Überwiesen')}
    <td class="note"><span class="inline ${it.note ? '' : 'placeholder'}" data-edit="item:${it.id}:note">${esc(it.note || '+ Notiz')}</span></td>
    <td class="del"><button class="del-btn" data-del="${it.id}" aria-label="Posten entfernen">×</button></td></tr>`;
}

function statusChip(g) {
  const st = STATUS[g.status] || STATUS.angebot;
  return `<span class="chip-select"><span class="chip ${st.cls}">${st.label}</span>
    <select id="statusSel" aria-label="Status">${Object.entries(STATUS).map(([k, s]) => `<option value="${k}" ${k === g.status ? 'selected' : ''}>${s.label}</option>`).join('')}</select></span>`;
}

function renderDetail() {
  const main = $('#main');
  const g = state.gig;
  if (!g) {
    main.innerHTML = state.gigs.length
      ? '<div class="empty">Wähle links einen Gig aus.</div>'
      : '<div class="empty">Noch kein Gig – lege den ersten an.<br><br><button class="btn primary" id="emptyNew">+ Neuer Gig</button></div>';
    $('#emptyNew')?.addEventListener('click', openNewGig);
    return;
  }
  const v = activeVariant(g);
  const t = totalsOf(v);
  v.totals = t;
  const paidPct = t.items ? (100 * t.paid_done) / t.items : 0;
  const invPct = t.items ? (100 * Math.max(0, t.invoice_done - t.paid_done)) / t.items : 0;
  const restCls = t.rest < 0 ? 'bad' : t.rest > 0 ? 'good' : '';
  const mus = v.items.filter((i) => i.kind === 'musician');
  const costs = v.items.filter((i) => i.kind === 'cost');
  const forward = g.status === 'angebot' ? ['bestaetigt', 'Als bestätigt markieren']
    : g.status === 'bestaetigt' ? ['abgerechnet', 'Abrechnung abschließen'] : null;

  main.innerHTML = `
    <a class="back" href="#/gigs">‹ Alle Gigs</a>
    <div class="topbar">
      <div style="min-width:0">
        <h2><span class="inline" data-edit="gig:title">${esc(g.title)}</span></h2>
        <div class="meta">${statusChip(g)}
          <span class="inline" data-edit="gig:date" data-type="date">${esc(fmtDate(g.date))}</span>
          <span>·</span><span class="inline ${g.venue ? '' : 'placeholder'}" data-edit="gig:venue">${esc(g.venue || 'Ort / Anlass')}</span></div>
      </div>
      <div class="actions">
        <button class="btn" id="btnDup">Duplizieren</button>
        <button class="btn icon" id="btnMore" aria-label="Weitere Aktionen">⋯</button>
        ${forward ? `<button class="btn primary" id="btnForward" data-status="${forward[0]}">${forward[1]}</button>` : ''}
      </div>
    </div>

    <div class="scen" role="tablist">
      <span class="eyebrow" style="margin-right:6px">Variante</span>
      ${g.variants.map((x) => `<button role="tab" data-scen="${x.id}" aria-selected="${x.id === v.id}" title="Rechtsklick / lang drücken für Optionen">${esc(x.name)} · ${eur(x.fee)}</button>`).join('')}
      <button class="add" data-scen="new" aria-label="Variante hinzufügen">+</button>
      ${g.variants.length > 1 ? '<span class="hint">Jede Variante hat eigene Gagen pro Posten</span>' : ''}
    </div>

    <section class="summary">
      <div><div class="eyebrow">Gage (netto)</div><div class="gage-input num"><input id="fee" type="number" inputmode="numeric" min="0" step="1" value="${v.fee}" aria-label="Gage"><span>€</span></div><div class="s">Was der Veranstalter zahlt</div></div>
      <div><div class="eyebrow">Musiker</div><div class="v num">${eur(t.musicians)}</div><div class="s">${mus.filter((i) => +i.amount > 0).length} Posten</div></div>
      <div><div class="eyebrow">Kosten</div><div class="v num">${eur(t.costs)}</div><div class="s">${costs.length ? esc(costs.map((c) => c.role).join(', ')) : 'keine Nebenkosten'}</div></div>
      <div><div class="eyebrow">Übrig</div><div class="v num ${restCls}">${t.rest < 0 ? '–' : ''}${eur(Math.abs(t.rest))}</div>
        <div class="s">${t.rest < 0 ? 'über Budget – aus der Bandkasse?' : t.rest > 0 ? 'geht in die Bandkasse' : 'exakt aufgeteilt'}</div></div>
    </section>

    <div class="panel">
      <div class="panel-h"><h3>Besetzung &amp; Gagen</h3>
        <span class="cnt num">${t.info_done}/${t.items} informiert · ${t.invoice_done}/${t.items} Rechnung · <b style="color:${t.items && t.paid_done >= t.items ? 'var(--good)' : 'var(--warn)'}">${t.paid_done}/${t.items} bezahlt</b></span></div>
      <div class="progress" title="bezahlt / Rechnung liegt vor / offen"><i class="p" style="width:${paidPct}%"></i><i class="r" style="width:${invPct}%"></i><i class="i" style="flex:1"></i></div>
      <div class="tbl-wrap"><table>
        <thead><tr><th>Rolle</th><th>Musiker</th><th class="r">Gage</th><th class="c">Info</th><th class="c"><span class="full">Rechnung</span><abbr class="short" title="Rechnung">Rech.</abbr></th><th class="c"><span class="full">Bezahlt</span><abbr class="short" title="Bezahlt">Bez.</abbr></th><th class="note-h">Notiz</th><th></th></tr></thead>
        <tbody>${mus.map((i) => rowHTML(i, g)).join('') || '<tr><td colspan="8" style="color:var(--faint)">Noch keine Besetzung – füge Musiker hinzu.</td></tr>'}</tbody>
        <tfoot><tr><td colspan="2">Summe Musiker</td><td class="r num">${eur(t.musicians)}</td><td colspan="5"></td></tr></tfoot>
      </table></div>
      <div class="addrow"><button data-add="musician">+ Musiker hinzufügen</button></div>
    </div>

    <div class="two">
      <div class="panel">
        <div class="panel-h"><h3>Nebenkosten</h3><span class="cnt">PA, Licht, Fotograf, Bandkasse …</span></div>
        <div class="tbl-wrap"><table>
          <thead><tr><th>Posten</th><th>Wer</th><th class="r">Betrag</th><th class="c">Info</th><th class="c"><span class="full">Rechnung</span><abbr class="short" title="Rechnung">Rech.</abbr></th><th class="c"><span class="full">Bezahlt</span><abbr class="short" title="Bezahlt">Bez.</abbr></th><th class="note-h">Notiz</th><th></th></tr></thead>
          <tbody>${costs.map((i) => rowHTML(i, g)).join('') || '<tr><td colspan="8" style="color:var(--faint)">Keine Nebenkosten erfasst</td></tr>'}</tbody>
        </table></div>
        <div class="addrow"><button data-add="cost">+ Kosten hinzufügen</button></div>
      </div>
      <div class="panel">
        <div class="panel-h"><h3>Notizen</h3><span class="cnt">wird beim Verlassen gespeichert</span></div>
        <div class="notes"><textarea id="notes" placeholder="Absprachen, Fahrtkosten, Besonderheiten …">${esc(g.notes || '')}</textarea></div>
      </div>
    </div>`;
  wireDetail();
}

// ---------------------------------------------------------------------------
// Detail: Interaktion
// ---------------------------------------------------------------------------
function findItem(id) {
  for (const v of state.gig.variants) { const it = (v.items || []).find((i) => i.id === id); if (it) return it; }
  return null;
}

async function patchItem(id, patch, { rerender = true } = {}) {
  const it = findItem(id); if (!it) return;
  const before = { ...it };
  Object.assign(it, patch);
  if (rerender) { renderDetail(); } else { refreshSummary(); }
  syncListItem();
  try {
    const fresh = await api('PATCH', `/api/items/${id}`, patch);
    Object.assign(it, fresh);
    if (!rerender) refreshSummary();
    else if (patch.musician_id !== undefined) renderDetail();
  } catch (e) {
    Object.assign(it, before);
    renderDetail(); syncListItem();
    toast(e.message, true);
  }
}

function refreshSummary() {
  // nur Zahlen/Fortschritt aktualisieren, Fokus bleibt im Feld
  const g = state.gig; const v = activeVariant(g); const t = totalsOf(v); v.totals = t;
  const cells = $$('.summary .v');
  if (cells.length === 3) {
    cells[0].textContent = eur(t.musicians);
    cells[1].textContent = eur(t.costs);
    cells[2].textContent = `${t.rest < 0 ? '–' : ''}${eur(Math.abs(t.rest))}`;
    cells[2].className = `v num ${t.rest < 0 ? 'bad' : t.rest > 0 ? 'good' : ''}`;
    const s = cells[2].nextElementSibling; if (s) s.textContent = t.rest < 0 ? 'über Budget – aus der Bandkasse?' : t.rest > 0 ? 'geht in die Bandkasse' : 'exakt aufgeteilt';
  }
  const foot = $('tfoot td.num'); if (foot) foot.textContent = eur(t.musicians);
  const cnt = $('.panel-h .cnt.num');
  if (cnt) cnt.innerHTML = `${t.info_done}/${t.items} informiert · ${t.invoice_done}/${t.items} Rechnung · <b style="color:${t.items && t.paid_done >= t.items ? 'var(--good)' : 'var(--warn)'}">${t.paid_done}/${t.items} bezahlt</b>`;
  const p = $('.progress');
  if (p) { p.querySelector('.p').style.width = `${t.items ? (100 * t.paid_done) / t.items : 0}%`; p.querySelector('.r').style.width = `${t.items ? (100 * Math.max(0, t.invoice_done - t.paid_done)) / t.items : 0}%`; }
  const tab = $(`[data-scen="${v.id}"]`); if (tab) tab.textContent = `${v.name} · ${eur(v.fee)}`;
}

async function patchGig(patch) {
  const g = state.gig; const before = { ...g };
  Object.assign(g, patch);
  renderDetail(); syncListItem();
  try {
    const fresh = await api('PATCH', `/api/gigs/${g.id}`, patch);
    Object.assign(g, fresh);
    renderDetail(); syncListItem();
  } catch (e) {
    Object.assign(g, before); renderDetail(); syncListItem(); toast(e.message, true);
  }
}

/** Span → Input; speichert bei Enter/Blur, Escape bricht ab. */
function startInlineEdit(span) {
  const [scope, a, b] = span.dataset.edit.split(':');
  const type = span.dataset.type || 'text';
  let current;
  if (scope === 'gig') current = state.gig[a] || '';
  else current = findItem(Number(a))?.[b] || '';
  const inp = document.createElement('input');
  inp.className = 'inline-input'; inp.type = type; inp.value = current;
  inp.style.width = type === 'date' ? 'auto' : `${Math.max(6, String(current).length + 2)}ch`;
  span.replaceWith(inp); inp.focus(); if (type !== 'date') inp.select();
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    const val = inp.value.trim();
    if (!save || val === current) { renderDetail(); return; }
    if (scope === 'gig') {
      if (a === 'title' && !val) { toast('Titel darf nicht leer sein', true); renderDetail(); return; }
      await patchGig({ [a]: a === 'date' ? (val || null) : val });
    } else {
      await patchItem(Number(a), { [b]: val });
    }
  };
  inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); finish(true); } if (e.key === 'Escape') finish(false); });
  inp.addEventListener('blur', () => finish(true));
}

function wireDetail() {
  const g = state.gig; const v = activeVariant(g);

  // Inline-Felder
  $$('[data-edit]').forEach((s) => s.addEventListener('click', () => startInlineEdit(s)));

  // Status
  $('#statusSel')?.addEventListener('change', (e) => patchGig({ status: e.target.value }));
  $('#btnForward')?.addEventListener('click', (e) => patchGig({ status: e.currentTarget.dataset.status }));

  // Gage der Variante
  const fee = $('#fee');
  fee?.addEventListener('change', async () => {
    const val = Math.max(0, parseInt(fee.value, 10) || 0); const before = v.fee;
    v.fee = val; refreshSummary(); syncListItem();
    try { Object.assign(v, await api('PATCH', `/api/variants/${v.id}`, { fee: val }), { items: v.items }); refreshSummary(); }
    catch (e) { v.fee = before; renderDetail(); syncListItem(); toast(e.message, true); }
  });

  // Toggles
  $$('[data-tg]').forEach((b) => b.addEventListener('click', () => {
    const [id, k] = b.dataset.tg.split(':'); const it = findItem(Number(id));
    patchItem(Number(id), { [k]: NEXT_FLAG[it[k]] });
  }));

  // Beträge (kein Rerender während des Tippens)
  $$('[data-amt]').forEach((inp) => {
    inp.addEventListener('change', () => patchItem(Number(inp.dataset.amt), { amount: Math.max(0, parseInt(inp.value, 10) || 0) }, { rerender: false }));
    inp.addEventListener('focus', () => inp.select());
  });

  // Musiker-Auswahl
  $$('[data-mus]').forEach((sel) => sel.addEventListener('change', async () => {
    const id = Number(sel.dataset.mus); const it = findItem(id);
    if (sel.value === '__new') {
      const m = await openMusicianDialog(null, { role: it.role });
      if (m) { await patchItem(id, { musician_id: m.id, amount: it.amount || m.default_fee || 0 }); }
      else renderDetail();
      return;
    }
    const mid = sel.value ? Number(sel.value) : null;
    const m = state.musicians.find((x) => x.id === mid);
    const patch = { musician_id: mid };
    if (m && !(+it.amount) && m.default_fee) patch.amount = m.default_fee;
    if (m && !it.role && m.role) patch.role = m.role;
    it.musician_name = m ? m.name : null;
    patchItem(id, patch);
  }));

  // Löschen
  $$('[data-del]').forEach((b) => b.addEventListener('click', async () => {
    const id = Number(b.dataset.del); const it = findItem(id);
    const label = it.musician_name || it.role || 'Posten';
    if (!confirm(`„${label}“ aus dieser Variante entfernen?`)) return;
    const idx = v.items.indexOf(it); v.items.splice(idx, 1); renderDetail(); syncListItem();
    try { await api('DELETE', `/api/items/${id}`); toast('Posten entfernt'); }
    catch (e) { v.items.splice(idx, 0, it); renderDetail(); syncListItem(); toast(e.message, true); }
  }));

  // Hinzufügen
  $$('[data-add]').forEach((b) => b.addEventListener('click', async () => {
    const kind = b.dataset.add;
    const body = kind === 'cost' ? { kind: 'cost', role: '', label: '', amount: 0 } : { kind: 'musician', role: '', amount: 0 };
    try {
      const it = await api('POST', `/api/variants/${v.id}/items`, body);
      v.items.push(it); renderDetail(); syncListItem();
      const span = $(`[data-edit="item:${it.id}:role"]`); if (span) { span.scrollIntoView({ block: 'nearest' }); startInlineEdit(span); }
    } catch (e) { toast(e.message, true); }
  }));

  // Varianten
  $$('[data-scen]').forEach((b) => {
    if (b.dataset.scen === 'new') { b.addEventListener('click', () => openVariantDialog(null)); return; }
    const vid = Number(b.dataset.scen);
    b.addEventListener('click', () => { if (vid !== g.active_variant_id) patchGig({ active_variant_id: vid }); });
    b.addEventListener('contextmenu', (e) => { e.preventDefault(); variantMenu(vid, e.clientX, e.clientY); });
    let timer;
    b.addEventListener('touchstart', (e) => { timer = setTimeout(() => { const t = e.touches[0]; variantMenu(vid, t.clientX, t.clientY); }, 550); }, { passive: true });
    ['touchend', 'touchmove', 'touchcancel'].forEach((ev) => b.addEventListener(ev, () => clearTimeout(timer)));
  });

  // Notizen
  const notes = $('#notes');
  notes?.addEventListener('blur', async () => {
    const val = notes.value; if (val === (g.notes || '')) return;
    const before = g.notes; g.notes = val;
    try { await api('PATCH', `/api/gigs/${g.id}`, { notes: val }); toast('Notizen gespeichert'); }
    catch (e) { g.notes = before; notes.value = before || ''; toast(e.message, true); }
  });

  // Aktionen
  $('#btnDup')?.addEventListener('click', async () => {
    try {
      const n = await api('POST', `/api/gigs/${g.id}/duplicate`, { title: `${g.title} (Kopie)`, status: 'angebot' });
      await loadGigs(); toast('Gig dupliziert'); go(`#/gigs/${n.id}`);
    } catch (e) { toast(e.message, true); }
  });
  $('#btnMore')?.addEventListener('click', (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    showMenu(r.right, r.bottom + 4, [
      { label: 'Als Vorlage speichern', run: async () => {
        try { await api('POST', `/api/gigs/${g.id}/duplicate`, { title: `${g.title} (Vorlage)`, status: 'vorlage' }); await loadGigs(); renderSidebar(); toast('Vorlage angelegt'); }
        catch (err) { toast(err.message, true); }
      } },
      { label: 'Variante umbenennen', run: () => openVariantDialog(v) },
      { label: 'Gig löschen', danger: true, run: async () => {
        if (!confirm(`„${g.title}“ endgültig löschen? Alle Varianten und Posten gehen verloren.`)) return;
        try { await api('DELETE', `/api/gigs/${g.id}`); state.gig = null; state.gigId = null; try { localStorage.removeItem(LS_KEY); } catch { /* egal */ } await loadGigs(); toast('Gig gelöscht'); go('#/gigs'); }
        catch (err) { toast(err.message, true); }
      } },
    ], { alignRight: true });
  });
}

function variantMenu(vid, x, y) {
  const g = state.gig; const v = g.variants.find((z) => z.id === vid);
  const items = [{ label: 'Umbenennen / Gage ändern', run: () => openVariantDialog(v) }];
  if (g.variants.length > 1 && vid !== g.active_variant_id) {
    items.push({ label: 'Variante löschen', danger: true, run: async () => {
      if (!confirm(`Variante „${v.name}“ löschen?`)) return;
      try { await api('DELETE', `/api/variants/${vid}`); g.variants = g.variants.filter((z) => z.id !== vid); renderDetail(); syncListItem(); toast('Variante gelöscht'); }
      catch (e) { toast(e.message, true); }
    } });
  } else if (g.variants.length > 1) {
    items.push({ label: 'Zum Löschen erst andere Variante aktivieren', run: () => {} });
  }
  showMenu(x, y, items);
}

function showMenu(x, y, items, { alignRight = false } = {}) {
  closeMenu();
  const bd = document.createElement('div'); bd.className = 'menu-backdrop';
  const m = document.createElement('div'); m.className = 'menu'; m.setAttribute('role', 'menu');
  for (const it of items) {
    const b = document.createElement('button'); b.textContent = it.label; b.setAttribute('role', 'menuitem');
    if (it.danger) b.classList.add('danger');
    b.addEventListener('click', () => { closeMenu(); it.run(); });
    m.appendChild(b);
  }
  document.body.append(bd, m);
  const w = m.offsetWidth, h = m.offsetHeight;
  let left = alignRight ? x - w : x; let top = y;
  left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
  top = Math.max(8, Math.min(top, window.innerHeight - h - 8));
  m.style.left = `${left}px`; m.style.top = `${top}px`;
  bd.addEventListener('click', closeMenu);
  m.querySelector('button')?.focus();
}
function closeMenu() { $$('.menu, .menu-backdrop').forEach((e) => e.remove()); }
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });

// ---------------------------------------------------------------------------
// Dialoge
// ---------------------------------------------------------------------------
function dialogSubmit(dlg, form) {
  return new Promise((resolve) => {
    const onSubmit = (e) => { e.preventDefault(); cleanup(); resolve(new FormData(form)); };
    const onCancel = () => { cleanup(); resolve(null); };
    const cleanup = () => { form.removeEventListener('submit', onSubmit); dlg.removeEventListener('close', onCancel); $$('[data-close]', dlg).forEach((b) => b.removeEventListener('click', closeIt)); };
    const closeIt = () => dlg.close();
    form.addEventListener('submit', onSubmit); dlg.addEventListener('close', onCancel);
    $$('[data-close]', dlg).forEach((b) => b.addEventListener('click', closeIt));
    dlg.showModal();
  });
}

async function openNewGig() {
  const dlg = $('#dlgGig'); const form = $('#formGig');
  form.reset();
  const sel = $('#ngTemplate');
  sel.innerHTML = '<option value="">Leer (ohne Posten)</option>';
  try {
    if (!state.stats) await loadStats();
    for (const t of state.stats.templates) sel.innerHTML += `<option value="${t.id}">${esc(t.title)} · ${eur(t.totals.fee)}</option>`;
    // Auch abgeschlossene Gigs als Ausgangspunkt anbieten
    const recent = state.gigs.filter((g) => g.status !== 'vorlage').slice(0, 8);
    if (recent.length) sel.innerHTML += `<optgroup label="Wie letzter Gig">${recent.map((g) => `<option value="${g.id}">${esc(g.title)} ${g.year}</option>`).join('')}</optgroup>`;
  } catch { /* Vorlagen sind optional */ }
  sel.addEventListener('change', () => {
    const t = [...(state.stats?.templates || []), ...state.gigs].find((g) => g.id === Number(sel.value));
    if (t && !$('#ngFee').value) $('#ngFee').value = t.totals.fee;
  }, { once: false });
  const fd = await dialogSubmit(dlg, form);
  if (!fd) return;
  const body = {
    title: fd.get('title').trim(), date: fd.get('date') || null, venue: fd.get('venue').trim(),
    fee: parseInt(fd.get('fee'), 10) || 0, status: fd.get('status'),
  };
  if (fd.get('template_gig_id')) body.template_gig_id = Number(fd.get('template_gig_id'));
  try {
    const g = await api('POST', '/api/gigs', body);
    dlg.close(); await loadGigs(); state.stats = null; toast('Gig angelegt'); go(`#/gigs/${g.id}`);
  } catch (e) { toast(e.message, true); }
}

async function openVariantDialog(existing) {
  const dlg = $('#dlgVariant'); const form = $('#formVariant'); const g = state.gig; const cur = activeVariant(g);
  form.reset();
  $('#dlgVariantTitle').textContent = existing ? 'Variante bearbeiten' : 'Neue Variante';
  $('#vaSubmit').textContent = existing ? 'Speichern' : 'Anlegen';
  $('#vaName').value = existing ? existing.name : ''; $('#vaFee').value = existing ? existing.fee : cur.fee;
  $('#vaCopyWrap').hidden = !!existing; $('#vaCopy').checked = true;
  const fd = await dialogSubmit(dlg, form);
  if (!fd) return;
  const name = fd.get('name').trim(); const fee = parseInt(fd.get('fee'), 10) || 0;
  try {
    if (existing) {
      const fresh = await api('PATCH', `/api/variants/${existing.id}`, { name, fee });
      Object.assign(existing, fresh, { items: existing.items }); dlg.close(); renderDetail(); syncListItem(); toast('Variante gespeichert');
    } else {
      const body = { name, fee }; if (fd.get('copy')) body.copy_from_variant_id = cur.id;
      const nv = await api('POST', `/api/gigs/${g.id}/variants`, body);
      nv.items = nv.items || []; g.variants.push(nv); dlg.close();
      await patchGig({ active_variant_id: nv.id }); toast('Variante angelegt');
    }
  } catch (e) { toast(e.message, true); }
}

/** Musiker anlegen/bearbeiten. Gibt den gespeicherten Musiker zurück (oder null). */
async function openMusicianDialog(existing, defaults = {}) {
  const dlg = $('#dlgMusician'); const form = $('#formMusician');
  form.reset();
  $('#dlgMusicianTitle').textContent = existing ? existing.name : 'Neue Person';
  $('#muSubmit').textContent = existing ? 'Speichern' : 'Anlegen';
  const set = (id, v) => { $(id).value = v ?? ''; };
  set('#muName', existing?.name); set('#muRole', existing?.role ?? defaults.role); set('#muFee', existing?.default_fee ?? '');
  set('#muPhone', existing?.phone); set('#muEmail', existing?.email); set('#muIban', existing?.iban); set('#muNotes', existing?.notes);
  $('#muSelf').checked = !!existing?.is_self;
  const deact = $('#muDeactivate'); deact.hidden = !existing || existing.active === false;
  let deactivated = false;
  const onDeact = async () => {
    if (!confirm(`${existing.name} deaktivieren? Bisherige Gigs bleiben erhalten.`)) return;
    try { await api('DELETE', `/api/musicians/${existing.id}`); deactivated = true; dlg.close(); toast(`${existing.name} deaktiviert`); }
    catch (e) { toast(e.message, true); }
  };
  deact.addEventListener('click', onDeact);
  const fd = await dialogSubmit(dlg, form);
  deact.removeEventListener('click', onDeact);
  if (deactivated) { await loadMusicians(); if (state.tab === 'musicians') renderMusicians(); return null; }
  if (!fd) return null;
  const body = {
    name: fd.get('name').trim(), role: fd.get('role').trim(), default_fee: parseInt(fd.get('default_fee'), 10) || 0,
    email: fd.get('email').trim(), phone: fd.get('phone').trim(), iban: fd.get('iban').replace(/\s+/g, '').toUpperCase(), notes: fd.get('notes').trim(),
    is_self: fd.get('is_self') === 'on',
  };
  try {
    const m = existing ? await api('PATCH', `/api/musicians/${existing.id}`, body) : await api('POST', '/api/musicians', body);
    dlg.close(); await loadMusicians(); toast(existing ? 'Gespeichert' : `${m.name} angelegt`);
    if (state.tab === 'musicians') renderMusicians();
    return m;
  } catch (e) { toast(e.message, true); return null; }
}

// ---------------------------------------------------------------------------
// Musiker-Seite
// ---------------------------------------------------------------------------
let showInactive = false;
function renderMusicians() {
  const main = $('#main'); const list = state.musicians;
  main.innerHTML = `<div class="topbar"><div><h2>Musiker &amp; Crew</h2><div class="meta">${list.filter((m) => m.active).length} aktiv · Standardgagen werden beim Besetzen vorgeschlagen</div></div>
    <div class="actions"><button class="btn" id="toggleInactive">${showInactive ? 'Inaktive ausblenden' : 'Inaktive zeigen'}</button><button class="btn primary" id="newMusician">+ Person</button></div></div>
    ${list.length ? `<div class="people">${list.map((p) => `<button class="person-card ${p.active ? '' : 'inactive'}" data-person="${p.id}"><span class="avatar">${esc(initials(p.name))}</span><div>
      <h3>${esc(p.name)}</h3><div class="role">${esc(p.role || '—')}${p.is_self ? ' · das bin ich' : ''}${p.active ? '' : ' · inaktiv'}</div>
      <div class="kv"><span>Standardgage</span><b class="num">${p.default_fee ? eur(p.default_fee) : '—'}</b><span>Gigs</span><b class="num">${p.gig_count ?? 0}</b><span>IBAN</span>${p.iban ? '<b>hinterlegt</b>' : '<b class="iban">fehlt</b>'}</div>
    </div></button>`).join('')}</div>` : '<div class="empty">Noch niemand angelegt – füge die erste Person hinzu.</div>'}`;
  $('#newMusician').addEventListener('click', () => openMusicianDialog(null));
  $('#toggleInactive').addEventListener('click', async () => {
    showInactive = !showInactive;
    state.musicians = await api('GET', showInactive ? '/api/musicians?all=true' : '/api/musicians');
    renderMusicians();
  });
  $$('[data-person]').forEach((b) => b.addEventListener('click', () => {
    const m = state.musicians.find((x) => x.id === Number(b.dataset.person));
    openMusicianDialog(m);
  }));
}

// ---------------------------------------------------------------------------
// Statistik
// ---------------------------------------------------------------------------
function renderStats() {
  const main = $('#main'); const s = state.stats;
  const years = s.years.slice().sort((a, b) => a.year - b.year);
  const max = Math.max(1, ...years.map((y) => y.fee_total));
  const thisYear = new Date().getFullYear();
  const open = s.open_payments;
  const openSum = open.reduce((a, o) => a + o.amount, 0);
  main.innerHTML = `<div class="topbar"><div><h2>Statistik</h2><div class="meta">Gagen nach Jahr · offene Auszahlungen · Bandkasse</div></div></div>
  <div class="two">
    <div class="panel"><div class="panel-h"><h3>Gesamtgage pro Jahr</h3><span class="cnt">bestätigte &amp; abgerechnete Gigs</span></div>
      <div class="chart">${years.length ? `<div class="bars">${years.map((y) => `<div class="bar"><b class="num">${eur(y.fee_total)}</b><i class="${y.year >= thisYear ? 'small' : ''}" style="height:${Math.max(4, (150 * y.fee_total) / max)}px" title="${y.gigs} Gigs"></i><span>${y.year} · ${y.gigs} Gig${y.gigs === 1 ? '' : 's'}</span></div>`).join('')}</div>` : '<div class="empty">Noch keine gespielten Gigs.</div>'}</div>
    </div>
    <div class="panel"><div class="panel-h"><h3>Noch zu überweisen</h3><span class="chip ${open.length ? 'warn' : 'good'}">${open.length} Posten · ${eur(openSum)}</span></div>
      <div class="stat-list"><span class="h">Wer</span><span class="h">Gig</span><span class="h" style="text-align:right">Betrag</span>
      ${open.map((o) => `<a class="rowbtn" href="#/gigs/${o.gig_id}"><span><span class="avatar">${esc(initials(o.who))}</span>${esc(o.who)}${o.role && o.role !== o.who ? ` <small class="rolesub">· ${esc(o.role)}</small>` : ''}${o.invoice === 'done' ? ' <span class="chip good" style="font-size:10px">Rechnung da</span>' : ''}</span><span style="color:var(--muted)">${esc(o.gig_title)}${o.gig_date ? ` · ${new Date(o.gig_date + 'T00:00:00').toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: '2-digit' })}` : ''}</span><b class="num" style="text-align:right">${eur(o.amount)}</b></a>`).join('')}
      ${open.length ? '' : '<span style="grid-column:1/-1;color:var(--faint);padding:8px 0">Alles ausgezahlt 🎉</span>'}</div>
    </div>
  </div>
  <div class="panel"><div class="panel-h"><h3>Mein Anteil &amp; Bandkasse</h3><span class="cnt">je Jahr, nur gespielte Gigs · Rest = Summe „Übrig“ → Bandkasse</span></div>
    <div class="stat-list" style="grid-template-columns:1fr auto auto auto">
      <span class="h">Jahr</span><span class="h" style="text-align:right">Mein Anteil</span><span class="h" style="text-align:right">Offen</span><span class="h" style="text-align:right">Rest</span>
      ${years.slice().reverse().map((y) => `<span>${y.year}</span><b class="num" style="text-align:right">${s.self_musician_id ? eur(y.self_total) : '–'}</b><span class="num" style="text-align:right;color:${y.open_items ? 'var(--warn)' : 'var(--muted)'}">${y.open_items ? `${y.open_items} Posten` : '–'}</span><b class="num" style="text-align:right;color:${y.rest_total < 0 ? 'var(--bad)' : 'var(--good)'}">${y.rest_total < 0 ? '–' : '+'}${eur(Math.abs(y.rest_total))}</b>`).join('')}
      ${years.length ? `<span style="font-weight:700;border-top:1px solid var(--line);padding-top:6px">Gesamt</span><b class="num" style="text-align:right;border-top:1px solid var(--line);padding-top:6px">${s.self_musician_id ? eur(years.reduce((a, y) => a + y.self_total, 0)) : '–'}</b><span style="border-top:1px solid var(--line)"></span><b class="num" style="text-align:right;border-top:1px solid var(--line);padding-top:6px">${(() => { const r = years.reduce((a, y) => a + y.rest_total, 0); return `${r < 0 ? '–' : '+'}${eur(Math.abs(r))}`; })()}</b>` : ''}
      ${s.self_musician_id ? '' : '<span style="grid-column:1/-1;color:var(--faint);font-size:12px;padding-top:6px">„Mein Anteil“ erscheint, sobald du dich unter Musiker als „das bin ich“ markierst.</span>'}
    </div></div>
  <p class="draft-note">Auswertungen ergeben sich automatisch aus den Gig-Daten.</p>`;
}

// ---------------------------------------------------------------------------
// Globale Events & Start
// ---------------------------------------------------------------------------
$('#filters').addEventListener('click', (e) => {
  const b = e.target.closest('[data-filter]'); if (!b) return;
  state.filter = b.dataset.filter; renderSidebar();
});
$('#newGig').addEventListener('click', openNewGig);
window.addEventListener('hashchange', route);
window.matchMedia('(max-width:900px)').addEventListener('change', () => route());

// Beim Zurückkehren in die App (PWA) Daten auffrischen
document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState !== 'visible') return;
  try {
    await loadGigs();
    if (state.gig) await loadGig(state.gig.id);
    if (state.tab === 'gigs') { renderSidebar(); renderDetail(); }
  } catch { /* still */ }
});

/** Band-Name & Monogramm kommen vom Server (/api/config) – nichts Persönliches im Code. */
async function loadConfig() {
  try {
    const c = await api('GET', '/api/config');
    const app = c.app_name || 'Band Manager';
    const band = c.band_name || '';
    $('#brandName').textContent = app;
    $('#brandSub').textContent = band ? `${band} · Budget & Auszahlung` : 'Budget & Auszahlung';
    if (c.initials) $('#brandMark').textContent = c.initials;
    document.title = band ? `${app} · ${band}` : app;
  } catch { /* Fallback-Texte aus index.html bleiben */ }
}

async function start() {
  const main = $('#main'); main.innerHTML = '<div class="empty">Lade …</div>';
  loadConfig();
  try {
    await Promise.all([loadGigs(), loadMusicians()]);
  } catch (e) {
    main.innerHTML = `<div class="empty">${esc(e.message)}<br><br><button class="btn" onclick="location.reload()">Neu laden</button></div>`;
    $('#gigList').innerHTML = '';
    return;
  }
  if (!location.hash) location.replace(isPhone() ? '#/gigs' : `#/gigs/${pickDefaultGig() ?? ''}`.replace(/\/$/, ''));
  await route();
}
start();

if ('serviceWorker' in navigator && location.protocol === 'https:') {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}
