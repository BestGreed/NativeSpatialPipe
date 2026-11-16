#!/bin/sh -e

# TMPDIR change is needed as a workaround for datagram sockets not working correctly across containers
mkdir -p /shared/tmp-$INFERNO_NAME
export TMPDIR=/shared/tmp-$INFERNO_NAME

rm -rf $HOME/.local/state/inferno_aoip || true

rm $TMPDIR/fifo || true
mkfifo $TMPDIR/fifo
inferno2pipe -c 2 -o $TMPDIR/fifo &
head -c$((INFERNO_SAMPLE_RATE * 2 * 4 * (DURATION-7))) < $TMPDIR/fifo > /shared/rec-$INFERNO_NAME.raw

echo done > /shared/done-$INFERNO_NAME
