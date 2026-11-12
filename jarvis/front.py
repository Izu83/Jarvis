"""The phone port's front door.

The WebSocket server (remote.py) only understands GET requests, but the iPhone's "Send to Jarvis" Shortcut has to
POST a photo or file. So the tunnel reaches this small server first: a POST is answered here (on_post), and
everything else (the app's WebSocket, the Siri Shortcut's GET /ask, the desktop app) is passed through untouched,
byte for byte, to the WebSocket server on a private port.
"""
import asyncio
import logging

log = logging.getLogger("jarvis.front")

MAX_HEAD = 64 * 1024          # request line + headers
MAX_BODY = 100 * 1024 * 1024  # a shared video can be big, but not unlimited
REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 411: "Length Required",
           413: "Payload Too Large", 500: "Internal Server Error"}


def parse_head(head: bytes) -> tuple[str, str, dict]:
    """b'POST /ask HTTP/1.1\\r\\nX-A: 1\\r\\n\\r\\n' -> ("POST", "/ask", {"x-a": "1"}) (header names lower-cased)."""
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    method, path = (parts + ["", ""])[:2]
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    return method.upper(), path, headers


def response(status: int, text: str) -> bytes:
    body = text.encode("utf-8")
    return (f"HTTP/1.1 {status} {REASONS.get(status, 'OK')}\r\nContent-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body


async def read_body(reader: asyncio.StreamReader, headers: dict) -> bytes | int:
    """The request body, or an HTTP status when it can't be read (too big, no length)."""
    if "chunked" in headers.get("transfer-encoding", "").lower():
        body = bytearray()
        while True:
            size = int((await reader.readuntil(b"\r\n")).split(b";")[0].strip() or b"0", 16)
            if size == 0:
                await reader.readuntil(b"\r\n")   # the empty trailer
                return bytes(body)
            if len(body) + size > MAX_BODY:
                return 413
            body += await reader.readexactly(size)
            await reader.readexactly(2)
    try:
        length = int(headers.get("content-length", ""))
    except ValueError:
        return 411
    if length > MAX_BODY:
        return 413
    return await reader.readexactly(length) if length > 0 else b""


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


class Front:
    def __init__(self, inner_port: int, on_post):
        """on_post(path, headers, body) -> (status, text); it may block (it runs on a worker thread)."""
        self.inner_port = inner_port
        self.on_post = on_post

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, OSError):
            writer.close()
            return
        method, path, headers = parse_head(head)
        if method == "POST":
            await self._post(reader, writer, path, headers)
            return
        try:
            inner_reader, inner_writer = await asyncio.open_connection("127.0.0.1", self.inner_port)
        except OSError:
            log.warning("The phone server isn't answering on its private port.")
            writer.close()
            return
        inner_writer.write(head)   # anything already read past the head is still in `reader` and gets piped on
        await inner_writer.drain()
        await asyncio.gather(_pipe(reader, inner_writer), _pipe(inner_reader, writer))

    async def _post(self, reader, writer, path: str, headers: dict) -> None:
        try:
            if headers.get("expect", "").lower() == "100-continue":
                writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                await writer.drain()
            body = await read_body(reader, headers)
            if isinstance(body, int):
                status, text = body, "That's too big to send.\n" if body == 413 else "The request had no length.\n"
            else:
                status, text = await asyncio.get_running_loop().run_in_executor(None, self.on_post, path, headers, body)
            writer.write(response(status, text))
            await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            writer.write(response(400, "The request was cut off.\n"))
        except Exception:
            log.exception("A shared item couldn't be handled")
            writer.write(response(500, "Something went wrong on the PC.\n"))
        finally:
            try:
                await writer.drain()
                writer.close()
            except Exception:
                pass
