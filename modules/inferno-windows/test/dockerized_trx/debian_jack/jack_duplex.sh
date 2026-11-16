#!/bin/sh -e

# TMPDIR change is needed as a workaround for datagram sockets not working correctly across containers
mkdir -p /shared/tmp-$INFERNO_NAME
export TMPDIR=/shared/tmp-$INFERNO_NAME
#export INFERNO_DISABLE_POLLFD=1

rm -rf $HOME/.local/state/inferno_aoip || true
head -c$((INFERNO_SAMPLE_RATE * 2 * 2 * DURATION)) < /dev/urandom > /shared/play-$INFERNO_NAME.raw

/usr/bin/jackd -dalsa -dinferno -r$INFERNO_SAMPLE_RATE -p1024 -n3 &
sleep 2

jack-stdin --duration $DURATION system:playback_1 system:playback_2 < /shared/play-$INFERNO_NAME.raw &
jack-stdout --duration $DURATION system:capture_1 system:capture_2 > /shared/rec-$INFERNO_NAME.raw

echo done > /shared/done-$INFERNO_NAME
