FROM golang:1.26-alpine AS builder
WORKDIR /src
COPY go.mod *.go page.html ./
RUN CGO_ENABLED=0 go build -o /panel .

FROM scratch
COPY --from=builder /panel /panel
ENTRYPOINT ["/panel"]
