"""Drive the Python HOME side of the handshake over stdin/stdout JSON lines."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin-ltl-remote"))
from domovoi_plugin_ltl_remote import crypto

def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()

household = crypto.generate_keypair()
out({"t": "household_pub", "dh": household.public_b64})

hs = crypto.HomeHandshake(household)
link = None
for raw in sys.stdin:
    msg = json.loads(raw)
    if msg["t"] == "client_hello":
        device_pub = crypto.unb64u(msg["device_pub"])
        out(hs.respond(msg, device_pub))
    elif msg["t"] == "client_confirm":
        link = hs.finish(msg)
        out({"t": "sealed_ok"})
    elif msg["t"] == "sealed":
        plaintext = link.open(crypto.unb64u(msg["frame"]))
        out({"t": "opened", "text": plaintext.decode()})
        reply = link.seal(("home received: " + plaintext.decode()).encode())
        out({"t": "sealed", "frame": crypto.b64u(reply)})
    elif msg["t"] == "done":
        break
