FROM inferno_aoip:fedora-alsa
RUN dnf install -y pipewire wireplumber pipewire-libs pipewire-utils sox
WORKDIR /app
ENV XDG_RUNTIME_DIR=/tmp
ADD pipewire_duplex.sh /app
CMD [ "/app/pipewire_duplex.sh" ]
