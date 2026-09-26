"""Exercise images that degrade to an icon instead of a broken image box."""

from nicegui import ui

# Runs in the browser only: hide the failed <img> so the icon underneath shows.
_HIDE_ON_ERROR = "(e) => { e.target.style.display = 'none'; }"


def exercise_image(url: str | None, size: int, *, fit: str = "cover") -> ui.element:
    """A ``size``-px square image with the fitness_center icon as fallback.

    The icon sits beneath the image; if there is no URL or the image fails to
    load (404, dead remote host), the image is hidden and the icon remains.
    """
    with (
        ui.element("div")
        .classes("relative flex items-center justify-center shrink-0 overflow-hidden rounded-lg")
        .style(f"width:{size}px;height:{size}px") as box
    ):
        ui.icon("fitness_center").classes("text-gray-400").style(f"font-size:{size // 2}px")
        if url:
            img = ui.element("img").classes("absolute inset-0 w-full h-full")
            img.props["src"] = url
            img.props["loading"] = "lazy"
            img.props["alt"] = ""
            img.style(f"object-fit:{fit};background:#e2e8f0")
            img.on("error", js_handler=_HIDE_ON_ERROR)
    return box


def thumbnail_url(media) -> str | None:
    """First displayable media URL: a still image, else an animated GIF."""
    for kind in ("image", "gif"):
        url = next((m.url for m in media if m.type == kind), None)
        if url:
            return url
    return None
