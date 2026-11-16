FROM alpine:3.22
RUN apk add build-base
ADD fake_usrvclock_server.c /app/
RUN gcc -o /app/fake_usrvclock_server /app/fake_usrvclock_server.c
ENV USRVCLOCK_SOCKET=/shared/usrvclock
CMD [ "/app/fake_usrvclock_server" ]
