# rag-coding-mcp — GitOps overlay

Local mirror of the Flux-managed overlay for this app. Edit it here, sync it to
the living machine, then run `force-apply.sh` from `~/gitops/`.

## Layout

Matches the real tree:

```
deploy/gitops/
└── clusters/my-cluster/apps/rag-coding-mcp/
    ├── namespace.yaml
    ├── pvc.yaml
    ├── deployment.yaml
    ├── service.yaml
    ├── ingress.yaml                 # Authelia forward-auth enabled
    └── kustomization.yaml
```

## Sync to the living machine

```bash
# From this repo root
rsync -av --delete deploy/gitops/clusters/my-cluster/apps/rag-coding-mcp/ \
  iulius@living:/home/iulius/gitops/clusters/my-cluster/apps/rag-coding-mcp/
```

## One-time: register the app with the root kustomization

The first time this app is deployed, add it to the root resources list on the
living machine:

```bash
ssh iulius@living \
  "grep -q 'apps/rag-coding-mcp' ~/gitops/clusters/my-cluster/kustomization.yaml \
   || sed -i '/- apps\\/welcome/a\\  - apps/rag-coding-mcp' \
      ~/gitops/clusters/my-cluster/kustomization.yaml"
```

(Then commit on the living side.)

## Apply

```bash
ssh iulius@living 'cd ~/gitops && ./force-apply.sh'
```

## Endpoint

- UI/API: `https://rag-coding-mcp.bizrader.uk` (guarded by Authelia).
- MCP transport (streamable-http): same host, path `/mcp`.
- GraphQL for the admin UI: same host, path `/graphql`.
