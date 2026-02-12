"""
Docker-based environment provisioner.

Each environment is a Docker container running MCP Gateway + MCP servers.
Supports per-task Dockerfiles: the system hashes the Dockerfile + source
to decide whether to reuse an existing container (reset) or build a new image.
"""
# pylint: disable=broad-exception-caught,too-many-instance-attributes
import re
import os
import asyncio
import subprocess
import socket
import time
import hashlib
from typing import Dict, List, Optional, Tuple
from contextlib import closing

import aiohttp
from loguru import logger

from .base import BaseProvisioner, ContainerInfo, EnvConfig, EnvInfo, EnvStatus


class DockerProvisioner(BaseProvisioner):
    """Docker-based environment provisioner.

    Each environment is a Docker container running MCP Gateway + MCP servers.

    Usage::

        provisioner = DockerProvisioner(
            image="mcp-universe/gateway:latest",
            config=EnvConfig(servers=["playwright", "weather"]),
        )
        env = await provisioner.create("env-1")
        print(env.gateway_address)  # http://localhost:9001
        await provisioner.destroy("env-1")
    """

    def __init__(
        self,
        image: str = "mcp-universe/gateway:latest",
        config: Optional[EnvConfig] = None,
        base_port: int = 9000,
        host: str = "localhost",
        docker_host: Optional[str] = None,
        startup_timeout: float = 60.0,
        labels: Optional[Dict[str, str]] = None,
        build_context: str = ".",
        auto_build: bool = True,
        image_prefix: str = "mcp-universe/gateway",
    ):
        """Initialize Docker provisioner.

        Args:
            image: Default Docker image to use (when no dockerfile_path in config).
            config: Default environment config.
            base_port: Base port for port mapping (will find available ports).
            host: Hostname/IP that agents use to reach containers
                  (used to build gateway_address, e.g. "http://{host}:{port}").
            docker_host: Docker daemon endpoint for ``docker -H`` (e.g.
                         "tcp://10.144.44.88:2375").  None means local daemon.
            startup_timeout: Max seconds to wait for the gateway health
                             check after ``docker run``.
            labels: Extra Docker labels added to every container via
                    ``--label``.  Useful for filtering with
                    ``docker ps --filter label=key=value``.
            build_context: Root directory sent to the Docker daemon for
                           ``docker build``.  Dockerfile ``COPY`` / ``ADD``
                           instructions are relative to this path.
            auto_build: If True, automatically ``docker build`` when the
                        required image does not exist.
            image_prefix: Repository name for auto-built images.  The tag
                          is derived from the Dockerfile + source hash
                          (e.g. "mcp-universe/gateway:57d4e229").
        """
        self.image = image
        self.default_config = config or EnvConfig()
        self.base_port = base_port
        self.host = host
        self.docker_host = docker_host
        self.startup_timeout = startup_timeout
        self.labels = labels or {}
        self.build_context = build_context
        self.auto_build = auto_build
        self.image_prefix = image_prefix

        self._envs: Dict[str, EnvInfo] = {}
        self._port_map: Dict[str, int] = {}  # env_id -> host port
        self._port_lock = asyncio.Lock()  # atomic port allocation
        self._build_lock = asyncio.Lock()  # prevents parallel builds of same image
        self._building_images: Dict[str, asyncio.Event] = {}  # image -> completion event
        self._built_images: set = set()
        self._dockerfile_hash_cache: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Port allocation
    # ------------------------------------------------------------------

    async def _find_available_port(self, start_port: int = 9000,
                                   env_id: Optional[str] = None) -> int:
        """Find an available port and optionally register it atomically.

        Remote Docker: queries ``docker ps`` for used ports.
        Local Docker:  probes with ``socket.bind``.
        """
        async with self._port_lock:
            used_ports = set(self._port_map.values())

            if self.docker_host:
                try:
                    result = await self._run_docker_cmd(
                        ["ps", "-a", "--format", "{{.Ports}}"], check=False,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        for line in result.stdout.strip().split('\n'):
                            for m in re.findall(r':(\d+)->', line):
                                used_ports.add(int(m))
                except Exception as e:
                    logger.warning("Failed to query remote Docker ports: {}", e)

            for port in range(start_port, 65535):
                if port in used_ports:
                    continue
                if not self.docker_host:
                    try:
                        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
                            s.bind(("", port))
                    except OSError:
                        continue
                if env_id:
                    self._port_map[env_id] = port
                return port

            raise RuntimeError("No available ports")

    # ------------------------------------------------------------------
    # Image management
    # ------------------------------------------------------------------

    def _resolve_dockerfile_path(self, dockerfile_path: str) -> str:
        """Resolve *dockerfile_path* to an absolute path.

        Relative paths are resolved against ``self.build_context``.
        """
        if os.path.isabs(dockerfile_path):
            return dockerfile_path
        return os.path.join(os.path.abspath(self.build_context), dockerfile_path)

    def compute_dockerfile_hash(self, dockerfile_path: str) -> str:
        """Hash Dockerfile content + build-context source state.

        Combines the Dockerfile bytes with the git state (HEAD commit +
        uncommitted changes).  If git is unavailable, falls back to
        walking file metadata (path + size + mtime).  This ensures that
        source-code changes invalidate the cached image even when the
        Dockerfile itself hasn't changed.

        Returns:
            First 16 hex chars of the combined SHA-256 hash.
        """
        if dockerfile_path in self._dockerfile_hash_cache:
            return self._dockerfile_hash_cache[dockerfile_path]

        abs_path = self._resolve_dockerfile_path(dockerfile_path)
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"Dockerfile not found: {abs_path}")

        with open(abs_path, 'rb') as f:
            hasher = hashlib.sha256(f.read())

        # Include build-context state so source changes invalidate the image.
        # Strategy: try git first (fast, precise); fall back to file metadata scan.
        ctx = self.build_context
        if ctx:
            if not self._hash_build_context_git(hasher, ctx):
                self._hash_build_context_walk(hasher, ctx)

        hash_value = hasher.hexdigest()[:16]
        self._dockerfile_hash_cache[dockerfile_path] = hash_value
        return hash_value

    _WALK_SKIP_DIRS = {
        ".git", "__pycache__", "node_modules", ".tox", ".mypy_cache",
        ".pytest_cache", ".eggs", "*.egg-info", ".venv", "venv",
    }

    @staticmethod
    def _hash_build_context_git(hasher: "hashlib._Hash", ctx: str) -> bool:
        """Hash git state (HEAD + dirty files) into *hasher*.

        Returns True if git was usable, False otherwise.
        """
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ctx, capture_output=True, text=True, timeout=5, check=False,
            )
            if head.returncode != 0:
                return False
            hasher.update(head.stdout.strip().encode())

            diff = subprocess.run(
                ["git", "diff", "HEAD", "--name-only"],
                cwd=ctx, capture_output=True, text=True, timeout=5, check=False,
            )
            if diff.returncode == 0 and diff.stdout.strip():
                for changed_file in diff.stdout.strip().split('\n'):
                    fpath = os.path.join(ctx, changed_file)
                    if os.path.isfile(fpath):
                        try:
                            with open(fpath, 'rb') as f:
                                hasher.update(f.read())
                        except OSError:
                            pass
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    @classmethod
    def _hash_build_context_walk(cls, hasher: "hashlib._Hash", ctx: str) -> None:
        """Fallback when git is unavailable.

        Walks the build context and hashes every file's relative path,
        size and mtime.  Fast (no content reads) and catches most changes.
        """
        for dirpath, dirnames, filenames in os.walk(ctx):
            dirnames[:] = [d for d in sorted(dirnames) if d not in cls._WALK_SKIP_DIRS]
            for name in sorted(filenames):
                filepath = os.path.join(dirpath, name)
                try:
                    st = os.stat(filepath)
                    entry = f"{os.path.relpath(filepath, ctx)}:{st.st_size}:{st.st_mtime_ns}"
                    hasher.update(entry.encode())
                except OSError:
                    pass

    def _get_image_name_for_dockerfile(self, dockerfile_path: str) -> str:
        """Return ``{image_prefix}:{dockerfile_hash}``."""
        return f"{self.image_prefix}:{self.compute_dockerfile_hash(dockerfile_path)}"

    async def _image_exists(self, image_name: str) -> bool:
        """Check whether *image_name* exists on the Docker host."""
        result = await self._run_docker_cmd(["images", "-q", image_name], check=False)
        return bool(result.stdout.strip())

    async def _build_image(self, dockerfile_path: str, image_name: str) -> bool:
        """Run ``docker build`` and return True on success."""
        abs_path = self._resolve_dockerfile_path(dockerfile_path)
        abs_context = os.path.abspath(self.build_context)

        logger.info("Building image {} from {}", image_name, abs_path)
        logger.info("Build context: {}", abs_context)

        if not self.docker_host and not os.path.exists(abs_context):
            logger.error("Build context does not exist: {}", abs_context)
            return False
        if not os.path.exists(abs_path):
            logger.error("Dockerfile does not exist: {}", abs_path)
            return False

        logger.info("This may take several minutes for the first build...")
        try:
            await self._run_docker_cmd([
                "build", "-f", abs_path, "-t", image_name, abs_context,
            ])
            logger.info("Successfully built image {}", image_name)
            return True
        except Exception as e:
            logger.error("Failed to build image {}: {}", image_name, e)
            return False

    async def _ensure_image_for_config(self, config: EnvConfig) -> Tuple[str, str]:
        """Ensure the image for *config* exists, building if necessary.

        Uses a lock + event to prevent parallel builds of the same image.

        Returns:
            ``(image_name, dockerfile_hash)`` tuple.
        """
        if not config.dockerfile_path:
            return (self.image, "")

        dockerfile_hash = self.compute_dockerfile_hash(config.dockerfile_path)
        image_name = self._get_image_name_for_dockerfile(config.dockerfile_path)

        # Fast path: already built this session
        if image_name in self._built_images:
            return (image_name, dockerfile_hash)

        # Check remote Docker
        if await self._image_exists(image_name):
            self._built_images.add(image_name)
            return (image_name, dockerfile_hash)

        # Acquire build lock — coordinate with other concurrent tasks
        async with self._build_lock:
            if image_name in self._built_images:
                return (image_name, dockerfile_hash)
            if image_name in self._building_images:
                build_event = self._building_images[image_name]
                logger.info("Waiting for another task to finish building {}...", image_name)
            else:
                build_event = asyncio.Event()
                self._building_images[image_name] = build_event
                build_event = None  # this task is responsible for building

        # Wait for another task's build to finish
        if build_event is not None:
            try:
                await asyncio.wait_for(build_event.wait(), timeout=600.0)
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    f"Timed out waiting for another task to build image {image_name} (600s)"
                ) from exc
            if image_name in self._built_images:
                return (image_name, dockerfile_hash)
            raise RuntimeError(f"Another task failed to build image {image_name}")

        # This task builds the image
        try:
            if self.auto_build:
                if await self._build_image(config.dockerfile_path, image_name):
                    self._built_images.add(image_name)
                    return (image_name, dockerfile_hash)
                raise RuntimeError(
                    f"Failed to build image from {config.dockerfile_path}\n"
                    f"Build context: {self.build_context}\n"
                    f"Image name: {image_name}"
                )
            raise RuntimeError(
                f"Image {image_name} not found. "
                f"Set auto_build=True or build manually: "
                f"docker build -f {config.dockerfile_path} -t {image_name} {self.build_context}"
            )
        finally:
            async with self._build_lock:
                event = self._building_images.pop(image_name, None)
                if event is not None:
                    event.set()

    # ------------------------------------------------------------------
    # Docker command execution
    # ------------------------------------------------------------------

    async def _run_docker_cmd(self, args: List[str],
                              check: bool = True) -> subprocess.CompletedProcess:
        """Run a ``docker`` CLI command asynchronously."""
        cmd = ["docker"]
        if self.docker_host:
            cmd.extend(["-H", self.docker_host])
        cmd.extend(args)
        logger.debug("Running: {}", ' '.join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if check and proc.returncode != 0:
            msg = f"Docker command failed (exit code {proc.returncode})"
            if stderr:
                msg += f"\nSTDERR:\n{stderr.decode()}"
            if stdout:
                msg += f"\nSTDOUT:\n{stdout.decode()}"
            raise RuntimeError(msg)

        return subprocess.CompletedProcess(
            cmd, proc.returncode, stdout.decode(), stderr.decode()
        )

    # ------------------------------------------------------------------
    # Lifecycle: create / destroy / reset / health_check
    # ------------------------------------------------------------------

    async def create(self, env_id: str, config: Optional[EnvConfig] = None) -> EnvInfo:
        """Create a Docker container environment.

        Builds the image automatically when *config.dockerfile_path* is set
        and the image does not already exist.
        """
        config = config or self.default_config
        image_name, dockerfile_hash = await self._ensure_image_for_config(config)
        host_port = await self._find_available_port(self.base_port, env_id=env_id)

        container_name = f"mcp-env-{env_id}"
        cmd = [
            "run", "-d", "--name", container_name,
            "-p", f"{host_port}:{config.gateway_port}",
            "--cpus", config.cpu_limit, "--memory", config.memory_limit,
        ]

        if config.shm_size:
            cmd.extend(["--shm-size", config.shm_size])

        # Labels (includes dockerfile hash for config matching during reuse)
        labels = {
            "mcp.env_id": env_id, "mcp.managed": "true",
            "mcp.dockerfile_hash": dockerfile_hash, **self.labels,
        }
        for k, v in labels.items():
            cmd.extend(["--label", f"{k}={v}"])

        # Environment variables
        env_vars = {
            "MCP_GATEWAY_PORT": str(config.gateway_port),
            "MCP_GATEWAY_MODE": config.gateway_mode,
            "MCP_SERVERS": ",".join(config.servers),
            **config.env_vars,
        }
        for k, v in env_vars.items():
            cmd.extend(["-e", f"{k}={v}"])

        if config.network:
            cmd.extend(["--network", config.network])
        if config.workspace_template:
            wp = config.workspace_template.format(env_id=env_id)
            os.makedirs(wp, exist_ok=True)
            cmd.extend(["-v", f"{wp}:/workspace"])

        cmd.append(image_name)

        # Append gateway startup command unless the Dockerfile handles it
        if not config.use_dockerfile_cmd:
            gw = ["python", "-m", "mcpuniverse.mcp.gateway",
                  "--port", str(config.gateway_port), "--mode", config.gateway_mode,
                  "--servers", ",".join(config.servers)]
            if config.mcp_config_path:
                gw.extend(["--config", config.mcp_config_path])
            cmd.extend(gw)

        env_info = EnvInfo(
            env_id=env_id, status=EnvStatus.PENDING,
            gateway_address=f"http://{self.host}:{host_port}", config=config,
        )
        self._envs[env_id] = env_info

        try:
            result = await self._run_docker_cmd(cmd)
            env_info.container_id = result.stdout.strip()[:12]

            if await self._wait_for_ready(env_id, host_port):
                env_info.status = EnvStatus.READY
                logger.info("Environment {} ready at {}", env_id, env_info.gateway_address)
            else:
                env_info.status = EnvStatus.ERROR
                logger.error("Environment {} failed to become ready", env_id)
            return env_info
        except Exception as e:
            env_info.status = EnvStatus.ERROR
            self._port_map.pop(env_id, None)
            self._envs.pop(env_id, None)
            logger.error("Failed to create environment {}: {}", env_id, e)
            raise

    async def destroy(self, env_id: str) -> bool:
        """Stop and remove a container.  Idempotent."""
        env_info = self._envs.get(env_id)
        if env_info is None:
            self._port_map.pop(env_id, None)
            return False
        container_name = f"mcp-env-{env_id}"
        try:
            await self._run_docker_cmd(["stop", container_name], check=False)
            await self._run_docker_cmd(["rm", "-f", container_name], check=False)
            env_info.status = EnvStatus.TERMINATED
            self._port_map.pop(env_id, None)
            self._envs.pop(env_id, None)
            logger.info("Environment {} destroyed", env_id)
            return True
        except Exception as e:
            logger.error("Failed to destroy environment {}: {}", env_id, e)
            return False

    async def reset(self, env_id: str) -> bool:
        """Restart a container and wait for it to become ready again."""
        if env_id not in self._envs:
            return False
        env_info = self._envs[env_id]
        env_info.status = EnvStatus.RESETTING
        container_name = f"mcp-env-{env_id}"
        try:
            await self._run_docker_cmd(["restart", container_name])
            port = self._port_map.get(env_id)
            if port and await self._wait_for_ready(env_id, port):
                env_info.status = EnvStatus.READY
                env_info.assigned_agent = None
                env_info.assigned_at = None
                logger.info("Environment {} reset successfully", env_id)
                return True
            env_info.status = EnvStatus.ERROR
            return False
        except Exception as e:
            logger.error("Failed to reset environment {}: {}", env_id, e)
            env_info.status = EnvStatus.ERROR
            return False

    async def health_check(self, env_id: str) -> bool:
        """Return True if the container is running."""
        if env_id not in self._envs:
            return False
        try:
            result = await self._run_docker_cmd(
                ["inspect", "--format", "{{.State.Running}}", f"mcp-env-{env_id}"],
                check=False,
            )
            return result.stdout.strip().lower() == "true"
        except Exception:
            return False

    async def get_info(self, env_id: str) -> Optional[EnvInfo]:
        return self._envs.get(env_id)

    async def list_all(self) -> List[EnvInfo]:
        return list(self._envs.values())

    async def cleanup_all(self) -> int:
        count = 0
        for env_id in list(self._envs):
            if await self.destroy(env_id):
                count += 1
        return count

    async def reset_all(self) -> int:
        """Restart all containers (faster than cleanup + re-provision)."""
        count = 0
        for env_id in list(self._envs):
            if await self.reset(env_id):
                count += 1
        return count

    # ------------------------------------------------------------------
    # Health-check helpers
    # ------------------------------------------------------------------

    async def _wait_for_ready(self, env_id: str, port: int) -> bool:
        """Poll until the gateway inside the container is healthy."""
        container_name = f"mcp-env-{env_id}"
        env_info = self._envs.get(env_id)
        container_port = env_info.config.gateway_port if env_info else 8000
        start = time.time()
        ready = False

        while time.time() - start < self.startup_timeout:
            alive = await self._check_container_alive(container_name)
            if alive is False:
                return False
            if self.docker_host:
                ready = ready or await self._check_remote_health(container_name, container_port)
            elif await self._check_local_health(port):
                ready = True
            if ready:
                logger.info("Container {} health check passed", container_name)
                return True
            await asyncio.sleep(1.0)
        return False

    async def _check_container_alive(self, container_name: str) -> bool | None:
        """True = running, False = exited/missing, None = unclear."""
        try:
            return await self._inspect_container_state(container_name)
        except Exception as e:
            logger.debug("Container alive check error: {}", e)
            return None

    async def _inspect_container_state(self, container_name: str) -> bool | None:
        """Inspect container; log stderr on non-zero exit."""
        result = await self._run_docker_cmd(
            ["inspect", "--format", "{{.State.Running}}", container_name], check=False,
        )
        if result.returncode != 0:
            logger.warning("Container {} not found or can't be inspected", container_name)
            return False
        if result.stdout.strip().lower() == "true":
            return True

        # Container exited — log exit code and recent output for debugging
        exit_r = await self._run_docker_cmd(
            ["inspect", "--format", "{{.State.ExitCode}}", container_name], check=False,
        )
        exit_code = exit_r.stdout.strip() if exit_r.returncode == 0 else "unknown"
        if exit_code != "0":
            logger.warning("Container {} exited with code {}", container_name, exit_code)
            logs_r = await self._run_docker_cmd(
                ["logs", "--tail", "50", container_name], check=False,
            )
            if logs_r.returncode == 0:
                if logs_r.stdout.strip():
                    logger.debug("Container stdout: {}", logs_r.stdout.strip()[:500])
                if logs_r.stderr.strip():
                    logger.warning("Container stderr: {}", logs_r.stderr.strip()[:1000])
            return False
        return None

    async def _check_remote_health(self, name: str, port: int) -> bool:
        """Health check via ``docker exec curl`` (for remote Docker)."""
        try:
            r = await self._run_docker_cmd([
                "exec", name, "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                "--connect-timeout", "2", f"http://localhost:{port}/",
            ], check=False)
            if r.returncode != 0:
                return False
            code = r.stdout.strip()
            if code and code.isdigit() and int(code) < 500:
                logger.info("Container {} gateway ready (HTTP {})", name, code)
                return True
        except Exception as e:
            logger.debug("Remote health check error: {}", e)
        return False

    async def _check_local_health(self, port: int) -> bool:
        """Health check via socket + HTTP (for local Docker)."""
        try:
            if not self._is_port_open(port):
                return False
            return await self._http_health_check(port)
        except Exception as e:
            logger.debug("Local health check error: {}", e)
            return False

    def _is_port_open(self, port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        try:
            return sock.connect_ex((self.host, port)) == 0
        finally:
            sock.close()

    async def _http_health_check(self, port: int) -> bool:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://{self.host}:{port}/",
                timeout=aiohttp.ClientTimeout(total=5.0),
            ) as resp:
                return resp.status < 500

    # ------------------------------------------------------------------
    # Container reuse
    # ------------------------------------------------------------------

    async def _get_container_dockerfile_hash(self, name: str) -> str:
        """Read the ``mcp.dockerfile_hash`` label from a container."""
        try:
            r = await self._run_docker_cmd(
                ["inspect", "--format",
                 '{{index .Config.Labels "mcp.dockerfile_hash"}}', name],
                check=False,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception as e:
            logger.debug("Failed to get dockerfile hash for {}: {}", name, e)
        return ""

    async def _get_container_config(self, name: str) -> Optional[EnvConfig]:
        """Reconstruct an EnvConfig from a container's environment variables."""
        try:
            r = await self._run_docker_cmd(
                ["inspect", "--format",
                 "{{range .Config.Env}}{{println .}}{{end}}", name],
                check=False,
            )
            if r.returncode != 0:
                return None
            env = {}
            for line in r.stdout.strip().split('\n'):
                if '=' in line:
                    k, v = line.split('=', 1)
                    env[k] = v
            servers = ([s.strip() for s in env['MCP_SERVERS'].split(',') if s.strip()]
                       if env.get('MCP_SERVERS') else [])
            return EnvConfig(
                servers=servers,
                gateway_port=int(env.get('MCP_GATEWAY_PORT', '8000')),
                gateway_mode=env.get('MCP_GATEWAY_MODE', 'stdio'),
            )
        except Exception as e:
            logger.debug("Failed to get container config for {}: {}", name, e)
            return None

    async def _get_container_port(self, name: str) -> Optional[int]:
        """Get the host port mapping for a container.

        Tries ``NetworkSettings.Ports`` first (works while running), then
        ``HostConfig.PortBindings`` (works even when stopped).
        """
        inspect_network_ports = ("{{range $p, $conf := .NetworkSettings.Ports}}"
                                 "{{if $conf}}{{(index $conf 0).HostPort}}{{end}}{{end}}")
        inspect_port_bindings = ("{{range $p, $conf := .HostConfig.PortBindings}}"
                                 "{{if $conf}}{{(index $conf 0).HostPort}}{{end}}{{end}}")
        try:
            for template in (inspect_network_ports, inspect_port_bindings):
                r = await self._run_docker_cmd(
                    ["inspect", "--format", template, name], check=False,
                )
                if r.returncode == 0 and r.stdout.strip():
                    return int(r.stdout.strip())
        except Exception as e:
            logger.debug("Failed to get container port for {}: {}", name, e)
        return None

    async def find_existing_containers(self) -> List[ContainerInfo]:
        """Discover all ``mcp-env-*`` containers (including stopped ones).

        Returns a list of :class:`ContainerInfo` for containers labelled
        ``mcp.managed=true`` that are *not* currently tracked in ``_envs``.
        """
        try:
            r = await self._run_docker_cmd(
                ["ps", "-a", "--filter", "label=mcp.managed=true",
                 "--format", "{{.Names}}\t{{.Status}}"],
                check=False,
            )
            if r.returncode != 0:
                return []

            containers = []
            for line in r.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = line.strip().split('\t')
                if len(parts) < 2:
                    continue
                cname, status_str = parts[0], parts[1].lower()
                if not cname.startswith('mcp-env-'):
                    continue
                eid = cname[len('mcp-env-'):]
                if eid in self._envs:
                    continue
                cfg = await self._get_container_config(cname)
                if not cfg:
                    continue
                if 'up' in status_str:
                    status = 'running'
                elif 'exited' in status_str:
                    status = 'exited'
                else:
                    status = 'unknown'
                containers.append(ContainerInfo(
                    env_id=eid, config=cfg, status=status,
                    host_port=await self._get_container_port(cname),
                    dockerfile_hash=await self._get_container_dockerfile_hash(cname),
                ))
            return containers
        except Exception as e:
            logger.debug("Failed to find existing containers: {}", e)
            return []

    def configs_match(self, config1: EnvConfig, config2: EnvConfig,
                      dockerfile_hash1: str = "", dockerfile_hash2: str = "") -> bool:
        """Check whether two configs are compatible for container reuse.

        Compares dockerfile hash (when both present), servers (order-independent),
        and gateway_mode.  Resource limits and gateway_port are intentionally
        ignored because they don't affect reusability.
        """
        if dockerfile_hash1 and dockerfile_hash2 and dockerfile_hash1 != dockerfile_hash2:
            return False
        if set(config1.servers) != set(config2.servers):
            return False
        return config1.gateway_mode == config2.gateway_mode

    async def recover_container(self, env_id: str, config: EnvConfig,
                                host_port: int) -> Optional[EnvInfo]:
        """Bring an existing container back under management.

        Starts or restarts the container and waits for the gateway to
        become ready.  Returns the EnvInfo on success, None on failure.
        """
        container_name = f"mcp-env-{env_id}"
        try:
            r = await self._run_docker_cmd(
                ["inspect", "--format", "{{.State.Status}}", container_name],
                check=False,
            )
            if r.returncode != 0:
                return None
            status = r.stdout.strip()

            env_info = EnvInfo(
                env_id=env_id, status=EnvStatus.RESETTING,
                gateway_address=f"http://{self.host}:{host_port}", config=config,
            )
            id_r = await self._run_docker_cmd(
                ["inspect", "--format", "{{.Id}}", container_name], check=False,
            )
            if id_r.returncode == 0:
                env_info.container_id = id_r.stdout.strip()[:12]

            self._envs[env_id] = env_info
            self._port_map[env_id] = host_port

            if status != 'running':
                logger.info("Starting stopped container {}", container_name)
                await self._run_docker_cmd(["start", container_name])
            else:
                logger.info("Restarting running container {}", container_name)
                await self._run_docker_cmd(["restart", container_name])

            if await self._wait_for_ready(env_id, host_port):
                env_info.status = EnvStatus.READY
                logger.info("Recovered container {} at {}", container_name, env_info.gateway_address)
                return env_info

            env_info.status = EnvStatus.ERROR
            logger.error("Failed to recover container {}", container_name)
            self._envs.pop(env_id, None)
            self._port_map.pop(env_id, None)
            return None
        except Exception as e:
            logger.error("Failed to recover container {}: {}", container_name, e)
            self._envs.pop(env_id, None)
            self._port_map.pop(env_id, None)
            return None
