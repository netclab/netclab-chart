"""What `up` computes before it runs anything: addresses, what it writes to a node, and
the order it puts Crossplane's objects in."""

from __future__ import annotations

import ipaddress
from importlib.metadata import version
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from netclab import lab
from netclab.cli import app


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


@pytest.mark.parametrize("release", ["v2.4.2", "2.4.2"])
def test_the_crossplane_chart_is_the_release_without_its_v(release):
    assert lab.chart_version(release) == "2.4.2"


@pytest.mark.parametrize(
    "package",
    [
        "xpkg.upbound.io/netclab/configuration-avd:v0.2.1",
        "172.18.0.2:5000/netclab/configuration-avd:v0.2.1",
        "xpkg.upbound.io/netclab/configuration-avd@sha256:0f00",
    ],
)
def test_a_configuration_is_named_after_its_repository(package):
    wanted = lab.configuration(package)

    assert wanted["metadata"]["name"] == "configuration-avd"
    assert wanted["spec"]["package"] == package


def test_a_manifest_is_read_whatever_tags_its_values_carry(tmp_path):
    path = tmp_path / "fabric.yaml"
    path.write_text(
        "---\n"
        "apiVersion: avd.netclab.dev/v1alpha1\nkind: Fabric\nmetadata: {name: dc1}\n"
        "---\n"
        "apiVersion: avd.netclab.dev/v1alpha1\nkind: FabricInput\nmetadata: {name: dc1-a}\n"
        "spec:\n  design:\n    bgp_password: !vault |\n      $ANSIBLE_VAULT;1.1;AES256\n"
    )

    assert lab.read_manifest(path).docs == (
        ("avd.netclab.dev/v1alpha1", "Fabric", "dc1"),
        ("avd.netclab.dev/v1alpha1", "FabricInput", "dc1-a"),
    )


def test_a_file_that_is_no_manifest_is_refused(tmp_path):
    path = tmp_path / "values.yaml"
    path.write_text("topology:\n  nodes: []\n")

    with pytest.raises(lab.LabError, match="values.yaml: not a Kubernetes manifest"):
        lab.read_manifest(path)


def test_a_manifest_is_described_by_its_kinds():
    manifest = lab.Manifest(
        Path("fabric.yaml"),
        (
            ("avd.netclab.dev/v1alpha1", "Fabric", "dc1"),
            ("avd.netclab.dev/v1alpha1", "FabricInput", "dc1-a"),
            ("avd.netclab.dev/v1alpha1", "FabricInput", "dc1-b"),
        ),
    )

    assert lab.describe(manifest) == "Fabric/dc1, FabricInput (2)"


def test_the_kinds_not_served_are_named_once_each():
    manifest = lab.Manifest(
        Path("fabric.yaml"),
        (
            ("v1", "ConfigMap", "dc1-pools"),
            ("avd.netclab.dev/v1alpha1", "Fabric", "dc1"),
            ("avd.netclab.dev/v1alpha1", "FabricInput", "dc1-a"),
            ("avd.netclab.dev/v1alpha1", "FabricInput", "dc1-b"),
        ),
    )
    kinds = {"v1": {"ConfigMap"}, "avd.netclab.dev/v1alpha1": {"Fabric"}}

    assert lab.unserved(manifest, lambda api: kinds.get(api, set())) == [
        "FabricInput (avd.netclab.dev/v1alpha1)"
    ]


class FakeCluster:
    """Serves pkg.crossplane.io from the start, and the Configuration's kinds once it is in."""

    def __init__(self, monkeypatch):
        self.kinds = {"pkg.crossplane.io/v1beta1": {"DeploymentRuntimeConfig", "ImageConfig"}}
        self.done = []
        monkeypatch.setattr(lab, "KINDS_POLL", 0)
        monkeypatch.setattr(lab, "install_crossplane", lambda cluster, release: None)
        monkeypatch.setattr(lab, "served", lambda cluster, api: self.kinds.get(api, set()))
        monkeypatch.setattr(lab, "install_configuration", self.install_configuration)
        monkeypatch.setattr(lab, "apply_manifest", lambda c, ns, m: self.done.append(m.path.name))

    def install_configuration(self, cluster, package):
        self.done.append(package)
        self.kinds["http.m.crossplane.io/v1alpha2"] = {"ClusterProviderConfig"}


RUNTIME = lab.Manifest(
    Path("runtime.yaml"),
    (
        ("pkg.crossplane.io/v1beta1", "DeploymentRuntimeConfig", "function-avd"),
        ("pkg.crossplane.io/v1beta1", "ImageConfig", "function-avd"),
    ),
)
PROVIDERCONFIG = lab.Manifest(
    Path("providerconfig.yaml"),
    (("http.m.crossplane.io/v1alpha2", "ClusterProviderConfig", "default"),),
)


def test_a_manifest_goes_in_before_the_configuration_when_its_kinds_are_served(monkeypatch):
    cluster = FakeCluster(monkeypatch)
    wanted = lab.Crossplane("v2.4.2", "xpkg.upbound.io/netclab/configuration-avd:v0.2.1")

    lab.install_crossplane_lab("netclab", "l3ls", wanted, [PROVIDERCONFIG, RUNTIME])

    assert cluster.done == [
        "runtime.yaml",
        "xpkg.upbound.io/netclab/configuration-avd:v0.2.1",
        "providerconfig.yaml",
    ]


def test_a_kind_never_served_stops_up_with_the_file_and_the_kind(monkeypatch):
    FakeCluster(monkeypatch)
    monkeypatch.setattr(lab, "KINDS_TIMEOUT", -1)

    with pytest.raises(
        lab.LabError,
        match=r"providerconfig.yaml: ClusterProviderConfig \(http.m.crossplane.io/v1alpha2\)",
    ):
        lab.install_crossplane_lab("netclab", "l3ls", lab.Crossplane("v2.4.2"), [PROVIDERCONFIG])


def test_a_configuration_needs_crossplane(tmp_path, monkeypatch):
    # Were the check gone, `up` would bring a real lab up.
    monkeypatch.setattr(lab, "up", lambda *args: pytest.fail("up ran"))
    values = tmp_path / "values.yaml"
    values.write_text("topology: {}\n")

    done = CliRunner().invoke(
        app, ["up", "--namespace", "l3ls", "--values", str(values), "--configuration", "x/y:v1"]
    )

    assert done.exit_code != 0
    assert "need --crossplane" in done.output
