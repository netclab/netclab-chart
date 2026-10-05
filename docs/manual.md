# netclab-chart by hand

A lab with kind, Multus and helm, step by step.

## 📦 Prerequisites

- [docker](https://docs.docker.com/engine/install/)
- [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation)
- [kubectl](https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/)
- [helm](https://helm.sh/docs/intro/install/)
- [git](https://git-scm.com/downloads)


## 🚀 The cluster

- Kind cluster:
```bash
kind create cluster --name netclab
```

- CNI plugins (bridge and host-device), from their
  [latest release](https://github.com/containernetworking/plugins/releases/latest):
```bash
CNI=$(basename "$(curl -s -o /dev/null -w '%{redirect_url}' https://github.com/containernetworking/plugins/releases/latest)")
docker exec netclab-control-plane bash -c \
"curl -L https://github.com/containernetworking/plugins/releases/download/${CNI}/cni-plugins-linux-amd64-${CNI}.tgz \
| tar -xz -C /opt/cni/bin ./bridge ./host-device"
```

- Multus CNI plugin, thin, from its
  [latest release](https://github.com/k8snetworkplumbingwg/multus-cni/releases/latest):
```bash
MULTUS=$(basename "$(curl -s -o /dev/null -w '%{redirect_url}' https://github.com/k8snetworkplumbingwg/multus-cni/releases/latest)")
kubectl apply -f https://raw.githubusercontent.com/k8snetworkplumbingwg/multus-cni/${MULTUS}/deployments/multus-daemonset.yml
kubectl -n kube-system wait --for=jsonpath='{.status.numberReady}'=1 --timeout=5m daemonset.apps/kube-multus-ds
```

- Add helm repo for netclab chart:
```bash
helm repo add netclab https://netclab.github.io/netclab-chart
helm repo update
```

- cEOS cannot be pulled, so the chart has no default image for it. Download the cEOS image
  from Arista Networks and name it in `ceos.image`. `netclab` keeps it in a local
  registry; by hand, it is loaded into the node:
```bash
docker import ./cEOS64-lab-<version>.tar.xz ceos:<version>
kind load docker-image ceos:<version> -n netclab
```
  After loading, you can verify the image with:
```bash
docker exec netclab-control-plane crictl images | grep ceos
```


## 🧱 Example topology

To make the topology and config files easy to reach:

```bash
git clone https://github.com/netclab/netclab-chart.git && cd netclab-chart
```

```console
+--------+
| h01    |
|        |
|    e1  |
+--------+
    |
    b2
    |
+-----------+          +-----------+
| e1-2      |          | srl02 or  |
| or eth2   |          | frr02 or  |
|           |          | ceos02    |
|           |          |           |
|           |          |           |
|       e1-1| -- b1 -- | e1-1      |
|    or eth1|          | or eth1   |
|           |          |           |
|           |          |           |
| srl01 or  |          |           |
| frr01 or  |          |     e1-2  |
| ceos01    |          |  or eth2  |
+-----------+          +-----------+
                              |
                              b3
                              |
                         +--------+
                         |     e1 |
                         |        |
                         | h02    |
                         +--------+
```

### Follow instructions for **SR Linux** or/and **FRRouting** or/and **cEOS**

> **Note:** The topologies are independent and can run in separate Kubernetes namespaces.


<details>
<summary>SR Linux details</summary>

- Start nodes:
  ```bash
  helm install dc1 netclab/netclab --values ./examples/topology-srlinux.yaml
  ```
  
  ```bash
  kubectl get pod
  ```

  ```console
  NAME                 READY   STATUS    RESTARTS   AGE
  h01                  1/1     Running   0          12s
  h02                  1/1     Running   0          12s
  srl01                1/1     Running   0          12s
  srl02                1/1     Running   0          12s
  ```

- Configure nodes (repeat if they're not ready yet):
  ```bash
  kubectl exec h01 -- ip address replace 172.20.0.2/24 dev e1
  kubectl exec h01 -- ip route replace 172.30.0.0/24 via 172.20.0.1

  kubectl exec h02 -- ip address replace 172.30.0.2/24 dev e1
  kubectl exec h02 -- ip route replace 172.20.0.0/24 via 172.30.0.1

  kubectl cp ./examples/srl01.cfg srl01:/srl01.cfg
  kubectl exec srl01 -- bash -c 'sr_cli --candidate-mode --commit-at-end < /srl01.cfg'

  kubectl cp ./examples/srl02.cfg srl02:/srl02.cfg
  kubectl exec srl02 -- bash -c 'sr_cli --candidate-mode --commit-at-end < /srl02.cfg'
  ```

  ```console
  All changes have been committed. Leaving candidate mode.
  All changes have been committed. Leaving candidate mode.
  ```

- Test (convergence may take time):
  ```bash
  kubectl exec h01 -- ping 172.30.0.2 -I 172.20.0.2
  ```

- LLDP neighbor information:
  ```bash
  kubectl exec srl01 -- sr_cli show system lldp neighbor
  ```

  ```console
  +--------------+-------------------+----------------------+---------------------+------------------------+----------------------+---------------+
  |     Name     |     Neighbor      | Neighbor System Name | Neighbor Chassis ID | Neighbor First Message | Neighbor Last Update | Neighbor Port |
  +==============+===================+======================+=====================+========================+======================+===============+
  | ethernet-1/1 | 00:01:03:FF:00:00 | srl02                | 00:01:03:FF:00:00   | 47 seconds ago         | 24 seconds ago       | ethernet-1/1  |
  +--------------+-------------------+----------------------+---------------------+------------------------+----------------------+---------------+
  ```
</details>


<details>
<summary>FRRouting details</summary>

- Start nodes:
  ```bash
  helm install dc2 netclab/netclab --values examples/topology-frrouting.yaml --namespace dc2 --create-namespace
  kubectl config set-context --current --namespace dc2
  ```

  ```bash
  kubectl get pod
  ```

  ```console
  NAME               READY   STATUS    RESTARTS   AGE
  frr01              1/1     Running   0          6s
  frr02              1/1     Running   0          6s
  h01                1/1     Running   0          6s
  h02                1/1     Running   0          6s
  ```

- Configure nodes (repeat if they're not ready yet):
  ```bash
  kubectl exec h01 -- ip address replace 172.20.0.2/24 dev e1
  kubectl exec h01 -- ip route replace 172.30.0.0/24 via 172.20.0.1
  
  kubectl exec h02 -- ip address replace 172.30.0.2/24 dev e1
  kubectl exec h02 -- ip route replace 172.20.0.0/24 via 172.30.0.1
  
  kubectl exec frr01 -- ip address add 10.0.0.1/32 dev lo
  kubectl exec frr01 -- ip address replace 10.0.1.1/24 dev e1-1
  kubectl exec frr01 -- ip address replace 172.20.0.1/24 dev e1-2
  kubectl exec frr01 -- touch /etc/frr/vtysh.conf
  kubectl exec frr01 -- sed -i -e 's/bgpd=no/bgpd=yes/g' /etc/frr/daemons
  kubectl exec frr01 -- sh -c '/usr/lib/frr/frrinit.sh start > /var/log/frrinit.log 2>&1; tail -1 /var/log/frrinit.log'
  kubectl cp ./examples/frr01.cfg frr01:/frr01.cfg
  kubectl exec frr01 -- vtysh -f /frr01.cfg
  
  kubectl exec frr02 -- ip address add 10.0.0.2/32 dev lo
  kubectl exec frr02 -- ip address replace 10.0.1.2/24 dev e1-1
  kubectl exec frr02 -- ip address replace 172.30.0.1/24 dev e1-2
  kubectl exec frr02 -- touch /etc/frr/vtysh.conf
  kubectl exec frr02 -- sed -i -e 's/bgpd=no/bgpd=yes/g' /etc/frr/daemons
  kubectl exec frr02 -- sh -c '/usr/lib/frr/frrinit.sh start > /var/log/frrinit.log 2>&1; tail -1 /var/log/frrinit.log'
  kubectl cp ./examples/frr02.cfg frr02:/frr02.cfg
  kubectl exec frr02 -- vtysh -f /frr02.cfg
  ```

  FRR's daemons keep the output of the command that starts them open, so `kubectl exec`
  would not return: the start writes to a file instead.

- Test (convergence may take time):
  ```bash
  kubectl exec h01 -- ping 172.30.0.2 -I 172.20.0.2
  ```
</details>


<details>
<summary>cEOS details</summary>

- Start nodes:
  ```bash
  helm install dc3 netclab/netclab --values examples/topology-ceos.yaml --set ceos.image=ceos:<version> --namespace dc3 --create-namespace
  kubectl config set-context --current --namespace dc3
  ```

  ```bash
  kubectl get pod
  ```

  ```console
  NAME     READY   STATUS    RESTARTS   AGE
  ceos01   1/1     Running   0          8s
  ceos02   1/1     Running   0          8s
  h01      1/1     Running   0          8s
  h02      1/1     Running   0          8s
  ```

- Configure nodes (repeat if they're not ready yet):
  ```bash
  kubectl exec h01 -- ip address replace 172.20.0.2/24 dev e1
  kubectl exec h01 -- ip route replace 172.30.0.0/24 via 172.20.0.1

  kubectl exec h02 -- ip address replace 172.30.0.2/24 dev e1
  kubectl exec h02 -- ip route replace 172.20.0.0/24 via 172.30.0.1

  kubectl cp ./examples/ceos01.cfg ceos01:/ceos01.cfg
  kubectl exec ceos01 -- bash -c 'Cli -p 15 /ceos01.cfg'

  kubectl cp ./examples/ceos02.cfg ceos02:/ceos02.cfg
  kubectl exec ceos02 -- bash -c 'Cli -p 15 /ceos02.cfg'
  ```

- Test (convergence may take time):
  ```bash
  kubectl exec h01 -- ping 172.30.0.2 -I 172.20.0.2
  ```

- LLDP neighbor information:
  ```bash
  kubectl exec -ti ceos01 -- Cli -p 15 -c "show lldp neighbors"
  ```

  ```console
  Last table change time   : 0:00:48 ago
  Number of table inserts  : 1
  Number of table deletes  : 0
  Number of table drops    : 0
  Number of table age-outs : 0
  
  Port Neighbor Device ID Neighbor Port ID TTL
  ---- ------------------ ---------------- ---
  Et1  ceos02             Ethernet1        120
  ```
</details>


<details>
<summary>Uninstall topologies</summary>

- dc3:
  ```bash
  kubectl config set-context --current --namespace default
  helm uninstall dc3 --namespace dc3
  kubectl delete ns dc3
  ```
- dc2:
  ```bash
  helm uninstall dc2 --namespace dc2
  kubectl delete ns dc2
  ```
- dc1:
  ```bash
  helm uninstall dc1
  ```
</details>
