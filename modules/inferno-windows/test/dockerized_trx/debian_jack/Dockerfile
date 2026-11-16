FROM inferno_aoip:debian-alsa
RUN apt-get update && apt-get install -y --no-install-recommends jack-stdio jackd2 && rm -rf /var/lib/apt/lists/*

ADD jack_duplex.sh /app/
CMD [ "/app/jack_duplex.sh" ]
