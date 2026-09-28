"""What `up` computes before it runs anything: addresses, and what it writes to a node."""

from __future__ import annotations

import ipaddress
from importlib.metadata import version

import pytest
import yaml

from netclab import lab


def test_the_registry_takes_the_last_usable_address_of_the_ipv4_subnet():
    subnets = ["fc00:f853:ccd:e793::/64", "172.18.0.0/16"]

    assert lab.registry_address(subnets) == ipaddress.ip_address("172.18.255.254")


def test_a_subnet_docker_picked_elsewhere_moves_the_registry_with_it():
    assert lab.registry_address(["192.168.32.0/20"]) == ipaddress.ip_address("192.168.47.254")


def test_a_subnet_outside_rfc_1918_is_refused():
    with pytest.raises(lab.LabError, match="outside RFC 1918"):
        lab.registry_address(["100.64.0.0/16"])


def test_a_network_without_ipv4_is_refused():
    with pytest.raises(lab.LabError, match="no IPv4 subnet"):
        lab.registry_address(["fc00:f853:ccd:e793::/64"])


def test_containerd_reaches_the_registry_by_both_its_names_over_http():
    address = ipaddress.ip_address("172.18.255.254")
    registry = '[host."http://172.18.255.254:5000"]\n  capabilities = ["pull", "resolve"]\n'

    found = lab.mirrors(address)

    assert found["localhost:5001"] == registry
    assert found["172.18.255.254:5000"] == registry


def test_docker_hub_is_tried_after_the_registry():
    address = ipaddress.ip_address("172.18.255.254")

    docker_io = lab.mirrors(address)["docker.io"]

    assert docker_io == (
        'server = "https://registry-1.docker.io"\n\n'
        '[host."http://172.18.255.254:5000"]\n  capabilities = ["pull", "resolve"]\n'
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
