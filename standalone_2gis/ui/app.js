const $ = id => document.getElementById(id);
let apiReady = false;
const labels = {idle:'Ожидает запуска', collecting:'Идёт поиск', done:'Поиск завершён', error:'Ошибка источника', blocked:'Доступ ограничен', cancelled:'Остановлено'};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function render(s) {
  const running = !!s.running, status = labels[s.status] || s.status || labels.idle, rows = s.rows || [], counts = s.counts || {};
  $('status').textContent = status; $('headStatus').textContent = status; $('footerStatus').textContent = status;
  document.querySelector('.status-pill').classList.toggle('running', running);
  document.querySelector('.status-pill').classList.toggle('issue', ['error','blocked'].includes(s.status));
  $('message').textContent = s.message || 'После запуска здесь появится статус поиска. Отсутствие сайта проверяется только по карточке 2ГИС.';
  for (const id of ['all','qualified','website']) $(id).textContent = counts[id] ?? 0;
  $('historySkipped').textContent = s.history?.skipped ?? 0;
  $('progressCount').textContent = `Новых карточек: ${counts.all ?? 0} · Всего в базе: ${s.history?.total ?? 0}`;
  $('progressTrack').classList.toggle('running', running); $('start').disabled = running; $('stop').disabled = !running; $('export').disabled = !rows.length;
  $('count').textContent = $('navCount').textContent = rows.length;
  $('rows').innerHTML = rows.length ? rows.map(r => {
    const source = /^https:\/\/2gis\.ru\/(?:[^/]+\/)?firm\/\d+(?:[/?#].*)?$/i.test(r.sourceUrl || '') ? `<a href="${esc(r.sourceUrl)}" target="_blank" rel="noopener noreferrer">2ГИС ↗</a>` : '—';
    return `<div class="row"><div><b>${esc(r.name)}</b><small>${esc([r.city,r.address].filter(Boolean).join(' · '))}</small></div><div>${esc((r.phones || []).join(' · ') || r.telegram || r.vk || 'Контакт не указан')}</div><div>${source}</div></div>`;
  }).join('') : '<div class="empty"><span class="empty-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 4 4"/></svg></span><b>Подходящих карточек пока нет</b><p>Статус поиска указан выше.</p></div>';
}
async function refresh() { if (!apiReady) return; try { render(await window.pywebview.api.state()); } catch(e) { $('message').textContent = 'Не удалось получить статус: ' + e; } }
window.addEventListener('pywebviewready', async () => {
  apiReady = true;
  try {
    const list = await window.pywebview.api.cities();
    cities.clear();
    list.forEach(c => cities.set(cityKey(c.name), c));
    $('cityList').innerHTML = list.map(c => `<option value="${esc(c.name)}"></option>`).join('');
    syncCity();
  } catch (e) { $('message').textContent = 'Не удалось загрузить справочник городов: ' + e; }
  refresh(); setInterval(refresh, 1800);
});
$('start').addEventListener('click', async () => {
  if (!apiReady) return;
  const r = await window.pywebview.api.start($('city').value, $('cityCode').value, $('limit').value, $('visible').checked);
  if (!r.ok) $('message').textContent = r.error;
  else { $('city').value = r.city; $('cityCode').value = r.city_code; refresh(); }
});
$('stop').addEventListener('click', async () => { await window.pywebview.api.stop(); $('message').textContent = 'Запрошена остановка. Ожидаем завершения текущего действия.'; });
$('export').addEventListener('click', async () => { const r = await window.pywebview.api.export(); $('message').textContent = r.ok ? 'Файлы сохранены: ' + r.path : r.error; });
const cities = new Map();
const cityKey = value => value.trim().toLocaleLowerCase().replace(/ё/g, 'е').replace(/[–—]/g, '-').replace(/\s+/g, ' ');
function syncCity() { const city = cities.get(cityKey($('city').value)); $('cityCode').value = city ? city.code : ''; }
$('city').addEventListener('input', syncCity);
$('city').addEventListener('change', syncCity);
document.querySelectorAll('.nav-link').forEach(link => link.addEventListener('click', () => { document.querySelectorAll('.nav-link').forEach(item => item.classList.remove('active')); link.classList.add('active'); }));
