[![GitHub Repo stars](https://img.shields.io/github/stars/netclab/netclab-chart?style=for-the-badge&color=CFB002)](https://github.com/netclab/netclab-chart)
[![GitHub Downloads](https://img.shields.io/github/downloads/netclab/netclab-chart/total?style=for-the-badge&label=HELM%20CHART%20DOWNLOADS&color=f200ff)](https://github.com/netclab/netclab-chart/releases)
[![GitHub Release](https://img.shields.io/github/v/release/netclab/netclab-chart?style=for-the-badge&color=007EC6)](https://github.com/netclab/netclab-chart/releases/latest)
[![GitHub Issues](https://img.shields.io/github/issues/netclab/netclab-chart?style=for-the-badge&color=FF6F00)](https://github.com/netclab/netclab-chart/issues)
[![GitHub Pull Requests](https://img.shields.io/github/issues-pr/netclab/netclab-chart?style=for-the-badge&color=44CC11)](https://github.com/netclab/netclab-chart/pulls)

<br>

## ❓ What is this?
Helm chart for automating the deployment of virtual network topologies on Kubernetes using Pods with multiple interfaces.
It leverages the [Multus CNI](https://github.com/k8snetworkplumbingwg/multus-cni) plugin and renders the required Kubernetes resources (e.g., ConfigMaps, Pods, NetworkAttachmentDefinitions) from a structured YAML-based topology definition.
<br/>
Use it to quickly bring up containerized network labs for testing, automation, development, and education — all within your cluster.
`netclab`, on PyPI, brings the cluster and a lab up around it in one command.


## ⚙️ Use cases
This chart enables rapid deployment of containerized network topologies on Kubernetes. Key use cases include:
- **Network design validation**: Test high- and low-level design (HLD/LLD) configurations and device behavior before committing to a final design.
- **Test automation**: Develop and verify automation scripts for traffic or protocol generators/analyzers (e.g., IxNetwork APIs, OTG) — effectively unit-testing your test logic.
- **Image validation**: Validate new versions of network operating systems (NOS), whether virtual or hardware-aligned, to ensure feature support and functionality.
- **Training & certification prep**: Practice CLI, protocols, and topologies in a safe, repeatable lab — ideal for students and professionals preparing for vendor certifications.


## 📦 Prerequisites
Before installing Netclab Chart, ensure the following are present:

- [docker](https://docs.docker.com/engine/install/)
- [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation)
- [kubectl](https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/)
- [helm](https://helm.sh/docs/intro/install/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/), for `uvx netclab`


## ⚡ Quick start: `netclab`

`netclab` brings a lab up in one command: the kind cluster, the CNI plugins,
Multus, a local registry, and this chart, from a topology file such as
[`examples/topology-frrouting.yaml`](https://github.com/netclab/netclab-chart/blob/main/examples/topology-frrouting.yaml):

```bash
uvx netclab up --namespace dc2 --values topology-frrouting.yaml
uvx netclab down --namespace dc2    # this lab
uvx netclab down                    # the whole cluster; the registry and its images stay
```

cEOS cannot be pulled, so it goes into the registry once. The cluster finds `ceos:<version>`
there before Docker Hub, and the topology names it in `ceos.image`:

```bash
docker import ./cEOS64-lab-<version>.tar.xz ceos:<version>
docker tag ceos:<version> localhost:5001/library/ceos:<version>
docker push localhost:5001/library/ceos:<version>
```

```yaml
ceos:
  image: ceos:<version>
```

`netclab` works only on a cluster made with its
[`kind.yaml`](https://github.com/netclab/netclab-chart/blob/main/src/netclab/kind.yaml),
and refuses any other; `netclab down` removes it. It also refuses a lab whose nodes ask
for more CPU or memory than the cluster has free. etcd keeps its data in memory, so the
cluster does not survive a restart of Docker or of the host.


## 🧩 Chart values

> **Note:**<br>
> Node and network names must use lowercase letters, digits, or hyphens.<br>
> Host interface names are limited to **15 characters**.<br>
> Netclab computes veth names as `<release>-<network>-<hash(node)>`, automatically shortening long node names for Linux compatibility.<br>
> **Recommendation:** keep Helm release and network names short (≤ 3 characters).


Configuration options are documented in the table below.
You can override these values in your own file.

| Parameter                | Description                                                  | Defaults                           |
| ------------------------ | -------------------------------------------------------------| -----------------------------------|
| `topology.networks.type` | Type of connection between nodes. Can be `bridge` or `veth`. | `veth`                             |
| `topology.nodes.type`    | Type of node. Can be: `srlinux`, `frrouting`, `ceos`, `linux`|                                    |
| `topology.nodes.image`   | Container images used for topology nodes.                    | each node type's template, in [`templates/`](https://github.com/netclab/netclab-chart/tree/main/templates)<br>ceos: none, see `ceos.image` |
| `topology.nodes.memory`  | Memory allocation per node type.                             | srlinux: `4Gi`<br>frr: `512Mi`<br>ceos: `4Gi`<br>linux: `200Mi` |
| `topology.nodes.cpu`     | CPU allocation per node type.                                | srlinux: `2000m`<br>frr: `500m`<br>ceos: `2000m`<br>linux: `200m` |
| `ceos.image`             | Image of every cEOS node that names none. Required when a cEOS node names none. | none |
| `ceos.restconfSslProfile`| SSL profile for cEOS RESTCONF on port 6020.                  | `ARISTA_DEFAULT_SELF_SIGNED_PROFILE` |


## 🧪 A lab with Crossplane

A lab for Kubernetes-managed network configuration, such as netadopt's AVD fabric, also
needs Crossplane and its packages. `netclab up` installs them after the chart:

```bash
uvx netclab up --namespace dc1 --values topology.yaml \
  --crossplane <version> \
  --configuration xpkg.upbound.io/netclab/configuration-avd:<version> \
  --manifest runtime.yaml --manifest providerconfig.yaml --manifest fabric.yaml
```

- `--configuration` installs the package and waits until it is healthy.
- Each `--manifest` is applied server-side once the cluster serves every kind in it:
  before the Configuration if it already does, after it otherwise, in the order given.

Without `--namespace` and `--values` there is no chart, only the cluster with Crossplane,
for devices started apart from it:

```bash
uvx netclab up --cluster xp --crossplane <version>
```


## 🛠️ Without the `netclab` command

A lab with kind, Multus and helm, step by step, and the example topologies for
SR Linux, FRR and cEOS:
[docs/manual.md](https://github.com/netclab/netclab-chart/blob/main/docs/manual.md).


## 🧭 Future Plans

- Add support for additional containerized or virtualized routers


## 🤝 Contributing

Found an issue or have an idea?
Open an issue or submit a PR at: <br>
👉 [https://github.com/netclab/netclab-chart](https://github.com/netclab/netclab-chart)
