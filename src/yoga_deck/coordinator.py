"""Serialized ownership of Yoga Deck input side effects."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable
from contextlib import suppress
from typing import Any, Protocol

from yoga_deck.adapters.hyprland_inputs import InternalInput, is_owned_input
from yoga_deck.core import (
    ApplyRotation,
    CaptureText,
    Effect,
    LaunchScratchpad,
    Orientation,
    RefreshOskTheme,
    SetInternalInputsEnabled,
    SetOskEnabled,
)


class CoordinatorError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class InputAdapter(Protocol):
    def discover_owned(self) -> Any: ...

    def set_enabled(self, device: InternalInput, enabled: bool) -> Any: ...


class RotationAdapter(Protocol):
    def apply(self, orientation: Orientation) -> Any: ...


class OcrAdapter(Protocol):
    def capture_text(self) -> Any: ...


class ScratchpadAdapter(Protocol):
    def launch(self) -> Any: ...


class OskAdapter(Protocol):
    def set_enabled(self, enabled: bool) -> Any: ...

    def refresh_theme(self) -> Any: ...


class RotationCoordinator:
    """Serialize rotation intents so stale compositor work cannot win."""

    def __init__(self, adapter: RotationAdapter) -> None:
        self._adapter = adapter
        self._lock = asyncio.Lock()
        self._desired_orientation = Orientation.NORMAL
        self.latest_sequence = 0

    async def apply(self, effect: ApplyRotation) -> None:
        async with self._lock:
            if effect.sequence <= self.latest_sequence:
                return
            self.latest_sequence = effect.sequence
            previous = self._desired_orientation
            self._desired_orientation = effect.orientation
            try:
                await self._apply_desired()
            except CoordinatorError:
                with suppress(Exception):
                    await _resolve(self._adapter.apply(previous))
                raise

    async def reconcile(self) -> None:
        async with self._lock:
            await self._apply_desired()

    async def _apply_desired(self) -> None:
        try:
            await _resolve(self._adapter.apply(self._desired_orientation))
        except Exception as error:
            raise CoordinatorError("rotation_transaction_failed") from error


class SystemCoordinator:
    """One serialized ordering boundary for every implemented runtime effect."""

    def __init__(
        self,
        inputs: InputCoordinator,
        rotation: RotationCoordinator,
        ocr: OcrCoordinator | None = None,
        scratchpad: ScratchpadCoordinator | None = None,
        osk: OskCoordinator | None = None,
    ) -> None:
        self._inputs = inputs
        self._rotation = rotation
        self._ocr = ocr
        self._scratchpad = scratchpad
        self._osk = osk
        self._lock = asyncio.Lock()
        self.latest_sequence = 0

    async def apply(self, effect: Effect) -> None:
        # Interactive captures have their own ordering and may outlive many posture
        # transitions. Never hold the compositor lock while waiting for a picker.
        if isinstance(effect, CaptureText):
            if self._ocr is None:
                raise CoordinatorError("ocr_adapter_unavailable")
            await self._ocr.apply(effect)
            return
        async with self._lock:
            if effect.sequence < self.latest_sequence:
                return
            if isinstance(effect, SetInternalInputsEnabled):
                await self._inputs.apply(effect)
            elif isinstance(effect, ApplyRotation):
                await self._rotation.apply(effect)
            elif isinstance(effect, LaunchScratchpad):
                if self._scratchpad is None:
                    raise CoordinatorError("scratchpad_adapter_unavailable")
                await self._scratchpad.apply(effect)
            elif isinstance(effect, SetOskEnabled):
                if self._osk is None:
                    raise CoordinatorError("osk_adapter_unavailable")
                await self._osk.apply(effect)
            elif isinstance(effect, RefreshOskTheme):
                if self._osk is None:
                    raise CoordinatorError("osk_adapter_unavailable")
                await self._osk.refresh(effect)
            else:
                return
            self.latest_sequence = max(self.latest_sequence, effect.sequence)

    async def recover_inputs(self) -> None:
        async with self._lock:
            await self._inputs.recover_inputs()

    async def reconcile(self) -> None:
        async with self._lock:
            await self._inputs.reconcile()
            await self._rotation.reconcile()
            if self._osk is not None:
                await self._osk.reconcile()


class InputCoordinator:
    def __init__(self, adapter: InputAdapter) -> None:
        self._adapter = adapter
        self._lock = asyncio.Lock()
        self._desired_enabled = True
        self.latest_sequence = 0

    async def apply(self, effect: SetInternalInputsEnabled) -> None:
        async with self._lock:
            if effect.sequence <= self.latest_sequence:
                return
            self.latest_sequence = effect.sequence
            self._desired_enabled = effect.enabled
            await self._transition(effect.enabled)

    async def reconcile(self) -> None:
        async with self._lock:
            await self._transition(self._desired_enabled)

    async def recover_inputs(self) -> None:
        async with self._lock:
            self._desired_enabled = True
            try:
                devices = await self._discover_owned()
            except Exception as error:
                raise CoordinatorError("input_recovery_incomplete") from error
            failures = await self._set_all(devices, True)
            if failures:
                raise CoordinatorError("input_recovery_incomplete")

    async def _transition(self, enabled: bool) -> None:
        try:
            devices = await self._discover_owned()
        except Exception as error:
            self._desired_enabled = True
            code = "input_recovery_incomplete" if enabled else "input_inhibition_failed"
            raise CoordinatorError(code) from error
        failures = await self._set_all(devices, enabled)
        if not failures:
            return
        if enabled:
            raise CoordinatorError("input_recovery_incomplete")

        self._desired_enabled = True
        await self._set_all(devices, True)
        raise CoordinatorError("input_inhibition_failed")

    async def _discover_owned(self) -> tuple[InternalInput, ...]:
        discovered = await _resolve(self._adapter.discover_owned())
        return tuple(device for device in discovered if is_owned_input(device))

    async def _set_all(self, devices: tuple[InternalInput, ...], enabled: bool) -> int:
        failures = 0
        for device in devices:
            try:
                await _resolve(self._adapter.set_enabled(device, enabled))
            except Exception:
                failures += 1
        return failures


class OcrCoordinator:
    """Serialize interactive OCR captures and surface only stable failures."""

    def __init__(self, adapter: OcrAdapter) -> None:
        self._adapter = adapter
        self._lock = asyncio.Lock()
        self.latest_sequence = 0

    async def apply(self, effect: CaptureText) -> None:
        async with self._lock:
            if effect.sequence <= self.latest_sequence:
                return
            self.latest_sequence = effect.sequence
            try:
                await _resolve(self._adapter.capture_text())
            except Exception as error:
                raise CoordinatorError("ocr_capture_failed") from error


class ScratchpadCoordinator:
    """Serialize optional scratchpad launches and expose stable failures only."""

    def __init__(self, adapter: ScratchpadAdapter) -> None:
        self._adapter = adapter
        self._lock = asyncio.Lock()
        self.latest_sequence = 0

    async def apply(self, effect: LaunchScratchpad) -> None:
        async with self._lock:
            if effect.sequence <= self.latest_sequence:
                return
            try:
                await _resolve(self._adapter.launch())
            except Exception as error:
                raise CoordinatorError("scratchpad_launch_failed") from error
            self.latest_sequence = effect.sequence


class OskCoordinator:
    """Serialize OSK lifecycle intents and retain the desired state for reconciliation."""

    def __init__(self, adapter: OskAdapter) -> None:
        self._adapter = adapter
        self._lock = asyncio.Lock()
        self._desired_enabled = False
        self.latest_sequence = 0

    async def apply(self, effect: SetOskEnabled) -> None:
        async with self._lock:
            if effect.sequence <= self.latest_sequence:
                return
            self.latest_sequence = effect.sequence
            self._desired_enabled = effect.enabled
            await self._apply_desired()

    async def refresh(self, effect: RefreshOskTheme) -> None:
        """Retint the backend in place; a stopped OSK has nothing to retint."""

        async with self._lock:
            if effect.sequence <= self.latest_sequence:
                return
            self.latest_sequence = effect.sequence
            if not self._desired_enabled:
                return
            try:
                await _resolve(self._adapter.refresh_theme())
            except Exception as error:
                code = getattr(error, "code", "osk_theme_refresh_failed")
                raise CoordinatorError(str(code)) from error

    async def reconcile(self) -> None:
        async with self._lock:
            await self._apply_desired()

    async def _apply_desired(self) -> None:
        try:
            await _resolve(self._adapter.set_enabled(self._desired_enabled))
        except Exception as error:
            code = getattr(error, "code", "osk_transition_failed")
            raise CoordinatorError(str(code)) from error


async def _resolve(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await _as_awaitable(value)
    return value


async def _as_awaitable(value: Awaitable[Any]) -> Any:
    return await value
