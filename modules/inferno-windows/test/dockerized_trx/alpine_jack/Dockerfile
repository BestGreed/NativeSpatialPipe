FROM alpine:3.22 AS builder
RUN apk add git build-base jack-dev
WORKDIR /app
RUN git clone https://github.com/x42/jack-stdio
WORKDIR /app/jack-stdio
RUN make all

FROM inferno_aoip:alpine-alsa
RUN apk add jack

RUN mkdir -p /app/jack-stdio
COPY --from=builder /app/jack-stdio/jack-stdin /app/
COPY --from=builder /app/jack-stdio/jack-stdout /app/

ADD jack_duplex.sh /app
CMD [ "/app/jack_duplex.sh" ]
