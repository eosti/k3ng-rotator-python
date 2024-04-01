import logging
from argparse import ArgumentParser

import rpyc  # type: ignore

from k3ng import K3NGService

if __name__ == "__main__":
    parser = ArgumentParser(
        prog="rpc_telegraf",
        description="Polls a K3NG rotator over RPC for basic telemetry",
    )
    parser.add_argument(
        "rpc_port",
        type=int,
        nargs="?",
        default=K3NGService.DEFAULT_PORT,
        help="Port of RPC server on localhost",
    )

    args = parser.parse_args()

    rot = rpyc.connect("localhost", args.rpc_port).root.K3NG

    az = rot.get_azimuth()
    el = rot.get_elevation()
