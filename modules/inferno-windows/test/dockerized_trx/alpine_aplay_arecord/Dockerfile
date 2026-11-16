FROM inferno_aoip:alpine-alsa
RUN apk add alsa-utils sox
RUN mkdir -p /app
ADD alsa_duplex.sh /app
CMD [ "/app/alsa_duplex.sh" ]
