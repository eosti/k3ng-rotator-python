import logging
from argparse import ArgumentParser

from IPython import get_ipython  # type: ignore

from k3ng import RotctlK3NG

parser = ArgumentParser(
    prog="ipython_start_rotctl",
    description="Configures an iPython shell to communicate to the rotator over rotctl",
)
parser.add_argument(
    "host",
    help="Hostname or IP address associated with rotctl",
)

parser.add_argument(
    "port",
    help="Port associated with rotctl",
    nargs='?',
    default=4533,
    type=int
)

logging.basicConfig(level=logging.INFO)

args = parser.parse_args()

rot = RotctlK3NG(args.host, args.port)

try:
    ipython = get_ipython()
    ipython.run_line_magic("load_ext", "autoreload")
    ipython.run_line_magic("autoreload", "2")
except ValueError:
    print("This script needs to be run in an iPython shell!")
    exit()
