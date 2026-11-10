from pathlib import Path

import pytest

from standalone_2gis.main import Api


def test_actual_city_ui_and_backend(tmp_path, monkeypatch):
    playwright = pytest.importorskip('playwright.sync_api')
    chrome = Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
    if not chrome.exists(): pytest.skip('Chrome is required for the optional UI check')
    monkeypatch.setattr('standalone_2gis.main.subprocess.Popen', lambda *a, **kw: object())
    api = Api(tmp_path)
    ui = Path(__file__).resolve().parents[1] / 'standalone_2gis/ui/index.html'
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=str(chrome))
        try:
            page = browser.new_page(viewport={'width': 1360, 'height': 900})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.expose_function('loadCities', api.cities)
            page.expose_function('startParser', api.start)
            page.add_init_script('''window.pywebview = {api: {
              cities: () => window.loadCities(),
              start: (...args) => window.startParser(...args),
              state: async () => ({status:'idle', rows:[], counts:{}})
            }};''')
            page.goto(ui.as_uri())
            page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
            page.wait_for_function("document.querySelectorAll('#cityList option').length > 100")
            page.locator('#city').fill('Нижний Новгород')
            assert page.locator('#cityCode').input_value() == 'n_novgorod'
            page.locator('#city').fill('Несуществующий город')
            assert page.locator('#cityCode').input_value() == ''
            page.locator('#city').fill('Тула')
            assert page.locator('#cityCode').input_value() == 'tula'
            page.locator('#cityCode').fill('krasnodar')  # Simulate an old/stale value.
            page.locator('#start').click()
            page.wait_for_function("document.querySelector('#cityCode').value === 'tula'")
            import json
            config = json.loads((api.run_dir / 'config.json').read_text(encoding='utf-8'))
            assert config['city'] == 'Тула' and config['city_code'] == 'tula'
            assert not errors, errors
            page.screenshot(path=str(tmp_path / 'renkai-cities.png'), full_page=True)
        finally:
            browser.close()
