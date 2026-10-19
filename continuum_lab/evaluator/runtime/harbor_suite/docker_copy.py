"""Harbor's Docker environment with native copy transport instead of host binds.

The task, agent, verifier, logs, and artifact lifecycle remain owned by Harbor.
This adapter avoids Docker Desktop's blocked grpcfuse bind-mount approval.
"""
from __future__ import annotations

from harbor.environments.capabilities import EnvironmentCapabilities
from harbor.environments.docker.docker import DockerEnvironment


class CopyDocker(DockerEnvironment):
    """Keep Docker isolation; let Harbor upload/download its logs and artifacts."""

    def __init__(self, *args, mounts=None, **kwargs):
        self._copy_transport_directories = sorted({
            "/logs/agent", "/logs/verifier", "/logs/artifacts", "/artifacts",
            *(str(mount["target"]) for mount in (mounts or [])),
        })
        super().__init__(*args, mounts=[], **kwargs)

    @property
    def capabilities(self) -> EnvironmentCapabilities:
        return super().capabilities.model_copy(update={"mounted": False})

    async def start(self, force_build: bool = False) -> None:
        await super().start(force_build=force_build)
        # Lightweight setup only. Never run evaluation, copy project inputs to
        # output, sync remote storage, or clear an existing output directory.
        await self.ensure_dirs(self._copy_transport_directories)
