#!/bin/sh -e

# TMPDIR change is needed as a workaround for datagram sockets not working correctly across containers
mkdir -p /shared/tmp-$INFERNO_NAME
export TMPDIR=/shared/tmp-$INFERNO_NAME

rm -rf $HOME/.local/state/inferno_aoip || true

sox -t null null -b 16 /shared/play-$INFERNO_NAME.wav synth $DURATION whitenoise whitenoise
sox /shared/play-$INFERNO_NAME.wav -e signed-integer -b 32 /shared/play-native-$INFERNO_NAME.wav

INFERNO_PROCESS_ID=1 INFERNO_NAME=${INFERNO_NAME}p INFERNO_ALT_PORT=10100 aplay -D inferno /shared/play-native-$INFERNO_NAME.wav &
INFERNO_PROCESS_ID=2 INFERNO_NAME=${INFERNO_NAME}c INFERNO_ALT_PORT=10200 arecord -D inferno -d $DURATION -c 2 -r $INFERNO_SAMPLE_RATE -f S32_LE /shared/rec-native-$INFERNO_NAME.wav

sox --no-dither /shared/rec-native-$INFERNO_NAME.wav -e signed-integer -b 16 /shared/rec-$INFERNO_NAME.wav

rm /shared/rec-native-$INFERNO_NAME.wav /shared/play-native-$INFERNO_NAME.wav

echo done > /shared/done-$INFERNO_NAME
