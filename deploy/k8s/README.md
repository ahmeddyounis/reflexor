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

1. Review and update:

```sh
cp deploy/k8s/base/secret.example.yaml deploy/k8s/base/secret.yaml
```

2. Edit the secret values and image references.

3. Apply the namespace-scoped resources:

```sh
kubectl apply -k deploy/k8s/base
kubectl apply -f deploy/k8s/base/secret.yaml
```

4. Run the migration job before scaling API/worker:

```sh
kubectl delete job reflexor-db-migrate -n reflexor --ignore-not-found
kubectl apply -f deploy/k8s/base/migrate-job.yaml
kubectl wait --for=condition=complete job/reflexor-db-migrate -n reflexor --timeout=5m
```

5. Roll out API and worker:

```sh
kubectl rollout status deploy/reflexor-api -n reflexor
kubectl rollout status deploy/reflexor-worker -n reflexor
```

## Notes

- The manifests assume a namespace called `reflexor`.
- `secret.example.yaml` is intentionally excluded from the Kustomize resource list.
- The maintenance CronJob runs `reflexor maintenance run --json` hourly.
- Add ingress, cert-manager, and network policies according to your cluster standards.
