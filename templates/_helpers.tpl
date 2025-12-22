{{- define "netclab.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end }}


{{- define "netclab.podAffinity" -}}
podAffinity:
  requiredDuringSchedulingIgnoredDuringExecution:
  - labelSelector:
      matchExpressions:
      - key: app.kubernetes.io/name
        operator: In
        values:
        - {{ .Chart.Name }}
      - key: app.kubernetes.io/instance
        operator: In
        values:
        - {{ .Release.Name }}
    topologyKey: kubernetes.io/hostname
{{- end }}


{{- define "netclab.networkTypeMap" -}}
  {{- $networkDefaultType := "veth" -}}
  {{- $networkTypeMap := dict -}}
  {{- range .Values.topology.networks -}}
    {{- $networkTypeMap = merge $networkTypeMap (dict .name (default $networkDefaultType .type)) -}}
  {{- end -}}
  {{ toYaml $networkTypeMap }}
{{- end -}}


{{/*
Generate a Linux-safe veth name (<=15 chars):
<release>-<network>-<hash(node)>
*/}}
{{- define "netclab.vethName" -}}
{{- $release := .release -}}
{{- $network := .network -}}
{{- $node := .node -}}

{{- $baseLen := add (len $release) (len $network) 2 -}}
{{- $hashLen := sub 15 $baseLen -}}

{{- if le $hashLen 0 -}}
{{- fail "release/network names too long to generate veth name" -}}
{{- end -}}

{{- $hash := trunc (int $hashLen) (sha1sum $node) -}}
{{- printf "%s-%s-%s" $release $network $hash -}}
{{- end }}


{{- /*
Return the joined networks string for a given node and the networkTypeMap,
networks prefixed with .Release.Name
Expected inputs:
- .node: the node object (with interfaces and name)
- .networkTypeMap: map of network name -> network type
- .root: full context
*/ -}}
{{- define "netclab.nodeNetworks" -}}
  {{- $node := .node -}}
  {{- $networkTypeMap := .networkTypeMap -}}
  {{- $root := .root -}}
  {{- $nets := list -}}

  {{- range $iface := $node.interfaces -}}
    {{- if eq (index $networkTypeMap $iface.network) "veth" }}
      {{- $veth := include "netclab.vethName" (dict
          "release" $root.Release.Name
          "network" $iface.network
          "node"    $node.name
        ) -}}
      {{- $nets = append $nets (printf "%s@%s" $veth $iface.name) -}}
    {{- else }}
      {{- $nets = append $nets (printf "%s-%s@%s" $root.Release.Name $iface.network $iface.name) }}
    {{- end }}
  {{- end }}

  {{- join "," $nets }}
{{- end }}


{{- define "netclab.hasVeth" -}}
  {{- $networkTypeMap := include "netclab.networkTypeMap" . | fromYaml -}}
  {{- $hasVeth := false -}}
  {{- range $name, $type := $networkTypeMap -}}
    {{- if eq $type "veth" -}}
      {{- $hasVeth = true -}}
    {{- end -}}
  {{- end -}}
  {{- if $hasVeth }}true{{ else }}false{{ end }}
{{- end -}}
