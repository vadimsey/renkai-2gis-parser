import json
import threading
from types import SimpleNamespace

from standalone_2gis.main import Api, DesktopBridge


def test_real_pywebview_injection_does_not_inspect_native_state(tmp_path, monkeypatch):
    """Exercise the real recursive inspector, not a simulated JS bridge."""
    import webview.util
    api = Api(tmp_path)

    class NativeState:
        @property
        def blocking_native_property(self):
            raise AssertionError('Native objects must never be inspected by pywebview')

    api._window = NativeState()
    api.process = NativeState()
    bridge = DesktopBridge(api)
    scripts = []
    loaded = threading.Event()
    window = SimpleNamespace(
        _js_api=bridge, _functions={}, _expose_lock=threading.Lock(),
        events=SimpleNamespace(before_load=threading.Event(), _pywebviewready=threading.Event(), loaded=loaded),
        run_js=scripts.append,
    )
    monkeypatch.setattr(webview.util, 'load_js_files', lambda *args: ('init', '%(functions)s'))
    webview.util.inject_pywebview('edgechromium', window)
    assert loaded.wait(3), 'Native bridge injection stalled'
    functions = json.loads(scripts[-1])
    assert {f['func'] for f in functions} == {
        'cities', 'start', 'state', 'stop', 'export', 'history_page', 'export_history', 'backup_history'
    }
    assert bridge.cities()
    assert bridge.history_page()['total'] == 0
    assert bridge.stop() == {'ok': False}
