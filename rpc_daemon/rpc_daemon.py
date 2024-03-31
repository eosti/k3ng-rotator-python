import logging
from argparse import ArgumentParser

from rpyc.utils.server import ThreadedServer  # type: ignore

from k3ng import K3NGService


def do_daemon(ser_port: str, rpc_port: int) -> None:
    t = ThreadedServer(K3NGService(ser_port), port=rpc_port)
    t.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = ArgumentParser(
        prog="rpc_daemon", description="Start an RPC daemon for distributed control"
    )

    parser.add_argument(
        "serial_port", help="Serial port connected to the K3NG rotator Arduino"
    )
    parser.add_argument(
        "rpc_port",
        type=int,
        nargs="?",
        default=K3NGService.DEFAULT_PORT,
        help="Port for RPC to bind to",
    )

    args = parser.parse_args()

    do_daemon(args.serial_port, args.rpc_port)
