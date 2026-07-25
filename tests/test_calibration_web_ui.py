from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

from pathlib import Path


def test_candidate_update_uses_explicit_confirmation_checkbox() -> None:
    app_js = (
        Path(__file__).parents[1] / "src" / "sqvm" / "web" / "static" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'id="dialog-confirm-candidates" type="checkbox"' in app_js
    assert 'confirmation_phrase: phrase' in app_js
    assert 'confirmButton.disabled = true' in app_js
    assert 'confirmButton.disabled = !confirmation.checked' in app_js
    assert 'confirmButton.disabled = false' in app_js
    assert 'confirmation_phrase: document.querySelector("#dialog-confirmation").value' not in app_js


def test_rabi_detail_renders_published_projection_fields() -> None:
    app_js = (
        Path(__file__).parents[1] / "src" / "sqvm" / "web" / "static" / "app.js"
    ).read_text(encoding="utf-8")

    assert "const rabi = detail.rabi_detail || {};" in app_js
    assert "扫描步进" in app_js
    assert "父配置" in app_js
    assert "QCIS source" in app_js
    assert "相位审计摘要" in app_js
    assert "当前 / 候选" in app_js
    assert "installUnifiedPlots(detail.plot_specs || [], routeContext)" in app_js
    assert 'calibration_scan: "校准扫描"' in app_js


def test_storage_views_have_recoverable_actions_and_mobile_safe_table() -> None:
    root = Path(__file__).parents[1] / "src" / "sqvm" / "web" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    app_js = (root / "app.js").read_text(encoding="utf-8")
    styles = (root / "styles.css").read_text(encoding="utf-8")

    assert 'href="#/storage"' in html and 'href="#/trash"' in html
    assert 'api("/api/v1/experiment-storage")' in app_js
    assert 'api("/api/v1/experiment-trash")' in app_js
    assert 'data-storage-action="' in app_js
    assert 'id="dialog-confirm-trash" type="checkbox"' in app_js
    assert 'experiment-trash/${encodeURIComponent(item.run_id)}/restore' in app_js
    assert "/purge" not in app_js
    assert '(item.allowed_actions || []).includes(action)' in app_js
    assert '保留状态：${statusText(item.retention_state)}' in app_js
    assert '引用：${item.reference_count}' in app_js
    assert '"trash"' in app_js
    assert '"trashed"' not in app_js
    assert ".storage-table-wrap { overflow-x: auto;" in styles
    assert ".storage-capacity { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in styles
