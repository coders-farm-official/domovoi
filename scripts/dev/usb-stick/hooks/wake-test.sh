#!/bin/sh
# TEST ONLY (room stick kit): one wake through a test audio feed on an
# installed, approved room. Uses the satellite's own test hooks
# (satellite/debug_audio.py): DOMOVOI_DEBUG_MIC_DIR feeds WAV files in as
# the microphone, DOMOVOI_DEBUG_AUDIO_SINK=wav:<dir> writes what it says
# to WAV files. A drop-in sets both for the room's service, then
# "hey jarvis" and "what time is it" go in, and the core's reply should come
# out. Markers on ttyS0 (domovoi-stick-testhook.service).
PATH=/usr/sbin:/usr/bin:/sbin:/bin
T=/usr/local/lib/domovoi-stick-test
H=/var/lib/domovoi-sat
SOCK=$H/.domovoi/control.sock
echo "DOMOVOI-STICK: HOOK-START wake-test"

until [ -e /var/lib/domovoi-stick/installed ]; do sleep 5; done
state() {  # the engine's connection state, asked on its control socket (root may)
	python3 -I -c '
import json, socket, sys
s = socket.socket(socket.AF_UNIX); s.settimeout(5); s.connect(sys.argv[1])
s.sendall(b"{\"verb\": \"status\"}\n"); buf = b""
while b"\n" not in buf:
    c = s.recv(65536)
    if not c: break
    buf += c
d = json.loads(buf.split(b"\n")[0]); print(d.get("connection"), d.get("mic"))' "$SOCK" 2>/dev/null
}
until state | grep -q "^connected"; do sleep 3; done
echo "DOMOVOI-STICK: WAKE-CONNECTED $(state)"

if [ ! -f /etc/systemd/system/domovoi-satellite.service.d/test-audio.conf ]; then
	mkdir -p /etc/systemd/system/domovoi-satellite.service.d
	printf '%s\n' "[Service]" "Environment=DOMOVOI_DEBUG_MIC_DIR=$H/test-mic" \
		"Environment=DOMOVOI_DEBUG_AUDIO_SINK=wav:$H/test-sink" \
		>/etc/systemd/system/domovoi-satellite.service.d/test-audio.conf
	runuser -u domovoi-sat -- mkdir -p "$H/test-mic" "$H/test-sink"
	systemctl daemon-reload
	systemctl restart domovoi-satellite.service
	sleep 3
	until state | grep -q "^connected"; do sleep 3; done
fi
since=$(date '+%Y-%m-%d %H:%M:%S')
n=0
until journalctl -u domovoi-satellite --no-pager | grep -q "wake word armed"; do
	sleep 2; n=$((n + 2)); [ "$n" -lt 120 ] || break
done
echo "DOMOVOI-STICK: WAKE-ARMED $(journalctl -u domovoi-satellite --no-pager | grep -c 'wake word armed')"
for try in 1 2; do
	install -o domovoi-sat -g domovoi-sat -m 0644 "$T/hey_jarvis.wav" "$H/test-mic/${try}1-wake.wav"
	n=0
	until state | grep -q " capturing"; do sleep 0.5; n=$((n + 1)); [ "$n" -lt 60 ] || break; done
	echo "DOMOVOI-STICK: WAKE-MIC $(state)"
	install -o domovoi-sat -g domovoi-sat -m 0644 "$T/what_time.wav" "$H/test-mic/${try}2-what-time.wav"
	n=0
	until journalctl -u domovoi-satellite --since "$since" --no-pager | grep -q "heard:"; do
		sleep 1; n=$((n + 1)); [ "$n" -lt 40 ] || break
	done
	journalctl -u domovoi-satellite --since "$since" --no-pager | grep -q "heard:" && break
done
heard=$(journalctl -u domovoi-satellite --since "$since" --no-pager | grep "heard:" | tail -1 | sed 's/.*heard: //')
echo "DOMOVOI-STICK: WAKE-HEARD ${heard:-nothing}"
n=0
until ls "$H"/test-sink/*speech*.wav >/dev/null 2>&1; do sleep 1; n=$((n + 1)); [ "$n" -lt 60 ] || break; done
sleep 5
for f in "$H"/test-sink/*.wav; do
	[ -f "$f" ] || continue
	echo "DOMOVOI-STICK: WAKE-SINK $(basename "$f") $(stat -c %s "$f") bytes"
done
echo "DOMOVOI-STICK: HOOK-DONE"
