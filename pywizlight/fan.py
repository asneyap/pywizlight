"""pywizlight – fan support
========================

Native support for **WiZ‑enabled ceiling fans**.

This module follows the coding conventions already used in *pywizlight*:

* Builder pattern (`FanPilotBuilder`) identical to `PilotBuilder`.
* Async helpers that **validate the response** of each UDP command and
  raise the canonical exceptions from ``pywizlight.exceptions``.
* Discovery keys registered via ``DISCOVERY_MATCHER`` exactly like
  ``plug.py`` and ``switch.py``.
* Formatted with *Black* (line length 88) and fully type‑checked under
  *mypy*.

The firmware contract —derived from packet capture— is:

================  =============================================
Parameter         Values / semantics
----------------  ---------------------------------------------
``fanSpeed``      1‑6  → RPM steps (lower‑to‑higher)
``fanMode``       1 = Normal · 2 = Breeze
``fanRevrs``      0 = Summer (forward) · 1 = Winter (reverse)
``fanState``      0 = Off · 1 = On (**must be sent *alone*** when ↘off)
================  =============================================
"""

from __future__ import annotations

import logging
from enum import IntEnum
from typing import Any, Dict, Final, Optional

from pywizlight.device import WizDevice
from pywizlight.discovery import DISCOVERY_MATCHER
from pywizlight.exceptions import (
    WizLightConnectionError,
    WizLightProtocolError,
    WizLightTimeOutError,
)

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enumerations – explicit ints kept for wire‑level compatibility
# ---------------------------------------------------------------------------

class FanMode(IntEnum):
    """Operating mode of the fan."""

    NORMAL = 1
    BREEZE = 2


class FanReverse(IntEnum):
    """Direction of rotation."""

    SUMMER = 0  # forward
    WINTER = 1  # reverse


# ---------------------------------------------------------------------------
# Pilot builder – parallels pywizlight.PilotBuilder
# ---------------------------------------------------------------------------

class FanPilotBuilder:  # pylint: disable=too-few-public-methods
    """Compose a *setPilot* payload for WiZ ceiling fans."""

    __slots__ = ("_payload",)

    def __init__(
        self,
        *,
        speed: Optional[int] = None,
        mode: Optional[FanMode] = None,
        reverse: Optional[FanReverse] = None,
    ) -> None:
        self._payload: Dict[str, Any] = {}
        if speed is not None:
            self.speed(speed)
        if mode is not None:
            self.mode(mode)
        if reverse is not None:
            self.reverse(reverse)

    # -------------------------- fluent helpers ------------------------

    def speed(self, value: int) -> "FanPilotBuilder":
        if value not in range(1, 7):
            raise ValueError("fanSpeed must be between 1 and 6 inclusive")
        self._payload["fanSpeed"] = value
        return self

    def mode(self, value: FanMode) -> "FanPilotBuilder":
        self._payload["fanMode"] = int(value)
        return self

    def reverse(self, value: FanReverse) -> "FanPilotBuilder":
        self._payload["fanRevrs"] = int(value)
        return self

    # ----------------------------- dunder -----------------------------

    @property
    def payload(self) -> Dict[str, Any]:
        """Return a *copy* suitable for ``setPilot`` params."""

        return dict(self._payload)

    def __bool__(self) -> bool:  # truthiness → non‑empty payload
        return bool(self._payload)


# ---------------------------------------------------------------------------
# WizFan device
# ---------------------------------------------------------------------------

FAN_STATE_OFF: Final[int] = 0
FAN_STATE_ON: Final[int] = 1


class WizFan(WizDevice):
    """High‑level wrapper for WiZ ceiling‑fan devices."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def turn_on(
        self,
        *,
        speed: Optional[int] = None,
        mode: Optional[FanMode] = None,
        reverse: Optional[FanReverse] = None,
    ) -> None:
        """Turn the fan **on** and (optionally) update parameters."""

        await self._send_state(FAN_STATE_ON)
        if any(arg is not None for arg in (speed, mode, reverse)):
            builder = FanPilotBuilder(speed=speed, mode=mode, reverse=reverse)
            await self._send_pilot(builder.payload)

    async def turn_off(self) -> None:
        """Turn the fan **off** – *fanState* must be the only field sent."""

        await self._send_state(FAN_STATE_OFF)

    async def set_speed(self, speed: int) -> None:
        await self._send_pilot(FanPilotBuilder(speed=speed).payload)

    async def set_mode(self, mode: FanMode) -> None:
        await self._send_pilot(FanPilotBuilder(mode=mode).payload)

    async def set_reverse(self, reverse: FanReverse) -> None:
        await self._send_pilot(FanPilotBuilder(reverse=reverse).payload)

    # ------------------------------------------------------------------
    # Polling helpers
    # ------------------------------------------------------------------

    async def get_status(self) -> Dict[str, Any]:
        """Return the raw *getPilot* payload."""

        try:
            return await self._do_command("getPilot", {})
        except (WizLightConnectionError, WizLightTimeOutError) as exc:
            _LOGGER.warning("%s: could not fetch status: %s", self.ip, exc)
            raise

    async def get_speed(self) -> int:
        return int((await self.get_status()).get("fanSpeed", 0))

    async def get_mode(self) -> FanMode:
        return FanMode((await self.get_status()).get("fanMode", FanMode.NORMAL))

    async def is_reversed(self) -> bool:
        return bool((await self.get_status()).get("fanRevrs", FanReverse.SUMMER))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _send_state(self, state: int) -> None:
        if state not in (FAN_STATE_OFF, FAN_STATE_ON):
            raise ValueError("fanState must be 0 (off) or 1 (on)")
        await self._send_pilot({"fanState": state})

    async def _send_pilot(self, params: Dict[str, Any]) -> None:
        if not params:
            return  # nothing to send

        try:
            response = await self._do_command("setPilot", params)
        except (WizLightConnectionError, WizLightTimeOutError) as exc:
            _LOGGER.error("%s: UDP send failed: %s", self.ip, exc)
            raise

        self._validate_response(response, params)

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_response(response: Dict[str, Any], sent: Dict[str, Any]) -> None:
        """Ensure firmware replied *ok*; raise otherwise."""

        if response.get("result") == "ok":
            return  # happy path

        # Some firmwares answer with {"error":...} instead
        error_code = response.get("error") or response.get("result")
        raise WizLightProtocolError(
            f"Unexpected response to setPilot {sent!r}: {response!r} (error={error_code})"
        )


# ---------------------------------------------------------------------------
# Discovery registration
# ---------------------------------------------------------------------------

_DISCOVERY_KEYS: Final[Dict[str, str]] = {
    "ESP02_V2.1": "fan",
    "ESP02_V3.1": "fan",
}

if DISCOVERY_MATCHER.update(_DISCOVERY_KEYS):  # pragma: no cover
    _LOGGER.debug("Registered WizFan discovery keys: %s", ", ".join(_DISCOVERY_KEYS))
