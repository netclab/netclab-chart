"""What `up` computes before it runs anything: addresses, and what it writes to a node."""

from __future__ import annotations

import ipaddress
from importlib.metadata import version

import pytest
import yaml

from netclab import lab


@pytest.mark.parametrize("address", ["172.18.0.3", "192.168.32.2", "10.89.0.5"])
def test_a_registry_at_a_private_address_is_taken(address):
    assert lab.private(ipaddress.ip_address(address)) == ipaddress.ip_address(address)


def test_a_registry_outside_rfc_1918_is_refused():
    with pytest.raises(lab.LabError, match="outside RFC 1918"):
        lab.private(ipaddress.ip_address("100.64.0.3"))


def test_containerd_reaches_the_registry_by_its_name_under_both_of_its_names():
    registry = '[host."http://kind-registry:5000"]\n  capabilities = ["pull", "resolve"]\n'

    found = lab.mirrors(ipaddress.ip_address("172.18.0.3"))

    assert found["localhost:5001"] == registry
    assert found["172.18.0.3:5000"] == registry


def test_docker_hub_is_tried_after_the_registry():
    docker_io = lab.mirrors(ipaddress.ip_address("172.18.0.3"))["docker.io"]

    assert docker_io == (
        'server = "https://registry-1.docker.io"\n\n'
        '[host."http://kind-registry:5000"]\n  capabilities = ["pull", "resolve"]\n'
    )


def test_the_kind_cluster_reads_the_registry_mirrors():
    config = yaml.safe_load(lab.KIND_CONFIG)

    assert f'config_path = "{lab.CONTAINERD_CERTS}"' in config["containerdConfigPatches"][0]


@pytest.mark.parametrize(("machine", "arch"), [("x86_64", "amd64"), ("aarch64", "arm64")])
def test_the_cni_plugins_are_the_nodes_architecture(machine, arch):
    assert lab.cni_plugins_url(machine).endswith(f"/cni-plugins-linux-{arch}-{lab.CNI_PLUGINS}.tgz")


def test_a_node_on_another_architecture_is_refused():
    with pytest.raises(lab.LabError, match="riscv64"):
        lab.cni_plugins_url("riscv64")


@pytest.mark.parametrize("namespace", lab.KUBERNETES_NAMESPACES)
def test_a_lab_in_a_namespace_kubernetes_made_leaves_the_namespace(namespace):
    assert lab.owned_by_kubernetes(namespace)


def test_a_lab_in_its_own_namespace_takes_the_namespace_with_it():
    assert not lab.owned_by_kubernetes("dc2")


def test_the_chart_is_the_release_of_the_packages_version():
    assert lab.chart_source(None) == [
        "netclab",
        "--repo",
        "https://netclab.github.io/netclab-chart",
        "--version",
        version("netclab"),
    ]


def test_a_chart_directory_replaces_the_release(tmp_path):
    assert lab.chart_source(tmp_path) == [str(tmp_path)]
