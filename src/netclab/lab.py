"""A lab on kind: the registry, the cluster, the CNI plugins, Multus, and the chart; and
when asked, Crossplane, a Configuration and the manifests that go with it.

Every step looks before it acts, so `up` on a running lab changes only what differs.
"""

from __future__ import annotations

import functools
import ipaddress
import json
import shutil
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import yaml

from netclab.steps import say, step

TOOLS = ("docker", "kind", "kubectl", "helm")

REGISTRY = "kind-registry"
REGISTRY_IMAGE = "registry:2"
REGISTRY_VOLUME = "kind-registry-data"
REGISTRY_HOST_PORT = 5001
REGISTRY_PORT = 5000
KIND_NETWORK = "kind"
DOCKER_HUB = "https://registry-1.docker.io"

CNI_PLUGINS = "v1.9.1"
MULTUS = "v4.3.1"
CHART_REPO = "https://netclab.github.io/netclab-chart"
CHART = "netclab"

CROSSPLANE_REPO = "https://charts.crossplane.io/stable"
CROSSPLANE_NAMESPACE = "crossplane-system"
# A Configuration is healthy once its dependencies are installed, and pulling them is
# most of the wait.
PACKAGE_TIMEOUT = "10m"
# How long a manifest waits for its kinds after the Configuration is healthy: the CRDs of
# its XRDs and of its providers are served a little after it.
KINDS_TIMEOUT = 300
KINDS_POLL = 5

CONTAINERD_CERTS = "/etc/containerd/certs.d"
KIND_CONFIG = f"""\
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
containerdConfigPatches:
- |-
  [plugins."io.containerd.grpc.v1.cri".registry]
    config_path = "{CONTAINERD_CERTS}"
"""

# go-containerregistry, which Crossplane pulls packages with, falls back to plain HTTP
# only for these; the registry serves no TLS.
RFC1918 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]

# Namespaces Kubernetes makes itself: a lab installed into one leaves it standing.
KUBERNETES_NAMESPACES = ("default", "kube-system", "kube-public", "kube-node-lease")

# `uname -m` on a node, as the CNI plugins' release names it.
ARCHES = {"x86_64": "amd64", "aarch64": "arm64"}

# A Kubernetes quantity's suffix, and what it multiplies by.
QUANTITY_SUFFIXES = {
    "Ki": 2**10,
    "Mi": 2**20,
    "Gi": 2**30,
    "Ti": 2**40,
    "Pi": 2**50,
    "Ei": 2**60,
    "m": 1e-3,
    "k": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
    "P": 1e15,
    "E": 1e18,
}

# A pod that has ended holds nothing on its node.
ENDED = ("Succeeded", "Failed")


class LabError(Exception):
    """What stops a lab from coming up, said so a user can act on it."""


@dataclass(frozen=True)
class Requests:
    """CPU in cores and memory in bytes, as the scheduler adds them up."""

    cpu: float = 0.0
    memory: float = 0.0

    def __add__(self, other: Requests) -> Requests:
        return Requests(self.cpu + other.cpu, self.memory + other.memory)

    def __sub__(self, other: Requests) -> Requests:
        return Requests(self.cpu - other.cpu, self.memory - other.memory)

    def fits(self, free: Requests) -> bool:
        return self.cpu <= free.cpu and self.memory <= free.memory

    def __str__(self) -> str:
        return f"{self.cpu:.1f} CPU and {self.memory / 2**30:.1f}Gi"


@dataclass(frozen=True)
class Crossplane:
    """Crossplane in the lab, and what goes into it.

    `configuration` is a package, installed and waited for until healthy. Each of
    `manifests` is applied server-side once the cluster serves every kind in it: before
    the Configuration when it already does, after it otherwise.
    """

    version: str
    configuration: str | None = None
    manifests: tuple[Path, ...] = ()


@dataclass(frozen=True)
class Manifest:
    """A file to apply, and the (apiVersion, kind, name) of each document in it."""

    path: Path
    docs: tuple[tuple[str, str, str], ...]


class _Loader(yaml.SafeLoader):
    """Reads what a manifest declares, whatever tags its values carry, such as `!vault`.

    Only apiVersion, kind and name are read; the file goes to the cluster as written.
    """


_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def read_manifest(path: Path) -> Manifest:
    try:
        docs = [doc for doc in yaml.load_all(path.read_text(), Loader=_Loader) if doc]
        return Manifest(
            path,
            tuple((d["apiVersion"], d["kind"], d["metadata"]["name"]) for d in docs),
        )
    except (yaml.YAMLError, KeyError, TypeError) as err:
        raise LabError(f"{path}: not a Kubernetes manifest ({err})") from err


def describe(manifest: Manifest) -> str:
    """`Kind/name` for a kind the file holds once, `Kind (n)` for one it holds more."""
    counts = Counter(kind for _, kind, _ in manifest.docs)
    names = {kind: name for _, kind, name in manifest.docs}
    return ", ".join(
        f"{kind}/{names[kind]}" if count == 1 else f"{kind} ({count})"
        for kind, count in counts.items()
    )


def unserved(manifest: Manifest, serves: Callable[[str], set[str]]) -> list[str]:
    """The kinds in `manifest` the cluster does not serve yet, as `kind (apiVersion)`."""
    missing = [
        f"{kind} ({api_version})"
        for api_version, kind, _ in manifest.docs
        if kind not in serves(api_version)
    ]
    return list(dict.fromkeys(missing))


def quantity(value: str | float) -> float:
    """A Kubernetes quantity as a number: `500m` is 0.5, `1536Mi` is 1610612736."""
    text = str(value)
    for suffix, factor in QUANTITY_SUFFIXES.items():
        if text.endswith(suffix):
            return float(text.removesuffix(suffix)) * factor
    return float(text)


def container_requests(container: dict) -> Requests:
    """A container's requests; where one is not set, its limit, as Kubernetes defaults it.

    The chart sets only limits.
    """
    resources = container.get("resources") or {}
    asked = (resources.get("limits") or {}) | (resources.get("requests") or {})
    return Requests(quantity(asked.get("cpu", 0)), quantity(asked.get("memory", 0)))


def pod_requests(spec: dict) -> Requests:
    """What the scheduler reserves for a pod: its containers together, or its largest
    init container where that asks for more, CPU and memory each."""
    running = sum((container_requests(c) for c in spec.get("containers") or []), Requests())
    init = [container_requests(c) for c in spec.get("initContainers") or []]
    return Requests(
        max([running.cpu, *(i.cpu for i in init)]),
        max([running.memory, *(i.memory for i in init)]),
    )


def requested(docs: list[dict]) -> Requests:
    """What the pods of `docs`, a rendered chart, ask for together.

    A workload's template counts once for each replica; a DaemonSet once, as on the one
    node of a cluster netclab makes.
    """
    total = Requests()
    for doc in docs:
        if doc.get("kind") == "Pod":
            total += pod_requests(doc.get("spec") or {})
            continue
        template = ((doc.get("spec") or {}).get("template") or {}).get("spec")
        if template is not None:
            replicas = (doc.get("spec") or {}).get("replicas", 1)
            for _ in range(replicas):
                total += pod_requests(template)
    return total


def free(nodes: list[dict], pods: list[dict], namespace: str) -> Requests:
    """What `nodes` can still give, beside what the pods of other namespaces hold.

    The pods in `namespace` are the lab's own, which the chart's release replaces.
    """
    allocatable = Requests()
    for node in nodes:
        can = (node.get("status") or {}).get("allocatable") or {}
        allocatable += Requests(quantity(can.get("cpu", 0)), quantity(can.get("memory", 0)))
    held = Requests()
    for pod in pods:
        if pod["metadata"]["namespace"] == namespace:
            continue
        if (pod.get("status") or {}).get("phase") in ENDED:
            continue
        held += pod_requests(pod.get("spec") or {})
    return allocatable - held


def chart_version(release: str) -> str:
    """The Crossplane chart's version for a Crossplane release: its tag without the `v`."""
    return release.removeprefix("v")


def configuration(package: str) -> dict:
    """The Configuration installing `package`, named after its repository's last part."""
    name = package.rsplit("/", 1)[-1].split("@")[0].split(":")[0]
    return {
        "apiVersion": "pkg.crossplane.io/v1",
        "kind": "Configuration",
        "metadata": {"name": name},
        "spec": {"package": package},
    }


def private(address: ipaddress.IPv4Address) -> ipaddress.IPv4Address:
    """`address`, the registry's on the kind network, refused if Crossplane would not use HTTP."""
    if not any(address in network for network in RFC1918):
        raise LabError(
            f"the registry is at {address} on the {KIND_NETWORK} network, outside RFC 1918: "
            "Crossplane would not pull from it over plain HTTP"
        )
    return address


# containerd reaches the registry by its container name, which Docker resolves on the
# kind network whatever address it gave the registry this time.
HOSTS_TOML = f'[host."http://{REGISTRY}:{REGISTRY_PORT}"]\n  capabilities = ["pull", "resolve"]\n'


def mirrors(address: ipaddress.IPv4Address) -> dict[str, str]:
    """containerd's hosts.toml, by the registry name it answers for.

    The registry by the names images in it carry: from the host, and at its address on
    the kind network, which Crossplane uses. And in front of Docker Hub, so an image the
    chart names there, like `ceos:4.36.1F`, is found in the registry as
    `library/ceos:4.36.1F`, and any other still comes from Docker Hub.
    """
    return {
        f"localhost:{REGISTRY_HOST_PORT}": HOSTS_TOML,
        f"{address}:{REGISTRY_PORT}": HOSTS_TOML,
        "docker.io": f'server = "{DOCKER_HUB}"\n\n{HOSTS_TOML}',
    }


def cni_plugins_url(machine: str) -> str:
    arch = ARCHES.get(machine)
    if arch is None:
        raise LabError(f"no CNI plugins for a node on {machine}")
    return (
        "https://github.com/containernetworking/plugins/releases/download/"
        f"{CNI_PLUGINS}/cni-plugins-linux-{arch}-{CNI_PLUGINS}.tgz"
    )


def multus_url() -> str:
    return (
        "https://raw.githubusercontent.com/k8snetworkplumbingwg/multus-cni/"
        f"{MULTUS}/deployments/multus-daemonset.yml"
    )


def context(cluster: str) -> str:
    return f"kind-{cluster}"


def run(*command: str, stdin: str | None = None) -> str:
    done = subprocess.run(command, input=stdin, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise LabError(f"{' '.join(command)}\n{done.stderr.strip()}")
    return done.stdout


def check_tools() -> None:
    missing = [tool for tool in TOOLS if shutil.which(tool) is None]
    if missing:
        raise LabError(f"not found: {', '.join(missing)}")


def ensure_registry() -> None:
    running = run("docker", "ps", "--quiet", "--filter", f"name=^{REGISTRY}$").strip()
    if running:
        return
    with step(f"registry {REGISTRY}, its images on the volume {REGISTRY_VOLUME}"):
        exists = run("docker", "ps", "--all", "--quiet", "--filter", f"name=^{REGISTRY}$")
        if exists.strip():
            run("docker", "start", REGISTRY)
            return
        run(
            "docker",
            "run",
            "--detach",
            "--restart=always",
            "--publish",
            f"127.0.0.1:{REGISTRY_HOST_PORT}:{REGISTRY_PORT}",
            "--volume",
            f"{REGISTRY_VOLUME}:/var/lib/registry",
            "--name",
            REGISTRY,
            REGISTRY_IMAGE,
        )


def ensure_cluster(cluster: str) -> list[str]:
    """The cluster's nodes, the cluster made first if there is none."""
    if cluster not in run("kind", "get", "clusters").split():
        with step(f"kind cluster {cluster}"):
            run("kind", "create", "cluster", "--name", cluster, "--config", "-", stdin=KIND_CONFIG)
    nodes = run("kind", "get", "nodes", "--name", cluster).split()
    for node in nodes:
        config = run("docker", "exec", node, "cat", "/etc/containerd/config.toml")
        if CONTAINERD_CERTS not in config:
            raise LabError(
                f"cluster {cluster} was not made by netclab: its containerd does not read "
                f"{CONTAINERD_CERTS}, so it cannot pull from the registry. `netclab down "
                f"--cluster {cluster}` removes it, or `--cluster` names another"
            )
    return nodes


def connect_registry() -> ipaddress.IPv4Address:
    """The registry's address on the kind network, joined if it is not yet.

    Docker gives the address: pinning one needs a network whose subnet was configured by
    hand, and kind leaves the IPv4 one to Docker.
    """
    networks = json.loads(
        run("docker", "inspect", REGISTRY, "--format", "{{json .NetworkSettings.Networks}}")
    )
    if KIND_NETWORK not in networks:
        with step(f"registry on the {KIND_NETWORK} network"):
            run("docker", "network", "connect", KIND_NETWORK, REGISTRY)
        networks = json.loads(
            run("docker", "inspect", REGISTRY, "--format", "{{json .NetworkSettings.Networks}}")
        )
    return private(ipaddress.ip_address(networks[KIND_NETWORK]["IPAddress"]))


def trust_registry(nodes: list[str], address: ipaddress.IPv4Address) -> None:
    for node in nodes:
        for name, toml in mirrors(address).items():
            directory = f"{CONTAINERD_CERTS}/{name}"
            run("docker", "exec", node, "mkdir", "-p", directory)
            run(
                "docker",
                "exec",
                "-i",
                node,
                "cp",
                "/dev/stdin",
                f"{directory}/hosts.toml",
                stdin=toml,
            )


def install_cni_plugins(nodes: list[str]) -> None:
    with step(f"CNI plugins {CNI_PLUGINS}: bridge, host-device"):
        for node in nodes:
            url = cni_plugins_url(run("docker", "exec", node, "uname", "-m").strip())
            run(
                "docker",
                "exec",
                node,
                "bash",
                "-c",
                f"curl -sSfL {url} | tar -xz -C /opt/cni/bin ./bridge ./host-device",
            )


def install_multus(cluster: str) -> None:
    with step(f"Multus {MULTUS}"):
        run("kubectl", "--context", context(cluster), "apply", "--filename", multus_url())
        run(
            "kubectl",
            "--context",
            context(cluster),
            "--namespace",
            "kube-system",
            "rollout",
            "status",
            "daemonset/kube-multus-ds",
            "--timeout=5m",
        )


def chart_source(chart: Path | None) -> list[str]:
    """Where helm takes the chart from: the release of this package's version, or `chart`."""
    if chart is not None:
        return [str(chart)]
    return [CHART, "--repo", CHART_REPO, "--version", version("netclab")]


def check_requests(cluster: str, namespace: str, values: Path, chart: Path | None) -> None:
    """Refuse a lab whose pods ask for more than the cluster has free.

    Its pods would stay Pending, some of the lab up and the rest never.
    """
    text = run(
        "helm",
        "template",
        namespace,
        *chart_source(chart),
        "--namespace",
        namespace,
        "--values",
        str(values),
        # The chart refuses a cluster without Multus, which comes after this check;
        # rendering offline, helm knows no cluster's API.
        "--api-versions",
        "k8s.cni.cncf.io/v1",
    )
    wanted = requested([doc for doc in yaml.safe_load_all(text) if doc])
    nodes = json.loads(run("kubectl", "--context", context(cluster), "get", "nodes", "-o", "json"))
    pods = json.loads(
        run(
            "kubectl",
            "--context",
            context(cluster),
            "get",
            "pods",
            "--all-namespaces",
            "-o",
            "json",
        )
    )
    available = free(nodes["items"], pods["items"], namespace)
    if not wanted.fits(available):
        raise LabError(
            f"lab {namespace} asks for {wanted}, and cluster {cluster} has {available} free: "
            "fewer nodes, or less cpu and memory for each"
        )
    say(f"lab {namespace} asks for {wanted}, of {available} free")


def install_chart(cluster: str, namespace: str, values: Path, chart: Path | None) -> None:
    with step(f"netclab chart {chart or version('netclab')} as {namespace} in {namespace}"):
        run(
            "helm",
            "upgrade",
            "--install",
            namespace,
            *chart_source(chart),
            "--kube-context",
            context(cluster),
            "--namespace",
            namespace,
            "--create-namespace",
            "--values",
            str(values),
        )


def served(cluster: str, api_version: str) -> set[str]:
    """The kinds `cluster` serves under `api_version`; none when it serves no such group."""
    path = f"/apis/{api_version}" if "/" in api_version else f"/api/{api_version}"
    done = subprocess.run(
        ["kubectl", "--context", context(cluster), "get", "--raw", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        if "NotFound" in done.stderr or "could not find the requested resource" in done.stderr:
            return set()
        raise LabError(f"kubectl get --raw {path}\n{done.stderr.strip()}")
    return {resource["kind"] for resource in json.loads(done.stdout)["resources"]}


def install_crossplane(cluster: str, release: str) -> None:
    with step(f"Crossplane {release}"):
        run(
            "helm",
            "upgrade",
            "--install",
            "crossplane",
            "crossplane",
            "--repo",
            CROSSPLANE_REPO,
            "--version",
            chart_version(release),
            "--kube-context",
            context(cluster),
            "--namespace",
            CROSSPLANE_NAMESPACE,
            "--create-namespace",
            "--wait",
            # A first install that fails is uninstalled, or its release would stay
            # pending and refuse the next `up`.
            "--rollback-on-failure",
        )


def install_configuration(cluster: str, package: str) -> None:
    wanted = configuration(package)
    name = wanted["metadata"]["name"]
    with step(f"Configuration {package}, until it is healthy"):
        run(
            "kubectl",
            "--context",
            context(cluster),
            "apply",
            "--server-side",
            "--filename",
            "-",
            stdin=json.dumps(wanted),
        )
        done = subprocess.run(
            [
                "kubectl",
                "--context",
                context(cluster),
                "wait",
                f"configuration/{name}",
                "--for=condition=Healthy",
                f"--timeout={PACKAGE_TIMEOUT}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            # Crossplane says why in the condition, such as the dependency it cannot resolve.
            message = run(
                "kubectl",
                "--context",
                context(cluster),
                "get",
                f"configuration/{name}",
                '--output=jsonpath={.status.conditions[?(@.type=="Healthy")].message}',
            ).strip()
            raise LabError(
                f"Configuration {name} is not healthy after {PACKAGE_TIMEOUT}: "
                f"{message or done.stderr.strip()}"
            )


def apply_manifest(cluster: str, namespace: str, manifest: Manifest) -> None:
    with step(f"apply {manifest.path.name}: {describe(manifest)}"):
        run(
            "kubectl",
            "--context",
            context(cluster),
            "apply",
            "--server-side",
            "--namespace",
            namespace,
            "--filename",
            str(manifest.path),
        )


def apply_served(cluster: str, namespace: str, manifests: list[Manifest]) -> list[Manifest]:
    """Apply each manifest whose kinds `cluster` serves; the others, still to apply."""
    serves = functools.cache(functools.partial(served, cluster))
    later = []
    for manifest in manifests:
        if unserved(manifest, serves):
            later.append(manifest)
        else:
            apply_manifest(cluster, namespace, manifest)
    return later


def wait_until_served(cluster: str, manifests: list[Manifest]) -> None:
    """Return once `cluster` serves every kind in `manifests`, or say which it does not."""
    deadline = time.monotonic() + KINDS_TIMEOUT
    while True:
        serves = functools.cache(functools.partial(served, cluster))
        waiting = {m.path.name: unserved(m, serves) for m in manifests}
        waiting = {name: kinds for name, kinds in waiting.items() if kinds}
        if not waiting:
            return
        if time.monotonic() > deadline:
            said = "; ".join(f"{name}: {', '.join(kinds)}" for name, kinds in waiting.items())
            raise LabError(f"the cluster does not serve these kinds: {said}")
        time.sleep(KINDS_POLL)


def install_crossplane_lab(
    cluster: str, namespace: str, crossplane: Crossplane, manifests: list[Manifest]
) -> None:
    install_crossplane(cluster, crossplane.version)
    later = apply_served(cluster, namespace, manifests)
    if crossplane.configuration:
        install_configuration(cluster, crossplane.configuration)
    if not later:
        return
    # All of them before any, so they go in as the list has them.
    with step(f"the kinds in {', '.join(m.path.name for m in later)}"):
        wait_until_served(cluster, later)
    for manifest in later:
        apply_manifest(cluster, namespace, manifest)


def up(
    cluster: str,
    namespace: str,
    values: Path,
    chart: Path | None = None,
    crossplane: Crossplane | None = None,
) -> None:
    """The lab `values` describes, in `namespace` of `cluster`; the cluster made if need be.

    `chart` is a chart directory to install instead of the released chart. `crossplane`
    adds Crossplane, and what goes into it, once the chart is installed.
    """
    manifests = [read_manifest(path) for path in crossplane.manifests] if crossplane else []
    check_tools()
    ensure_registry()
    nodes = ensure_cluster(cluster)
    check_requests(cluster, namespace, values, chart)
    address = connect_registry()
    trust_registry(nodes, address)
    install_cni_plugins(nodes)
    install_multus(cluster)
    install_chart(cluster, namespace, values, chart)
    if crossplane:
        install_crossplane_lab(cluster, namespace, crossplane, manifests)


def owned_by_kubernetes(namespace: str) -> bool:
    return namespace in KUBERNETES_NAMESPACES


def down(cluster: str, namespace: str | None) -> None:
    """The lab in `namespace` removed, or with no namespace the whole cluster.

    The registry, and the images in it, stay either way.
    """
    check_tools()
    if cluster not in run("kind", "get", "clusters").split():
        say(f"no kind cluster {cluster}")
        return
    if namespace is None:
        with step(f"kind cluster {cluster} deleted; the registry {REGISTRY} stays"):
            run("kind", "delete", "cluster", "--name", cluster)
        return
    released = run(
        "helm", "list", "--kube-context", context(cluster), "--namespace", namespace, "--short"
    ).split()
    # The chart's post-delete hook unplugs the lab's links from the node, so the
    # release goes before its namespace.
    if namespace in released:
        with step(f"lab {namespace} uninstalled"):
            run(
                "helm",
                "uninstall",
                namespace,
                "--kube-context",
                context(cluster),
                "--namespace",
                namespace,
                "--wait",
            )
    else:
        say(f"no lab {namespace}")
    if owned_by_kubernetes(namespace):
        return
    found = run(
        "kubectl",
        "--context",
        context(cluster),
        "get",
        "namespace",
        namespace,
        "--ignore-not-found",
        "--output=name",
    ).strip()
    if not found:
        return
    with step(f"namespace {namespace} deleted"):
        run(
            "kubectl",
            "--context",
            context(cluster),
            "delete",
            "namespace",
            namespace,
            "--wait=true",
        )
