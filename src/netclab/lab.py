"""A lab on kind: the registry, the cluster, the CNI plugins, Multus, and the chart.

Every step looks before it acts, so `up` on a running lab changes only what differs.
"""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
from importlib.metadata import version
from pathlib import Path

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


class LabError(Exception):
    """What stops a lab from coming up, said so a user can act on it."""


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


def say(step: str) -> None:
    print(f">> {step}", flush=True)


def check_tools() -> None:
    missing = [tool for tool in TOOLS if shutil.which(tool) is None]
    if missing:
        raise LabError(f"not found: {', '.join(missing)}")


def ensure_registry() -> None:
    running = run("docker", "ps", "--quiet", "--filter", f"name=^{REGISTRY}$").strip()
    if running:
        return
    say(f"registry {REGISTRY}, its images on the volume {REGISTRY_VOLUME}")
    exists = run("docker", "ps", "--all", "--quiet", "--filter", f"name=^{REGISTRY}$").strip()
    if exists:
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
        say(f"kind cluster {cluster}")
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
        say(f"registry on the {KIND_NETWORK} network")
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
    say(f"CNI plugins {CNI_PLUGINS}: bridge, host-device")
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
    say(f"Multus {MULTUS}")
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


def install_chart(cluster: str, namespace: str, values: Path, chart: Path | None) -> None:
    say(f"netclab chart {chart or version('netclab')} as {namespace} in {namespace}")
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


def up(cluster: str, namespace: str, values: Path, chart: Path | None = None) -> None:
    """The lab `values` describes, in `namespace` of `cluster`; the cluster made if need be.

    `chart` is a chart directory to install instead of the released chart.
    """
    check_tools()
    ensure_registry()
    nodes = ensure_cluster(cluster)
    address = connect_registry()
    trust_registry(nodes, address)
    install_cni_plugins(nodes)
    install_multus(cluster)
    install_chart(cluster, namespace, values, chart)


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
        say(f"kind cluster {cluster} deleted; the registry {REGISTRY} stays")
        run("kind", "delete", "cluster", "--name", cluster)
        return
    released = run(
        "helm", "list", "--kube-context", context(cluster), "--namespace", namespace, "--short"
    ).split()
    # The chart's post-delete hook unplugs the lab's links from the node, so the
    # release goes before its namespace.
    if namespace in released:
        say(f"lab {namespace} uninstalled")
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
    say(f"namespace {namespace} deleted")
    run("kubectl", "--context", context(cluster), "delete", "namespace", namespace, "--wait=true")
