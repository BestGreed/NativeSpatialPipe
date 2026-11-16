#!/bin/sh -e

cd /shared

while ! netaudio device list > devlist && \
    test `grep jack < devlist | wc -l` -eq 2 && \
    test `grep aplayrec < devlist | wc -l` -eq 4 && \
    test `grep pipewire < devlist | wc -l` -eq 1 && \
    test `grep i2pipe < devlist | wc -l` -eq 1
do sleep 1; done


echo '✅ device list test passed'

substart=$(date +%s)

netaudio subscription add --rx-device-name jack1 --rx-channel-name 'RX 1' --tx-device-name jack2 --tx-channel-name 'TX 1' &
netaudio subscription add --rx-device-name jack2 --rx-channel-name 'RX 1' --tx-device-name jack1 --tx-channel-name 'TX 1' &
netaudio subscription add --rx-device-name aplayrec1c --rx-channel-name 'RX 1' --tx-device-name pipewire1 --tx-channel-name 'TX 1' &
netaudio subscription add --rx-device-name aplayrec2c --rx-channel-name 'RX 1' --tx-device-name aplayrec1p --tx-channel-name 'TX 1' &
netaudio subscription add --rx-device-name pipewire1 --rx-channel-name 'RX 1' --tx-device-name aplayrec1p --tx-channel-name 'TX 1' &
netaudio subscription add --rx-device-name i2pipe1 --rx-channel-name 'RX 1' --tx-device-name jack1 --tx-channel-name 'TX 1'

netaudio subscription add --rx-device-name jack1 --rx-channel-name 'RX 2' --tx-device-name jack2 --tx-channel-name 'TX 2' &
netaudio subscription add --rx-device-name jack2 --rx-channel-name 'RX 2' --tx-device-name jack1 --tx-channel-name 'TX 2' &
netaudio subscription add --rx-device-name aplayrec1c --rx-channel-name 'RX 2' --tx-device-name pipewire1 --tx-channel-name 'TX 2' &
netaudio subscription add --rx-device-name aplayrec2c --rx-channel-name 'RX 2' --tx-device-name aplayrec1p --tx-channel-name 'TX 2' &
netaudio subscription add --rx-device-name pipewire1 --rx-channel-name 'RX 2' --tx-device-name aplayrec1p --tx-channel-name 'TX 2' &
netaudio subscription add --rx-device-name i2pipe1 --rx-channel-name 'RX 2' --tx-device-name jack1 --tx-channel-name 'TX 2'

wait
subend=$(date +%s)

signal_min_seconds=$((DURATION-8-subend+substart))
if test $signal_min_seconds -lt $((DURATION/4)); then
    echo '❌ wasted too much time'
    exit 1
fi

echo '✅ after subscribe'

netaudio subscription list > sublist
test `grep jack sublist | wc -l` -eq 6
test `grep aplayrec sublist | wc -l` -eq 6
test `grep pipewire sublist | wc -l` -eq 4
test `grep i2pipe sublist | wc -l` -eq 2

echo '✅ subscription test passed, waiting for recorded audio'


function wait_for {
    while test -n "$1"; do
        while ! test -e "done-$1"; do
            sleep 1
        done
        shift
    done
}

wait_for jack1 jack2 aplayrec1 aplayrec2 pipewire1 i2pipe1

samples_expected=$((DURATION*INFERNO_SAMPLE_RATE))
tolerance=$INFERNO_SAMPLE_RATE

echo "Minimal signal duration: $signal_min_seconds seconds"
function test_recording {
    infile="rec-$1.wav"
    echo "Testing file $infile"

    samples=$((`stat -c %s $infile`/4))
    test $samples -ge $((signal_min_seconds*INFERNO_SAMPLE_RATE))
    test $samples -le $((samples_expected+tolerance))
    echo '  ✅ recording length test passed'

    seconds_nonsilent="$(sox $infile recns-$1.wav silence 1 0.1 1% stat 2>&1 | sed -E 's/^Length \(seconds\):\s+([0-9]+).*$/\1/; t; d')"
    echo "  seconds non-silent: $seconds_nonsilent"
    test $seconds_nonsilent -ge $signal_min_seconds
    echo '  ✅ signal presence test passed'

    seconds_both_channels="$(sox $infile -t null null remix 2 silence 1 0.1 1% stat 2>&1 | sed -E 's/^Length \(seconds\):\s+([0-9]+).*$/\1/; t; d')"
    echo "  seconds non-silent both channels: $seconds_both_channels"
    test $seconds_both_channels -ge $signal_min_seconds
    echo '  ✅ signal presence test passed (both channels)'

    (/diff_tools/wd -align$samples_expected -ll play-$2.wav recns-$1.wav || true) | tee report-$1.txt
    grep '|MATCH' report-$1.txt
    echo '  ✅ longer channel recording matching'
    test `sed -E 's/^Compared:\s+([0-9]+).*$/\1/; t; d' < report-$1.txt` -ge $signal_min_seconds
    echo '  ✅ longer channel recording long enough'

    sox $infile recnsbc-$1.wav trim $((samples-seconds_both_channels*INFERNO_SAMPLE_RATE))s
    /diff_tools/wd -align$samples_expected -ll play-$2.wav recnsbc-$1.wav | tee reportbc-$1.txt
    echo '  ✅ shorter channel recording matching'
    test `sed -E 's/^Compared:\s+([0-9]+).*$/\1/; t; d' < reportbc-$1.txt` -ge $((seconds_both_channels*INFERNO_SAMPLE_RATE*8/10))
    echo '  ✅ shorter channel recording long enough'

    dd if=/dev/zero of=recnsbc-$1.wav bs=1 seek=100000 count=2 conv=notrunc
    ! /diff_tools/wd -align$samples_expected -ll play-$2.wav recnsbc-$1.wav
    echo '  ✅ wd tool correctly detects damaged data'

    echo "✅ $infile all OK"
}

function raw_to_wav {
    sox -t raw -e signed-integer -b 16 -c 2 -r $INFERNO_SAMPLE_RATE /shared/$1.raw /shared/$1.wav
    rm /shared/$1.raw
}

raw_to_wav play-jack1
raw_to_wav play-jack2
raw_to_wav rec-jack1
raw_to_wav rec-jack2
sox --no-dither -t raw -e signed-integer -b 32 -c 2 -r $INFERNO_SAMPLE_RATE /shared/rec-i2pipe1.raw -b 16 /shared/rec-i2pipe1.wav
rm /shared/rec-i2pipe1.raw

test_recording aplayrec1 pipewire1
test_recording aplayrec2 aplayrec1
test_recording pipewire1 aplayrec1
test_recording i2pipe1 jack1

test_recording jack1 jack2
test_recording jack2 jack1

rm -f /shared/rec* /shared/play*
