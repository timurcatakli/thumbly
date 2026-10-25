# Third-party dependencies and references

This repository implements the local application around Apple's CoreDevice services as
exposed by pymobiledevice3. iMouse is an action-design and calibration reference only; no
dongle package, vendor report extension, or proprietary transport code is included.

Device dependency, installed with the `device` extra:

| Component | Source / pin | Upstream license |
|---|---|---|
| pymobiledevice3 | PyPI `pymobiledevice3==11.19.4`, resolved in `uv.lock` under the `device` extra | GPL-3.0 |
| pmd-pytcp, qh3, pytun-pmd3, PyAV and other pymobiledevice3 dependencies | as resolved in `uv.lock` | see each distribution |
| usbmuxd | Built into macOS; used, not redistributed | Apple |

Application dependencies include Pydantic (MIT), Starlette (BSD-3-Clause), Uvicorn
(BSD-3-Clause), HTTPX (BSD-3-Clause), and Pillow (MIT-CMU), plus the optional official
MCP Python SDK and mcp-types (MIT) for the MCP entries. Exact Python resolutions
are in `uv.lock`. Installed
distributions retain their license files. Preserve upstream notices and source availability
when redistributing binary releases; this file is an inventory, not a relicensing of those
projects. The worker's touch, keyboard and button wire formats are documented reverse
engineering in pymobiledevice3; keep the exact pin until a new release is validated on a real iPhone.
