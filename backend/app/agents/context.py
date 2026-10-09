"""Per-run dependencies and progress reporting shared by the workflow and the agents."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel

from app.config import Settings
from app.db import Database
from app.domain.models import DataOrigin, ProviderRunStatus
from app.providers.base import AccommodationProvider, DestinationInfo, FlightProvider, PoiProvider
from app.services.money import FxTable

log = logging.getLogger(__name__)

ProgressStatus = Literal["running", "done", "warning", "error", "skipped"]


@dataclass
class ProgressEvent:
    stage: str
    message: str
    status: ProgressStatus = "running"
    agent: str | None = None
    tool: str | None = None
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class RunDeps:
    settings: Settings
    mode: Literal["demo", "live"]
    fx: FxTable
    fx_status: str
    flight_providers: list[FlightProvider]
    accommodation_providers: list[AccommodationProvider]
    poi_provider: PoiProvider | None
    llm: BaseChatModel | None
    db: Database | None = None
    on_progress: Callable[[ProgressEvent], Awaitable[None]] | None = None
    provider_status: list[ProviderRunStatus] = field(default_factory=list)
    destinations: dict[str, DestinationInfo] = field(default_factory=dict)

    async def emit(
        self,
        stage: str,
        message: str,
        status: ProgressStatus = "running",
        *,
        agent: str | None = None,
        tool: str | None = None,
    ) -> None:
        event = ProgressEvent(stage=stage, message=message, status=status, agent=agent, tool=tool)
        log.info("[%s] %s", stage, message)
        if self.on_progress:
            await self.on_progress(event)

    def record(self, provider: str, status: str, message: str | None = None, origin: DataOrigin | None = None) -> None:
        self.provider_status.append(
            ProviderRunStatus(
                provider=provider,
                status=status,
                message=message,
                origin=origin,  # type: ignore[arg-type]
                checked_at=datetime.now(UTC),
            )
        )

    async def with_timeout(self, coro: Awaitable[Any], seconds: float | None = None) -> Any:
        return await asyncio.wait_for(coro, timeout=seconds or self.settings.provider_timeout_seconds)
