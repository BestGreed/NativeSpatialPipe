#!/bin/bash

# This is intended to be a script for `git bisect run`, but can be also used manually.
# Copy it to root directory of the repo before use, to avoid being deleted/replaced by git checkouts.
# When running it, connect Inferno2pipe to a Dante device (or a different proven-good Inferno instance)
# that will transmit 1kHz sine wave.
# (see also sinegen.sh, may be useful)

rm /tmp/fifo || true
mkfifo /tmp/fifo

git submodule update --init --recursive || exit 125
cargo build -p inferno2pipe || exit 125
cargo run -p inferno2pipe -- -c 2 -o /tmp/fifo &
ffmpeg -nostdin -fflags nobuffer -t 20 -f s32le -sample_rate 48000 -ac 2 -i /tmp/fifo -af silencedetect=d=0.0000625 -y $(date '+%H-%M-%S').wav 2>&1 | grep silence_
r=$?
killall inferno2pipe

test $r -ne 0

