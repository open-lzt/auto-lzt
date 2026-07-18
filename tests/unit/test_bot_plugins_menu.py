"""Bot plugin screens — callback_data budget + typed rendering (T4)."""

from __future__ import annotations

from app.bot.handlers.plugins import (
    PluginCardScreen,
    PluginCb,
    PluginMenuScreen,
    PluginSettingsScreen,
    _Action,
)
from app.plugin_runtime.dtos import (
    AvailablePlugin,
    InstalledPluginView,
    PluginCatalogView,
    PluginTogglesView,
)

_CATALOG = PluginCatalogView(
    available=[
        AvailablePlugin(name="alpha", version="1.0.0", description="A"),
        AvailablePlugin(name="beta", version="2.0.0", description="B"),
    ],
    installed=[InstalledPluginView(name="alpha", version="1.0.0")],
)


def _labels(markup: object) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]  # type: ignore[attr-defined]


def test_callback_data_fits_64_bytes() -> None:
    longest = "a-really-long-plugin-name-of-about-forty-c"  # ~42 chars
    packed = PluginCb(action=_Action.INSTALL, name=longest).pack()
    assert len(packed.encode()) <= 64


def test_menu_lists_installed_and_available_once_plus_settings() -> None:
    assert "Плагины" in PluginMenuScreen.text(_CATALOG)
    labels = _labels(PluginMenuScreen.keyboard(_CATALOG))
    assert any("alpha" in b for b in labels)
    assert any("beta" in b for b in labels)
    assert sum("alpha" in b for b in labels) == 1  # installed alpha not double-listed
    assert any("Настройки" in b for b in labels)


def test_menu_note_appears_in_text() -> None:
    assert "рестарт" in PluginMenuScreen.text(_CATALOG, note="нужен рестарт")


def test_no_emoji_in_button_labels() -> None:
    labels = _labels(PluginMenuScreen.keyboard(_CATALOG)) + _labels(
        PluginSettingsScreen.keyboard(PluginTogglesView())
    )
    assert all(b.isascii() or all(ord(ch) < 0x1F000 for ch in b) for b in labels)


def test_card_install_for_available_remove_for_installed() -> None:
    assert "Установить" in _labels(PluginCardScreen.keyboard("beta", _CATALOG))
    assert "Удалить" not in _labels(PluginCardScreen.keyboard("beta", _CATALOG))
    assert "Удалить" in _labels(PluginCardScreen.keyboard("alpha", _CATALOG))


def test_settings_shows_both_toggle_states() -> None:
    labels = _labels(
        PluginSettingsScreen.keyboard(PluginTogglesView(auto_update=True, alerts=False))
    )
    assert any("Автообновление" in b and "вкл" in b for b in labels)
    assert any("Алерты" in b and "выкл" in b for b in labels)
