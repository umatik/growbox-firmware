# ota_http.py - trasy HTTP aktualizacji OTA (logika w ota.py)
import machine
import uasyncio as asyncio

import ota
import server


async def _reset_soon():
    # chwila na zamkniecie polaczenia, zanim plytka zniknie
    await asyncio.sleep(1)
    machine.reset()


async def handle(method, route, request, body, reader, writer, cors):
    """Trasy OTA; zwraca False, gdy route nie jest trasa OTA."""

    # GET /api/update  (stan, pliki z sha256, pliki czekajace na apply)

    if method == "GET" and route == "/api/update":
        query = server._parse_query(request.split("\r\n", 1)[0].split(" ")[1])
        names = query.get("files")
        await server._send_json(
            writer, cors, "200 OK",
            await ota.status(names.split(",") if names else None)
        )
        return True

    # PUT /api/files/<plik>?sha256=<hex>

    if method == "PUT" and route.startswith("/api/files/"):
        name = route[len("/api/files/"):]

        try:
            length = int(server._header(request, "content-length") or "0")
            query = server._parse_query(request.split("\r\n", 1)[0].split(" ")[1])
            digest = await ota.receive(
                name, reader, length, body, query.get("sha256")
            )
        except ota.OtaError as e:
            await server._send_json(
                writer, cors, "400 Bad Request",
                {"status": "error", "error": str(e)}
            )
            return True

        print("OTA: staged", name, length, "B")

        await server._send_json(
            writer, cors, "200 OK",
            {"status": "staged", "name": name, "sha256": digest}
        )
        return True

    # POST /api/update/apply  (podmiana plikow i reset)

    if method == "POST" and route == "/api/update/apply":
        try:
            files = ota.apply()
        except ota.OtaError as e:
            await server._send_json(
                writer, cors, "400 Bad Request",
                {"status": "error", "error": str(e)}
            )
            return True

        print("OTA: applied", files, "-> reset")

        await server._send_json(
            writer, cors, "200 OK",
            {"status": "applied", "files": files}
        )
        asyncio.create_task(_reset_soon())
        return True

    # POST /api/update/discard  (usun pliki .new po przerwanym uploadzie)

    if method == "POST" and route == "/api/update/discard":
        await server._send_json(
            writer, cors, "200 OK",
            {"status": "discarded", "files": ota.discard()}
        )
        return True

    return False
