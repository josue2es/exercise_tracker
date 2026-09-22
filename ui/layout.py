"""Shared mobile-first page shell: header, navigation, reconnect banner."""

from __future__ import annotations

from nicegui import ui

from ui.auth import current_user

RECONNECT_BANNER_HTML = """
<div id="gym-reconnect-banner" role="status"
     style="display:none;position:fixed;top:0;left:0;right:0;z-index:9999;
            background:#b91c1c;color:#fff;text-align:center;padding:10px;
            font-weight:600;font-family:sans-serif;">Reconnecting...</div>
<script>
(function () {
  var attempts = 0;
  function hook() {
    if (window.socket && window.socket.io) {
      window.socket.io.on('reconnect_attempt', function () {
        var b = document.getElementById('gym-reconnect-banner');
        if (b) { b.style.display = 'block'; b.textContent = 'Reconnecting...'; }
      });
      window.socket.on('connect', function () {
        var b = document.getElementById('gym-reconnect-banner');
        if (b) { b.style.display = 'none'; b.textContent = 'Reconnecting...'; }
      });
      window.socket.io.on('reconnect_failed', function () {
        var b = document.getElementById('gym-reconnect-banner');
        if (b) { b.textContent = 'Connection lost. Please reload the page.'; b.style.display = 'block'; }
      });
      return true;
    }
    return false;
  }
  var timer = setInterval(function () { if (hook()) clearInterval(timer); }, 500);
})();
</script>
"""


def page_head() -> None:
    """Elements every page needs: reconnect banner, manifest and theme color."""
    ui.add_body_html(RECONNECT_BANNER_HTML)
    ui.add_head_html(
        '<link rel="manifest" href="/static/manifest.webmanifest">'
        '<meta name="theme-color" content="#1e293b">'
        "<style>"
        # Mobile rule: comfortable tap targets everywhere.
        ".q-btn { min-height: 44px; }"
        ".q-btn--dense { min-height: 40px; }"
        "</style>"
    )


def page_shell(title: str):
    """Render header + reconnect banner and return the content column.

    Must be used inside a @ui.page function; the caller renders page content
    into the returned column.
    """
    user = current_user()

    page_head()

    with ui.header().classes("items-center justify-between px-4 py-2"):
        ui.button(icon="home", on_click=lambda: ui.navigate.to("/")).props(
            "flat round dense color=white"
        ).tooltip("Workouts")
        ui.label("Gym Tracker").classes("text-base font-bold")
        with ui.row().classes("gap-1"):
            if user and user.role == "admin":
                ui.button(icon="admin_panel_settings", on_click=lambda: ui.navigate.to("/admin")).props(
                    "flat round dense color=white"
                ).tooltip("Admin")
            ui.button(icon="settings", on_click=lambda: ui.navigate.to("/settings")).props(
                "flat round dense color=white"
            ).tooltip("Settings")
            ui.button(
                icon="logout",
                on_click=lambda: (_logout(), ui.navigate.to("/login")),
            ).props("flat round dense color=white").tooltip("Log out")

    return ui.column().classes("max-w-xl mx-auto w-full px-3 py-4 gap-4")


def _logout() -> None:
    from ui.auth import logout_user

    logout_user()
