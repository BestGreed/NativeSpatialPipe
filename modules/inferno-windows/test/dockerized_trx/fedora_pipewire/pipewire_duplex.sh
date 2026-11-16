#!/bin/sh -e

# TMPDIR change is needed as a workaround for datagram sockets not working correctly across containers
mkdir -p /shared/tmp-$INFERNO_NAME
export TMPDIR=/shared/tmp-$INFERNO_NAME

rm -rf $HOME/.local/state/inferno_aoip || true

sox -t null null -b 16 /shared/play-$INFERNO_NAME.wav synth $DURATION whitenoise whitenoise

/usr/bin/pipewire &
/usr/bin/wireplumber &
sleep 2

pw-cli create-node adapter '{ object.linger=1 factory.name=api.alsa.pcm.source node.name="Inferno source" media.class=Audio/Source api.alsa.path="inferno:RX_CHANNELS=6,TX_CHANNELS=10" session.suspend-timeout-seconds=0 node.pause-on-idle=false node.suspend-on-idle=false node.always-process=true api.alsa.headroom=128 api.alsa.pcm.card=999 }'
pw-cli create-node adapter '{ object.linger=1 factory.name=api.alsa.pcm.sink node.name="Inferno sink" media.class=Audio/Sink api.alsa.path="inferno:RX_CHANNELS=6,TX_CHANNELS=10" session.suspend-timeout-seconds=0 node.pause-on-idle=false node.suspend-on-idle=false node.always-process=true api.alsa.headroom=128 api.alsa.pcm.card=999 }'

pw-play --rate=$INFERNO_SAMPLE_RATE --channels=2 --format=s16 /shared/play-$INFERNO_NAME.wav &
sox --no-dither -c 2 -b 16 -e signed-integer -r $INFERNO_SAMPLE_RATE -t alsa pipewire -b 16 /shared/rec-$INFERNO_NAME.wav trim 0 $DURATION

echo done > /shared/done-$INFERNO_NAME
