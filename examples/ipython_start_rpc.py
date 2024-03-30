import logging
from argparse import ArgumentParser

from IPython import get_ipython
import rpyc

from k3ng import K3NGService

parser = ArgumentParser(
    prog="ipython_start_rpc",
    description="Configures an iPython shell to communicate to the rotator over RPC",
)
parser.add_argument(
    "rpc_port",
    type=int,
    nargs="?",
    default=K3NGService.DEFAULT_PORT,
    help="Port of RPC server on localhost",
)

logging.basicConfig(level=logging.INFO)

args = parser.parse_args()


rpc_conn = rpyc.connect("localhost", args.rpc_port)
rot = rpc_conn.root.K3NG

try:
    ipython = get_ipython()
    ipython.run_line_magic("load_ext", "autoreload")
    ipython.run_line_magic("autoreload", "2")
except ValueError:
    print("This script needs to be run in an iPython shell!")
    exit()
