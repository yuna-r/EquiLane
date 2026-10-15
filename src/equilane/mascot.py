"""Embed the generated mascot so reports stay portable single-file HTML."""

import base64
from importlib.resources import files


def mascot_markup() -> str:
    image = files("equilane").joinpath("assets/mascot.png").read_bytes()
    encoded = base64.b64encode(image).decode("ascii")
    return (
        '<img class="mascot" src="data:image/png;base64,' + encoded
        + '" alt="EquiLaneのムササビ" width="1254" height="1254" decoding="async">'
    )
