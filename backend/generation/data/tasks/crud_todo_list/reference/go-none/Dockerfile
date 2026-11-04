FROM golang:1.25-trixie@sha256:2c4c60ef415fbfa5e90300722293bef36c5e63fae17570ce18f580af933dbd73 AS build
ENV GOTOOLCHAIN=local
WORKDIR /src
COPY backend/go.mod backend/go.sum* backend/
RUN cd backend && if [ -f go.sum ]; then go mod download; else go mod download all; fi
COPY backend/ backend/
RUN mkdir -p /out && cd backend && go build -trimpath -o /out/server .

FROM debian:trixie-slim@sha256:f6b08b09a05725f9431502d3b5714f5c4576e82c1a84fa1debe0c24a7e0c3ec4
ENV PORT=8000
WORKDIR /app
COPY --from=build /src/backend backend/
COPY --from=build /out/server /usr/local/bin/server
RUN mkdir -p data
EXPOSE 8000
CMD ["server"]
