"""Daily auto-update job. Refreshes priors and pre-warms caches.

Runs as a Container Apps Job (cron) — separate from the always-on API service
so it doesn't affect scale-to-zero. See infra/modules/containerApp.bicep.
"""

from __future__ import annotations

import asyncio

import structlog

from iplodds.agents import priors as priors_agent
from iplodds.data import iplt20_client
from iplodds.data.cache import get_cache

log = structlog.get_logger(__name__)


async def run_once() -> None:
    log.info("daily_job.start")
    try:
        await iplt20_client.get_standings()
        await iplt20_client.get_schedule()
        result = await priors_agent.compute_priors(force=True)
        log.info("daily_job.done", priors=len(result.get("priors", {})))
    finally:
        await get_cache().aclose()


def main() -> None:
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
