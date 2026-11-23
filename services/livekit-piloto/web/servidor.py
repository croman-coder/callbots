"""Sirve la página de prueba y firma el token de la sala (la clave de LiveKit nunca llega al navegador).

Se abre con un túnel SSH desde la notebook (el micrófono del navegador solo anda en http://localhost o en https):

    ssh -N -L 8081:localhost:8081 -L 7880:localhost:7880 dgx-spark      # y abrir http://localhost:8081
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from aiohttp import web
from livekit import api

AQUI = Path(__file__).parent


async def token(request: web.Request) -> web.Response:
    sala = f"piloto-{secrets.token_hex(3)}"
    jwt = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(f"persona-{secrets.token_hex(2)}")
        .with_grants(api.VideoGrants(room_join=True, room=sala))
        .to_jwt()
    )
    return web.json_response({"url": os.getenv("LIVEKIT_URL_NAVEGADOR", "ws://localhost:7880"), "token": jwt, "sala": sala})


async def inicio(_: web.Request) -> web.FileResponse:
    return web.FileResponse(AQUI / "index.html", headers={"Cache-Control": "no-store"})


app = web.Application()
app.add_routes([web.get("/", inicio), web.get("/api/token", token)])

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8081)
