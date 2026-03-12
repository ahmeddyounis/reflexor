# Kubernetes deployment (production baseline)

This directory contains a Kubernetes-oriented production baseline for Reflexor.

Scope:

- API deployment
- worker deployment
- migration job
- maintenance cron job
- PVC-backed workspace
- service account, service, and disruption budgets

## Layout

- `base/`: apply-ready manifests for a single namespace deployment
- `base/secret.example.yaml`: copy to `secret.yaml`, replace placeholders, and apply separately

## Apply

1. Create the namespace first:

```sh
kubectl apply -f deploy/k8s/base/namespace.yaml
```

2. Review and update:

```sh
cp deploy/k8s/base/secret.example.yaml deploy/k8s/base/secret.yaml
```

3. Edit the secret values and image references.

4. Apply the secret before the workloads that reference it:

```sh
kubectl apply -f deploy/k8s/base/secret.yaml
```

5. Apply the namespace-scoped resources:

```sh
kubectl apply -k deploy/k8s/base
```

6. Run the migration job before scaling API/worker:

```sh
kubectl delete job reflexor-db-migrate -n reflexor --ignore-not-found
kubectl apply -f deploy/k8s/base/migrate-job.yaml
kubectl wait --for=condition=complete job/reflexor-db-migrate -n reflexor --timeout=5m
```

7. Roll out API and worker:

```sh
kubectl rollout status deploy/reflexor-api -n reflexor
kubectl rollout status deploy/reflexor-worker -n reflexor
```

## Notes

- The manifests assume a namespace called `reflexor`.
- `secret.example.yaml` is intentionally excluded from the Kustomize resource list but includes
  `metadata.namespace: reflexor` so direct `kubectl apply -f ...` lands in the right namespace.
- The maintenance CronJob runs `reflexor maintenance run --json` hourly.
- `workspace-pvc.yaml` assumes a storage class that supports `ReadWriteMany`; replace it or
  reduce shared workspace usage if your cluster only supports `ReadWriteOnce`.
- Add ingress, cert-manager, and network policies according to your cluster standards.
