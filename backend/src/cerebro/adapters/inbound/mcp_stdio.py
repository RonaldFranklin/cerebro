"""Development stdio-to-HTTP bridge. Each input line is one bounded MCP message."""

import http.client
import json
import re
from typing import BinaryIO

from mcp import types
from mcp.shared.inbound import NAME_BEARING_METHODS, encode_header_value

from cerebro.adapters.inbound.input_limits import MAX_INPUT_BYTES
from cerebro.adapters.inbound.mcp_http import decode_frame, invalid_arguments, validate_params
from cerebro.adapters.inbound.mcp_tools import InvalidToolArguments


def bridge(source: BinaryIO, sink: BinaryIO, *, host: str, port: int, secret: str) -> int:
    version: str | None = None
    while line := source.readline(MAX_INPUT_BYTES + 2):
        frame = line.removesuffix(b"\n")
        if len(frame) > MAX_INPUT_BYTES:
            return 2
        rpc_id: object = None
        notification = False
        try:
            message = decode_frame(frame)
            rpc_id = message.get("id")
            notification = rpc_id is None
            validate_params(message)
            method = str(message["method"])
            params = message.get("params")
            params = params if isinstance(params, dict) else {}
            meta = params.get("_meta")
            stamped = meta.get(types.PROTOCOL_VERSION_META_KEY) if isinstance(meta, dict) else None
            headers = {
                "Authorization": "Bearer " + secret,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            if isinstance(stamped, str):
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", stamped) or not re.fullmatch(
                    r"[A-Za-z0-9_/.-]+", method
                ):
                    raise ValueError("invalid routing headers")
                headers["MCP-Protocol-Version"] = stamped
                headers["Mcp-Method"] = method
                if (key := NAME_BEARING_METHODS.get(method)) and isinstance(name := params.get(key), str):
                    headers["Mcp-Name"] = encode_header_value(name)
            elif version is not None:
                headers["MCP-Protocol-Version"] = version
            # HTTPConnection opens the given socket directly. It never discovers proxies or follows redirects.
            conn = http.client.HTTPConnection(host, port, timeout=5)
            try:
                conn.request("POST", "/mcp", body=frame, headers=headers)
                response = conn.getresponse()
                if response.status in (401, 403):
                    raise PermissionError
                data = response.read()
                if rpc_id is None:
                    continue
                if not response.getheader("Content-Type", "").startswith("application/json"):
                    raise OSError
                reply = json.loads(data)
                if not isinstance(reply, dict) or reply.get("id") != rpc_id:
                    raise OSError
                result = reply.get("result")
                if (
                    method == "initialize"
                    and isinstance(result, dict)
                    and isinstance(result.get("protocolVersion"), str)
                ):
                    version = result["protocolVersion"]
            finally:
                conn.close()
        except InvalidToolArguments:
            reply = invalid_arguments(rpc_id)
        except ValueError, RecursionError:
            reply = {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32600, "message": "Invalid request."}}
        except PermissionError:
            reply = {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32001, "message": "Access denied."}}
        except http.client.HTTPException, OSError:
            reply = {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32603, "message": "Cérebro offline"}}
        if notification:
            continue
        sink.write(json.dumps(reply, ensure_ascii=True, allow_nan=False).encode() + b"\n")
        sink.flush()
    return 0
