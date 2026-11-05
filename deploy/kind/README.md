`docker run -d --restart=always --name kind-registry -p 127.0.0.1:5001:5000 registry:2 && make kind-up` (runs `kind create cluster --config deploy/kind/kind-config.yaml` from the repo root, then `docker network connect kind kind-registry`).
`docker build -t localhost:5001/xstore:dev . && docker push localhost:5001/xstore:dev`
`helm upgrade --install xstore deploy/helm/xstore --set image.repository=localhost:5001/xstore --set image.tag=dev`
