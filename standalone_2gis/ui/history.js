let databasePage = 1, databaseRequest = 0, databaseTimer;
const historyStatus = {qualified: 'Подходит', website: 'С сайтом'};
function databaseFilters() { return [$('databaseSearch').value, $('databaseFilter').value]; }
function safeLink(url, label) {
  try { const u = new URL(url); if (!['https:', 'http:'].includes(u.protocol)) return esc(url); }
  catch { return esc(url || '—'); }
  return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label || url)}</a>`;
}
async function loadDatabase(page = databasePage) {
  if (!apiReady) return;
  const request = ++databaseRequest;
  $('databaseLoading').textContent = 'Загрузка…';
  try {
    const data = await window.pywebview.api.history_page(...databaseFilters(), page);
    if (request !== databaseRequest) return;
    databasePage = data.page;
    $('databaseTotal').textContent = `Всего сохранено: ${data.counts.all} · Подходят: ${data.counts.qualified} · С сайтом: ${data.counts.website}`;
    $('databaseCount').textContent = data.total;
    $('databasePagination').textContent = `Страница ${data.page} из ${data.pages} · Найдено: ${data.total}`;
    $('databasePrev').disabled = data.page <= 1;
    $('databaseNext').disabled = data.page >= data.pages;
    $('databaseDownload').disabled = !data.total;
    $('databaseRows').innerHTML = data.rows.length ? data.rows.map(r => {
      const websites = r.websites || (r.card_check || {}).websites || [];
      const links = (Array.isArray(websites) ? websites : [websites]).map(v => safeLink(v)).join('<br>');
      const date = new Date(r._first_seen * 1000).toLocaleDateString('ru-RU');
      return `<tr><td><b>${esc(r.name)}</b><small>${esc([r.city, r.address].filter(Boolean).join(' · '))}</small></td><td>${esc((r.phones || []).join(', ') || 'Телефон не указан')}<small>${r.telegram ? safeLink(r.telegram, 'Telegram ↗') : ''}</small></td><td><span class="database-status ${r._history_status}">${historyStatus[r._history_status]}</span><small>${links || (r._history_status === 'website' ? 'Сайт отмечен в карточке' : 'Сайт не обнаружен в 2ГИС')}</small></td><td>${safeLink(r.sourceUrl, '2ГИС ↗')}<small>${esc(date)}</small></td></tr>`;
    }).join('') : '<tr><td colspan="4" class="database-empty">Ничего не найдено. Измените запрос или фильтр.</td></tr>';
    $('databaseLoading').textContent = '';
    $('databaseView').dataset.loaded = 'true';
  } catch (e) { if (request === databaseRequest) $('databaseLoading').textContent = 'Не удалось загрузить базу: ' + e; }
}
document.querySelectorAll('.nav-link').forEach(link => link.addEventListener('click', () => {
  const database = link.hash === '#database';
  $('searchView').hidden = database;
  $('databaseView').hidden = !database;
  if (database) loadDatabase();
}));
$('databaseSearch').addEventListener('input', () => { clearTimeout(databaseTimer); ++databaseRequest; databaseTimer = setTimeout(() => loadDatabase(1), 250); });
$('databaseFilter').addEventListener('change', () => loadDatabase(1));
$('databaseRefresh').addEventListener('click', () => loadDatabase());
$('databasePrev').addEventListener('click', () => loadDatabase(databasePage - 1));
$('databaseNext').addEventListener('click', () => loadDatabase(databasePage + 1));
async function saveDatabase(button, backup) {
  if (!apiReady) return;
  button.disabled = true;
  $('databaseNotice').textContent = 'Выберите, куда сохранить файл…';
  try {
    const result = backup ? await window.pywebview.api.backup_history() : await window.pywebview.api.export_history(...databaseFilters(), $('databaseFormat').value);
    $('databaseNotice').textContent = result.cancelled ? 'Сохранение отменено.' : result.ok ? `Сохранено${result.count === undefined ? '' : ' карточек: ' + result.count}. ${result.path}` : 'Не удалось сохранить: ' + result.error;
  } catch (e) { $('databaseNotice').textContent = 'Не удалось сохранить: ' + e; }
  finally { button.disabled = false; }
}
$('databaseDownload').addEventListener('click', () => saveDatabase($('databaseDownload'), false));
$('databaseBackup').addEventListener('click', () => saveDatabase($('databaseBackup'), true));
